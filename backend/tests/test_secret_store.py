"""Isolated tests using synthetic values only (never production secrets)."""

import base64
import os
import unittest

from dwh_api.secret_store import (
    SecretStore,
    SecretStoreError,
    decode_master_key,
    generate_secret_ref,
)


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.key = os.urandom(32)
        self.store = SecretStore(self.key)
        self.ref = generate_secret_ref()
        self.sample = "SYNTHETIC-ONLY-секрет-🗝"

    def test_round_trip_unicode(self):
        sealed = self.store.encrypt(secret_ref=self.ref, value=self.sample)
        self.assertIsInstance(sealed, bytes)
        self.assertNotIn(self.sample.encode("utf-8"), sealed)
        self.assertEqual(self.store.decrypt(secret_ref=self.ref, envelope=sealed), self.sample)

    def test_nonces_are_random(self):
        one = self.store.encrypt(secret_ref=self.ref, value=self.sample)
        two = self.store.encrypt(secret_ref=self.ref, value=self.sample)
        self.assertNotEqual(one, two)

    def test_reference_binding(self):
        sealed = self.store.encrypt(secret_ref=self.ref, value=self.sample)
        with self.assertRaises(SecretStoreError):
            self.store.decrypt(secret_ref=generate_secret_ref(), envelope=sealed)

    def test_tamper_detected(self):
        sealed = bytearray(self.store.encrypt(secret_ref=self.ref, value=self.sample))
        sealed[-1] ^= 1
        with self.assertRaises(SecretStoreError):
            self.store.decrypt(secret_ref=self.ref, envelope=bytes(sealed))

    def test_wrong_master_key(self):
        sealed = self.store.encrypt(secret_ref=self.ref, value=self.sample)
        with self.assertRaises(SecretStoreError):
            SecretStore(os.urandom(32)).decrypt(secret_ref=self.ref, envelope=sealed)

    def test_invalid_envelopes(self):
        for value in (b"", b"bad", b"DWH-SS\x02" + os.urandom(50)):
            with self.subTest(value=value[:2]):
                with self.assertRaises(SecretStoreError):
                    self.store.decrypt(secret_ref=self.ref, envelope=value)

    def test_invalid_inputs(self):
        with self.assertRaises(SecretStoreError):
            SecretStore(b"bad")
        with self.assertRaises(SecretStoreError):
            self.store.encrypt(secret_ref=self.ref, value="")
        with self.assertRaises(SecretStoreError):
            self.store.encrypt(secret_ref="../other", value=self.sample)

    def test_key_decoder(self):
        self.assertEqual(decode_master_key(base64.b64encode(self.key).decode("ascii")), self.key)
        for invalid in ("bad!", "", base64.b64encode(b"short").decode("ascii")):
            with self.subTest(invalid=bool(invalid)):
                with self.assertRaises(SecretStoreError):
                    decode_master_key(invalid)

    def test_failure_message_hides_plaintext(self):
        sealed = self.store.encrypt(secret_ref=self.ref, value=self.sample)
        try:
            self.store.decrypt(secret_ref=generate_secret_ref(), envelope=sealed)
        except SecretStoreError as exc:
            self.assertNotIn(self.sample, str(exc))
            self.assertNotIn(base64.b64encode(self.key).decode("ascii"), str(exc))
        else:
            self.fail("Expected SecretStoreError")

    def test_repr_hides_key(self):
        self.assertNotIn(base64.b64encode(self.key).decode("ascii"), repr(self.store))


if __name__ == "__main__":
    unittest.main()
