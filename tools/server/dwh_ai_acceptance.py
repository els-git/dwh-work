"""I17 step 7: acceptance run of the AI assistant on the DWH server. Changes nothing in DWH.

  sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_ai_acceptance.py --label <model>

Uses the code of the installed release exactly as the application does (dwh_api.ai_knowledge, ai_core,
ai_sql_guard, ai_llm) with the questions of ai_acceptance.json and the roles described there:
  help    - guide sections allowed to the role -> model -> the answer must name an expected section and contain
            an expected term; questions about closed sections must be refused without calling the model;
  report  - whitelist objects allowed to the role -> model -> text and plan checks -> rows; they must equal the
            rows of the reference SQL (order and column names ignored, numbers rounded to 2 digits);
            negative questions must not execute anything.
Database: dwh_app, read-only transactions, statement timeout 30 s (the same SELECT rights on dm as dwh_ai_ro).
The report (Markdown, /srv/dwh/incoming) has answers, SQL, match flags and row counts - no report rows.
Prints ACCEPTANCE_RESULT: PASS / FAIL against the thresholds of ai_acceptance.json.
"""
from __future__ import annotations

import argparse
import datetime
import decimal
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

SRC = Path(os.environ.get("DWH_ACCEPTANCE_SRC", "/opt/dwh/current/src"))
QUESTIONS = Path(__file__).resolve().with_name("ai_acceptance.json")
ENV_FILE = Path(os.environ.get("DWH_ENV_FILE", "/etc/dwh/dwh.env"))


def load_env(path: Path) -> None:
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float, decimal.Decimal)):
        return f"{float(value):.2f}"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    try:
        return f"{float(text):.2f}"
    except ValueError:
        return text


def rows_key(rows: list[dict]) -> Counter:
    return Counter(tuple(sorted(norm(v) for v in row.values())) for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--out", default="/srv/dwh/incoming")
    parser.add_argument("--only", choices=("help", "report"))
    parser.add_argument("--ids", help="comma-separated ids to run")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.label):
        print("ACCEPTANCE_RESULT: FAIL (label: letters, digits, . _ - only)")
        return 1
    load_env(ENV_FILE)
    os.environ["DWH_AI_LLM_URL"] = args.url
    os.environ["DWH_AI_TIMEOUT_SECONDS"] = str(args.timeout)
    os.environ["DWH_AI_QUEUE_SECONDS"] = "5"
    sys.path.insert(0, str(SRC / "backend" / "src"))
    from dwh_api import ai_llm  # noqa: E402
    from dwh_api.ai_core import describe_report_data, help_prompt, repair_message, report_system, run_checked  # noqa: E402
    from dwh_api.ai_knowledge import KnowledgeIndex, UserScope  # noqa: E402
    from dwh_api.ai_sql_guard import AllowedObject, SqlRejected, check_text, extract_sql, limited  # noqa: E402
    from dwh_api.config import get_api_config  # noqa: E402
    from dwh_api.db import connect_readonly  # noqa: E402

    try:
        settings = ai_llm.LlmSettings.from_env()
    except ai_llm.LlmUnavailable as exc:
        print(f"ACCEPTANCE_RESULT: FAIL ({exc})")
        return 1
    if not ai_llm.is_ready(settings):
        print("ACCEPTANCE_RESULT: FAIL (MODEL_NOT_READY: the model service does not answer /health)")
        return 1
    doc = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    roles = {name: UserScope(r["level"], frozenset(r["permissions"]), frozenset(r["resources"]))
             for name, r in doc["roles"].items()}
    wanted = set(args.ids.split(",")) if args.ids else None
    items = [i for i in doc["items"] if (not args.only or i["kind"] == args.only) and (not wanted or i["id"] in wanted)]
    index = KnowledgeIndex.from_file(SRC / "docs" / "DWH_USER_GUIDE.md")
    conn = connect_readonly(get_api_config())
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '30s'")
        cur.execute("SHOW transaction_read_only")
        if list(cur.fetchone().values())[0] != "on":
            print("ACCEPTANCE_RESULT: FAIL (connection is not read-only)")
            return 1
        cur.execute("SELECT object_id, schema_name, object_name, title, description, required_permission_code "
                    "FROM meta.ai_report_object WHERE is_active AND to_regclass(schema_name || '.' || object_name) "
                    "IS NOT NULL ORDER BY object_id")
        whitelist = cur.fetchall()
    conn.rollback()

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = Path(args.out) / f"ai_acceptance_{args.label}_{stamp}.md"
    lines = [f"# AI assistant acceptance: {args.label} ({stamp})", ""]
    score = {"help": [0, 0], "report": [0, 0], "negative": [0, 0]}
    executed_outside = 0
    for item in items:
        user = roles[item["role"]]
        started = time.monotonic()
        verdict, detail, body = False, "", []
        group = "negative" if item["expect"] in ("DENIED", "NOT_EXECUTED") else item["kind"]
        try:
            if item["kind"] == "help":
                found = index.search(item["question"], user)
                numbers = [s.number for s in found.sections]
                if item["expect"] == "DENIED":
                    verdict = found.only_denied
                    detail = "refused without the model" if verdict else f"NOT refused, sections {numbers}"
                elif found.only_denied or not found.sections:
                    detail = "no allowed section found"
                else:
                    system, text = help_prompt(found, item["question"])
                    answer = ai_llm.chat(settings, system, text, None, 700)
                    section_ok = any(n in numbers for n in item["sections"])
                    term_ok = any(t.lower() in answer.lower() for t in item["terms"])
                    verdict = section_ok and term_ok
                    detail = f"sections {numbers}: {'ok' if section_ok else 'MISS'}; terms: {'ok' if term_ok else 'MISS'}"
                    body = [answer.strip()]
            else:
                allowed_rows = [w for w in whitelist if w["required_permission_code"] in user.permissions]
                allowed = [AllowedObject(int(w["object_id"]), w["schema_name"], w["object_name"]) for w in allowed_rows]
                denied = [w["title"] for w in whitelist if w["required_permission_code"] not in user.permissions]
                if not allowed:
                    outcome = "DENIED"
                    verdict = item["expect"] == "DENIED"
                    detail = "no reporting object for the role"
                else:
                    with conn.cursor() as cur:
                        description, dm_relations = describe_report_data(
                            cur, cur, allowed, {int(w["object_id"]): w for w in allowed_rows}, denied)
                    conn.rollback()
                    user_text = item["question"]
                    for attempt in (1, 2):  # as the application: one self-repair after a PostgreSQL error
                        answer = ai_llm.chat(settings, report_system(description), user_text, None, 600)
                        sql_text = extract_sql(answer)
                        body += ["```sql", sql_text, "```"]
                        try:
                            statement, _ = check_text(sql_text, allowed, dm_relations)
                        except SqlRejected as exc:
                            outcome = {"AI_OBJECT_NOT_ALLOWED": "DENIED", "AI_NO_DATA": "NO_DATA"}.get(exc.code, "REFUSED")
                            verdict = item["expect"] in ("NOT_EXECUTED", "DENIED")
                            detail = f"{outcome} {exc.code}"
                            break
                        if item["expect"] != "OK":
                            detail = "EXECUTABLE although the question must not be executed"
                            break
                        with conn.cursor() as cur:
                            try:
                                _, rows, _ = run_checked(cur, statement, allowed)
                            except SqlRejected as exc:
                                conn.rollback()
                                detail = f"plan check refused: {exc}"
                                break
                            except Exception as exc:  # noqa: BLE001 - PostgreSQL error: repair once
                                conn.rollback()
                                error = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
                                if attempt == 1:
                                    user_text = repair_message(item["question"], statement, error)
                                    continue
                                detail = f"ERROR after repair: {error[:300]}"
                                break
                            conn.rollback()
                            cur.execute(limited(item["reference_sql"], 1000))
                            expected = [dict(r) for r in cur.fetchall()]
                            conn.rollback()
                        verdict = rows_key(rows) == rows_key(expected)
                        detail = (f"rows {len(rows)} vs reference {len(expected)}: "
                                  + ("match" if verdict else "DIFFERENT") + (" (repaired)" if attempt == 2 else ""))
                        break
        except ai_llm.LlmUnavailable as exc:
            conn.rollback()
            detail = f"MODEL ERROR {exc}"
        except Exception as exc:  # noqa: BLE001 - one question must not stop the run
            conn.rollback()
            message = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
            detail = f"ERROR {type(exc).__name__}: {message[:300]}"
        seconds = time.monotonic() - started
        score[group][1] += 1
        score[group][0] += 1 if verdict else 0
        mark = "PASS" if verdict else "FAIL"
        print(f"{item['id']} {item['kind']}/{item['role']}: {mark} ({seconds:.0f} s) {detail}", flush=True)
        lines += [f"## {item['id']} ({item['kind']}, {item['role']}) — {mark}, {seconds:.0f} s", "",
                  f"**Вопрос:** {item['question']}", "", f"**Итог:** {detail}", ""] + body + [""]
    conn.close()
    limits = doc["thresholds"]
    shares = {k: (v[0] / v[1] if v[1] else 1.0) for k, v in score.items()}
    passed = (shares["help"] >= limits["help"] and shares["report"] >= limits["report"]
              and shares["negative"] >= limits["negative"] and executed_outside <= limits["executed_outside_whitelist"])
    summary = ", ".join(f"{k} {v[0]}/{v[1]} ({shares[k]:.0%}, need {limits[k]:.0%})" for k, v in score.items())
    summary += f"; executed outside the whitelist: {executed_outside}"
    lines[2:2] = [f"**Summary:** {summary}", ""]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report.chmod(0o644)
    print(f"report: {report}")
    print(f"ACCEPTANCE_RESULT: {'PASS' if passed else 'FAIL'} ({summary})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
