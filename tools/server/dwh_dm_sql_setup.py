"""I23: login of the role dwh_dm_sql_ro (queries of SQL marts) on the DWH server.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_dm_sql_setup.py            show what will change
  sudo python3 /opt/dwh/current/src/tools/server/dwh_dm_sql_setup.py --apply    make the changes, then check
  sudo python3 /opt/dwh/current/src/tools/server/dwh_dm_sql_setup.py --check    check only

The role is created by package 023 (SELECT on ods only, read-only transactions, no password). Changes (each
only if still needed):
  Q1 password               random, set by ALTER ROLE through psql standard input, written only to
                            /etc/dwh/dwh.env as DWH_DM_SQL_DB_PASSWORD (never printed); dwh-api and
                            dwh-dispatcher are restarted to read it
  Q2 GRANT CONNECT          on database dwh to dwh_dm_sql_ro (in dwh PUBLIC has no CONNECT)
Check: dwh_dm_sql_ro logs in, read-only, reads a table of ods (row count only), is refused on app and cannot
write to ods. Prints DM_SQL_SETUP_RESULT: PASS / FAIL / PLAN. Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dwh_ai_setup as ai  # noqa: E402  (env file helpers, run)

ROLE = "dwh_dm_sql_ro"
KEY = "DWH_DM_SQL_DB_PASSWORD"
DWH_ENV = ai.DWH_ENV
HEADER = "# I23 SQL marts (tools/server/dwh_dm_sql_setup.py)"


def psql_admin(sql: str, check: bool = True):
    return ai.run(["runuser", "-u", "postgres", "--", "psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", "dwh", "-c", sql],
                  check=check)


def role_ready() -> bool:
    result = psql_admin(f"SELECT rolcanlogin FROM pg_roles WHERE rolname = '{ROLE}'", check=False)
    return result.returncode == 0 and result.stdout.strip() == "t"


def has_connect() -> bool:
    result = psql_admin(f"SELECT has_database_privilege('{ROLE}', 'dwh', 'CONNECT')", check=False)
    return result.returncode == 0 and result.stdout.strip() == "t"


def plan(args) -> list[tuple[str, str]]:
    steps = []
    if not ai.read_env(DWH_ENV).get(KEY) or args.rotate_password:
        steps.append(("Q1", f"{ROLE}: new random password -> ALTER ROLE and {DWH_ENV} (not printed); restart dwh-api, "
                            "dwh-dispatcher"))
    if not has_connect():
        steps.append(("Q2", f"GRANT CONNECT ON DATABASE dwh TO {ROLE} (in dwh PUBLIC has no CONNECT)"))
    return steps


def apply(steps: list[tuple[str, str]]) -> None:
    codes = {code for code, _ in steps}
    if "Q1" in codes:
        password = secrets.token_urlsafe(32)
        # The password goes through standard input only: it is not in the process list or in a file.
        ai.run(["runuser", "-u", "postgres", "--", "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "dwh", "-f", "-"],
               stdin=f"ALTER ROLE {ROLE} PASSWORD '{password}';\n")
        stat = DWH_ENV.stat()
        tmp = DWH_ENV.with_suffix(".env.new")
        tmp.write_text(ai.env_with(DWH_ENV.read_text(), {KEY: password}, HEADER))
        os.chown(tmp, stat.st_uid, stat.st_gid)
        tmp.chmod(stat.st_mode & 0o777)
        tmp.replace(DWH_ENV)
    if "Q2" in codes:
        psql_admin(f"GRANT CONNECT ON DATABASE dwh TO {ROLE}")
    if "Q1" in codes:
        ai.run(["systemctl", "restart", "dwh-api.service", "dwh-dispatcher.service"])


def check() -> list[str]:
    problems = []
    env = ai.read_env(DWH_ENV)
    password = env.get(KEY)
    if not password:
        return [f"{KEY} is not in {DWH_ENV}"]
    table = psql_admin("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                       "WHERE n.nspname = 'ods' AND c.relkind = 'r' ORDER BY c.relname LIMIT 1", check=False).stdout.strip()
    pg_env = dict(os.environ, PGPASSWORD=password, PGCONNECT_TIMEOUT="5")
    conn = ["psql", "-X", "-At", "-h", env.get("DWH_DB_HOST", "127.0.0.1"), "-p", env.get("DWH_DB_PORT", "5432"),
            "-U", ROLE, "-d", env.get("DWH_DB_NAME", "dwh")]
    commands = ["-c", "SHOW transaction_read_only"] + (["-c", f'SELECT count(*) FROM ods."{table}"'] if table else [])
    good = ai.run(conn + commands, check=False, env=pg_env)
    lines = good.stdout.split()
    if good.returncode != 0 or not lines or lines[0] != "on":
        problems.append(f"{ROLE}: login or read-only check failed: " + good.stderr.strip()[-200:])
    else:
        print(f"{ROLE}: read-only on" + (f", ods.{table} rows {lines[1]}" if table and len(lines) > 1 else ", no ods table yet"))
    bad = ai.run(conn + ["-c", "SELECT count(*) FROM app.user_account"], check=False, env=pg_env)
    if bad.returncode == 0:
        problems.append(f"{ROLE} can read app.user_account")
    elif "permission denied for schema app" in bad.stderr:
        print(f"{ROLE}: app.user_account refused (as expected)")
    else:
        problems.append(f"{ROLE}: app check inconclusive: " + bad.stderr.strip()[-200:])
    if table:
        write = ai.run(conn + ["-c", f'DELETE FROM ods."{table}" WHERE false'], check=False, env=pg_env)
        if write.returncode == 0:
            problems.append(f"{ROLE} can write to ods.{table}")
        else:
            print(f"{ROLE}: write to ods.{table} refused (as expected)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rotate-password", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("DM_SQL_SETUP_RESULT: FAIL (run with sudo)")
        return 1
    if not role_ready():
        print(f"DM_SQL_SETUP_RESULT: FAIL (role {ROLE} not found: package 023 is not applied)")
        return 1
    if not args.check:
        steps = plan(args)
        for code, text in steps:
            print(f"{code} {text}")
        if not steps:
            print("nothing to change")
        if not args.apply:
            print("DM_SQL_SETUP_RESULT: PLAN (run with --apply to make these changes)")
            return 0
        try:
            apply(steps)
        except RuntimeError as exc:
            print(f"DM_SQL_SETUP_RESULT: FAIL ({exc})")
            return 1
        print("changes applied")
    problems = check()
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print(f"DM_SQL_SETUP_RESULT: {'PASS' if not problems else 'FAIL'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
