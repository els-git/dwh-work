"""Synthetic encrypted-file adapter tests; no real credentials."""

import os
from pathlib import Path
import tempfile
import unittest

from dwh_api.secret_store import SecretStore, generate_secret_ref
from dwh_api.secret_file_store import EncryptedFileSecretStore, SecretFileStoreError


class EncryptedFileStoreTests(unittest.TestCase):
    def setUp(self):
        base = os.environ["DWH_I02_TEST_TEMP"]
        self.context = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.context.cleanup)
        self.directory = Path(self.context.name)
        self.crypto = SecretStore(os.urandom(32))
        self.storage = EncryptedFileSecretStore(crypto=self.crypto, directory=self.directory)
        self.ref = generate_secret_ref()
        self.value = "synthetic-only-secret-not-a-real-password"

    def test_persist_and_read(self):
        self.storage.create(secret_ref=self.ref, value=self.value)
        second = EncryptedFileSecretStore(crypto=self.crypto, directory=self.directory)
        self.assertEqual(second.read(secret_ref=self.ref), self.value)

    def test_only_ciphertext_on_disk(self):
        self.storage.create(secret_ref=self.ref, value=self.value)
        file_data = (self.directory / (self.ref + ".bin")).read_bytes()
        self.assertNotIn(self.value.encode("utf-8"), file_data)
        self.assertTrue(file_data.startswith(b"DWH-SS\x01"))

    def test_duplicate_does_not_overwrite(self):
        self.storage.create(secret_ref=self.ref, value=self.value)
        with self.assertRaises(SecretFileStoreError):
            self.storage.create(secret_ref=self.ref, value="different-synthetic")
        self.assertEqual(self.storage.read(secret_ref=self.ref), self.value)

    def test_invalid_reference_blocks_path_traversal(self):
        for bad in ("../outside", "/tmp/outside", "", "ABC", self.ref.upper()):
            with self.subTest(bad=bad):
                with self.assertRaises(SecretFileStoreError):
                    self.storage.create(secret_ref=bad, value=self.value)
                with self.assertRaises(SecretFileStoreError):
                    self.storage.read(secret_ref=bad)

    def test_missing_record_denied(self):
        with self.assertRaises(SecretFileStoreError):
            self.storage.read(secret_ref=self.ref)

    def test_wrong_master_key_denied(self):
        self.storage.create(secret_ref=self.ref, value=self.value)
        wrong = EncryptedFileSecretStore(
            crypto=SecretStore(os.urandom(32)), directory=self.directory
        )
        with self.assertRaises(SecretFileStoreError):
            wrong.read(secret_ref=self.ref)

    def test_tampered_ciphertext_denied(self):
        self.storage.create(secret_ref=self.ref, value=self.value)
        target = self.directory / (self.ref + ".bin")
        corrupted = bytearray(target.read_bytes())
        corrupted[-1] ^= 1
        target.write_bytes(corrupted)
        with self.assertRaises(SecretFileStoreError):
            self.storage.read(secret_ref=self.ref)

    def test_no_temp_files_after_creation(self):
        self.storage.create(secret_ref=self.ref, value=self.value)
        self.assertEqual(len(list(self.directory.iterdir())), 1)
        self.assertFalse(list(self.directory.glob(".pending-*")))

    def test_no_default_folder_created(self):
        missing = self.directory / "nonexistent"
        with self.assertRaises(SecretFileStoreError):
            EncryptedFileSecretStore(crypto=self.crypto, directory=missing)
        self.assertFalse(missing.exists())

    def test_no_secrets_in_representation_or_errors(self):
        text = repr(self.storage)
        self.assertNotIn(str(self.directory), text)
        self.assertNotIn(self.value, text)
        try:
            self.storage.read(secret_ref=self.ref)
        except SecretFileStoreError as exc:
            self.assertNotIn(self.value, str(exc))
            self.assertNotIn(str(self.directory), str(exc))
        else:
            self.fail("Expected missing record error")


if __name__ == "__main__":
    unittest.main()
