"""Synthetic-only tests for an external master-key provider."""

import base64
import os
import unittest
from unittest import mock

from dwh_api.secret_store import SecretStore, SecretStoreError, generate_secret_ref
from dwh_api.secret_key_provider import (
    MASTER_KEY_ENV,
    SecretKeyProviderError,
    load_crypto_from_environment,
)


class MasterKeyProviderTests(unittest.TestCase):
    def setUp(self):
        self.key = os.urandom(32)
        self.encoded = base64.b64encode(self.key).decode("ascii")
        self.fake_env = {MASTER_KEY_ENV: self.encoded}
        self.ref = generate_secret_ref()
        self.value = "synthetic-master-key-test-only"

    def test_valid_injected_key_round_trip(self):
        crypto = load_crypto_from_environment(environ=self.fake_env)
        envelope = crypto.encrypt(secret_ref=self.ref, value=self.value)
        self.assertEqual(crypto.decrypt(secret_ref=self.ref, envelope=envelope), self.value)

    def test_new_instance_can_decrypt_same_key(self):
        a = load_crypto_from_environment(environ=self.fake_env)
        b = load_crypto_from_environment(environ=self.fake_env)
        ciphertext = a.encrypt(secret_ref=self.ref, value=self.value)
        self.assertEqual(b.decrypt(secret_ref=self.ref, envelope=ciphertext), self.value)

    def test_missing_key_fails_closed(self):
        with self.assertRaises(SecretKeyProviderError):
            load_crypto_from_environment(environ={})

    def test_empty_key_fails_closed(self):
        with self.assertRaises(SecretKeyProviderError):
            load_crypto_from_environment(environ={MASTER_KEY_ENV: ""})

    def test_bad_base64_fails_closed(self):
        with self.assertRaises(SecretKeyProviderError):
            load_crypto_from_environment(environ={MASTER_KEY_ENV: "not/base64!"})

    def test_wrong_length_fails_closed(self):
        encoded = base64.b64encode(b"short").decode("ascii")
        with self.assertRaises(SecretKeyProviderError):
            load_crypto_from_environment(environ={MASTER_KEY_ENV: encoded})

    def test_wrong_type_fails_closed(self):
        with self.assertRaises(SecretKeyProviderError):
            load_crypto_from_environment(environ={MASTER_KEY_ENV: 123})

    def test_invalid_environment_container_fails_closed(self):
        with self.assertRaises(SecretKeyProviderError):
            load_crypto_from_environment(environ="not-a-mapping")

    def test_wrong_key_cannot_decrypt(self):
        original = load_crypto_from_environment(environ=self.fake_env)
        wrong_encoded = base64.b64encode(os.urandom(32)).decode("ascii")
        wrong = load_crypto_from_environment(environ={MASTER_KEY_ENV: wrong_encoded})
        envelope = original.encrypt(secret_ref=self.ref, value=self.value)
        with self.assertRaises(SecretStoreError):
            wrong.decrypt(secret_ref=self.ref, envelope=envelope)

    def test_provider_errors_hide_key(self):
        bad = self.encoded + "!"
        try:
            load_crypto_from_environment(environ={MASTER_KEY_ENV: bad})
        except SecretKeyProviderError as exc:
            self.assertNotIn(bad, str(exc))
            self.assertNotIn(self.encoded, repr(exc))
        else:
            self.fail("Expected a provider error")

    def test_success_representation_hides_key(self):
        crypto = load_crypto_from_environment(environ=self.fake_env)
        self.assertNotIn(self.encoded, repr(crypto))
        self.assertNotIn(self.value, repr(crypto))

    def test_does_not_change_injected_environment(self):
        before = dict(self.fake_env)
        load_crypto_from_environment(environ=self.fake_env)
        self.assertEqual(before, self.fake_env)

    def test_default_environment_is_mocked(self):
        # Never examine the real process environment in this test.
        with mock.patch("dwh_api.secret_key_provider.os.environ", self.fake_env):
            crypto = load_crypto_from_environment()
            envelope = crypto.encrypt(secret_ref=self.ref, value=self.value)
            self.assertEqual(crypto.decrypt(secret_ref=self.ref, envelope=envelope), self.value)


if __name__ == "__main__":
    unittest.main()
