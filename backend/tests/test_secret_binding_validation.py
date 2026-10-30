"""Synthetic, side-effect-free SecretStore metadata validation tests."""
from __future__ import annotations

from dataclasses import replace
import unittest

from dwh_api.secret_binding_validation import (
    SecretBinding,
    SecretBindingValidationError,
    validate_binding,
    validate_registry,
    validate_transition,
)

REF1 = "123e4567-e89b-42d3-a456-426614174000"
REF2 = "123e4567-e89b-42d3-a456-426614174001"
OP1 = "223e4567-e89b-42d3-a456-426614174000"
OP2 = "223e4567-e89b-42d3-a456-426614174001"


class BindingValidationTests(unittest.TestCase):
    def setUp(self):
        self.first = SecretBinding(REF1, 1, "PENDING", OP1)

    def test_valid_single_and_empty_registry(self):
        validate_binding(self.first)
        validate_registry([])

    def test_multiple_secrets_same_owner(self):
        validate_registry([self.first, SecretBinding(REF2, 1, "ACTIVE", OP2)])

    def test_different_owners_allowed_for_different_secrets(self):
        validate_registry((self.first, SecretBinding(REF2, 5, "REVOKED", OP2)))

    def test_invalid_reference_rejected(self):
        for ref in (None, 42, "../../etc", REF1.upper(), "not-uuid"):
            with self.subTest(ref=ref), self.assertRaises(SecretBindingValidationError):
                validate_binding(replace(self.first, secret_ref=ref))

    def test_invalid_owner_rejected(self):
        for owner in (None, True, 0, -1, "1", 2**63):
            with self.subTest(owner=owner), self.assertRaises(SecretBindingValidationError):
                validate_binding(replace(self.first, source_system_id=owner))

    def test_bad_state_rejected(self):
        for state in (None, 1, "active", "FAILED", ""):
            with self.subTest(state=state), self.assertRaises(SecretBindingValidationError):
                validate_binding(replace(self.first, state=state))

    def test_bad_operation_id_rejected(self):
        for operation in (None, True, "operation-1", OP1.upper()):
            with self.subTest(operation=operation), self.assertRaises(SecretBindingValidationError):
                validate_binding(replace(self.first, create_operation_id=operation))

    def test_wrong_record_type_and_collection_rejected(self):
        with self.assertRaises(SecretBindingValidationError):
            validate_binding({"secret_ref": REF1})
        for collection in (None, {}, "not-a-registry", (x for x in [])):
            with self.subTest(collection=type(collection)), self.assertRaises(SecretBindingValidationError):
                validate_registry(collection)

    def test_duplicate_reference_rejected_even_different_owner(self):
        with self.assertRaises(SecretBindingValidationError):
            validate_registry([self.first, SecretBinding(REF1, 5, "ACTIVE", OP2)])

    def test_duplicate_operation_rejected(self):
        with self.assertRaises(SecretBindingValidationError):
            validate_registry([self.first, SecretBinding(REF2, 1, "PENDING", OP1)])

    def test_allowed_transitions(self):
        active = replace(self.first, state="ACTIVE")
        revoked = replace(self.first, state="REVOKED")
        validate_transition(self.first, active)
        validate_transition(active, revoked)

    def test_forbidden_transitions(self):
        for src, dest in (("PENDING", "REVOKED"), ("ACTIVE", "PENDING"),
                          ("REVOKED", "ACTIVE"), ("ACTIVE", "ACTIVE"),
                          ("REVOKED", "REVOKED"), ("PENDING", "PENDING")):
            with self.subTest(src=src, dest=dest), self.assertRaises(SecretBindingValidationError):
                validate_transition(replace(self.first, state=src), replace(self.first, state=dest))

    def test_identity_change_rejected(self):
        after = replace(self.first, state="ACTIVE")
        for change in (dict(secret_ref=REF2), dict(source_system_id=5),
                       dict(create_operation_id=OP2)):
            with self.subTest(change=change), self.assertRaises(SecretBindingValidationError):
                validate_transition(self.first, replace(after, **change))

    def test_errors_do_not_echo_values(self):
        bad = replace(self.first, secret_ref="password-like-sensitive-input")
        with self.assertRaises(SecretBindingValidationError) as ctx:
            validate_binding(bad)
        self.assertNotIn("password-like", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
