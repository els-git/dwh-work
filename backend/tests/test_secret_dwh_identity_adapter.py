"""Synthetic-only tests of DWH human identity bridging. No real users/keys."""
from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace

from dwh_api.secret_access_boundary import (
    SecretAccessBoundary, SecretAccessDenied,
)
from dwh_api.secret_dwh_identity_adapter import (
    DwhSecretIdentityDenied,
    create_human_secret_context,
)


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.operations = []

    def create(self, *, secret_ref, value):
        self.operations.append("CREATE")
        self.values[secret_ref] = value

    def read(self, *, secret_ref):
        self.operations.append("READ")
        return self.values[secret_ref]


class HumanIdentityAdapterTests(unittest.TestCase):
    def setUp(self):
        self.ref = str(uuid.uuid4())
        self.store = MemoryStore()
        self.events = []
        self.guard_calls = []
        self.permission = "TEST_EXISTING_PERMISSION"
        self.state = SimpleNamespace(
            authenticated=True,
            user=SimpleNamespace(user_id=42, is_system=False, must_change_password=False),
            permissions=[SimpleNamespace(permission_code=self.permission)],
        )

    def guard(self, ref):
        self.guard_calls.append(ref)
        return True

    def make(self, state=None, *, code=None, guard=None):
        return create_human_secret_context(
            self.state if state is None else state,
            create_permission_code=self.permission if code is None else code,
            resource_guard=self.guard if guard is None else guard,
        )

    def test_user_id_derived_from_dwh_auth_state(self):
        self.assertEqual(self.make().actor_id, "42")

    def test_create_allowed_with_existing_permission_and_resource(self):
        actor = self.make()
        self.assertTrue(actor.authorize(actor.actor_id, "CREATE", self.ref))
        self.assertEqual(self.guard_calls, [self.ref])

    def test_plaintext_read_always_denied_to_humans(self):
        actor = self.make()
        self.assertFalse(actor.authorize(actor.actor_id, "READ", self.ref))
        self.assertEqual(self.guard_calls, [])

    def test_actor_spoofing_denied(self):
        actor = self.make()
        self.assertFalse(actor.authorize("somebody_else", "CREATE", self.ref))
        self.assertEqual(self.guard_calls, [])

    def test_unknown_permission_denied(self):
        actor = self.make(code="NOT_GRANTED")
        self.assertFalse(actor.authorize(actor.actor_id, "CREATE", self.ref))
        self.assertEqual(self.guard_calls, [])

    def test_missing_permission_list_denied(self):
        state = SimpleNamespace(**{**vars(self.state), "permissions": None})
        with self.assertRaises(DwhSecretIdentityDenied):
            self.make(state)

    def test_unauthenticated_denied(self):
        state = SimpleNamespace(**{**vars(self.state), "authenticated": False})
        with self.assertRaises(DwhSecretIdentityDenied):
            self.make(state)

    def test_missing_user_denied(self):
        state = SimpleNamespace(**{**vars(self.state), "user": None})
        with self.assertRaises(DwhSecretIdentityDenied):
            self.make(state)

    def test_system_user_denied_from_human_adapter(self):
        user = SimpleNamespace(user_id=42, is_system=True, must_change_password=False)
        state = SimpleNamespace(**{**vars(self.state), "user": user})
        with self.assertRaises(DwhSecretIdentityDenied):
            self.make(state)

    def test_password_reset_required_denied(self):
        user = SimpleNamespace(user_id=42, is_system=False, must_change_password=True)
        state = SimpleNamespace(**{**vars(self.state), "user": user})
        with self.assertRaises(DwhSecretIdentityDenied):
            self.make(state)

    def test_zero_or_boolean_user_id_denied(self):
        for bad in (0, -1, True, False):
            user = SimpleNamespace(user_id=bad, is_system=False, must_change_password=False)
            state = SimpleNamespace(**{**vars(self.state), "user": user})
            with self.subTest(bad=bad):
                with self.assertRaises(DwhSecretIdentityDenied):
                    self.make(state)

    def test_unsafe_user_id_denied(self):
        user = SimpleNamespace(user_id="42\nsecrets", is_system=False, must_change_password=False)
        state = SimpleNamespace(**{**vars(self.state), "user": user})
        with self.assertRaises(DwhSecretIdentityDenied):
            self.make(state)

    def test_invalid_permission_setting_denied(self):
        for code in ("", "BAD CODE", "\n", 123):
            with self.subTest(code=code):
                with self.assertRaises(DwhSecretIdentityDenied):
                    self.make(code=code)

    def test_resource_denial_prevents_access(self):
        actor = self.make(guard=lambda ref: False)
        self.assertFalse(actor.authorize(actor.actor_id, "CREATE", self.ref))

    def test_resource_guard_exception_fails_closed(self):
        def broken(_):
            raise RuntimeError("synthetic-sensitive-error")
        actor = self.make(guard=broken)
        self.assertFalse(actor.authorize(actor.actor_id, "CREATE", self.ref))

    def test_resource_guard_must_be_callable(self):
        with self.assertRaises(DwhSecretIdentityDenied):
            self.make(guard=False)

    def test_secret_boundary_create_with_synthetic_audit(self):
        actor = self.make()
        gate = SecretAccessBoundary(
            store=self.store, authorize=actor.authorize, audit=self.events.append,
        )
        gate.create(actor_id=actor.actor_id, secret_ref=self.ref, value="synthetic-only")
        self.assertEqual(self.store.values[self.ref], "synthetic-only")
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].decision, "PERMITTED_ATTEMPT")

    def test_secret_boundary_read_denied_even_if_create_allowed(self):
        actor = self.make()
        gate = SecretAccessBoundary(
            store=self.store, authorize=actor.authorize, audit=self.events.append,
        )
        with self.assertRaises(SecretAccessDenied):
            gate.read(actor_id=actor.actor_id, secret_ref=self.ref)
        self.assertEqual(self.store.operations, [])
        self.assertEqual(self.events[0].decision, "DENIED")

    def test_repr_never_displays_actor_identity(self):
        actor = self.make()
        self.assertNotIn(actor.actor_id, repr(actor))
        self.assertNotIn(self.ref, repr(actor))


if __name__ == "__main__":
    unittest.main()
