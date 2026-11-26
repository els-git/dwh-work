"""I14-05: tools/server/dwh_v1_check.py with a fake runner (no systemctl, no psql) and temporary copies."""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "server"))
import dwh_v1_check as v1  # noqa: E402

PACKAGES = json.loads((ROOT / "tools" / "server" / "sql_packages.json").read_text(encoding="utf-8"))
NOW = datetime.datetime(2026, 11, 26, 12, 0)


def done(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class Fake:
    """Answers systemctl, the two tools and psql by the text of the request."""

    def __init__(self, **over):
        self.units = {u: "active" for u in v1.REQUIRED_UNITS + v1.OPTIONAL_UNITS}
        self.units.update(over.pop("units", {}))
        self.tls = over.pop("tls", "TLS_RESULT: PASS")
        self.secret = over.pop("secret", "SECRET_CHECK_RESULT: PASS (0 FAIL, 0 WARN, 9 checks)")
        self.missing = over.pop("missing", set())
        self.requests = over.pop("requests", [])
        self.runs = over.pop("runs", [])
        self.late = over.pop("late", [])
        self.check_pass = over.pop("check_pass", {})
        self.errors = over.pop("errors", [{"code": "SOURCE_CONNECT_FAILED", "n": 2}, {"code": "HTTP_429", "n": 1},
                                          {"code": "KEY_NULL", "n": 1}])
        self.calls: list[list[str]] = []

    def __call__(self, args, stdin=None):
        self.calls.append(list(args))
        if args[0] == "systemctl":
            return done(self.units[args[-1]] + "\n")
        if args[0] == sys.executable:
            line = self.tls if "dwh_tls.py" in args[1] else self.secret
            return done("some output\n" + line + "\n")
        if stdin is None:  # a check file under SET ROLE dwh_app
            assert "SET ROLE dwh_app" in args and "SET default_transaction_read_only = on" in args
            path = args[args.index("-f") + 1]
            number = Path(path).name[:3]
            n = self.check_pass.get(number, PACKAGES[number]["check_expected"])
            rows = [f"{i}|check {i}|PASS" for i in range(1, n + 1)]
            rows += [f"{i}|check {i}|FAIL" for i in range(n + 1, PACKAGES[number]["check_expected"] + 1)]
            return done("on|dwh|dwh_app|17\n" + "\n".join(rows) + "\n")
        assert stdin.startswith("SET default_transaction_read_only = on;")
        sql = stdin
        if "current_setting('transaction_read_only')" in sql:
            return done('"on"\n')
        if "json_build_object('016'" in sql:
            return done(json.dumps({n: n not in self.missing for n in PACKAGES}) + "\n")
        if "'tables'" in sql:
            return done(json.dumps({"now": "x", "tables": {t: True for t in (
                "etl.object_run_request", "etl.dm_mart_run_request", "etl.object_run", "etl.object_ods_run",
                "etl.dm_mart_run", "etl.object_schedule", "etl.error_log", "audit.security_event")}}))
        if "'object request'" in sql:
            return done(json.dumps(self.requests))
        if "'mart request'" in sql:
            return done("[]")
        if "'STG run'" in sql:
            return done(json.dumps(self.runs))
        if "'ODS run'" in sql or "'mart run'" in sql:
            return done("[]")
        if "is_enabled" in sql:
            return done(json.dumps({"enabled": 2, "late": self.late}))
        if "LATERAL" in sql:
            return done('[{"state":"NEVER","n":1},\n {"state":"SUCCESS","n":5}]')
        if "etl.error_log" in sql:
            return done(json.dumps(self.errors))
        if "audit.security_event" in sql:
            return done('{"last_24h": 40}')
        raise AssertionError("unexpected SQL: " + sql[:200])


class V1CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.backups = base / "backup"
        self.backups.mkdir()
        release = base / "releases" / "2026.11.80"
        release.mkdir(parents=True)
        self.current = base / "current"
        os.symlink(release, self.current)
        self.dump(hours=3)

    def tearDown(self):
        self.tmp.cleanup()

    def dump(self, hours: float, name: str = "dwh_20261126_090000.dump", digest: str | None = None) -> Path:
        path = self.backups / name
        path.write_bytes(b"copy")
        stamp = (NOW - datetime.timedelta(hours=hours)).timestamp()
        os.utime(path, (stamp, stamp))
        (self.backups / (name + ".sha256")).write_text(
            (digest or hashlib.sha256(b"copy").hexdigest()) + "  " + name + "\n", encoding="utf-8")
        return path

    def run_check(self, fake: Fake) -> tuple[int, str]:
        args = v1.main.__globals__["argparse"].Namespace(
            database="dwh", name="dwh.vtbaze.local", psql=["psql"], skip_system=False, backups=self.backups,
            current=self.current, packages=ROOT / "tools" / "server" / "sql_packages.json", root=ROOT, report=None)
        out = io.StringIO()
        with redirect_stdout(out):
            code = v1.run(args, runner=fake, now=NOW)
        return code, out.getvalue()

    def test_all_pass_and_information(self):
        code, out = self.run_check(Fake())
        self.assertEqual(code, 0, out)
        self.assertIn("V1_CHECK_RESULT: PASS", out)
        self.assertIn("release: 2026.11.80", out)
        self.assertIn("check 036 as dwh_app (read-only): 7/7 PASS", out)
        self.assertIn("check 035 as dwh_app (read-only): 5/5 PASS", out)
        self.assertIn(f"packages 016..036: {len(PACKAGES)} applied", out)
        self.assertIn("objects (active) by the last request: NEVER 1, SUCCESS 5", out)
        self.assertIn("errors of the last 7 days by kind: CONNECTION 3, KEY 1", out)
        self.assertIn("audit events of the last 24 h: 40", out)

    def test_optional_service_down_is_only_a_warning(self):
        code, out = self.run_check(Fake(units={"dwh-llm.service": "inactive"}))
        self.assertEqual(code, 0, out)
        self.assertIn("WARN  service dwh-llm.service: inactive", out)

    def test_required_service_down_fails(self):
        code, out = self.run_check(Fake(units={"dwh-dispatcher.service": "inactive"}))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  service dwh-dispatcher.service: inactive", out)

    def test_old_or_damaged_copy_fails(self):
        for p in self.backups.iterdir():
            p.unlink()
        self.dump(hours=30)
        code, out = self.run_check(Fake())
        self.assertEqual(code, 1)
        self.assertIn("30.0 h old", out)
        for p in self.backups.iterdir():
            p.unlink()
        self.dump(hours=1, digest="0" * 64)
        code, out = self.run_check(Fake())
        self.assertEqual(code, 1)
        self.assertIn("FAIL  copies: SHA-256", out)

    def test_tool_results(self):
        code, out = self.run_check(Fake(secret="SECRET_CHECK_RESULT: FAIL (1 FAIL, 0 WARN, 9 checks)"))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  SECRET_CHECK_RESULT: FAIL", out)

    def test_missing_package_and_failed_check(self):
        code, out = self.run_check(Fake(missing={"031"}, check_pass={"036": 6}))
        self.assertEqual(code, 1)
        self.assertIn("missing: 031", out)
        self.assertIn("check 036 as dwh_app (read-only): 6/7 PASS (expected 7); FAIL check 7", out)

    def test_stuck_and_fresh_requests(self):
        fake = Fake(requests=[{"kind": "object request", "id": 7, "status": "RUNNING", "hours": 3.5},
                              {"kind": "object request", "id": 9, "status": "QUEUED", "hours": 0.1}],
                    runs=[{"kind": "STG run", "id": 40, "hours": 0.2}])
        code, out = self.run_check(fake)
        self.assertEqual(code, 1)
        self.assertIn("active longer than 2 h: 1; object request #7 RUNNING 3.5 h", out)
        self.assertIn("WARN  in progress now: 2; object request #9 QUEUED; STG run #40 RUNNING", out)

    def test_late_schedule_fails(self):
        code, out = self.run_check(Fake(late=[{"object": 24, "next": "x", "minutes": 95}]))
        self.assertEqual(code, 1)
        self.assertIn("late by more than 30 min: 1; object 24 95 min", out)

    def test_every_database_request_is_read_only(self):
        fake = Fake()
        self.run_check(fake)
        psql = [c for c in fake.calls if c[0] == "psql"]
        self.assertTrue(psql)
        for call in psql:
            self.assertTrue("-f" in call)


if __name__ == "__main__":
    unittest.main()
