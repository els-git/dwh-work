"""I02-03: tools/server/dwh_secret_check.py on temporary folders and a prepared journal (no database)."""
from __future__ import annotations

import datetime
import hashlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "server"))
import dwh_secret_check as sc  # noqa: E402

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.timezone.utc)
REF = "11111111-1111-4111-8111-111111111111"


def journal(**over):
    data = {
        "read_only": "on",
        "bindings": [],
        "open_operations": [],
        "operations": [],
        "schema_rights": [{"role": "dwh_app", "usage": False, "tables": []}],
        "function_rights": [
            {"function": "secret_store.begin_operation(text,bigint,text,text,uuid,uuid)", "roles": []},
            {"function": "app.integration_source_secret_begin(text,bigint)", "roles": ["dwh_app"]},
            {"function": "app.integration_source_secret_connection(bigint)", "roles": []},
            {"function": "etl.source_secret_read_begin(text)", "roles": ["dwh_etl"]},
        ],
    }
    data.update(over)
    return data


def binding(state="ACTIVE", sha=None, created="2026-11-20T11:58:00+00:00", ref=REF, source="CRM_RETAIL"):
    return {"ref": ref, "source": source, "profile": "SOURCE", "state": state, "key_id": "K2026_04", "sha": sha,
            "created": created, "activated": None, "revoked": None}


@unittest.skipUnless(os.name == "posix", "server tool (Linux)")
class SecretCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.keyring, self.secrets = self.root / "keyring", self.root / "secrets"
        for d in (self.keyring, self.secrets):
            d.mkdir(mode=0o700)
            os.chmod(d, 0o700)
        self.put(self.keyring / "K2026_04.cred", b"cred")
        self.user = sc.owner(os.stat(self.root))

    def put(self, path, data):
        path.write_bytes(data)
        os.chmod(path, 0o600)

    def run_check(self, data):
        args = mock.Mock(keyring=self.keyring, secrets=self.secrets, service_user=self.user, database="dwh", psql=[])
        out = io.StringIO()
        # folder owners (root / dwh) are checked on the server; here only modes and files of the folders
        with mock.patch.object(sc, "read_journal", return_value=data), \
                mock.patch.object(sc, "check_dir", side_effect=lambda r, p, m, u, label: self._dir(r, p, m, label)), \
                redirect_stdout(out):
            code = sc.run(args, now=NOW)
        return code, out.getvalue()

    def _dir(self, report, path, mode, label):  # noqa: D401
        report.add("PASS", f"{label}: {path}")
        return True

    def active_file(self, data=b"cipher"):
        self.put(self.secrets / f"{REF}.bin", data)
        return hashlib.sha256(data).hexdigest()

    def test_clean_store_passes(self):
        sha = self.active_file()
        code, out = self.run_check(journal(bindings=[binding(sha=sha)]))
        self.assertEqual(code, 0, out)
        self.assertIn("SECRET_CHECK_RESULT: PASS", out)
        self.assertNotIn(REF, out)                       # references are shortened

    def test_active_without_file_or_wrong_digest(self):
        code, out = self.run_check(journal(bindings=[binding(sha="0" * 64)]))
        self.assertIn("has no encrypted file", out)
        self.assertEqual(code, 1)
        self.active_file()
        code, out = self.run_check(journal(bindings=[binding(sha="0" * 64)]))
        self.assertIn("FAIL  CRM_RETAIL/SOURCE: ACTIVE 11111111 file matches the journal", out)

    def test_two_active_for_one_source(self):
        sha = self.active_file()
        other = "22222222-2222-4222-8222-222222222222"
        self.put(self.secrets / f"{other}.bin", b"cipher")
        code, out = self.run_check(journal(bindings=[binding(sha=sha), binding(sha=sha, ref=other)]))
        self.assertIn("FAIL  one ACTIVE binding per source", out)

    def test_pending_fresh_warns_old_fails(self):
        code, out = self.run_check(journal(bindings=[binding("PENDING")]))
        self.assertEqual(code, 0, out)
        self.assertIn("WARN  PENDING bindings: 1, older than 10 min: 0", out)
        code, out = self.run_check(journal(bindings=[binding("PENDING", created="2026-11-20T10:00:00+00:00")]))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  PENDING bindings: 1, older than 10 min: 1", out)

    def test_operation_without_outcome(self):
        ops = [{"operation": REF, "type": "READ", "source": "CRM_RETAIL", "actor": "SERVICE",
                "at": "2026-11-20T09:00:00+00:00"}]
        code, out = self.run_check(journal(open_operations=ops))
        self.assertIn("FAIL  operations without OUTCOME older than 10 min: 1; READ CRM_RETAIL 11111111", out)

    def test_wrong_rights_fail(self):
        data = journal(schema_rights=[{"role": "dwh_etl", "usage": True, "tables": []}])
        data["function_rights"][0]["roles"] = ["dwh_app"]
        data["function_rights"][3]["roles"] = ["dwh_etl", "public"]
        code, out = self.run_check(data)
        self.assertIn("FAIL  role dwh_etl: secret_store schema usage True", out)
        self.assertIn("FAIL  EXECUTE secret_store.begin_operation", out)
        self.assertIn("FAIL  EXECUTE etl.source_secret_read_begin(text): dwh_etl, public", out)

    def test_link_and_mode_fail_orphan_warns(self):
        os.symlink("/etc/hostname", self.secrets / "33333333-3333-4333-8333-333333333333.bin")
        self.put(self.secrets / "44444444-4444-4444-8444-444444444444.bin", b"x")
        os.chmod(self.secrets / "44444444-4444-4444-8444-444444444444.bin", 0o644)
        self.put(self.secrets / "55555555-5555-4555-8555-555555555555.bin", b"x")
        code, out = self.run_check(journal())
        self.assertIn("is not a plain file", out)
        self.assertIn("FAIL  secrets: 44444444-444... 644", out)
        self.assertIn("WARN  file 55555555... has no binding", out)

    def test_missing_key_and_journal_error(self):
        sha = self.active_file()
        (self.keyring / "K2026_04.cred").unlink()
        code, out = self.run_check(journal(bindings=[binding(sha=sha)]))
        self.assertIn("FAIL  CRM_RETAIL/SOURCE: key K2026_04 is in the keyring", out)
        args = mock.Mock(keyring=self.keyring, secrets=self.secrets, service_user=self.user, database="dwh",
                         psql=["false"])
        out = io.StringIO()
        with mock.patch.object(sc, "check_dir", side_effect=lambda r, p, m, u, label: self._dir(r, p, m, label)), \
                redirect_stdout(out):
            self.assertEqual(sc.run(args, now=NOW), 1)
        self.assertIn("FAIL  journal not read", out.getvalue())


if __name__ == "__main__":
    unittest.main()
