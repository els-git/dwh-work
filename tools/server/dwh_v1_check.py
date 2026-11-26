"""I14-05: read-only acceptance check of the DWH server (v1). Changes nothing.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_v1_check.py

Plan docs/DWH_I14_V1_ACCEPTANCE_PLAN_APPROVED_20261126.md (R4 = A). Sections:
  S  services: dwh-api, dwh-dispatcher, nginx, postgresql and the copy timer are active (FAIL);
     dwh-llm and dwh-test-api only reported (WARN when not active)
  R  release: /opt/dwh/current points to an installed release
  C  copies: the newest /backup/dwh/dwh_*.dump is at most 26 hours old and its .sha256 matches
  T  TLS (dwh_tls.py check) and SecretStore (dwh_secret_check.py) of the same release: their result lines
  P  packages 016..036 of tools/server/sql_packages.json applied (marker present); check of the last packages
     (036, 035) as dwh_app in a read-only transaction gives the expected number of PASS
  Q  queues and runs: no request of objects or marts QUEUED/CLAIMED/RUNNING longer than 2 hours, no run
     RUNNING longer than 2 hours, no enabled schedule late by more than 30 minutes (the dispatcher fires them)
  I  information only: objects by state of the last request, errors of the last 7 days by kind
     (backend/src/dwh_api/error_groups.py), audit events of the last 24 hours
The database is read by psql as postgres (peer login, no passwords) in read-only transactions; the checks of
packages run under SET ROLE dwh_app. Output has no passwords and no business rows. Report:
/srv/dwh/incoming/v1_check_<time>.txt. Prints V1_CHECK_RESULT: PASS / FAIL (WARN does not fail).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
PACKAGES = TOOLS / "sql_packages.json"
REPORTS = Path("/srv/dwh/incoming")
BACKUPS = Path("/backup/dwh")
CURRENT = Path("/opt/dwh/current")
REQUIRED_UNITS = ("dwh-api.service", "dwh-dispatcher.service", "nginx.service", "postgresql.service",
                  "dwh-backup.timer")
OPTIONAL_UNITS = ("dwh-llm.service", "dwh-test-api.service")
CHECKED_PACKAGES = ("036", "035")
BACKUP_MAX_HOURS = 26
STUCK_HOURS = 2
SCHEDULE_LATE_MINUTES = 30
ACTIVE = ("QUEUED", "CLAIMED", "RUNNING")


class Report:
    def __init__(self, path: Path | None):
        self.path = path
        self.levels: list[str] = []

    def out(self, text: str) -> None:
        print(text, flush=True)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")

    def add(self, level: str, text: str) -> None:
        self.levels.append(level)
        self.out(f"{level:4}  {text}")

    def check(self, ok: bool, text: str, warn: bool = False) -> None:
        self.add("PASS" if ok else ("WARN" if warn else "FAIL"), text)

    def info(self, text: str) -> None:
        self.out(f"      {text}")

    def count(self, level: str) -> int:
        return self.levels.count(level)


def run_cmd(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=stdin, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
                          env=dict(os.environ, PGCLIENTENCODING="UTF8"))


class Db:
    """psql as postgres; every statement in a read-only transaction."""

    def __init__(self, psql: list[str], database: str, runner=run_cmd):
        self.psql = psql
        self.database = database
        self.runner = runner

    def json(self, sql: str):
        text = "SET default_transaction_read_only = on;\n" + sql
        result = self.runner([*self.psql, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", self.database, "-f", "-"],
                             text)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-600:])
        text = result.stdout.strip()
        return json.loads(text) if text else None

    def check_file(self, path: Path, role: str = "dwh_app") -> subprocess.CompletedProcess:
        return self.runner([*self.psql, "-X", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-d", self.database,
                            "-c", "SET default_transaction_read_only = on", "-c", f"SET ROLE {role}",
                            "-f", str(path)])


# --- S, R, C, T ---------------------------------------------------------------------------------------------------

def check_services(report: Report, runner=run_cmd) -> None:
    for unit in REQUIRED_UNITS + OPTIONAL_UNITS:
        state = runner(["systemctl", "is-active", unit]).stdout.strip() or "unknown"
        report.check(state == "active", f"service {unit}: {state}", warn=unit in OPTIONAL_UNITS)


def check_release(report: Report, current: Path = CURRENT) -> None:
    if not current.is_symlink():
        report.add("FAIL", f"release: {current} is not a link to a release")
        return
    target = Path(os.path.realpath(current))
    report.check(target.is_dir(), f"release: {target.name} ({target})")


def check_backups(report: Report, directory: Path = BACKUPS, now: datetime.datetime | None = None) -> None:
    dumps = sorted(directory.glob("dwh_*.dump"), key=lambda p: p.stat().st_mtime) if directory.is_dir() else []
    if not dumps:
        report.add("FAIL", f"copies: no dwh_*.dump in {directory}")
        return
    newest = dumps[-1]
    age = ((now or datetime.datetime.now()) - datetime.datetime.fromtimestamp(newest.stat().st_mtime))
    hours = age.total_seconds() / 3600
    report.check(hours <= BACKUP_MAX_HOURS,
                 f"copies: {len(dumps)} in {directory}, newest {newest.name}, {hours:.1f} h old "
                 f"(at most {BACKUP_MAX_HOURS})")
    sidecar = newest.with_name(newest.name + ".sha256")
    if not sidecar.is_file():
        report.add("FAIL", f"copies: {sidecar.name} missing")
        return
    expected = sidecar.read_text(encoding="utf-8").split()[0].lower()
    digest = hashlib.sha256()
    with open(newest, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    report.check(digest.hexdigest() == expected, f"copies: SHA-256 of {newest.name} matches {sidecar.name}")


def result_line(text: str, label: str) -> str | None:
    for line in reversed(text.splitlines()):
        if line.startswith(label + ":"):
            return line
    return None


def check_tool(report: Report, label: str, args: list[str], runner=run_cmd) -> None:
    result = runner([sys.executable, *args])
    line = result_line(result.stdout, label)
    report.check(line is not None and line.startswith(f"{label}: PASS"),
                 line or f"{label}: no result line ({(result.stderr or result.stdout).strip()[-200:]})")


# --- P ------------------------------------------------------------------------------------------------------------

def check_packages(report: Report, db: Db, packages: dict, root: Path = ROOT) -> None:
    lookups = []
    for number in sorted(packages):
        marker = packages[number]["marker"]
        fn = "to_regprocedure" if "(" in marker else "to_regclass"
        lookups.append(f"'{number}', {fn}('{marker}') IS NOT NULL")
    present = db.json("SELECT json_build_object(" + ", ".join(lookups) + ");")
    missing = [n for n, ok in sorted(present.items()) if not ok]
    report.check(not missing, f"packages {min(packages)}..{max(packages)}: {len(present) - len(missing)} applied"
                 + (f", missing: {', '.join(missing)}" if missing else ""))
    for number in CHECKED_PACKAGES:
        if number not in packages:
            continue
        item = packages[number]
        path = root / item["check"]["path"]
        result = db.check_file(path)
        rows = [line.rsplit("|", 1) for line in result.stdout.splitlines() if line.endswith(("|PASS", "|FAIL"))]
        passed = sum(1 for _, r in rows if r == "PASS")
        expected = item["check_expected"]
        report.check(result.returncode == 0 and passed == expected == len(rows),
                     f"check {number} as dwh_app (read-only): {passed}/{len(rows)} PASS (expected {expected})"
                     + "".join(f"; FAIL {name.split('|')[-1][:80]}" for name, r in rows if r != "PASS")
                     + (f"; {result.stderr.strip()[-200:]}" if result.returncode else ""))


# --- Q, I ---------------------------------------------------------------------------------------------------------

QUEUE_SQL = """
SELECT json_build_object(
  'now', now(),
  'tables', (SELECT json_object_agg(t, to_regclass(t) IS NOT NULL) FROM unnest(ARRAY[
      'etl.object_run_request', 'etl.dm_mart_run_request', 'etl.object_run', 'etl.object_ods_run',
      'etl.dm_mart_run', 'etl.object_schedule', 'etl.error_log', 'audit.security_event']) t));
"""


def active_requests_sql(table: str, label: str) -> str:
    return (f"SELECT coalesce(json_agg(json_build_object('kind', '{label}', 'id', request_id, 'status', status, "
            f"'since', requested_at, 'hours', round((extract(epoch FROM now() - requested_at) / 3600)::numeric, 1)) "
            f"ORDER BY request_id), '[]') FROM {table} WHERE status IN ('QUEUED', 'CLAIMED', 'RUNNING');")


def running_sql(table: str, key: str, label: str) -> str:
    return (f"SELECT coalesce(json_agg(json_build_object('kind', '{label}', 'id', {key}, 'since', started_at, "
            f"'hours', round((extract(epoch FROM now() - started_at) / 3600)::numeric, 1)) ORDER BY {key}), '[]') "
            f"FROM {table} WHERE status = 'RUNNING';")


SCHEDULE_SQL = """
SELECT json_build_object(
  'enabled', (SELECT count(*) FROM etl.object_schedule WHERE is_enabled),
  'late', (SELECT coalesce(json_agg(json_build_object('object', source_object_id, 'next', next_run_at,
             'minutes', round((extract(epoch FROM now() - next_run_at) / 60)::numeric))), '[]')
             FROM etl.object_schedule
            WHERE is_enabled AND next_run_at < now() - make_interval(mins => %(late)s)));
"""

OBJECTS_SQL = """
SELECT coalesce(json_agg(x), '[]') FROM (
  SELECT coalesce(last.status, 'NEVER') AS state, count(*) AS n
    FROM meta.source_object o
    LEFT JOIN LATERAL (SELECT r.status FROM etl.object_run_request r WHERE r.source_object_id = o.source_object_id
                        ORDER BY r.request_id DESC LIMIT 1) last ON true
   WHERE o.is_active
   GROUP BY 1 ORDER BY 1) x;
"""

ERRORS_SQL = """
SELECT coalesce(json_agg(json_build_object('code', error_code, 'n', n) ORDER BY n DESC), '[]') FROM (
  SELECT coalesce(error_code, '') AS error_code, count(*) AS n FROM etl.error_log
   WHERE occurred_at > now() - interval '7 days' GROUP BY 1) x;
"""

AUDIT_SQL = """
SELECT json_build_object('last_24h', (SELECT count(*) FROM audit.security_event
                                       WHERE occurred_at > now() - interval '24 hours'));
"""


def group_of_loader(root: Path = ROOT):
    sys.path.insert(0, str(root / "backend" / "src"))
    try:
        from dwh_api.error_groups import group_of  # noqa: WPS433 (the module of the same release)
        return group_of
    except ImportError:
        return lambda code: "OTHER"
    finally:
        sys.path.pop(0)


def check_queues(report: Report, db: Db, group_of=None) -> None:
    head = db.json(QUEUE_SQL)
    tables = head["tables"]
    stuck_limit = STUCK_HOURS
    items: list[dict] = []
    for table, label in (("etl.object_run_request", "object request"), ("etl.dm_mart_run_request", "mart request")):
        if tables.get(table):
            items += db.json(active_requests_sql(table, label))
    for table, key, label in (("etl.object_run", "run_id", "STG run"), ("etl.object_ods_run", "ods_run_id", "ODS run"),
                              ("etl.dm_mart_run", "run_id", "mart run")):
        if tables.get(table):
            items += db.json(running_sql(table, key, label))
    stuck = [i for i in items if float(i["hours"] or 0) > stuck_limit]
    fresh = [i for i in items if i not in stuck]
    report.check(not stuck, f"queues and runs active longer than {stuck_limit} h: {len(stuck)}"
                 + "".join(f"; {i['kind']} #{i['id']} {i.get('status', 'RUNNING')} {i['hours']} h" for i in stuck))
    if fresh:
        report.add("WARN", f"in progress now: {len(fresh)}"
                   + "".join(f"; {i['kind']} #{i['id']} {i.get('status', 'RUNNING')}" for i in fresh))
    if tables.get("etl.object_schedule"):
        data = db.json(SCHEDULE_SQL.replace("%(late)s", str(SCHEDULE_LATE_MINUTES)))
        report.check(not data["late"], f"schedules: {data['enabled']} enabled, late by more than "
                     f"{SCHEDULE_LATE_MINUTES} min: {len(data['late'])}"
                     + "".join(f"; object {s['object']} {s['minutes']} min" for s in data["late"]))
    report.info("objects (active) by the last request: " + ", ".join(
        f"{o['state']} {o['n']}" for o in db.json(OBJECTS_SQL)))
    if tables.get("etl.error_log"):
        group_of = group_of or group_of_loader()
        groups: dict[str, int] = {}
        codes = db.json(ERRORS_SQL)
        for c in codes:
            groups[group_of(c["code"])] = groups.get(group_of(c["code"]), 0) + c["n"]
        report.info("errors of the last 7 days by kind: " + (", ".join(
            f"{g} {n}" for g, n in sorted(groups.items(), key=lambda kv: -kv[1])) or "none"))
        if codes:
            report.info("  codes: " + ", ".join(f"{c['code'] or '-'} {c['n']}" for c in codes[:12]))
    if tables.get("audit.security_event"):
        report.info(f"audit events of the last 24 h: {db.json(AUDIT_SQL)['last_24h']}")


# --- main ---------------------------------------------------------------------------------------------------------

def run(args, runner=run_cmd, now=None) -> int:
    report = Report(args.report)
    report.out(f"V1 check of {args.database}, {datetime.datetime.now():%Y-%m-%d %H:%M:%S}, tools {TOOLS}")
    db = Db(args.psql, args.database, runner)
    if not args.skip_system:
        check_services(report, runner)
        check_release(report, args.current)
        check_backups(report, args.backups, now)
        check_tool(report, "TLS_RESULT", [str(TOOLS / "dwh_tls.py"), "check", "--name", args.name], runner)
        check_tool(report, "SECRET_CHECK_RESULT", [str(TOOLS / "dwh_secret_check.py")], runner)
    try:
        read_only = db.json("SELECT to_json(current_setting('transaction_read_only'));")
        report.check(read_only == "on", f"database {args.database}: read-only transaction ({read_only})")
        check_packages(report, db, json.loads(args.packages.read_text(encoding="utf-8")), args.root)
        check_queues(report, db)
    except (RuntimeError, ValueError, KeyError, OSError) as exc:
        report.add("FAIL", f"database not read: {exc}")
    failed, warns = report.count("FAIL"), report.count("WARN")
    report.out(f"V1_CHECK_RESULT: {'PASS' if not failed else 'FAIL'} ({failed} FAIL, {warns} WARN, "
               f"{len(report.levels)} checks)")
    if args.report is not None:
        print(f"report: {args.report}")
    return 0 if not failed else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--database", default="dwh")
    p.add_argument("--name", default="dwh.vtbaze.local", help="DNS name for the TLS check")
    p.add_argument("--psql", default="runuser -u postgres -- psql",
                   help="psql command line (tests: 'psql -h <socket dir> -p <port> -U postgres')")
    p.add_argument("--skip-system", action="store_true", help="only the database sections (tests)")
    p.add_argument("--backups", type=Path, default=BACKUPS)
    p.add_argument("--current", type=Path, default=CURRENT)
    p.add_argument("--packages", type=Path, default=PACKAGES)
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--report-dir", type=Path, default=REPORTS)
    p.add_argument("--no-report", action="store_true")
    args = p.parse_args(argv)
    args.psql = shlex.split(args.psql)
    args.report = None if args.no_report else (
        args.report_dir / f"v1_check_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
    if not args.skip_system and os.geteuid() != 0:
        print("Run with sudo: services, copies and the SecretStore check need root.")
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
