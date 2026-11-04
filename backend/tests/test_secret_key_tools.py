"""SecretStore keyring and key administration (I02-05) with DPAPI replaced."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dwh_api import secret_key_tools as kt
from dwh_api import secret_keyring as kr
from dwh_api.secret_store import SecretStore

PASS = "correct horse battery"


def fake_protect(raw):
    return b"DPAPI" + bytes(b ^ 0x5A for b in raw)


def fake_unprotect(blob):
    if not blob.startswith(b"DPAPI"):
        raise kt.KeyToolError("Windows DPAPI refused the operation")
    return bytes(b ^ 0x5A for b in blob[5:])


class Folders(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("DWH_I02_TEST_TEMP") or None
        self.root = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.root.cleanup)
        self.keyring = Path(self.root.name) / "keyring"
        self.backup = Path(self.root.name) / "backup"
        self.keyring.mkdir()
        self.backup.mkdir()

    def create(self, key_id="K2026_03"):
        return kt.create_key(self.keyring, self.backup, key_id, PASS, fake_protect, fake_unprotect)


class BackupFormatTests(unittest.TestCase):
    def test_roundtrip_and_no_key_in_file(self):
        raw = os.urandom(32)
        data = kt.make_backup(raw, "K1", PASS)
        self.assertNotIn(raw, data)
        self.assertNotIn(PASS.encode(), data)
        self.assertEqual(kt.open_backup(data, PASS), ("K1", raw))
        self.assertEqual(kt.backup_header(data), ("K1", kt.fingerprint(raw)))

    def test_wrong_passphrase_and_tampering(self):
        raw = os.urandom(32)
        data = kt.make_backup(raw, "K1", PASS)
        with self.assertRaises(kt.KeyToolError):
            kt.open_backup(data, PASS + "x")
        for old, new in ((b'"K1"', b'"K2"'), (b'"fingerprint": "', b'"fingerprint": "0')):
            with self.subTest(old), self.assertRaises(kt.KeyToolError):
                kt.open_backup(data.replace(old, new, 1), PASS)
        with self.assertRaises(kt.KeyToolError):
            kt.open_backup(b"not json", PASS)

    def test_short_passphrase_rejected(self):
        with self.assertRaises(kt.KeyToolError):
            kt.make_backup(os.urandom(32), "K1", "short")

    def test_fingerprint_is_stable_and_not_the_key(self):
        raw = os.urandom(32)
        self.assertEqual(kt.fingerprint(raw), kt.fingerprint(raw))
        self.assertNotEqual(kt.fingerprint(raw), kt.fingerprint(os.urandom(32)))
        self.assertNotIn(kt.fingerprint(raw).lower(), raw.hex())


class KeyLifecycleTests(Folders):
    def test_create_writes_key_and_backup(self):
        print_, backup = self.create()
        self.assertTrue((self.keyring / "K2026_03.dpapi").is_file())
        self.assertEqual(backup, self.backup / "K2026_03.dwhkey")
        self.assertEqual(kt.verify_key(self.keyring, "K2026_03", fake_unprotect), print_)

    def test_create_refuses_existing_and_same_folder(self):
        self.create()
        with self.assertRaises(kt.KeyToolError):
            self.create()
        with self.assertRaises(kt.KeyToolError):
            kt.create_key(self.keyring, self.keyring, "K2", PASS, fake_protect, fake_unprotect)
        with self.assertRaises(kt.KeyToolError):
            kt.create_key(self.keyring, self.backup, "../K", PASS, fake_protect, fake_unprotect)

    def test_lost_key_is_restored_from_backup(self):
        print_, backup = self.create()
        (self.keyring / "K2026_03.dpapi").unlink()
        with self.assertRaises(kt.KeyToolError):
            kt.verify_key(self.keyring, "K2026_03", fake_unprotect)
        self.assertEqual(kt.restore_key(self.keyring, backup, PASS, fake_protect, fake_unprotect),
                         ("K2026_03", print_, True))
        self.assertEqual(kt.verify_key(self.keyring, "K2026_03", fake_unprotect), print_)
        self.assertEqual(kt.restore_key(self.keyring, backup, PASS, fake_protect, fake_unprotect)[2], False)

    def test_restore_refuses_a_different_key_with_same_id(self):
        _, backup = self.create()
        (self.keyring / "K2026_03.dpapi").write_bytes(fake_protect(os.urandom(32)))
        with self.assertRaises(kt.KeyToolError):
            kt.restore_key(self.keyring, backup, PASS, fake_protect, fake_unprotect)

    def test_restored_key_decrypts_old_envelopes(self):
        _, backup = self.create()
        raw = fake_unprotect((self.keyring / "K2026_03.dpapi").read_bytes())
        envelope = SecretStore(raw).encrypt(secret_ref="22222222-2222-4222-8222-222222222222", value="synthetic")
        (self.keyring / "K2026_03.dpapi").unlink()
        kt.restore_key(self.keyring, backup, PASS, fake_protect, fake_unprotect)
        restored = fake_unprotect((self.keyring / "K2026_03.dpapi").read_bytes())
        self.assertEqual(SecretStore(restored).decrypt(
            secret_ref="22222222-2222-4222-8222-222222222222", envelope=envelope), "synthetic")


class KeyringTests(Folders):
    def env(self, active="K2026_03"):
        return {kr.KEYRING_DIR_ENV: str(self.keyring), kr.ACTIVE_KEY_ID_ENV: active}

    def loader(self, path):
        return SecretStore(fake_unprotect(path.read_bytes()))

    def test_rotation_keeps_old_keys_readable(self):
        self.create("K2026_03")
        self.create("K2026_04")
        old = kr.load_key("K2026_03", environ=self.env(), loader=self.loader)
        envelope = old.encrypt(secret_ref="22222222-2222-4222-8222-222222222222", value="synthetic")
        crypto, key_id = kr.load_active_key(environ=self.env("K2026_04"), loader=self.loader)
        self.assertEqual(key_id, "K2026_04")
        again = kr.load_key("K2026_03", environ=self.env("K2026_04"), loader=self.loader)
        self.assertEqual(again.decrypt(secret_ref="22222222-2222-4222-8222-222222222222", envelope=envelope),
                         "synthetic")

    def test_fail_closed(self):
        self.create()
        for name, environ in {
            "no dir": {kr.ACTIVE_KEY_ID_ENV: "K2026_03"},
            "relative dir": {kr.KEYRING_DIR_ENV: "keyring", kr.ACTIVE_KEY_ID_ENV: "K2026_03"},
            "no active": {kr.KEYRING_DIR_ENV: str(self.keyring)},
            "traversal": self.env("..\\K2026_03"),
            "absent": self.env("K2026_09"),
        }.items():
            with self.subTest(name), self.assertRaises(kr.KeyringUnavailable):
                kr.load_active_key(environ=environ, loader=self.loader)
        (self.keyring / "K2026_03.dpapi").write_bytes(b"damaged")
        with self.assertRaises(kr.KeyringUnavailable):
            kr.load_active_key(environ=self.env(), loader=self.loader)

    def test_errors_do_not_leak(self):
        try:
            kr.load_active_key(environ=self.env("K2026_09"), loader=self.loader)
        except kr.KeyringUnavailable as exc:
            self.assertEqual(str(exc), "Master key unavailable")


class EnvUpdateTests(unittest.TestCase):
    def test_sets_keyring_and_removes_plain_key(self):
        text = ("DWH_DB_NAME=dwh\r\n# I03 source credentials (synthetic master key, decision V2)\r\n"
                "DWH_SECRET_STORE_DIR=C:\\s\r\nDWH_CONNECTOR_MASTER_KEY_ID=K2026_02\r\n"
                "DWH_CONNECTOR_MASTER_KEY_B64=AAAA\r\n")
        new, names, removed = kt.update_env_text(text, {kr.KEYRING_DIR_ENV: "C:\\k", kr.ACTIVE_KEY_ID_ENV: "K2026_03"})
        self.assertNotIn("MASTER_KEY", new)
        self.assertIn("DWH_DB_NAME=dwh\r\n", new)
        self.assertIn("DWH_SECRET_STORE_DIR=C:\\s\r\n", new)
        self.assertIn("DWH_SECRET_ACTIVE_KEY_ID=K2026_03\r\n", new)
        self.assertEqual(sorted(removed), ["DWH_CONNECTOR_MASTER_KEY_B64", "DWH_CONNECTOR_MASTER_KEY_ID"])
        again, _, removed2 = kt.update_env_text(new, {kr.KEYRING_DIR_ENV: "C:\\k", kr.ACTIVE_KEY_ID_ENV: "K2026_04"})
        self.assertIn("DWH_SECRET_ACTIVE_KEY_ID=K2026_04\r\n", again)
        self.assertNotIn("K2026_03", again)
        self.assertEqual(removed2, [])

    def test_masked_copy(self):
        masked = kt.mask_env_text("A=1\r\nDWH_CONNECTOR_MASTER_KEY_B64=SECRET\r\nCRM_DB_PASSWORD=pw1\r\n"
                                  "DWH_APP_PASSWORD=pw2\r\nAPI_TOKEN=tk\r\n# PASSWORD=comment\r\nDWH_DB_NAME=dwh\r\n")
        for value in ("SECRET", "pw1", "pw2", "tk"):
            self.assertNotIn(value, masked)
        self.assertIn("A=1\r\n", masked)
        self.assertIn("DWH_DB_NAME=dwh\r\n", masked)
        self.assertIn("CRM_DB_PASSWORD=<removed>\r\n", masked)
        self.assertIn("# PASSWORD=comment\r\n", masked)


class DpapiOnlyOnWindows(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "checks the non-Windows refusal")
    def test_refuses_outside_windows(self):
        with self.assertRaises(kt.KeyToolError):
            kt.dpapi_protect(os.urandom(32))


if __name__ == "__main__":
    unittest.main()
