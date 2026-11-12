"""Import of the database dwh on the DWH server from a PC export (I16 step 7). Run as root.

  sudo python3 dwh_import_db.py /srv/dwh/incoming/dwh_export_<time>            first import
  sudo python3 dwh_import_db.py /srv/dwh/incoming/dwh_export_<time> --replace  repeat (final transfer)

Steps:
  1. SHA256 of every export file (SHA256SUMS);
  2. roles from roles.sql (no passwords in it): created when missing, attributes and memberships as on
     the PC; the owner role of the database stays without login;
  3. the database itself: pg_restore --create (owner, encoding, collation and settings as on the PC);
     --replace drops the existing dwh first (the DWH services must be stopped);
  4. comparison: rows of every table and objects per schema, server against counts.txt of the PC;
  5. new random passwords for dwh_app and dwh_etl, written straight into /etc/dwh/dwh.env
     (root:dwh 0640) from the template of the installed release. Passwords are never shown.
Prints IMPORT_RESULT: PASS / FAIL. Uses the system python3 only; files are read by root and passed to
psql / pg_restore (running as postgres) on standard input.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

DB = "dwh"
SERVICE_ROLES = ("dwh_app", "dwh_etl")
ENV_FILE = Path("/etc/dwh/dwh.env")
ENV_TEMPLATE = Path("/opt/dwh/current/src/deploy/dwh.env.example")
COUNTS_SQL = Path(__file__).resolve().with_name("table_counts.sql")
ROLE_RE = re.compile(r'^CREATE ROLE ("?)([A-Za-z_][A-Za-z0-9_$]*)\1;$')


class ImportError_(Exception):
    pass


def as_postgres(args: list[str], stdin: bytes | None = None, check: bool = True) -> str:
    result = subprocess.run(["runuser", "-u", "postgres", "--", *args], input=stdin,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise ImportError_(f"{args[0]} failed (exit code {result.returncode}): " + " | ".join(tail))
    return result.stdout.decode("utf-8", "replace")


def psql(sql: str, database: str = "postgres") -> str:
    return as_postgres(["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", database, "-f", "-"],
                       stdin=sql.encode("utf-8"))


def check_sums(export: Path) -> None:
    sums = export / "SHA256SUMS"
    if not sums.is_file():
        raise ImportError_("SHA256SUMS missing")
    names = []
    for line in sums.read_text().splitlines():
        digest, _, name = line.partition("  ")
        names.append(name)
        if hashlib.sha256((export / name).read_bytes()).hexdigest() != digest.lower():
            raise ImportError_(f"SHA256 does not match for {name}")
    missing = {"roles.sql", "dwh.dump", "info.txt", "counts.txt"} - set(names)
    if missing:
        raise ImportError_(f"Not in SHA256SUMS: {sorted(missing)}")


def read_info(export: Path) -> dict[str, str]:
    info = dict(line.split("=", 1) for line in (export / "info.txt").read_text().splitlines() if "=" in line)
    if not info.get("owner") or not info.get("server_version_num", "").startswith("17"):
        raise ImportError_(f"Export is not from PostgreSQL 17 or has no owner: {info}")
    return info


def roles_script(text: str) -> str:
    """roles.sql without the role postgres; CREATE ROLE only when the role is missing."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        match = ROLE_RE.match(line)
        if match:
            name = match.group(2)
            if name == "postgres":
                continue
            out.append(f"DO $$BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{name}') THEN "
                       f'CREATE ROLE "{name}"; END IF; END$$;')
            continue
        if re.match(r'^ALTER ROLE "?postgres"? ', line) or line.startswith("\\"):
            continue
        out.append(raw)
    return "\n".join(out) + "\n"


def compare(expected_text: str, actual_text: str) -> list[str]:
    expected = {l.rsplit("|", 1)[0]: l.rsplit("|", 1)[1] for l in expected_text.splitlines() if "|" in l}
    actual = {l.rsplit("|", 1)[0]: l.rsplit("|", 1)[1] for l in actual_text.splitlines() if "|" in l}
    problems = []
    for key in sorted(set(expected) | set(actual)):
        if expected.get(key) != actual.get(key):
            problems.append(f"{key}: PC {expected.get(key, '-')}, server {actual.get(key, '-')}")
    return problems


def write_env(passwords: dict[str, str]) -> None:
    if not ENV_TEMPLATE.is_file():
        raise ImportError_(f"{ENV_TEMPLATE} missing (install the release first)")
    lines = []
    for line in ENV_TEMPLATE.read_text().splitlines():
        name = line.split("=", 1)[0]
        if name == "DWH_APP_PASSWORD":
            line = f"DWH_APP_PASSWORD={passwords['dwh_app']}"
        elif name == "DWH_ETL_PASSWORD":
            line = f"DWH_ETL_PASSWORD={passwords['dwh_etl']}"
        lines.append(line)
    tmp = ENV_FILE.with_name(ENV_FILE.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chown(tmp, 0, grp.getgrnam("dwh").gr_gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, ENV_FILE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("export", help="folder copied from the PC (dwh_export_<time>)")
    parser.add_argument("--replace", action="store_true", help="drop the existing database dwh first")
    parser.add_argument("--keep-env", action="store_true", help="do not change passwords and /etc/dwh/dwh.env")
    args = parser.parse_args(argv)
    if os.name != "posix" or os.geteuid() != 0:
        print("IMPORT_RESULT: FAIL (run with sudo)")
        return 1
    export = Path(args.export)
    try:
        check_sums(export)
        info = read_info(export)
        print(f"export OK: owner {info['owner']}, {info.get('encoding')}, collation {info.get('collate')}")
        exists = psql(f"SELECT count(*) FROM pg_database WHERE datname = '{DB}'").strip() == "1"
        if exists and not args.replace:
            raise ImportError_("database dwh already exists; use --replace for a repeated transfer")
        for unit in ("dwh-api", "dwh-dispatcher"):
            if subprocess.run(["systemctl", "is-active", "--quiet", unit], stderr=subprocess.DEVNULL).returncode == 0:
                raise ImportError_(f"{unit} is running; stop it first: sudo systemctl stop dwh-api dwh-dispatcher")
        psql(roles_script((export / "roles.sql").read_text()))
        if info["owner"] != "postgres":
            psql(f'ALTER ROLE "{info["owner"]}" NOLOGIN;')
        print("roles: as on the PC (no passwords); owner role without login")
        if exists:
            psql(f"DROP DATABASE {DB} WITH (FORCE);")
            print("previous database dwh dropped (--replace)")
        as_postgres(["pg_restore", "--create", "--exit-on-error", "-d", "postgres"],
                    stdin=(export / "dwh.dump").read_bytes())
        psql("ANALYZE;", DB)
        print("database dwh restored")
        actual = psql(COUNTS_SQL.read_text(), DB)
        problems = compare((export / "counts.txt").read_text(), actual)
        tables = sum(1 for line in actual.splitlines() if line.startswith("rows|"))
        rows = sum(int(line.rsplit("|", 1)[1]) for line in actual.splitlines() if line.startswith("rows|"))
        if problems:
            for problem in problems[:30]:
                print(f"DIFF  {problem}")
            raise ImportError_(f"{len(problems)} differences between the PC and the server")
        print(f"comparison: {tables} tables, {rows} rows, objects per schema - identical to the PC")
        if not args.keep_env:
            passwords = {role: secrets.token_urlsafe(32) for role in SERVICE_ROLES}
            for role, password in passwords.items():
                try:
                    psql(f"ALTER ROLE {role} LOGIN PASSWORD '{password}';")
                except ImportError_:
                    raise ImportError_(f"password of {role} could not be set") from None  # no statement text
            write_env(passwords)
            print(f"new passwords of {', '.join(SERVICE_ROLES)} set and written to {ENV_FILE} (not shown)")
    except (ImportError_, OSError, ValueError, KeyError) as exc:
        print(f"IMPORT_RESULT: FAIL ({exc})")
        return 1
    print("IMPORT_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
