"""I02-03 step 5: acceptance of SecretStore on the DWH server (plan
docs/DWH_I02_03_SECRETSTORE_ACCEPTANCE_PLAN_APPROVED_20261120.md, section 5, decision of 20.11.2026: A).

  sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py marker
  sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py journal [--source SECRET_ACCEPT]
  sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py etl-deny [--source CRM_RETAIL]
  sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py backup-check
  sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_accept.py scan

marker      creates a SYNTHETIC password-marker (not a real password) in /root/dwh_secret_accept.marker (0600)
            and prints it once: it is entered in the UI as the password of the test source SECRET_ACCEPT.
journal     read only: the SecretStore journal of the test source - CREATE, READ, REPLACE, REVOKE each with
            ATTEMPT and OUTCOME SUCCEEDED, no READ after the last REVOKE, no ACTIVE or PENDING binding left.
etl-deny    the ETL code reads the password of a source (as the user dwh, in a unit like dwh_task.py) without
            a load request, with an unknown request and with a finished request: all three are refused,
            the last two are journaled as DENIED READ (2 rows are added to the journal: the test itself).
backup-check read only: the newest copy /backup/dwh/secrets/secrets_<time>.tar - its SHA-256 file, every file
            inside, and every ACTIVE binding (except the old ones without profile, G8) has its file with the SHA-256 of the journal (F06, R3).
scan        read only: the marker is searched in the system journal (all services, since the marker was made),
            the logs of nginx, DWH and PostgreSQL, /var/lib/dwh, /srv/dwh/incoming and a plain dump of the
            database dwh (pg_dump to a pipe, nothing is written; includes etl.error_log and the audit tables).
            Prints only the number of matches per place.
Each command prints SECRET_ACCEPT_<COMMAND>_RESULT: PASS / FAIL. No real password is read or printed.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dwh_task  # noqa: E402  (same folder)

MARKER_FILE = Path("/root/dwh_secret_accept.marker")
MARKER_RE = re.compile(r"^DWHACCEPT[0-9a-f]{24}$")
BACKUP_DIR = Path("/backup/dwh/secrets")
COPY_RE = re.compile(r"^secrets_\d{8}_\d{6}\.tar$")
LOG_DIRS = (Path("/var/log/nginx"), Path("/var/log/dwh"), Path("/var/log/postgresql"), Path("/var/lib/dwh"),
            Path("/srv/dwh/incoming"))
SKIP_SUFFIXES = (".zip", ".dump", ".gguf")
CHUNK = 1 << 20

SOURCE_QUERY = r"""
SET default_transaction_read_only = on;
SELECT json_build_object(
  'read_only', current_setting('transaction_read_only'),
  'found', s.source_system_id IS NOT NULL,
  'events', (SELECT coalesce(json_agg(json_build_object(
        'op', e.operation_id::text, 'type', e.operation_type, 'phase', e.phase, 'code', e.code,
        'actor', e.actor_kind, 'at', e.occurred_at) ORDER BY e.occurred_at, e.phase), '[]')
      FROM secret_store.operation_event e WHERE e.source_system_id = s.source_system_id),
  'bindings', (SELECT coalesce(json_agg(json_build_object('ref', b.secret_ref::text, 'state', b.state,
        'sha', b.ciphertext_sha256) ORDER BY b.created_at), '[]')
      FROM secret_store.binding b WHERE b.source_system_id = s.source_system_id),
  'finished_request', (SELECT max(r.request_id) FROM etl.object_run_request r
      JOIN meta.source_object o ON o.source_object_id = r.source_object_id
     WHERE o.source_system_id = s.source_system_id AND r.status IN ('SUCCESS', 'FAILED', 'CANCELLED'))
) FROM (SELECT 1) one LEFT JOIN meta.source_system s ON s.code = :'code';
"""

ACTIVE_QUERY = r"""
SET default_transaction_read_only = on;
SELECT coalesce(json_agg(json_build_object('ref', b.secret_ref::text, 'source', s.code, 'sha', b.ciphertext_sha256)
       ORDER BY s.code), '[]')
  FROM secret_store.binding b JOIN meta.source_system s ON s.source_system_id = b.source_system_id
 WHERE b.state = 'ACTIVE' AND b.profile_code IS NOT NULL;  -- bindings without profile: G8, task I02-06
"""


class Report:
    def __init__(self, name: str):
        self.name, self.failed, self.count = name, 0, 0

    def check(self, ok: bool, text: str) -> bool:
        self.count += 1
        self.failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {text}", flush=True)
        return ok

    def result(self) -> int:
        print(f"SECRET_ACCEPT_{self.name}_RESULT: {'PASS' if not self.failed else 'FAIL'} "
              f"({self.failed} FAIL, {self.count} checks)")
        return 0 if not self.failed else 1


def psql_json(psql: list[str], database: str, sql: str, variables: dict[str, str] | None = None):
    cmd = [*psql, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", database]
    for name, value in (variables or {}).items():
        cmd += ["-v", f"{name}={value}"]
    result = subprocess.run([*cmd, "-f", "-"], input=sql, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd="/", env=dict(os.environ, PGCLIENTENCODING="UTF8"))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-600:])
    return json.loads(result.stdout.strip())


def source_data(args, code: str) -> dict:
    data = psql_json(args.psql, args.database, SOURCE_QUERY, {"code": code})
    if data["read_only"] != "on":
        raise RuntimeError("journal transaction is not read-only")
    return data


# ---------------------------------------------------------------- marker
def make_marker(path: Path, force: bool = False) -> str:
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to make a new marker)")
    if path.exists():
        path.unlink()
    marker = "DWHACCEPT" + secrets.token_hex(12)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(marker + "\n")
    return marker


def read_marker(path: Path) -> str:
    marker = path.read_text().strip()
    if not MARKER_RE.match(marker):
        raise ValueError(f"{path} does not hold a marker made by this tool")
    return marker


def cmd_marker(args) -> int:
    try:
        marker = make_marker(args.marker_file, args.force)
    except FileExistsError as exc:
        print(f"STOP: {exc}")
        return 2
    print("Synthetic password-marker (not a real password; enter it as the password of SECRET_ACCEPT):")
    print(f"  {marker}")
    print(f"Kept in {args.marker_file} (0600 root) for the scan command.")
    print("SECRET_ACCEPT_MARKER_RESULT: PASS (0 FAIL, 1 checks)")
    return 0


# ---------------------------------------------------------------- journal
def evaluate_journal(report: Report, data: dict) -> None:
    if not report.check(bool(data["found"]), "test source found"):
        return
    events = data["events"]
    by_op: dict[str, dict] = {}
    for e in events:
        by_op.setdefault(e["op"], {})[e["phase"]] = e
    print("      operations (type / attempt / outcome / actor / time):")
    for op in by_op.values():
        a, o = op.get("ATTEMPT"), op.get("OUTCOME")
        head = a or o
        print(f"        {head['type']} / {a['code'] if a else '-'} / {o['code'] if o else '-'} / "
              f"{head['actor']} / {str(head['at'])[:19]}")
    permitted = [op for op in by_op.values() if op.get("ATTEMPT", {}).get("code") == "PERMITTED_ATTEMPT"]
    report.check(all("OUTCOME" in op for op in permitted),
                 f"every permitted operation has an OUTCOME ({len(permitted)} operation(s))")
    done = {t: [op["ATTEMPT"]["at"] for op in permitted
                if op["ATTEMPT"]["type"] == t and op.get("OUTCOME", {}).get("code") == "SUCCEEDED"]
            for t in ("CREATE", "READ", "REPLACE", "REVOKE")}
    for t in ("CREATE", "READ", "REPLACE", "REVOKE"):
        report.check(bool(done[t]), f"{t} succeeded: {len(done[t])}")
    if all(done.values()):
        report.check(min(done["CREATE"]) < min(done["REPLACE"]) < max(done["REVOKE"]),
                     "order: CREATE, then REPLACE, then REVOKE")
        last_revoke = max(done["REVOKE"])
        late = [op for op in permitted if op["ATTEMPT"]["type"] == "READ" and op["ATTEMPT"]["at"] > last_revoke]
        report.check(not late, f"no READ given after the last REVOKE ({len(late)})")
    states = [b["state"] for b in data["bindings"]]
    report.check("ACTIVE" not in states and "PENDING" not in states,
                 f"no ACTIVE or PENDING binding left ({', '.join(states) or 'none'})")


def cmd_journal(args) -> int:
    report = Report("JOURNAL")
    try:
        evaluate_journal(report, source_data(args, args.source))
    except (RuntimeError, ValueError, OSError) as exc:
        report.check(False, f"journal not read: {exc}")
    return report.result()


# ---------------------------------------------------------------- etl-deny
def denied_reads(data: dict) -> int:
    return sum(1 for e in data["events"] if e["type"] == "READ" and e["phase"] == "ATTEMPT"
               and e["code"] == "DENIED_ATTEMPT")


def probe_command(source: str, finished_request: int, root: Path = dwh_task.ROOT) -> list[str]:
    cmd = dwh_task.command("run_object.py", env={"DWH_PROBE_SOURCE": source,
                                                 "DWH_PROBE_FINISHED_REQUEST": str(finished_request)}, root=root)
    cmd[-1] = str(root / "src" / "tools" / "server" / "secret_read_probe.py")
    return cmd


def evaluate_probe(report: Report, output: str, before: int, after: int) -> None:
    got = dict(re.findall(r"^PROBE (\w+): (.+)$", output, re.M))
    report.check(got.get("no_request", "").startswith("REFUSED"),
                 f"without a load request: {got.get('no_request', 'no answer')}")
    report.check(got.get("unknown_request") == "REFUSED REQUEST_NOT_FOUND",
                 f"unknown request: {got.get('unknown_request', 'no answer')}")
    report.check(got.get("finished_request") == "REFUSED REQUEST_NOT_RUNNING",
                 f"finished request: {got.get('finished_request', 'no answer')}")
    report.check(after - before == 2, f"DENIED READ rows added to the journal: {after - before} (expected 2)")


def cmd_etl_deny(args) -> int:
    report = Report("ETL_DENY")
    try:
        data = source_data(args, args.source)
        if not report.check(bool(data["found"]) and data["finished_request"] is not None,
                            f"{args.source}: finished load request {data['finished_request']}"):
            return report.result()
        before = denied_reads(data)
        result = subprocess.run(probe_command(args.source, data["finished_request"]), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines = [line for line in result.stdout.splitlines() if line.startswith("PROBE ")]
        for line in lines:
            print(f"      {line}")
        evaluate_probe(report, "\n".join(lines), before, denied_reads(source_data(args, args.source)))
    except (RuntimeError, ValueError, OSError) as exc:
        report.check(False, f"check not completed: {exc}")
    return report.result()


# ---------------------------------------------------------------- backup-check
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evaluate_backup(report: Report, directory: Path, active: list[dict]) -> None:
    copies = sorted(p for p in directory.iterdir() if COPY_RE.match(p.name)) if directory.is_dir() else []
    if not report.check(bool(copies), f"copies in {directory}: {len(copies)}"):
        return
    copy = copies[-1]
    side = copy.with_name(copy.name + ".sha256")
    report.check(oct(copy.stat().st_mode & 0o777) == "0o600", f"{copy.name}: mode {oct(copy.stat().st_mode & 0o777)}")
    if not report.check(side.is_file(), f"{side.name} present"):
        return
    expected = {name: digest for digest, name in (line.split(None, 1) for line in side.read_text().splitlines() if line)}
    report.check(expected.get(copy.name) == sha256_bytes(copy.read_bytes()), f"{copy.name}: SHA-256 of the copy")
    members: dict[str, str] = {}
    with tarfile.open(copy) as archive:
        for member in archive.getmembers():
            handle = archive.extractfile(member) if member.isfile() else None
            members[member.name] = sha256_bytes(handle.read()) if handle else ""
    report.check(all(expected.get(n) == d for n, d in members.items()) and len(members) == len(expected) - 1,
                 f"{len(members)} file(s) inside, each with its SHA-256")
    for b in active:
        name = b["ref"] + ".bin"
        report.check(members.get(name, "").lower() == (b["sha"] or "").lower() != "",
                     f"{b['source']}: ACTIVE {b['ref'][:8]} is in the copy with the journal SHA-256")


def cmd_backup_check(args) -> int:
    report = Report("BACKUP")
    try:
        evaluate_backup(report, args.backup_dir, psql_json(args.psql, args.database, ACTIVE_QUERY))
    except (RuntimeError, ValueError, OSError, tarfile.TarError) as exc:
        report.check(False, f"check not completed: {exc}")
    return report.result()


# ---------------------------------------------------------------- scan
def count_in_stream(chunks, needle: bytes) -> tuple[int, int]:
    """Matches of needle in a stream of byte chunks (matches across chunk borders are counted once)."""
    count, total, tail = 0, 0, b""
    for chunk in chunks:
        total += len(chunk)
        data = tail + chunk
        count += data.count(needle) - tail.count(needle)
        tail = data[-(len(needle) - 1):] if len(needle) > 1 else b""
    return count, total


def file_chunks(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            yield chunk


def process_chunks(cmd: list[str]):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd="/")
    try:
        while chunk := proc.stdout.read(CHUNK):
            yield chunk
    finally:
        proc.stdout.close()
        if proc.wait() != 0:
            raise RuntimeError(f"{shlex.join(cmd[:4])}... exit code {proc.returncode}")


def scan_places(marker: str, since: datetime.datetime, args) -> list[tuple[str, int, int, bool]]:
    """(place, matches, bytes read, the place must have data): an empty journal or dump is a failed read."""
    needle = marker.encode()
    places = [("system journal since the marker",
               count_in_stream(process_chunks(["journalctl", "--no-pager", "-o", "cat", "--since",
                                               since.strftime("%Y-%m-%d %H:%M:%S")]), needle), True)]
    for directory in args.log_dirs:
        found, total, files = 0, 0, 0
        if directory.is_dir():
            for path in sorted(p for p in directory.rglob("*") if p.is_file() and not p.is_symlink()):
                if path.suffix in SKIP_SUFFIXES:
                    continue
                n, size = count_in_stream(file_chunks(path), needle)
                found, total, files = found + n, total + size, files + 1
        places.append((f"{directory} ({files} files)", (found, total), False))
    places.append((f"database {args.database} (plain pg_dump to a pipe)",
                   count_in_stream(process_chunks([*args.pg_dump, "-d", args.database]), needle), True))
    return [(name, n, size, needed) for name, (n, size), needed in places]


def cmd_scan(args) -> int:
    report = Report("SCAN")
    try:
        marker = read_marker(args.marker_file)
        since = datetime.datetime.fromtimestamp(args.marker_file.stat().st_mtime) - datetime.timedelta(minutes=1)
        for name, found, size, needed in scan_places(marker, since, args):
            report.check(found == 0 and (size > 0 or not needed),
                         f"{name}: {found} match(es), {size} bytes read" + (" (nothing read)" if needed and not size else ""))
    except (RuntimeError, ValueError, OSError) as exc:
        report.check(False, f"scan not completed: {exc}")
    return report.result()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=("marker", "journal", "etl-deny", "backup-check", "scan"))
    p.add_argument("--source", default=None, help="journal: SECRET_ACCEPT; etl-deny: CRM_RETAIL")
    p.add_argument("--force", action="store_true", help="marker: replace an existing marker")
    p.add_argument("--marker-file", type=Path, default=MARKER_FILE)
    p.add_argument("--backup-dir", type=Path, default=BACKUP_DIR)
    p.add_argument("--log-dir", dest="log_dirs", type=Path, action="append", default=None)
    p.add_argument("--database", default="dwh")
    p.add_argument("--psql", default="runuser -u postgres -- psql")
    p.add_argument("--pg-dump", default="runuser -u postgres -- pg_dump")
    args = p.parse_args(argv)
    args.psql, args.pg_dump = shlex.split(args.psql), shlex.split(args.pg_dump)
    args.log_dirs = args.log_dirs or list(LOG_DIRS)
    if os.geteuid() != 0 and args.marker_file == MARKER_FILE:
        print("Run with sudo.")
        return 2
    if args.source is None:
        args.source = "CRM_RETAIL" if args.command == "etl-deny" else "SECRET_ACCEPT"
    return {"marker": cmd_marker, "journal": cmd_journal, "etl-deny": cmd_etl_deny,
            "backup-check": cmd_backup_check, "scan": cmd_scan}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
