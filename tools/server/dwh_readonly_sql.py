"""Read-only SQL diagnostics on the DWH server (the server counterpart of database/tools/run_readonly_sql.ps1).

  sudo /opt/dwh/current/venv/bin/python /srv/dwh/incoming/dwh_readonly_sql.py <file.sql>

Connects as dwh_app through get_api_config() + connect_readonly() with the settings of /etc/dwh/dwh.env,
stops unless SHOW transaction_read_only = on, runs the statements of the file (separated by ';',
lines starting with '--' ignored), always rolls back. Prints every row; the report is also written
next to the SQL file as <file>.report.txt. Prints no passwords.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DWH_ENV_FILE", "/etc/dwh/dwh.env")
sys.path.insert(0, "/opt/dwh/current/src/backend/src")

from dwh_api.config import get_api_config  # noqa: E402
from dwh_api.db import connect_readonly  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: dwh_readonly_sql.py <file.sql>")
        return 2
    sql_file = Path(sys.argv[1])
    text = sql_file.read_text(encoding="utf-8-sig")
    code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    statements = [s for s in code.split(";") if s.strip()]
    out = []

    def emit(line: str) -> None:
        print(line)
        out.append(line)

    conn = connect_readonly(get_api_config())
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            ro = list(cur.fetchone().values())[0]
            emit("SHOW transaction_read_only: " + str(ro))
            if ro != "on":
                emit("STOP: connection is not read-only")
                return 1
            for statement in statements:
                cur.execute(statement)
                for row in cur.fetchall():
                    emit(" | ".join("" if v is None else str(v) for v in row.values()))
    finally:
        conn.rollback()
        conn.close()
    emit("READONLY_SQL_FINISHED")
    report = sql_file.with_suffix(sql_file.suffix + ".report.txt")
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
