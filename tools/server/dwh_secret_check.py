"""I02-03 step 2: read-only inventory of SecretStore on the DWH server. Changes nothing.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_secret_check.py

Plan docs/DWH_I02_03_SECRETSTORE_ACCEPTANCE_PLAN_APPROVED_20261120.md (R5, R6). Checks:
  K  master keys: /etc/dwh/keyring (0700 root), every <key_id>.cred (0600 root); key_id of every ACTIVE
     binding has its .cred file
  F  encrypted files: /var/lib/dwh/secrets (0700 dwh), files <secret_ref>.bin (0600 dwh, regular, no links);
     every ACTIVE binding has its file and the file's SHA-256 equals the journal (ciphertext_sha256)
  S  bindings: at most one ACTIVE per source; PENDING bindings and operations without OUTCOME (S07-S08, A08)
     are listed (nothing is repaired: that is a decision of the user)
  R  rights: which database roles can read the secret_store schema or run its functions and the
     app.integration_source_secret_* / etl.source_secret_* functions
The journal is read by psql as postgres in a read-only transaction (R6: dwh_app gets no rights). Output has no
credential values; secret_ref and digests are shortened to 8 characters. Prints SECRET_CHECK_RESULT: PASS / FAIL
(WARN does not fail: things to look at, e.g. old REVOKED files or files without a binding).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

KEYRING = Path("/etc/dwh/keyring")
SECRETS = Path("/var/lib/dwh/secrets")
SERVICE_USER = "dwh"
STALE_MINUTES = 10
FILE_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.bin$")
LOGIN_ROLES = ("dwh_app", "dwh_etl", "dwh_ai_ro", "dwh_dm_sql_ro")

QUERY = r"""
SET default_transaction_read_only = on;
SELECT json_build_object(
  'read_only', current_setting('transaction_read_only'),
  'bindings', (SELECT coalesce(json_agg(json_build_object(
        'ref', b.secret_ref::text, 'source', s.code, 'profile', b.profile_code, 'state', b.state,
        'key_id', b.key_id, 'sha', b.ciphertext_sha256, 'created', b.created_at, 'activated', b.activated_at,
        'revoked', b.revoked_at,
        'outcome', (SELECT o.code FROM secret_store.operation_event o
                     WHERE o.operation_id = b.create_operation_id AND o.phase = 'OUTCOME'))
        ORDER BY s.code, b.created_at), '[]')
      FROM secret_store.binding b JOIN meta.source_system s ON s.source_system_id = b.source_system_id),
  'open_operations', (SELECT coalesce(json_agg(json_build_object(
        'operation', a.operation_id::text, 'type', a.operation_type, 'source', s.code,
        'actor', a.actor_kind, 'at', a.occurred_at) ORDER BY a.occurred_at), '[]')
      FROM secret_store.operation_event a JOIN meta.source_system s ON s.source_system_id = a.source_system_id
     WHERE a.phase = 'ATTEMPT' AND a.code = 'PERMITTED_ATTEMPT'
       AND NOT EXISTS (SELECT 1 FROM secret_store.operation_event o
                        WHERE o.operation_id = a.operation_id AND o.phase = 'OUTCOME')),
  'operations', (SELECT coalesce(json_agg(x), '[]') FROM (
       SELECT operation_type AS type, phase, code, count(*) AS n FROM secret_store.operation_event
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3) x),
  'schema_rights', (SELECT coalesce(json_agg(json_build_object('role', r.rolname,
        'usage', has_schema_privilege(r.oid, 'secret_store', 'USAGE'),
        'tables', (SELECT coalesce(json_agg(c.relname ORDER BY c.relname), '[]') FROM pg_class c
                    WHERE c.relnamespace = 'secret_store'::regnamespace AND c.relkind IN ('r', 'v', 'p')
                      AND has_table_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')))
      ORDER BY r.rolname), '[]')
      FROM pg_roles r WHERE r.rolname = ANY(%(roles)s)),
  'function_rights', (SELECT coalesce(json_agg(json_build_object('function', f.fn, 'roles', f.roles)
      ORDER BY f.fn), '[]') FROM (
       SELECT p.oid::regprocedure::text AS fn,
              (SELECT coalesce(json_agg(r.rolname ORDER BY r.rolname), '[]') FROM pg_roles r
                WHERE (r.rolname = ANY(%(roles)s) OR r.rolname = 'public')
                  AND has_function_privilege(r.oid, p.oid, 'EXECUTE')) AS roles
         FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'secret_store'
           OR (n.nspname = 'app' AND p.proname LIKE 'integration\_source\_secret\_%%')
           OR (n.nspname = 'etl' AND p.proname LIKE 'source\_secret\_%%')) f)
);
"""

# Who may run which function (anything else is FAIL). secret_store functions: nobody (package 004).
EXPECTED_FUNCTION_ROLES = (
    (re.compile(r"^secret_store\."), set()),
    (re.compile(r"^app\.integration_source_secret_connection\("), set()),
    (re.compile(r"^app\.integration_source_secret_"), {"dwh_app"}),
    (re.compile(r"^etl\.source_secret_"), {"dwh_etl"}),
)


class Report:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def add(self, level: str, text: str) -> None:
        self.lines.append((level, text))
        print(f"{level:4}  {text}", flush=True)

    def check(self, ok: bool, text: str, warn: bool = False) -> None:
        self.add("PASS" if ok else ("WARN" if warn else "FAIL"), text)

    @property
    def failed(self) -> int:
        return sum(1 for level, _ in self.lines if level == "FAIL")


def short(value: str | None) -> str:
    return (value or "-")[:8]


def owner(st: os.stat_result) -> str:
    import pwd  # Linux server only
    try:
        return pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        return str(st.st_uid)


def check_dir(report: Report, path: Path, mode: int, user: str, label: str) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        report.add("FAIL", f"{label}: {path} missing")
        return False
    ok = stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == mode and owner(st) == user
    report.check(ok, f"{label}: {path} {oct(stat.S_IMODE(st.st_mode))[2:]} {owner(st)} (expected {oct(mode)[2:]} {user})")
    return stat.S_ISDIR(st.st_mode)


def file_entries(report: Report, path: Path, mode: int, user: str, suffix: str, label: str) -> dict[str, Path]:
    """Regular files with the suffix; wrong mode/owner/links are FAIL, anything else in the folder is WARN."""
    found: dict[str, Path] = {}
    bad = 0
    for entry in sorted(path.iterdir()):
        st = os.lstat(entry)
        if not entry.name.endswith(suffix):
            report.add("WARN", f"{label}: unexpected entry {entry.name[:40]}")
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            report.add("FAIL", f"{label}: {entry.name[:12]}... is not a plain file (link or special)")
            bad += 1
            continue
        if stat.S_IMODE(st.st_mode) != mode or owner(st) != user:
            report.add("FAIL", f"{label}: {entry.name[:12]}... {oct(stat.S_IMODE(st.st_mode))[2:]} {owner(st)} "
                               f"(expected {oct(mode)[2:]} {user})")
            bad += 1
        found[entry.name[: -len(suffix)]] = entry
    if not bad:
        report.add("PASS", f"{label}: {len(found)} file(s), all {oct(mode)[2:]} {user}, no links")
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_journal(psql: list[str], database: str) -> dict:
    roles = "ARRAY[" + ",".join(f"'{r}'" for r in LOGIN_ROLES) + "]::name[]"
    sql = QUERY.replace("%(roles)s", roles).replace("%%", "%")
    result = subprocess.run([*psql, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", database, "-f", "-"],
                            input=sql, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
                            env=dict(os.environ, PGCLIENTENCODING="UTF8"))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-600:])
    return json.loads(result.stdout.strip())


def check_journal(report: Report, data: dict, files: dict[str, Path], keys: set[str], now: datetime.datetime) -> None:
    report.check(data["read_only"] == "on", f"journal read in a read-only transaction ({data['read_only']})")
    bindings = data["bindings"]
    print("      bindings (source / profile / state / key / ref / created / create outcome):")
    for b in bindings:
        print(f"        {b['source']} / {b['profile'] or '-'} / {b['state']} / {b['key_id'] or '-'} / "
              f"{short(b['ref'])} / {str(b['created'])[:19]} / {b.get('outcome') or '-'}")
    # Bindings without a profile were made before package 002/005 (28-29.10.2026, finding G8): they are not used
    # by the API or the ETL; their audited revocation is task I02-06. Reported apart, as WARN.
    legacy = [b for b in bindings if not b["profile"] and b["state"] != "REVOKED"]
    if legacy:
        report.add("WARN", f"{len(legacy)} binding(s) without profile from before package 005 (G8, task I02-06): "
                   + ", ".join(f"{b['state']} {short(b['ref'])} {str(b['created'])[:10]}" for b in legacy))
    current = [b for b in bindings if b["profile"]]
    active: dict[tuple, list] = {}
    for b in current:
        if b["state"] == "ACTIVE":
            active.setdefault((b["source"], b["profile"]), []).append(b)
    report.check(all(len(v) == 1 for v in active.values()),
                 f"one ACTIVE binding per source: {len(active)} source/profile pair(s)")
    for (source, profile), items in sorted(active.items(), key=lambda kv: str(kv[0])):
        for b in items:
            path = files.get(b["ref"])
            if path is None:
                report.add("FAIL", f"{source}/{profile}: ACTIVE {short(b['ref'])} has no encrypted file")
                continue
            digest = sha256(path)
            report.check(digest == b["sha"], f"{source}/{profile}: ACTIVE {short(b['ref'])} file matches the journal "
                                             f"({short(digest)} / {short(b['sha'])})")
            report.check(b["key_id"] in keys, f"{source}/{profile}: key {b['key_id']} is in the keyring")
    def age(value) -> float:
        return (now - datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))).total_seconds()

    failed = [b for b in current if b["state"] == "PENDING" and b.get("outcome") == "FAILED"]
    if failed:
        # S06: a failed CREATE/REPLACE leaves its PENDING binding unused; the previous ACTIVE one stays
        report.add("WARN", f"{len(failed)} PENDING binding(s) of failed operations (never activated): "
                   + ", ".join(f"{b['source']} {short(b['ref'])}" for b in failed))
    pending = [b for b in current if b["state"] == "PENDING" and b.get("outcome") != "FAILED"]
    old = [b for b in pending if age(b["created"]) > STALE_MINUTES * 60]
    # a fresh PENDING is an operation in progress (WARN); an old one is an interrupted operation (S08)
    report.check(not pending, f"PENDING bindings: {len(pending)}, older than {STALE_MINUTES} min: {len(old)}"
                 + "".join(f"; {b['source']} {short(b['ref'])} since {str(b['created'])[:19]}" for b in pending),
                 warn=not old)
    known = {b["ref"]: b for b in bindings}
    for ref in sorted(set(files) - set(known)):
        report.add("WARN", f"file {ref[:8]}... has no binding in the journal")
    revoked_files = [ref for ref, b in known.items() if b["state"] == "REVOKED" and ref in files]
    if revoked_files:
        report.add("WARN", f"{len(revoked_files)} file(s) of REVOKED bindings are still on disk")
    stale = [op for op in data["open_operations"] if age(op["at"]) > STALE_MINUTES * 60]
    report.check(not stale, f"operations without OUTCOME older than {STALE_MINUTES} min: {len(stale)}"
                 + "".join(f"; {o['type']} {o['source']} {short(o['operation'])} {str(o['at'])[:19]}" for o in stale))
    print("      journal events (type / phase / code: count):")
    for o in data["operations"]:
        print(f"        {o['type']} / {o['phase']} / {o['code']}: {o['n']}")
    for r in data["schema_rights"]:
        report.check(not r["usage"] and not r["tables"],
                     f"role {r['role']}: secret_store schema usage {r['usage']}, tables {', '.join(r['tables']) or 'none'}")
    for f in data["function_rights"]:
        expected = next((roles for pattern, roles in EXPECTED_FUNCTION_ROLES if pattern.match(f["function"])), set())
        got = set(f["roles"])
        report.check(got == expected, f"EXECUTE {f['function']}: {', '.join(sorted(got)) or 'nobody'}"
                                      + ("" if got == expected else f" (expected {', '.join(sorted(expected)) or 'nobody'})"))


def run(args, now=None) -> int:
    report = Report()
    keys: set[str] = set()
    if check_dir(report, args.keyring, 0o700, "root", "keyring"):
        keys = set(file_entries(report, args.keyring, 0o600, "root", ".cred", "keyring"))
        report.add("PASS" if keys else "FAIL", f"keyring: keys {', '.join(sorted(keys)) or 'none'}")
    files: dict[str, Path] = {}
    if check_dir(report, args.secrets, 0o700, args.service_user, "secrets"):
        files = file_entries(report, args.secrets, 0o600, args.service_user, ".bin", "secrets")
        for name in files:
            if not FILE_RE.match(name + ".bin"):
                report.add("FAIL", f"secrets: file name {name[:12]}... is not <uuid>.bin")
    try:
        data = read_journal(args.psql, args.database)
    except (RuntimeError, ValueError, OSError) as exc:
        report.add("FAIL", f"journal not read: {exc}")
    else:
        check_journal(report, data, files, keys, now or datetime.datetime.now(datetime.timezone.utc))
    warns = sum(1 for level, _ in report.lines if level == "WARN")
    print(f"SECRET_CHECK_RESULT: {'PASS' if not report.failed else 'FAIL'} "
          f"({report.failed} FAIL, {warns} WARN, {len(report.lines)} checks)")
    return 0 if not report.failed else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--keyring", type=Path, default=KEYRING)
    p.add_argument("--secrets", type=Path, default=SECRETS)
    p.add_argument("--service-user", default=SERVICE_USER)
    p.add_argument("--database", default="dwh")
    p.add_argument("--psql", default="runuser -u postgres -- psql",
                   help="psql command line (tests: 'psql -h <socket dir> -p <port>')")
    args = p.parse_args(argv)
    args.psql = shlex.split(args.psql)
    if os.geteuid() != 0 and args.keyring == KEYRING:
        print("Run with sudo: the keyring and the secrets folder are readable by root only.")
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
