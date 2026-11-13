"""I17 step 2: probe of a local LLM on the DWH server. Changes nothing in DWH.

  sudo /opt/dwh/current/venv/bin/python /opt/dwh/current/src/tools/server/dwh_llm_probe.py --label <model name>

Asks a running llama-server (OpenAI-compatible API, only 127.0.0.1) the questions of
llm_probe_questions.json:
  help - questions about the application, answered from pieces of the DWH documentation found by words;
  sql  - report descriptions turned into SQL over the reporting views (schema dm), described to the
         model by column names and types from the catalog (no data).
Each SQL answer is checked with EXPLAIN only (the query is NOT executed) as dwh_app in a read-only
transaction. Speed (tokens per second) comes from llama-server. Result: a Markdown report (answers,
SQL, EXPLAIN result, timings) next to --out; it contains no business data. Prints PROBE_RESULT.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

SRC = Path("/opt/dwh/current/src")  # documentation of the installed release
QUESTIONS = Path(__file__).resolve().with_name("llm_probe_questions.json")
DOCS = ("docs/DWH_CURRENT_FUNCTIONALITY.md", "docs/DWH_SERVER_RUNBOOK.md", "docs/LOCAL_DWH_RUNBOOK.md")
ENV_FILE = Path("/etc/dwh/dwh.env")
SQL_SCHEMAS = ("dm",)
WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁёƏəĞğİıÖöŞşÜüÇç_]{3,}")
SQL_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)

HELP_SYSTEM = ("Ты помощник по приложению DWH. Отвечай кратко, по-русски, только по приведённой справке. "
               "Называй экран или раздел, где это делается. Если в справке ответа нет, так и скажи.")
SQL_SYSTEM = ("Ты пишешь SQL для PostgreSQL 17 по описанию отчёта. Можно использовать только таблицы и колонки "
              "из описания ниже, только SELECT. Верни один запрос в блоке ```sql```, без пояснений. "
              "Если нужных данных нет в описании, верни ```sql\n-- NO_DATA\n```.")


def chunks(text: str, size: int = 1500) -> list[str]:
    parts, current = [], ""
    for block in re.split(r"\n(?=#)", text):
        while len(block) > size:
            parts.append(block[:size])
            block = block[size:]
        if len(current) + len(block) > size and current:
            parts.append(current)
            current = ""
        current += "\n" + block
    if current.strip():
        parts.append(current)
    return parts


def words(text: str) -> set[str]:
    return {w.lower()[:6] for w in WORD_RE.findall(text)}


def find_context(question: str, pieces: list[str], top: int = 4) -> str:
    wanted = words(question)
    scored = sorted(pieces, key=lambda p: len(wanted & words(p)), reverse=True)
    return "\n---\n".join(scored[:top])


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def connect():
    import psycopg  # from the DWH environment
    env = read_env(ENV_FILE)
    return psycopg.connect(host=env.get("DWH_DB_HOST", "127.0.0.1"), port=int(env.get("DWH_DB_PORT", "5432")),
                           dbname=env.get("DWH_DB_NAME", "dwh"), user="dwh_app",
                           password=env["DWH_APP_PASSWORD"], options="-c default_transaction_read_only=on",
                           connect_timeout=10)


def schema_text(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT table_schema || '.' || table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = ANY(%s) ORDER BY 1, ordinal_position", (list(SQL_SCHEMAS),))
        rows = cur.fetchall()
    conn.rollback()
    tables: dict[str, list[str]] = {}
    for table, column, kind in rows:
        tables.setdefault(table, []).append(f"{column} {kind}")
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())


def explain(conn, sql: str) -> str:
    if not sql or sql.lstrip().startswith("-- NO_DATA"):
        return "NO_DATA"
    statement = sql.strip().rstrip(";")
    if ";" in statement or not re.match(r"(?is)^\s*(select|with)\b", statement):
        return "REFUSED (not a single SELECT)"
    try:
        with conn.cursor() as cur:
            cur.execute("EXPLAIN " + statement)
        return "OK"
    except Exception as exc:  # noqa: BLE001 - reported per question
        return "ERROR " + str(getattr(getattr(exc, "diag", None), "message_primary", None) or type(exc).__name__)
    finally:
        conn.rollback()


def ask(url: str, system: str, user: str, max_tokens: int, timeout: int) -> tuple[str, dict, float]:
    body = json.dumps({"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       "temperature": 0.1, "max_tokens": max_tokens}).encode()
    request = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost only
        data = json.loads(response.read())
    message = data["choices"][0]["message"]
    timings = dict(data.get("timings") or {})
    timings["reasoning_chars"] = len(message.get("reasoning_content") or "")
    timings["finish_reason"] = data["choices"][0].get("finish_reason")
    return message.get("content") or "", timings, time.monotonic() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="model name for the report")
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--out", default="/srv/dwh/incoming")
    parser.add_argument("--only", choices=("help", "sql"))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="answer limit incl. reasoning (models that think need 3000+); default 500 help / 400 SQL")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.label):
        print("PROBE_RESULT: FAIL (label: letters, digits, . _ - only)")
        return 1
    if not args.url.startswith(("http://127.0.0.1", "http://localhost")):
        print("PROBE_RESULT: FAIL (the model must be on this server: 127.0.0.1)")
        return 1
    questions = [q for q in json.loads(QUESTIONS.read_text()) if not args.only or q["kind"] == args.only]
    pieces = [p for doc in DOCS if (SRC / doc).is_file() for p in chunks((SRC / doc).read_text())]
    conn = connect()
    schema = schema_text(conn)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = Path(args.out) / f"llm_probe_{args.label}_{stamp}.md"
    lines = [f"# LLM probe: {args.label} ({stamp})", "", f"Schema given for SQL: {len(schema.splitlines())} tables", ""]
    speeds, failures = [], 0
    checks = {"OK": 0, "NO_DATA": 0, "ERROR": 0}
    for q in questions:
        if q["kind"] == "help":
            system, user, limit = HELP_SYSTEM, f"Справка:\n{find_context(q['text'], pieces)}\n\nВопрос: {q['text']}", args.max_tokens or 500
        else:
            system, user, limit = SQL_SYSTEM + "\n\nТаблицы:\n" + schema, q["text"], args.max_tokens or 400
        try:
            answer, timings, seconds = ask(args.url, system, user, limit, args.timeout)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            lines += [f"## {q['id']} ({q['kind']}) — ERROR {type(exc).__name__}: {exc}", ""]
            print(f"{q['id']}: ERROR {type(exc).__name__}")
            continue
        speed = timings.get("predicted_per_second")
        if speed:
            speeds.append(speed)
        head = (f"## {q['id']} ({q['kind']}) — {seconds:.0f} s" + (f", {speed:.1f} tok/s" if speed else "")
                + (f", reasoning {timings['reasoning_chars']} chars" if timings.get("reasoning_chars") else "")
                + (" — CUT (limit reached)" if timings.get("finish_reason") == "length" else ""))
        lines += [head, "", f"**Вопрос:** {q['text']}", ""]
        if q["kind"] == "sql":
            found = SQL_RE.search(answer)
            sql = (found.group(1) if found else answer).strip()
            check = explain(conn, sql)
            checks[check if check in ("OK", "NO_DATA") else "ERROR"] += 1
            lines += ["```sql", sql, "```", "", f"EXPLAIN: {check}", ""]
            print(f"{q['id']}: {seconds:.0f} s, EXPLAIN {check}")
        else:
            lines += [answer.strip(), ""]
            print(f"{q['id']}: {seconds:.0f} s")
    conn.close()
    average = sum(speeds) / len(speeds) if speeds else 0
    summary = (f"answers {len(questions) - failures}/{len(questions)}, SQL: EXPLAIN OK {checks['OK']}, "
               f"NO_DATA {checks['NO_DATA']}, errors {checks['ERROR']}; average {average:.1f} tok/s")
    lines[3:3] = [f"**Summary:** {summary}", ""]
    report.write_text("\n".join(lines) + "\n")
    report.chmod(0o644)
    print(f"report: {report}")
    print(f"PROBE_RESULT: {'PASS' if not failures else 'FAIL'} ({summary})")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
