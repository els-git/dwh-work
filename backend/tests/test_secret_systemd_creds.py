"""I16 (decision C9): SecretStore master keys on the Linux server as systemd credentials.

Provider tests need no systemd. The SystemdCredsTool tests run the real
/usr/bin/systemd-creds when it is present and the test runs as root (host key);
otherwise they are skipped.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dwh_api import secret_key_tools as kt
from dwh_api import secret_keyring as kr
from dwh_api.secret_store import SecretStore, generate_secret_ref

PASS = "correct horse battery"


def env(creds_dir, key_id="K2026_04"):
    return {kr.KEY_PROVIDER_ENV: kr.PROVIDER_SYSTEMD_CREDS, kr.ACTIVE_KEY_ID_ENV: key_id,
            kr.CREDENTIALS_DIRECTORY_ENV: str(creds_dir)}


class Temp(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("DWH_I02_TEST_TEMP") or None
        self.root = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.root.cleanup)
        self.dir = Path(self.root.name)


class ProviderTests(Temp):
    def setUp(self):
        super().setUp()
        self.creds = self.dir / "creds"
        self.creds.mkdir()
        self.raw = os.urandom(32)
        (self.creds / "dwh-key.K2026_04").write_bytes(self.raw)

    def test_loads_the_credential_handed_over_by_systemd(self):
        crypto, key_id = kr.load_active_key(environ=env(self.creds))
        self.assertEqual(key_id, "K2026_04")
        ref = generate_secret_ref()
        envelope = SecretStore(self.raw).encrypt(secret_ref=ref, value="synthetic")
        self.assertEqual(crypto.decrypt(secret_ref=ref, envelope=envelope), "synthetic")

    def test_default_provider_is_still_dpapi(self):
        self.assertEqual(kr.key_provider({}), kr.PROVIDER_DPAPI)
        with self.assertRaises(kr.KeyringUnavailable):  # no DPAPI keyring folder configured
            kr.load_active_key(environ={kr.ACTIVE_KEY_ID_ENV: "K2026_04",
                                        kr.CREDENTIALS_DIRECTORY_ENV: str(self.creds)})

    def test_fail_closed(self):
        (self.creds / "dwh-key.K2026_05").write_bytes(b"short")
        os.symlink(self.creds / "dwh-key.K2026_04", self.creds / "dwh-key.K2026_06")
        cases = {
            "no credentials dir": {kr.KEY_PROVIDER_ENV: kr.PROVIDER_SYSTEMD_CREDS, kr.ACTIVE_KEY_ID_ENV: "K2026_04"},
            "relative dir": dict(env(self.creds), **{kr.CREDENTIALS_DIRECTORY_ENV: "creds"}),
            "unknown provider": dict(env(self.creds), **{kr.KEY_PROVIDER_ENV: "plain"}),
            "absent key": env(self.creds, "K2026_09"),
            "wrong size": env(self.creds, "K2026_05"),
            "symlink": env(self.creds, "K2026_06"),
            "traversal": env(self.creds, "../dwh-key.K2026_04"),
        }
        for name, environ in cases.items():
            with self.subTest(name), self.assertRaises(kr.KeyringUnavailable) as ctx:
                kr.load_active_key(environ=environ)
            self.assertNotIn(self.raw.hex(), str(ctx.exception))


class SystemdCredsCallTests(unittest.TestCase):
    def test_command_and_sanitized_refusal(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 1, b"", b"Failed to decrypt /etc/dwh/keyring/K.cred")
        with mock.patch.object(os, "name", "posix"), mock.patch("subprocess.run", fake_run):
            with self.assertRaises(kt.KeyToolError) as ctx:
                kt.systemd_unprotect(b"blob", "K2026_04")
        self.assertEqual(seen["cmd"], [kt.SYSTEMD_CREDS, "decrypt", "--name=dwh-key.K2026_04", "-", "-"])
        self.assertEqual(str(ctx.exception), "systemd-creds refused the operation")
        with self.assertRaises(kt.KeyToolError):
            kt.systemd_protect(os.urandom(32), "../x")

    def test_activation_texts(self):
        env_text, dropin = kt.systemd_activation_texts(Path("/etc/dwh/keyring"), "K2026_04", ["K2026_03", "K2026_04"])
        self.assertIn("DWH_SECRET_KEY_PROVIDER=systemd-creds\n", env_text)
        self.assertIn("DWH_SECRET_ACTIVE_KEY_ID=K2026_04\n", env_text)
        self.assertIn("EnvironmentFile=/etc/dwh/keyring.env\n", dropin)
        self.assertIn("LoadCredentialEncrypted=dwh-key.K2026_03:/etc/dwh/keyring/K2026_03.cred\n", dropin)
        self.assertIn("LoadCredentialEncrypted=dwh-key.K2026_04:/etc/dwh/keyring/K2026_04.cred\n", dropin)
        with self.assertRaises(kt.KeyToolError):
            kt.systemd_activation_texts(Path("/etc/dwh/keyring"), "K2026_05", ["K2026_04"])


def _systemd_creds_usable() -> bool:
    if os.name == "nt" or not os.path.exists(kt.SYSTEMD_CREDS) or os.geteuid() != 0:
        return False
    try:
        return kt.systemd_unprotect(kt.systemd_protect(b"x" * 32, "PROBE"), "PROBE") == b"x" * 32
    except kt.KeyToolError:
        return False


@unittest.skipUnless(_systemd_creds_usable(), "needs /usr/bin/systemd-creds and root (host key)")
class SystemdCredsToolTests(Temp):
    def setUp(self):
        super().setUp()
        self.keyring = self.dir / "keyring"
        self.backup = self.dir / "backup"
        self.keyring.mkdir(mode=0o700)
        self.backup.mkdir()

    def test_new_verify_and_no_key_in_file(self):
        print_, backup = kt.create_key(self.keyring, self.backup, "K2026_05", PASS, protector=kt.SYSTEMD_PROTECTOR)
        cred = self.keyring / "K2026_05.cred"
        self.assertTrue(cred.is_file() and backup.is_file())
        self.assertEqual(kt.verify_key(self.keyring, "K2026_05", protector=kt.SYSTEMD_PROTECTOR), print_)
        _, raw = kt.open_backup(backup.read_bytes(), PASS)
        self.assertNotIn(raw, cred.read_bytes())

    def test_key_from_the_pc_backup_moves_to_linux_and_reads_old_envelopes(self):
        # A key made on the PC (DPAPI there; any protector here) moves through its .dwhkey backup.
        pc_keyring = self.dir / "pc_keyring"
        pc_keyring.mkdir()
        fake = kt.Protector(".dpapi", lambda raw, k: raw[::-1], lambda blob, k: blob[::-1])
        print_, backup = kt.create_key(pc_keyring, self.backup, "K2026_04", PASS, protector=fake)
        _, raw = kt.open_backup(backup.read_bytes(), PASS)
        ref = generate_secret_ref()
        envelope = SecretStore(raw).encrypt(secret_ref=ref, value="synthetic credential")
        key_id, restored_print, written = kt.restore_key(self.keyring, backup, PASS, protector=kt.SYSTEMD_PROTECTOR)
        self.assertEqual((key_id, restored_print, written), ("K2026_04", print_, True))
        self.assertFalse(kt.restore_key(self.keyring, backup, PASS, protector=kt.SYSTEMD_PROTECTOR)[2])
        # What systemd does for the service with LoadCredentialEncrypted=dwh-key.K2026_04:<file>:
        creds = self.dir / "run_credentials"
        creds.mkdir(mode=0o700)
        decrypted = kt.systemd_unprotect((self.keyring / "K2026_04.cred").read_bytes(), "K2026_04")
        (creds / "dwh-key.K2026_04").write_bytes(decrypted)
        crypto, active = kr.load_active_key(environ=env(creds))
        self.assertEqual(crypto.decrypt(secret_ref=ref, envelope=envelope), "synthetic credential")

    def test_credential_name_is_bound_to_the_key_id(self):
        kt.create_key(self.keyring, self.backup, "K2026_05", PASS, protector=kt.SYSTEMD_PROTECTOR)
        shutil.copy(self.keyring / "K2026_05.cred", self.keyring / "K2026_06.cred")
        with self.assertRaises(kt.KeyToolError):
            kt.verify_key(self.keyring, "K2026_06", protector=kt.SYSTEMD_PROTECTOR)

    def test_rotation_and_activation_files(self):
        kt.create_key(self.keyring, self.backup, "K2026_05", PASS, protector=kt.SYSTEMD_PROTECTOR)
        kt.create_key(self.keyring, self.backup, "K2026_06", PASS, protector=kt.SYSTEMD_PROTECTOR)
        self.assertEqual(kt.keyring_key_ids(self.keyring), ["K2026_05", "K2026_06"])
        env_text, dropin = kt.systemd_activation_texts(self.keyring, "K2026_06", kt.keyring_key_ids(self.keyring))
        target = self.dir / "etc" / "keyring.env"
        kt.write_atomic(target, env_text, 0o640)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(dropin.count("LoadCredentialEncrypted="), 2)


if __name__ == "__main__":
    unittest.main()
