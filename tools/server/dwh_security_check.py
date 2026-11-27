"""I14-07: read-only security check of the DWH server (audit areas B3, B7, B9, B11). Changes nothing.

  sudo python3 /opt/dwh/current/src/tools/server/dwh_security_check.py

Plan docs/DWH_I14_07_SECURITY_AUDIT_PLAN_APPROVED_20261126.md, step 2. Sections:
  B3  database roles (psql as postgres, read-only transaction): working roles dwh_app, dwh_etl, dwh_ai_ro,
      dwh_dm_sql_ro are not superusers and cannot create roles or databases; no other superuser can log in;
      PUBLIC has no CONNECT, no CREATE on schemas and no table privileges; SECURITY DEFINER functions fix
      search_path; dwh_ai_ro reads only dm, dwh_dm_sql_ro only ods, both write nothing and keep read-only, a statement
      timeout and search_path of their schema; no working role may
      UPDATE/DELETE/TRUNCATE audit tables or write secret_store tables. Reported only (WARN): SECURITY DEFINER
      functions callable by PUBLIC (PUBLIC has no CONNECT), audit tables a working role reads directly (finding N4),
      direct reads of secret_store tables; information: default privileges of schemas
  B11 audit journal: both triggers of audit.security_event present and enabled, no disabled trigger that calls a
      function of schema audit; information: events of the last 7 days by type
  B7  secrets: the journal of the DWH services (7 days) and the reports in /srv/dwh/incoming contain no
      connection string with a password, no Bearer token, no private key, no "password=<value>"; only counts and
      file names are printed, never the text found
  B9  server: TCP ports open beyond localhost (22, 80, 443 expected; the API, the model and the test API must be
      local); nginx: server_tokens off, TLS 1.2/1.3 only, HSTS, nosniff, X-Frame-Options, Content-Security-Policy (N3), login limit (N8);
      /etc/dwh/dwh.env has DWH_COOKIE_SECURE=true (N7, only this line is read);
      services run as a non-root user with NoNewPrivileges and ProtectSystem=strict; files of /etc/dwh,
      /var/lib/dwh/secrets and /backup/dwh are not readable by others, keys only by the owner; no file of the
      current release is writable by others; SSH: root login and password login (password login: WARN, risk
      accepted in I13-12)
Prints SECURITY_CHECK_RESULT: PASS / FAIL (WARN does not fail). Report: /srv/dwh/incoming/security_check_<time>.txt.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from dwh_v1_check import Db, Report, run_cmd  # noqa: E402  (same release)

REPORTS = Path("/srv/dwh/incoming")
CURRENT = Path("/opt/dwh/current")
WORKING_ROLES = ("dwh_app", "dwh_etl", "dwh_ai_ro", "dwh_dm_sql_ro")
SERVICES = ("dwh-api.service", "dwh-dispatcher.service", "dwh-llm.service", "dwh-test-api.service")
PUBLIC_PORTS = {22, 80, 443}
LOCAL_ONLY_PORTS = {8000: "API", 8081: "model", 8090: "test API"}
PRIVATE_FILES = (  # (folder, glob, mask of forbidden mode bits, what); a glob with * matches files only
    ("/etc/dwh", "*.env", 0o007, "settings"),  # except NO_SECRET_FILES
    ("/etc/dwh/keyring", "*", 0o077, "master keys"),
    ("/etc/dwh/ldap", "*", 0o077, "LDAP service password"),
    ("/etc/dwh/credentials", "*", 0o077, "service passwords"),
    ("/etc/dwh/tls", "**/*.key", 0o077, "TLS private keys"),
    ("/etc/dwh/tls", "**/*.pfx", 0o077, "TLS PFX"),
    ("/var/lib/dwh", "secrets", 0o007, "SecretStore folder"),
    ("/var/lib/dwh/secrets", "**/*", 0o077, "SecretStore files"),
    ("/backup/dwh", "*.dump", 0o007, "database copies"),
    ("/backup/dwh", "secrets", 0o007, "SecretStore copies folder"),
    ("/backup/dwh/secrets", "**/*", 0o007, "SecretStore copies"),
)
NO_SECRET_FILES = {"/etc/dwh/llm.env"}  # model files and threads of llama-server, 0644 by dwh_ai_setup.py
SECRET_PATTERNS = {
    "connection string with password": re.compile(rb"postgres(?:ql)?://[^:@/\s]+:[^@/\s]+@"),
    "Bearer token": re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # password=<value> without spaces (URL, DSN, environment) or "password": "<value>" (JSON, YAML); a phrase like
    # "no password: login impossible" is not a value.
    "password=<value>": re.compile(
        rb"(?i)\b(?:password|passwd|pwd)=(?![*<${%]|none\b|null\b)[^\s,;'\")&]{4,}"
        rb"|[\"']?\b(?:password|passwd|pwd)[\"']?\s*:\s*[\"'](?![*<${%])[^\"'\s]{4,}[\"']"),
}

ROLES_SQL = """
WITH roles AS (SELECT unnest(%(roles)s::text[]) AS r),
ex AS (SELECT r FROM roles WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)),
sch AS (SELECT oid, nspname, nspacl FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'),
rel AS (SELECT c.oid, c.relname, c.relacl, s.nspname, format('%%I.%%I', s.nspname, c.relname) AS name
          FROM pg_class c JOIN sch s ON s.oid = c.relnamespace WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f')),
fn AS (SELECT p.oid, p.prosecdef, p.proconfig, p.proacl, s.nspname, p.oid::regprocedure::text AS name
         FROM pg_proc p JOIN sch s ON s.oid = p.pronamespace),
priv AS (SELECT ex.r, rel.name, rel.nspname, x.p
           FROM ex CROSS JOIN rel CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) x(p)
          WHERE has_table_privilege(ex.r, rel.oid, x.p))
SELECT json_build_object(
  'roles', (SELECT coalesce(json_agg(json_build_object('role', rolname, 'super', rolsuper,
              'createrole', rolcreaterole, 'createdb', rolcreatedb, 'bypassrls', rolbypassrls,
              'replication', rolreplication) ORDER BY rolname), '[]')
              FROM pg_roles WHERE rolname IN (SELECT r FROM ex)),
  'ro_settings', (SELECT coalesce(json_object_agg(r.rolname, coalesce(s.setconfig, r.rolconfig, '{}')), '{}')
                   FROM pg_roles r LEFT JOIN pg_db_role_setting s ON s.setrole = r.oid AND s.setdatabase = 0
                  WHERE r.rolname IN ('dwh_ai_ro', 'dwh_dm_sql_ro')),
  'missing_roles', (SELECT coalesce(json_agg(r ORDER BY r), '[]') FROM roles WHERE r NOT IN (SELECT r FROM ex)),
  'login_superusers', (SELECT coalesce(json_agg(rolname ORDER BY rolname), '[]') FROM pg_roles
                        WHERE rolsuper AND rolcanlogin AND rolname <> 'postgres'),
  'public_connect', (SELECT datacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(datacl) a
                       WHERE a.grantee = 0 AND a.privilege_type = 'CONNECT')
                       FROM pg_database WHERE datname = current_database()),
  'public_create', (SELECT coalesce(json_agg(nspname ORDER BY nspname), '[]') FROM sch
                     WHERE EXISTS (SELECT 1 FROM aclexplode(nspacl) a WHERE a.grantee = 0
                                     AND a.privilege_type = 'CREATE')),
  'public_tables', (SELECT coalesce(json_agg(DISTINCT name), '[]') FROM rel, aclexplode(rel.relacl) a
                     WHERE a.grantee = 0),
  'definer_no_path', (SELECT coalesce(json_agg(name ORDER BY name), '[]') FROM fn
                       WHERE prosecdef AND NOT EXISTS (SELECT 1 FROM unnest(proconfig) c
                                                         WHERE c LIKE 'search_path=%%')),
  'definer_public', (SELECT coalesce(json_agg(name ORDER BY name), '[]') FROM fn
                      WHERE prosecdef AND nspname <> 'public'
                        AND (proacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(proacl) a
                                                        WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))),
  'ai_outside', (SELECT coalesce(json_agg(DISTINCT name), '[]') FROM priv
                  WHERE r = 'dwh_ai_ro' AND p = 'SELECT' AND nspname <> 'dm'),
  'dm_sql_outside', (SELECT coalesce(json_agg(DISTINCT name), '[]') FROM priv
                      WHERE r = 'dwh_dm_sql_ro' AND p = 'SELECT' AND nspname <> 'ods'),
  'ro_write', (SELECT coalesce(json_agg(DISTINCT r || ' ' || p || ' ' || name), '[]') FROM priv
                WHERE r IN ('dwh_ai_ro', 'dwh_dm_sql_ro') AND p <> 'SELECT'),
  'audit_write', (SELECT coalesce(json_agg(DISTINCT r || ' ' || p || ' ' || name), '[]') FROM priv
                   WHERE nspname = 'audit' AND p <> 'SELECT'
                     AND NOT (p = 'INSERT' AND name = 'audit.security_event')),
  'audit_insert', (SELECT coalesce(json_agg(DISTINCT r), '[]') FROM priv
                    WHERE name = 'audit.security_event' AND p = 'INSERT'),
  'audit_read', (SELECT coalesce(json_agg(DISTINCT r || ' ' || name), '[]') FROM priv
                  WHERE nspname = 'audit' AND p = 'SELECT'),
  'secret_write', (SELECT coalesce(json_agg(DISTINCT r || ' ' || p || ' ' || name), '[]') FROM priv
                    WHERE nspname = 'secret_store' AND p <> 'SELECT'),
  'secret_read', (SELECT coalesce(json_agg(DISTINCT r || ' ' || name), '[]') FROM priv
                   WHERE nspname = 'secret_store' AND p = 'SELECT'),
  'default_acl', (SELECT coalesce(json_agg(json_build_object('owner', pg_get_userbyid(d.defaclrole),
                    'schema', coalesce(n.nspname, '(all)'), 'type', d.defaclobjtype,
                    'grantees', (SELECT string_agg(DISTINCT CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                                   ELSE pg_get_userbyid(a.grantee) END, ',') FROM aclexplode(d.defaclacl) a
                                  WHERE a.grantee <> d.defaclrole))
                    ORDER BY n.nspname, d.defaclobjtype), '[]')
                    FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace),
  'event_table', to_regclass('audit.security_event') IS NOT NULL,
  'event_triggers', (SELECT coalesce(json_object_agg(tgname, tgenabled <> 'D'), '{}') FROM pg_trigger
                      WHERE tgrelid = to_regclass('audit.security_event') AND NOT tgisinternal),
  'audit_triggers', (SELECT json_build_object('total', count(*),
                       'disabled', coalesce(json_agg(format('%%s on %%s', t.tgname, t.tgrelid::regclass))
                                     FILTER (WHERE t.tgenabled = 'D'), '[]'))
                       FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
                       JOIN pg_namespace n ON n.oid = p.pronamespace
                      WHERE NOT t.tgisinternal AND n.nspname = 'audit'));
"""

EVENTS_SQL = """
SELECT coalesce(json_agg(json_build_object('type', event_type, 'n', n) ORDER BY n DESC), '[]') FROM (
  SELECT coalesce(event_type, '') AS event_type, count(*) AS n FROM audit.security_event
   WHERE occurred_at > now() - interval '7 days' GROUP BY 1) x;
"""


def listing(items: list, limit: int = 8) -> str:
    if not items:
        return ""
    more = f" … (+{len(items) - limit})" if len(items) > limit else ""
    return ": " + ", ".join(str(i) for i in items[:limit]) + more


# --- B3, B11 ------------------------------------------------------------------------------------------------------

def check_database(report: Report, db: Db) -> None:
    roles_array = "ARRAY[" + ", ".join(f"'{r}'" for r in WORKING_ROLES) + "]"
    d = db.json(ROLES_SQL.replace("%(roles)s", roles_array).replace("%%", "%"))
    report.check(not d["missing_roles"], "B3 working roles exist" + listing(d["missing_roles"]), warn=True)
    bad = [f"{r['role']} {k}" for r in d["roles"] for k in ("super", "createrole", "createdb", "bypassrls",
                                                            "replication") if r[k]]
    report.check(not bad, "B3 working roles: no superuser, CREATEROLE, CREATEDB, BYPASSRLS, REPLICATION"
                 + listing(bad))
    report.check(not d["login_superusers"], "B3 superusers that can log in (besides postgres): "
                 f"{len(d['login_superusers'])}" + listing(d["login_superusers"]))
    report.check(not d["public_connect"], "B3 PUBLIC has no CONNECT to the database")
    report.check(not d["public_create"], "B3 PUBLIC cannot create objects in schemas" + listing(d["public_create"]))
    report.check(not d["public_tables"], f"B3 tables with privileges for PUBLIC: {len(d['public_tables'])}"
                 + listing(d["public_tables"]))
    report.check(not d["definer_no_path"], "B3 SECURITY DEFINER functions without a fixed search_path: "
                 f"{len(d['definer_no_path'])}" + listing(d["definer_no_path"]))
    report.check(not d["definer_public"], "B3 SECURITY DEFINER functions callable by PUBLIC (PUBLIC has no "
                 f"CONNECT): {len(d['definer_public'])}" + listing(d["definer_public"]), warn=True)
    report.check(not d["ai_outside"], f"B3 dwh_ai_ro reads only dm; outside: {len(d['ai_outside'])}"
                 + listing(d["ai_outside"]))
    report.check(not d["dm_sql_outside"], f"B3 dwh_dm_sql_ro reads only ods; outside: {len(d['dm_sql_outside'])}"
                 + listing(d["dm_sql_outside"]))
    for role, schema in (("dwh_ai_ro", "dm"), ("dwh_dm_sql_ro", "ods")):
        if role in d["ro_settings"]:
            conf = dict(c.split("=", 1) for c in d["ro_settings"][role] if "=" in c)
            ok = (conf.get("default_transaction_read_only") == "on" and bool(conf.get("statement_timeout"))
                  and conf.get("search_path") == schema)
            report.check(ok, f"B3 {role} settings: read-only {conf.get('default_transaction_read_only', '-')}, "
                         f"statement_timeout {conf.get('statement_timeout', '-')}, "
                         f"search_path {conf.get('search_path', '-')} (expected {schema})")
    report.check(not d["ro_write"], "B3 dwh_ai_ro and dwh_dm_sql_ro write nothing" + listing(d["ro_write"]))
    report.check(not d["audit_write"], "B3 working roles change no audit table (INSERT into audit.security_event "
                 "aside)" + listing(d["audit_write"]))
    report.check(not d["audit_insert"], "B3 roles that may INSERT into audit.security_event directly: "
                 f"{len(d['audit_insert'])}" + listing(d["audit_insert"]), warn=True)
    report.check(not d["audit_read"], "B3 audit tables read directly by working roles (N4): "
                 f"{len(d['audit_read'])}" + listing(d["audit_read"]), warn=True)
    report.check(not d["secret_write"], "B3 no working role writes secret_store tables directly"
                 + listing(d["secret_write"]))
    report.check(not d["secret_read"], "B3 secret_store tables read directly by working roles: "
                 f"{len(d['secret_read'])}" + listing(d["secret_read"]), warn=True)
    for item in d["default_acl"]:
        report.info(f"default privileges: owner {item['owner']}, schema {item['schema']}, type {item['type']}"
                    f" -> {item['grantees'] or '-'}")
    if not d["event_table"]:
        report.add("FAIL", "B11 audit.security_event is missing")
        return
    triggers = d["event_triggers"]
    needed = ("trg_security_event_append_only", "trg_security_event_no_truncate")
    report.check(all(triggers.get(t) for t in needed), "B11 audit.security_event protected: "
                 + ", ".join(f"{t} {'on' if triggers.get(t) else ('off' if t in triggers else 'missing')}"
                             for t in needed))
    audit = d["audit_triggers"]
    report.check(not audit["disabled"], f"B11 triggers calling audit functions: {audit['total']}, disabled: "
                 f"{len(audit['disabled'])}" + listing(audit["disabled"]))
    events = db.json(EVENTS_SQL)
    report.info("audit events of the last 7 days: " + (", ".join(f"{e['type'] or '-'} {e['n']}"
                                                                for e in events[:15]) or "none"))


# --- B7 -----------------------------------------------------------------------------------------------------------

def scan_text(data: bytes) -> dict[str, int]:
    return {name: len(rx.findall(data)) for name, rx in SECRET_PATTERNS.items()}


def check_secrets(report: Report, runner=run_cmd, reports: Path = REPORTS) -> None:
    args = ["journalctl", "--no-pager", "-o", "cat", "--since", "-7 days"]
    for unit in SERVICES + ("dwh-backup.service",):
        args += ["-u", unit]
    result = runner(args)
    if result.returncode != 0:
        report.add("FAIL", f"B7 journal not read: {(result.stderr or '').strip()[-200:]}")
    else:
        found = {k: v for k, v in scan_text(result.stdout.encode("utf-8", "replace")).items() if v}
        report.check(not found, f"B7 journal of the DWH services (7 days, {len(result.stdout.splitlines())} lines): "
                     "no secrets" + listing([f"{k} x{v}" for k, v in found.items()]))
    hits = []
    files = sorted(p for p in reports.glob("*") if p.is_file() and p.suffix in (".txt", ".md", ".log", ".json")
                   and not p.name.startswith("security_check_"))
    for path in files:
        try:
            found = {k: v for k, v in scan_text(path.read_bytes()).items() if v}
        except OSError as exc:
            hits.append(f"{path.name} not read ({exc.strerror})")
            continue
        hits += [f"{path.name}: {k} x{v}" for k, v in found.items()]
    report.check(not hits, f"B7 reports in {reports} ({len(files)} files): no secrets" + listing(hits))


# --- B9 -----------------------------------------------------------------------------------------------------------

def parse_listeners(text: str) -> list[tuple[str, int]]:
    out = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        address, _, port = cols[3].rpartition(":")
        if port.isdigit():
            out.append((address.strip("[]"), int(port)))
    return out


def is_local(address: str) -> bool:
    return address.startswith("127.") or address in ("::1", "localhost")


def check_ports(report: Report, runner=run_cmd) -> None:
    result = runner(["ss", "-Hltn"])
    if result.returncode != 0:
        report.add("FAIL", "B9 ports not read (ss)")
        return
    listeners = parse_listeners(result.stdout)
    public = sorted({port for address, port in listeners if not is_local(address)})
    leaks = [f"{LOCAL_ONLY_PORTS[p]} {p}" for p in public if p in LOCAL_ONLY_PORTS]
    other = [str(p) for p in public if p not in PUBLIC_PORTS and p not in LOCAL_ONLY_PORTS]
    report.check(not leaks, "B9 API, model and test API listen only on localhost" + listing(leaks))
    report.check(not other, "B9 ports open beyond localhost: " + ", ".join(str(p) for p in public)
                 + (f"; unexpected: {', '.join(other)}" if other else ""), warn=True)


def check_nginx(report: Report, runner=run_cmd) -> None:
    result = runner(["nginx", "-T"])
    if result.returncode != 0:
        report.add("FAIL", "B9 nginx configuration not read (nginx -T)")
        return
    text = "\n".join(line.split("#", 1)[0] for line in result.stdout.splitlines())
    protocols = set(re.findall(r"ssl_protocols\s+([^;]+);", text))
    weak = sorted({p for line in protocols for p in line.split() if p not in ("TLSv1.2", "TLSv1.3")})
    report.check(bool(protocols) and not weak, "B9 nginx TLS protocols: "
                 + ("; ".join(sorted(protocols)) or "not set") + (f" (weak: {', '.join(weak)})" if weak else ""))
    report.check(re.search(r"server_tokens\s+off\s*;", text) is not None, "B9 nginx server_tokens off")
    for header in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options"):
        report.check(re.search(rf"add_header\s+{header}\b", text, re.I) is not None, f"B9 nginx header {header}")
    report.check(re.search(r"add_header\s+Content-Security-Policy\s+\"default-src 'self'", text, re.I) is not None,
                 "B9 nginx header Content-Security-Policy default-src 'self' (N3)")
    report.check(re.search(r"limit_req_zone\s+\S+\s+zone=dwh_login:", text) is not None
                 and re.search(r"location\s*=\s*/api/auth/login\s*\{[^}]*limit_req\s+zone=dwh_login", text) is not None,
                 "B1 nginx limits login attempts by address (limit_req dwh_login, N8)")


def check_cookie_setting(report: Report, env_file: Path = Path("/etc/dwh/dwh.env")) -> None:
    """N7: the session cookie is sent with Secure. Only this one line of the file is read, nothing else is printed."""
    try:
        lines = env_file.read_text().splitlines()
    except OSError as exc:
        report.add("FAIL", f"B1 {env_file} not read: {type(exc).__name__}")
        return
    value = next((line.split("=", 1)[1].strip().strip('"').lower() for line in lines
                  if line.strip().startswith("DWH_COOKIE_SECURE=")), "")
    report.check(value in ("1", "true", "yes", "on"),
                 f"B1 {env_file}: DWH_COOKIE_SECURE=true (session cookie with Secure, N7): {value or 'not set'}")


def check_services(report: Report, runner=run_cmd) -> None:
    for unit in SERVICES:
        result = runner(["systemctl", "show", unit, "-p", "LoadState", "-p", "User", "-p", "NoNewPrivileges",
                         "-p", "ProtectSystem", "-p", "PrivateTmp"])
        props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if props.get("LoadState") != "loaded":
            report.info(f"service {unit}: {props.get('LoadState', 'unknown')}")
            continue
        user = props.get("User", "")
        report.check(user not in ("", "root"), f"B9 service {unit} runs as {user or 'root'}")
        weak = [k for k, good in (("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"), ("PrivateTmp", "yes"))
                if props.get(k) != good]
        report.check(not weak, f"B9 service {unit} limits: " + ", ".join(
            f"{k}={props.get(k, '?')}" for k in ("NoNewPrivileges", "ProtectSystem", "PrivateTmp")), warn=True)


def mode_problems(patterns=PRIVATE_FILES) -> tuple[int, list[str]]:
    seen, bad = 0, []
    for folder, pattern, mask, what in patterns:
        try:
            paths = sorted(Path(folder).glob(pattern))
        except OSError:
            continue
        for path in paths:
            try:
                st = path.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or ("*" in pattern and not stat.S_ISREG(st.st_mode)) \
                    or str(path) in NO_SECRET_FILES:
                continue
            seen += 1
            if st.st_mode & mask:
                bad.append(f"{path} {stat.filemode(st.st_mode)} ({what})")
    return seen, bad


def world_writable(root: Path) -> list[str]:
    found = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory, name)
            try:
                st = path.lstat()
            except OSError:
                continue
            if not stat.S_ISLNK(st.st_mode) and st.st_mode & stat.S_IWOTH:
                found.append(str(path))
    return found


def closed_parent(path: str) -> str | None:
    """The nearest folder above the file that others cannot enter (no o+x), or None if the file is reachable."""
    for parent in Path(path).parents:
        try:
            if not parent.stat().st_mode & stat.S_IXOTH:
                return str(parent)
        except OSError:
            return str(parent)
    return None


def check_files(report: Report, current: Path = CURRENT, patterns=PRIVATE_FILES) -> None:
    seen, bad = mode_problems(patterns)
    exposed = [b for b in bad if closed_parent(b.split(" ", 1)[0]) is None]
    hidden = [f"{b} behind {closed_parent(b.split(' ', 1)[0])}" for b in bad if b not in exposed]
    report.check(seen > 0 and not exposed, f"B9 private files ({seen} checked) not readable by others"
                 + listing(exposed))
    report.check(not hidden, f"B9 private files with open mode, closed only by their folder: {len(hidden)}"
                 + listing(hidden, 3), warn=True)
    release = Path(os.path.realpath(current))
    if release.is_dir():
        found = world_writable(release)
        report.check(not found, f"B9 files of the release {release.name} writable by others: {len(found)}"
                     + listing(found, 5))
    else:
        report.add("FAIL", f"B9 release folder {current} not found")


def check_ssh(report: Report, runner=run_cmd) -> None:
    result = runner(["sshd", "-T"])
    if result.returncode != 0:
        report.add("WARN", "B9 SSH settings not read (sshd -T)")
        return
    conf = dict(line.split(" ", 1) for line in result.stdout.lower().splitlines() if " " in line)
    root = conf.get("permitrootlogin", "?")
    report.check(root == "no", f"B9 SSH root login: {root}", warn=root in ("prohibit-password", "without-password"))
    password = conf.get("passwordauthentication", "?")
    report.check(password == "no", f"B9 SSH password login: {password} (risk accepted, I13-12)", warn=True)


# --- main ---------------------------------------------------------------------------------------------------------

def safe_run(args: list[str], stdin: str | None = None):
    try:
        return run_cmd(args, stdin)
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: {exc.strerror}")


def run(args, runner=safe_run) -> int:
    report = Report(args.report)
    report.out(f"Security check of {args.database}, {datetime.datetime.now():%Y-%m-%d %H:%M:%S}, tools {TOOLS}")
    db = Db(args.psql, args.database, runner)
    try:
        read_only = db.json("SELECT to_json(current_setting('transaction_read_only'));")
        report.check(read_only == "on", f"database {args.database}: read-only transaction ({read_only})")
        check_database(report, db)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        report.add("FAIL", f"database not read: {exc}")
    if not args.skip_system:
        check_secrets(report, runner, args.reports)
        check_ports(report, runner)
        check_nginx(report, runner)
        check_cookie_setting(report)
        check_services(report, runner)
        check_files(report, args.current)
        check_ssh(report, runner)
    failed, warns = report.count("FAIL"), report.count("WARN")
    report.out(f"SECURITY_CHECK_RESULT: {'PASS' if not failed else 'FAIL'} ({failed} FAIL, {warns} WARN, "
               f"{len(report.levels)} checks)")
    if args.report is not None:
        print(f"report: {args.report}")
    return 0 if not failed else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--database", default="dwh")
    p.add_argument("--psql", default="runuser -u postgres -- psql",
                   help="psql command line (tests: 'psql -h <socket dir> -p <port> -U postgres')")
    p.add_argument("--skip-system", action="store_true", help="only the database sections (tests)")
    p.add_argument("--current", type=Path, default=CURRENT)
    p.add_argument("--reports", type=Path, default=REPORTS, help="folder of reports scanned for secrets")
    p.add_argument("--report-dir", type=Path, default=REPORTS)
    p.add_argument("--no-report", action="store_true")
    args = p.parse_args(argv)
    args.psql = shlex.split(args.psql)
    args.report = None if args.no_report else (
        args.report_dir / f"security_check_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
    if not args.skip_system and os.geteuid() != 0:
        print("Run with sudo: the journal, nginx, SSH and private files need root.")
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
