"""SQL package on the DWH server: E1 (copy of dwh) and E2 (the working dwh). Since I16 the server replaces
the PowerShell scripts database/run_0NN_e1_test.ps1 / run_0NN_e2_apply_dwh.ps1 of the PC.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_sql_package.py --package 016 --stage e1
  sudo python3 /opt/dwh/current/src/tools/server/dwh_sql_package.py --package 016 --stage e2

Files come from the installed release (/opt/dwh/current/src); their SHA256 must match sql_packages.json.
E1: dwh must be in the state before the package (check of the previous package all PASS, package absent);
    pg_dump of dwh -> restored copy <copy_db> -> package in ONE transaction -> check (all PASS) and check of
    the previous package -> test (ALL_TESTS_PASS, rolled back) -> rollback script -> package again -> check.
    The copy is left with the package applied (as on the PC). dwh is only read.
E2: same state check of dwh -> fresh copy of dwh (dwh_backup.py, label pre-<package>) -> row counts ->
    confirmation by typing APPLY -> package in ONE transaction (lock_timeout 10s) -> checks -> row counts:
    unchanged except the rows the package adds (sql_packages.json "added_rows").
Any failure = STOP; a failed apply rolls back completely. Prints E1_RESULT / E2_RESULT: PASS / STOP.
Report: /srv/dwh/incoming/<stage>_<package>_<time>.txt. Runs psql as postgres (peer login, no passwords);
objects are created as the owner role of the package ("owner"). Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = Path(__file__).resolve().with_name("sql_packages.json")
COUNTS_SQL = Path(__file__).resolve().with_name("table_counts.sql")
BACKUP = Path(__file__).resolve().with_name("dwh_backup.py")
REPORTS = Path("/srv/dwh/incoming")
DB = "dwh"


class Stop(Exception):
    pass


class Run:
    def __init__(self, report: Path):
        self.report = report

    def log(self, text: str) -> None:
        print(text, flush=True)
        with open(self.report, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    def postgres(self, args: list[str], stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(["runuser", "-u", "postgres", "--", *args], input=stdin, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
                                env=dict(os.environ, PGCLIENTENCODING="UTF8"))
        if check and result.returncode != 0:
            raise Stop(f"{args[0]} failed: {(result.stderr or result.stdout).strip()[-1500:]}")
        return result

    def psql_file(self, database: str, path: Path, role: str, single: bool = False, read_only: bool = False,
                  lock_timeout: bool = False) -> subprocess.CompletedProcess:
        args = ["psql", "-X", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-d", database]
        if single:
            args.append("-1")
        if read_only:
            args += ["-c", "SET default_transaction_read_only = on"]
        if lock_timeout:
            args += ["-c", "SET lock_timeout = '10s'"]
        args += ["-c", f"SET ROLE {role}", "-f", str(path)]
        return self.postgres(args, check=False)

    def scalar(self, database: str, sql: str) -> str:
        return self.postgres(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", database, "-c", sql]).stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_package(name: str) -> dict:
    packages = json.loads(PACKAGES.read_text(encoding="utf-8"))
    if name not in packages:
        raise Stop(f"unknown package {name}; known: {', '.join(sorted(packages))}")
    return packages[name]


def verify_files(run: Run, package: dict) -> dict[str, Path]:
    paths = {}
    for key in ("migration", "down", "check", "test", "previous_check"):
        item = package[key]
        path = ROOT / item["path"]
        if not path.is_file():
            raise Stop(f"file not found: {item['path']}")
        actual = sha256_file(path)
        if actual != item["sha256"].upper():
            raise Stop(f"SHA256 mismatch: {item['path']} ({actual})")
        paths[key] = path
        run.log(f"SHA256 OK {item['path']}")
    return paths


def check_result(run: Run, label: str, result: subprocess.CompletedProcess, expected: int) -> None:
    rows = [line.rsplit("|", 1) for line in result.stdout.splitlines() if line.endswith(("|PASS", "|FAIL"))]
    passed = sum(1 for _, r in rows if r == "PASS")
    for name, r in rows:
        if r != "PASS":
            run.log(f"  FAIL: {name}")
    run.log(f"{label}: {passed}/{len(rows)} PASS (expected {expected})")
    if result.returncode != 0:
        raise Stop(f"{label}: {result.stderr.strip()[-800:]}")
    if passed != expected or len(rows) != expected:
        raise Stop(f"{label}: {passed}/{len(rows)}, expected {expected}/{expected}")


def apply(run: Run, database: str, paths: dict[str, Path], package: dict, lock_timeout: bool = False) -> None:
    result = run.psql_file(database, paths["migration"], package["owner"], single=True, lock_timeout=lock_timeout)
    for line in result.stderr.splitlines():
        if "NOTICE" in line or "ERROR" in line or "ЗАМЕЧАНИЕ" in line or "ОШИБКА" in line:
            run.log("  " + re.sub(r"^psql:[^ ]+ ", "", line))
    done = f"MIGRATION_{package['number']}_DONE"
    if result.returncode != 0 or done not in result.stdout:
        raise Stop(f"apply of {package['number']} failed (rolled back)")
    run.log(f"apply: {done}")


def state_before(run: Run, package: dict, paths: dict[str, Path]) -> None:
    if run.scalar(DB, f"SELECT to_regclass('{package['marker']}') IS NULL") != "t":
        raise Stop(f"{package['number']} is already applied to {DB} ({package['marker']} exists)")
    check_result(run, f"{DB}: check {package['previous_check']['label']}",
                 run.psql_file(DB, paths["previous_check"], "dwh_app", read_only=True),
                 package["previous_check"]["expected"])


def counts(run: Run, database: str) -> dict[str, str]:
    out = run.postgres(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", database, "-f", "-"],
                       stdin=COUNTS_SQL.read_text()).stdout
    values = {}
    for line in out.splitlines():
        key, _, value = line.rpartition("|")
        values[key] = value
    return values


def stage_e1(run: Run, package: dict, paths: dict[str, Path]) -> None:
    state_before(run, package, paths)
    copy = package["copy_db"]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dump = Path(f"/var/lib/postgresql/e1_{package['number']}_{stamp}.dump")
    try:
        run.postgres(["pg_dump", "-Fc", "-f", str(dump), DB])
        run.log(f"dump of {DB}: OK")
        run.postgres(["dropdb", "--if-exists", copy])
        run.postgres(["createdb", copy])
        run.postgres(["pg_restore", "--exit-on-error", "-d", copy, str(dump)])
        run.log(f"copy {copy}: restored")
    finally:
        if dump.exists():
            dump.unlink()
    apply(run, copy, paths, package)
    check_result(run, f"{copy}: check {package['number']}", run.psql_file(copy, paths["check"], "dwh_app", read_only=True),
                 package["check_expected"])
    check_result(run, f"{copy}: check {package['previous_check']['label']}",
                 run.psql_file(copy, paths["previous_check"], "dwh_app", read_only=True),
                 package["previous_check"]["expected"])
    result = run.psql_file(copy, paths["test"], package["owner"])
    passed = len(re.findall(r"PASS X\d+", result.stderr))
    run.log(f"{copy}: test {package['number']}: {passed} PASS lines")
    if result.returncode != 0 or "ALL_TESTS_PASS" not in result.stdout:
        failed = [line for line in result.stderr.splitlines() if "FAIL" in line or "ERROR" in line]
        raise Stop(f"test failed: {' / '.join(failed)[-800:]}")
    run.log(f"{copy}: test: ALL_TESTS_PASS")
    result = run.psql_file(copy, paths["down"], package["owner"])
    if result.returncode != 0 or f"ROLLBACK_{package['number']}_DONE" not in result.stdout:
        raise Stop(f"rollback script failed: {result.stderr.strip()[-800:]}")
    if run.scalar(copy, f"SELECT to_regclass('{package['marker']}') IS NULL") != "t":
        raise Stop("rollback script left the package objects")
    run.log(f"{copy}: rollback script: ROLLBACK_{package['number']}_DONE")
    apply(run, copy, paths, package)
    check_result(run, f"{copy}: check {package['number']} after re-apply",
                 run.psql_file(copy, paths["check"], "dwh_app", read_only=True), package["check_expected"])
    run.log(f"copy {copy} is left with {package['number']} applied")


def stage_e2(run: Run, package: dict, paths: dict[str, Path]) -> None:
    state_before(run, package, paths)
    backup = run.postgres(["python3", str(BACKUP), "--dir", "/backup/dwh", "--keep", "14",
                           "--label", f"pre-{package['number']}"], check=False)
    tail = [line for line in backup.stdout.splitlines() if line.strip()][-3:]
    for line in tail:
        run.log("  " + line)
    if backup.returncode != 0 or "BACKUP_RESULT: PASS" not in backup.stdout:
        raise Stop("backup failed")
    before = counts(run, DB)
    answer = input(f"Apply {package['number']} to the WORKING database {DB}? Type APPLY: ")
    if answer.strip() != "APPLY":
        raise Stop("not confirmed")
    apply(run, DB, paths, package, lock_timeout=True)
    check_result(run, f"{DB}: check {package['number']}", run.psql_file(DB, paths["check"], "dwh_app", read_only=True),
                 package["check_expected"])
    check_result(run, f"{DB}: check {package['previous_check']['label']}",
                 run.psql_file(DB, paths["previous_check"], "dwh_app", read_only=True),
                 package["previous_check"]["expected"])
    after = counts(run, DB)
    added = package.get("added_rows", {})
    problems = []
    for key, value in before.items():
        if not key.startswith("rows|"):
            continue
        table = key.split("|", 1)[1]
        expected = int(value) + int(added.get(table, 0))
        if after.get(key) != str(expected):
            problems.append(f"{table}: {value} -> {after.get(key)} (expected {expected})")
    new_tables = sorted(k.split("|", 1)[1] for k in after if k.startswith("rows|") and k not in before)
    run.log(f"row counts: {len([k for k in before if k.startswith('rows|')])} tables compared, "
            f"new tables: {', '.join(new_tables) or '-'}")
    if problems:
        raise Stop("row counts changed: " + "; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--stage", required=True, choices=("e1", "e2"))
    args = parser.parse_args(argv)
    label = f"{args.stage.upper()}_RESULT"
    if os.geteuid() != 0:
        print(f"{label}: STOP (run with sudo)")
        return 1
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run = Run(REPORTS / f"{args.stage}_{args.package}_{stamp}.txt")
    run.log(f"{args.stage.upper()} package {args.package}, {stamp}, release {ROOT}")
    try:
        package = load_package(args.package)
        paths = verify_files(run, package)
        (stage_e1 if args.stage == "e1" else stage_e2)(run, package, paths)
    except (Stop, KeyboardInterrupt, EOFError) as exc:
        run.log(f"STOP: {exc}")
        run.log(f"{label}: STOP")
        run.log(f"report: {run.report}")
        return 1
    run.log(f"{label}: PASS")
    run.log(f"report: {run.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
