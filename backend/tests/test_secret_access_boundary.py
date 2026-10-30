"""Synthetic-only tests of a disconnected fail-closed access boundary."""
from __future__ import annotations

import dataclasses
import uuid
import unittest

from dwh_api.secret_access_boundary import (
    SecretAccessBoundary, SecretAccessDenied, SecretAccessEvent,
    SecretAccessFailure,
)


class FakeStore:
    def __init__(self):
        self.values = {}
        self.operations = []
        self.fail = False

    def create(self, *, secret_ref, value):
        self.operations.append("CREATE")
        if self.fail:
            raise RuntimeError("synthetic-plaintext-must-not-leak")
        self.values[secret_ref] = value

    def read(self, *, secret_ref):
        self.operations.append("READ")
        if self.fail:
            raise RuntimeError("synthetic-plaintext-must-not-leak")
        return self.values[secret_ref]


class SecretAccessBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ref = str(uuid.uuid4())
        self.actor = "user_42"
        self.value = "synthetic-plaintext-must-not-leak"
        self.store = FakeStore()
        self.events = []
        self.authorizations = []

        def authorize(actor, action, ref):
            self.authorizations.append((actor, action, ref))
            return True

        self.authorize = authorize
        self.gate = SecretAccessBoundary(
            store=self.store, authorize=authorize, audit=self.events.append,
        )

    def test_create_requires_permission_then_audit(self):
        self.gate.create(actor_id=self.actor, secret_ref=self.ref, value=self.value)
        self.assertEqual(self.store.values[self.ref], self.value)
        self.assertEqual(self.authorizations[0][1], "CREATE")
        self.assertEqual(self.events[0].decision, "PERMITTED_ATTEMPT")

    def test_read_authorized(self):
        self.store.values[self.ref] = self.value
        self.assertEqual(self.gate.read(actor_id=self.actor, secret_ref=self.ref), self.value)
        self.assertEqual(self.authorizations[0][1], "READ")
        self.assertEqual(self.events[0].operation, "READ")

    def test_deny_create_without_storage(self):
        gate = SecretAccessBoundary(store=self.store, authorize=lambda *args: False,
                                    audit=self.events.append)
        with self.assertRaises(SecretAccessDenied):
            gate.create(actor_id=self.actor, secret_ref=self.ref, value=self.value)
        self.assertEqual(self.store.operations, [])
        self.assertEqual(self.events[0].decision, "DENIED")

    def test_deny_read_without_storage(self):
        gate = SecretAccessBoundary(store=self.store, authorize=lambda *args: False,
                                    audit=self.events.append)
        with self.assertRaises(SecretAccessDenied):
            gate.read(actor_id=self.actor, secret_ref=self.ref)
        self.assertEqual(self.store.operations, [])

    def test_authorizer_exception_denied_and_sanitized(self):
        def broken(*args):
            raise RuntimeError(self.value)
        gate = SecretAccessBoundary(store=self.store, authorize=broken,
                                    audit=self.events.append)
        with self.assertRaises(SecretAccessDenied) as ctx:
            gate.read(actor_id=self.actor, secret_ref=self.ref)
        self.assertNotIn(self.value, str(ctx.exception))
        self.assertEqual(self.events[0].decision, "DENIED")

    def test_truthy_non_boolean_is_not_authorized(self):
        gate = SecretAccessBoundary(store=self.store, authorize=lambda *args: 1,
                                    audit=self.events.append)
        with self.assertRaises(SecretAccessDenied):
            gate.read(actor_id=self.actor, secret_ref=self.ref)
        self.assertEqual(self.store.operations, [])

    def test_audit_failure_prevents_write(self):
        def broken(*args):
            raise RuntimeError(self.value)
        gate = SecretAccessBoundary(store=self.store, authorize=self.authorize, audit=broken)
        with self.assertRaises(SecretAccessFailure) as ctx:
            gate.create(actor_id=self.actor, secret_ref=self.ref, value=self.value)
        self.assertNotIn(self.value, str(ctx.exception))
        self.assertEqual(self.store.operations, [])

    def test_audit_failure_prevents_read(self):
        gate = SecretAccessBoundary(store=self.store, authorize=self.authorize,
                                    audit=lambda event: (_ for _ in ()).throw(ValueError(self.value)))
        with self.assertRaises(SecretAccessFailure):
            gate.read(actor_id=self.actor, secret_ref=self.ref)
        self.assertEqual(self.store.operations, [])

    def test_storage_create_failure_sanitized(self):
        self.store.fail = True
        with self.assertRaises(SecretAccessFailure) as ctx:
            self.gate.create(actor_id=self.actor, secret_ref=self.ref, value=self.value)
        self.assertNotIn(self.value, str(ctx.exception))

    def test_storage_read_failure_sanitized(self):
        self.store.fail = True
        with self.assertRaises(SecretAccessFailure) as ctx:
            self.gate.read(actor_id=self.actor, secret_ref=self.ref)
        self.assertNotIn(self.value, str(ctx.exception))

    def test_invalid_ref_prevents_auth_and_access(self):
        with self.assertRaises(SecretAccessDenied):
            self.gate.read(actor_id=self.actor, secret_ref="../bad")
        self.assertEqual(self.authorizations, [])
        self.assertEqual(self.events, [])
        self.assertEqual(self.store.operations, [])

    def test_uppercase_ref_rejected(self):
        with self.assertRaises(SecretAccessDenied):
            self.gate.read(actor_id=self.actor, secret_ref=self.ref.upper())
        self.assertEqual(self.store.operations, [])

    def test_invalid_actor_rejected(self):
        with self.assertRaises(SecretAccessDenied):
            self.gate.read(actor_id="user_42\npassword", secret_ref=self.ref)
        self.assertEqual(self.store.operations, [])

    def test_missing_callbacks_rejected(self):
        for field in ("authorize", "audit"):
            kwargs = dict(store=self.store, authorize=self.authorize, audit=self.events.append)
            kwargs[field] = None
            with self.assertRaises(SecretAccessFailure):
                SecretAccessBoundary(**kwargs)

    def test_no_plaintext_or_ref_in_audit_event(self):
        self.gate.create(actor_id=self.actor, secret_ref=self.ref, value=self.value)
        text = repr(self.events[0])
        self.assertNotIn(self.value, text)
        self.assertNotIn(self.ref, text)
        self.assertEqual(len(self.events[0].reference_fingerprint), 64)

    def test_event_is_immutable(self):
        self.store.values[self.ref] = self.value
        self.gate.read(actor_id=self.actor, secret_ref=self.ref)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.events[0].decision = "DENIED"

    def test_repr_hides_configuration(self):
        self.assertNotIn(self.ref, repr(self.gate))
        self.assertNotIn(self.value, repr(self.gate))
        self.assertNotIn(str(self.store.values), repr(self.gate))

    def test_event_is_written_before_storage(self):
        outer = self
        class InspectingStore(FakeStore):
            def read(self, *, secret_ref):
                outer.assertEqual(outer.events[0].decision, "PERMITTED_ATTEMPT")
                return outer.value
        gate = SecretAccessBoundary(store=InspectingStore(), authorize=self.authorize,
                                    audit=self.events.append)
        self.assertEqual(gate.read(actor_id=self.actor, secret_ref=self.ref), self.value)


if __name__ == "__main__":
    unittest.main()
