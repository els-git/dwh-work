"""Copy of the database dwh on the DWH server (I16, decision C7). Runs as the postgres user.

  dwh_backup.py --dir /backup/dwh --keep 14             nightly copy (systemd timer dwh-backup)
  dwh_backup.py --dir /backup/dwh --keep 14 --label pre-update-<version>   before an update
  dwh_backup.py --verify-restore <file.dump>             test restore into dwh_restore_test

A copy: pg_dump -Fc -> <dir>/dwh_<YYYYmmdd_HHMMSS>[_label].dump (+ .sha256), checked with
pg_restore --list. The oldest copies beyond --keep are removed (copies with a label are
counted too). Prints BACKUP_RESULT: PASS / FAIL. No passwords: local socket, peer login.
Uses the system python3 only (no DWH packages).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

NAME_RE = re.compile(r"^dwh_\d{8}_\d{6}(_[A-Za-z0-9._-]{1,60})?\.dump$")
RESTORE_DB = "dwh_restore_test"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)


def make_copy(directory: Path, database: str, label: str | None, now: datetime.datetime | None = None,
              runner=run) -> Path:
    if label is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", label):
        raise ValueError("Invalid label")
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    target = directory / (f"dwh_{stamp}" + (f"_{label}" if label else "") + ".dump")
    partial = target.with_name(target.name + ".partial")
    try:
        runner(["pg_dump", "-Fc", "--no-password", "-f", str(partial), database])
        runner(["pg_restore", "--list", str(partial)])  # the archive is complete and readable
        os.replace(partial, target)
    finally:
        if partial.exists():
            partial.unlink()
    target.with_name(target.name + ".sha256").write_text(f"{sha256_file(target)}  {target.name}\n")
    return target


def prune(directory: Path, keep: int) -> list[Path]:
    copies = sorted(p for p in directory.iterdir() if p.is_file() and NAME_RE.match(p.name))
    removed = []
    for old in copies[:-keep] if keep > 0 else []:
        old.unlink()
        side = old.with_name(old.name + ".sha256")
        if side.exists():
            side.unlink()
        removed.append(old)
    return removed


def table_counts(database: str, runner=run) -> dict[str, int]:
    sql = ("SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname) FROM pg_class c "
           "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p') "
           "AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1")
    tables = [t for t in runner(["psql", "-X", "-At", "-d", database, "-c", sql]).stdout.split("\n") if t]
    counts = {}
    for table in tables:
        counts[table] = int(runner(["psql", "-X", "-At", "-d", database, "-c", f"SELECT count(*) FROM {table}"])
                            .stdout.strip())
    return counts


def verify_restore(dump: Path, source_db: str, runner=run) -> tuple[bool, list[str]]:
    """Restore into dwh_restore_test; compare tables and rows with the live base (rows may differ
    for tables written after the copy - listed, not failed). The test base is dropped at the end."""
    runner(["dropdb", "--if-exists", RESTORE_DB])
    runner(["createdb", RESTORE_DB])
    try:
        runner(["pg_restore", "--exit-on-error", "--no-password", "-d", RESTORE_DB, str(dump)])
        restored, live = table_counts(RESTORE_DB, runner), table_counts(source_db, runner)
        notes = []
        if set(restored) != set(live):
            notes.append(f"tables differ: only in copy {sorted(set(restored) - set(live))}, "
                         f"only in {source_db} {sorted(set(live) - set(restored))}")
            return False, notes
        notes += [f"{t}: copy {restored[t]} rows, {source_db} {live[t]} rows" for t in sorted(live)
                  if restored[t] != live[t]]
        notes.insert(0, f"{len(restored)} tables, {sum(restored.values())} rows restored")
        return True, notes
    finally:
        runner(["dropdb", "--if-exists", RESTORE_DB])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="/backup/dwh")
    parser.add_argument("--keep", type=int, default=14)
    parser.add_argument("--database", default="dwh")
    parser.add_argument("--label")
    parser.add_argument("--verify-restore", metavar="DUMP")
    args = parser.parse_args(argv)
    try:
        if args.verify_restore:
            ok, notes = verify_restore(Path(args.verify_restore), args.database)
            for note in notes:
                print(note)
            print(f"BACKUP_RESULT: {'PASS' if ok else 'FAIL'} (restore test)")
            return 0 if ok else 1
        directory = Path(args.dir)
        if not directory.is_dir():
            raise ValueError(f"Backup folder does not exist: {directory}")
        target = make_copy(directory, args.database, args.label)
        removed = prune(directory, args.keep)
        print(f"copy: {target} ({target.stat().st_size} bytes)")
        print(f"removed old copies: {len(removed)}")
    except subprocess.CalledProcessError as exc:
        print(f"BACKUP_RESULT: FAIL ({Path(exc.cmd[0]).name} exit code {exc.returncode}: "
              f"{(exc.stderr or '').strip()[:300]})")
        return 1
    except (OSError, ValueError) as exc:
        print(f"BACKUP_RESULT: FAIL ({exc})")
        return 1
    print("BACKUP_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
