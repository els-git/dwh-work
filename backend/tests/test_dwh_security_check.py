"""I14-07: tools/server/dwh_security_check.py with fake commands, a fake database and temporary files."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "server"))
import dwh_security_check as sc  # noqa: E402

GOOD_NGINX = """
server { listen 443 ssl; ssl_protocols TLSv1.2 TLSv1.3; server_tokens off;
  add_header Strict-Transport-Security "max-age=31536000" always;
  add_header X-Content-Type-Options nosniff always;
  add_header X-Frame-Options DENY always; }
"""
SS = """LISTEN 0 511 0.0.0.0:443 0.0.0.0:*
LISTEN 0 511 0.0.0.0:80 0.0.0.0:*
LISTEN 0 128 0.0.0.0:22 0.0.0.0:*
LISTEN 0 2048 127.0.0.1:8000 0.0.0.0:*
LISTEN 0 512 127.0.0.1:8081 0.0.0.0:*
LISTEN 0 244 127.0.0.1:5432 0.0.0.0:*
LISTEN 0 511 [::]:443 [::]:*
LISTEN 0 244 [::1]:5432 [::]:*
"""
SERVICE = "LoadState=loaded\nUser=dwh\nNoNewPrivileges=yes\nProtectSystem=strict\nPrivateTmp=yes\n"
SECRET = "postgresql://dwh_app:S3cretValue@127.0.0.1/dwh"


def done(stdout: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, "")


class Fake:
    def __init__(self, **over):
        self.out = {"ss": SS, "nginx": GOOD_NGINX, "sshd": "permitrootlogin no\npasswordauthentication yes\n",
                    "journalctl": "started\nrequest 5 SUCCESS\n", "systemctl": SERVICE}
        self.out.update(over)

    def __call__(self, args, stdin=None):
        value = self.out[args[0]]
        return value if isinstance(value, subprocess.CompletedProcess) else done(value)


def capture(fn, *args) -> tuple[sc.Report, str]:
    report = sc.Report(None)
    out = io.StringIO()
    with redirect_stdout(out):
        fn(report, *args)
    return report, out.getvalue()


class SecretScanTest(unittest.TestCase):
    def test_patterns(self):
        found = sc.scan_text(b"connect " + SECRET.encode() + b"\nAuthorization: Bearer " + b"a" * 40
                             + b"\n-----BEGIN PRIVATE KEY-----\npassword=hunter22\n")
        self.assertEqual(set(k for k, v in found.items() if v), set(sc.SECRET_PATTERNS))
        self.assertEqual(sc.scan_text(b'{"password": "hunter22"}')["password=<value>"], 1)
        self.assertEqual(sc.scan_text(b"host=x password=hunter22 dbname=y")["password=<value>"], 1)
        clean = sc.scan_text(b"password=*** password: <hidden> DWH_DB_PASSWORD is set password=${PW} "
                             b"pwd: '' postgresql://127.0.0.1/dwh Bearer token missing password=None "
                             b"ROLE_CREATED dwh_ai_ro (no password: login impossible until it is set)")
        self.assertEqual(sum(clean.values()), 0, clean)

    def test_journal_and_reports_print_counts_not_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "e1_037.txt").write_text("E1_RESULT: PASS\n", encoding="utf-8")
            Path(tmp, "leak.txt").write_text("dsn " + SECRET + "\n", encoding="utf-8")
            Path(tmp, "security_check_1.txt").write_text(SECRET, encoding="utf-8")  # own reports are skipped
            report, out = capture(sc.check_secrets, Fake(journalctl="x " + SECRET + "\n"), Path(tmp))
        self.assertEqual(report.count("FAIL"), 2, out)
        self.assertIn("leak.txt: connection string with password x1", out)
        self.assertNotIn("S3cretValue", out)
        self.assertNotIn("security_check_1", out)

    def test_clean_journal_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, out = capture(sc.check_secrets, Fake(), Path(tmp))
        self.assertEqual(report.count("FAIL"), 0, out)


class ServerTest(unittest.TestCase):
    def test_ports(self):
        report, out = capture(sc.check_ports, Fake())
        self.assertEqual(report.levels, ["PASS", "PASS"], out)
        report, out = capture(sc.check_ports, Fake(ss=SS + "LISTEN 0 1 0.0.0.0:8000 0.0.0.0:*\n"
                                                  "LISTEN 0 1 0.0.0.0:5432 0.0.0.0:*\n"))
        self.assertEqual(report.levels, ["FAIL", "WARN"], out)
        self.assertIn("API 8000", out)
        self.assertIn("unexpected: 5432", out)

    def test_nginx(self):
        report, out = capture(sc.check_nginx, Fake())
        self.assertIn("FAIL  B9 nginx header Content-Security-Policy", out)
        self.assertIn("FAIL  B1 nginx limits login attempts", out)
        # I14-07 N3/N8: the nginx file of the release passes everything
        release = (Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "dwh.conf").read_text()
        report, out = capture(sc.check_nginx, Fake(nginx=release))
        self.assertEqual(report.count("FAIL") + report.count("WARN"), 0, out)
        weak = GOOD_NGINX.replace("TLSv1.2 TLSv1.3", "TLSv1 TLSv1.2").replace("server_tokens off;", "")
        report, out = capture(sc.check_nginx, Fake(nginx=weak + "# add_header X-Frame-Options DENY;\n"))
        self.assertIn("FAIL  B9 nginx TLS protocols: TLSv1 TLSv1.2 (weak: TLSv1)", out)
        self.assertIn("FAIL  B9 nginx server_tokens off", out)

    def test_cookie_setting_n7(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "dwh.env"
            env.write_text("DWH_APP_PASSWORD=secret-value\n")
            report, out = capture(sc.check_cookie_setting, env)
            self.assertEqual(report.levels, ["FAIL"], out)
            self.assertNotIn("secret-value", out)
            env.write_text("DWH_APP_PASSWORD=secret-value\nDWH_COOKIE_SECURE=true\n")
            report, out = capture(sc.check_cookie_setting, env)
            self.assertEqual(report.levels, ["PASS"], out)
            self.assertNotIn("secret-value", out)

    def test_services(self):
        report, _ = capture(sc.check_services, Fake())
        self.assertEqual(report.levels, ["PASS", "PASS"] * len(sc.SERVICES))
        root = SERVICE.replace("User=dwh", "User=").replace("ProtectSystem=strict", "ProtectSystem=no")
        report, out = capture(sc.check_services, Fake(systemctl=root))
        self.assertIn("FAIL  B9 service dwh-api.service runs as root", out)
        self.assertIn("WARN  B9 service dwh-api.service limits", out)
        report, out = capture(sc.check_services, Fake(systemctl="LoadState=not-found\n"))
        self.assertEqual(report.levels, [], out)

    def test_ssh(self):
        report, out = capture(sc.check_ssh, Fake())
        self.assertEqual(report.levels, ["PASS", "WARN"], out)
        report, _ = capture(sc.check_ssh, Fake(sshd="permitrootlogin yes\npasswordauthentication no\n"))
        self.assertEqual(report.levels, ["FAIL", "PASS"])
        report, _ = capture(sc.check_ssh, Fake(sshd="permitrootlogin prohibit-password\n"))
        self.assertEqual(report.levels[0], "WARN")

    def test_file_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "keyring").mkdir()
            key = base / "keyring" / "K1.cred"
            key.write_text("x")
            env = base / "dwh.env"
            env.write_text("x")
            os.chmod(key, 0o600)
            os.chmod(env, 0o640)
            patterns = ((tmp, "*.env", 0o007, "settings"), (str(base / "keyring"), "*", 0o077, "keys"),
                        (tmp, "keyring", 0o007, "folder"))
            os.chmod(base / "keyring", 0o750)
            self.assertEqual(sc.mode_problems(patterns), (3, []))
            os.chmod(key, 0o640)
            os.chmod(env, 0o644)
            seen, bad = sc.mode_problems(patterns)
            self.assertEqual(seen, 3)
            self.assertEqual(len(bad), 2, bad)
            os.chmod(base, 0o755)
            os.chmod(base / "keyring", 0o755)
            report, out = capture(lambda r: sc.check_files(r, base, patterns))
            self.assertIn("FAIL  B9 private files (3 checked)", out)
            os.chmod(base, 0o700)  # the folder closes the files: only a warning
            report, out = capture(lambda r: sc.check_files(r, base, patterns))
            os.chmod(base, 0o755)
            self.assertIn("PASS  B9 private files (3 checked)", out)
            self.assertIn("WARN  B9 private files with open mode, closed only by their folder: 3", out)
            release = base / "release"
            (release / "src").mkdir(parents=True)
            (release / "src" / "a.py").write_text("x")
            self.assertEqual(sc.world_writable(release), [])
            os.chmod(release / "src" / "a.py", 0o666)
            self.assertEqual(sc.world_writable(release), [str(release / "src" / "a.py")])

    def test_missing_command_is_reported(self):
        result = sc.safe_run(["no-such-command-dwh"])
        self.assertEqual(result.returncode, 127)


CLEAN_DB = {
    "roles": [{"role": r, "super": False, "createrole": False, "createdb": False, "bypassrls": False,
               "replication": False} for r in sc.WORKING_ROLES],
    "ro_settings": {"dwh_ai_ro": ["default_transaction_read_only=on", "statement_timeout=30s", "search_path=dm"],
                    "dwh_dm_sql_ro": ["default_transaction_read_only=on", "statement_timeout=30min",
                                      "search_path=ods"]},
    "missing_roles": [], "login_superusers": [], "public_connect": False, "public_create": [], "public_tables": [],
    "definer_no_path": [], "definer_public": [], "ai_outside": [], "dm_sql_outside": [], "ro_write": [],
    "audit_write": [], "audit_insert": [], "audit_read": [], "secret_write": [], "secret_read": [],
    "default_acl": [{"owner": "dwh_admin", "schema": "audit", "type": "r", "grantees": "dwh_etl"}],
    "event_table": True,
    "event_triggers": {"trg_security_event_append_only": True, "trg_security_event_no_truncate": True},
    "audit_triggers": {"total": 6, "disabled": []},
}


class FakeDb:
    def __init__(self, data):
        self.data = data

    def json(self, sql):
        if "audit.security_event" in sql and "event_type" in sql and "json_build_object('roles'" not in sql:
            return [{"type": "AUTH_SESSION", "n": 4}]
        assert sql.lstrip().startswith("WITH roles AS (SELECT unnest(ARRAY['dwh_app'"), sql[:80]
        assert "%%" not in sql and "%(roles)s" not in sql
        return self.data


class DatabaseTest(unittest.TestCase):
    def test_clean(self):
        report, out = capture(sc.check_database, FakeDb(CLEAN_DB))
        self.assertEqual(report.count("FAIL") + report.count("WARN"), 0, out)
        self.assertIn("default privileges: owner dwh_admin, schema audit, type r -> dwh_etl", out)
        self.assertIn("audit events of the last 7 days: AUTH_SESSION 4", out)

    def test_findings(self):
        data = dict(CLEAN_DB, ai_outside=["ods.x"], public_connect=True, audit_read=["dwh_etl audit.revoked_grant_036"],
                    audit_write=["dwh_etl INSERT audit.revoked_grant_036"], audit_insert=["dwh_etl"],
                    event_triggers={"trg_security_event_append_only": True},
                    roles=[dict(CLEAN_DB["roles"][0], super=True)],
                    ro_settings={"dwh_ai_ro": ["statement_timeout=30s", "search_path=dm,public"]})
        report, out = capture(sc.check_database, FakeDb(data))
        self.assertIn("FAIL  B3 dwh_ai_ro reads only dm; outside: 1: ods.x", out)
        self.assertIn("FAIL  B3 PUBLIC has no CONNECT", out)
        self.assertIn("FAIL  B3 working roles: no superuser, CREATEROLE, CREATEDB, BYPASSRLS, REPLICATION: "
                      "dwh_app super", out)
        self.assertIn("WARN  B3 audit tables read directly by working roles (N4): 1: dwh_etl audit.revoked_grant_036",
                      out)
        self.assertIn("trg_security_event_no_truncate missing", out)
        self.assertIn("FAIL  B3 dwh_ai_ro settings: read-only -, statement_timeout 30s, search_path dm,public", out)
        self.assertIn("FAIL  B3 working roles change no audit table (INSERT into audit.security_event aside): "
                      "dwh_etl INSERT audit.revoked_grant_036", out)
        self.assertIn("WARN  B3 roles that may INSERT into audit.security_event directly: 1: dwh_etl", out)
        self.assertEqual(report.count("FAIL"), 6, out)


if __name__ == "__main__":
    unittest.main()
