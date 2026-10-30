"""No-I/O tests for synthetic activation readiness metadata."""
from dataclasses import replace
import unittest

from dwh_api.secret_binding_validation import SecretBinding
from dwh_api.secret_activation_readiness import (
    SyntheticActivationEvidence,
    SecretActivationReadinessError,
    validate_activation_readiness,
)

REF = "123e4567-e89b-42d3-a456-426614174000"
REF2 = "123e4567-e89b-42d3-a456-426614174001"
OP = "223e4567-e89b-42d3-a456-426614174000"
OP2 = "223e4567-e89b-42d3-a456-426614174001"


class ActivationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.pending = SecretBinding(REF, 1, "PENDING", OP)
        self.active = replace(self.pending, state="ACTIVE")
        self.evidence = SyntheticActivationEvidence(REF, 1, OP, True, True, True, True)

    def assert_denied(self, before=None, after=None, evidence=None):
        with self.assertRaises(SecretActivationReadinessError):
            validate_activation_readiness(
                self.pending if before is None else before,
                self.active if after is None else after,
                self.evidence if evidence is None else evidence,
            )

    def test_all_synthetic_confirmations(self):
        self.assertIsNone(validate_activation_readiness(self.pending, self.active, self.evidence))

    def test_missing_file_confirmation(self):
        self.assert_denied(evidence=replace(self.evidence, encrypted_file_verified=False))

    def test_missing_pending_registry_commit(self):
        self.assert_denied(evidence=replace(self.evidence, pending_registry_commit_verified=False))

    def test_missing_attempt_audit_commit(self):
        self.assert_denied(evidence=replace(self.evidence, attempt_audit_commit_verified=False))

    def test_missing_outcome_audit_commit(self):
        self.assert_denied(evidence=replace(self.evidence, outcome_audit_commit_verified=False))

    def test_non_boolean_confirmation(self):
        for invalid in (None, 1, "True", 0, object()):
            with self.subTest(value=type(invalid).__name__):
                self.assert_denied(evidence=replace(self.evidence, encrypted_file_verified=invalid))

    def test_wrong_secret_ref(self):
        self.assert_denied(evidence=replace(self.evidence, secret_ref=REF2))

    def test_wrong_source(self):
        self.assert_denied(evidence=replace(self.evidence, source_system_id=5))

    def test_wrong_operation(self):
        self.assert_denied(evidence=replace(self.evidence, create_operation_id=OP2))

    def test_repeated_activation(self):
        self.assert_denied(before=self.active)

    def test_skip_pending(self):
        self.assert_denied(after=replace(self.pending, state="REVOKED"))

    def test_identity_change(self):
        self.assert_denied(after=replace(self.active, source_system_id=5))

    def test_invalid_binding_and_evidence(self):
        self.assert_denied(before="invalid")
        self.assert_denied(evidence={"encrypted_file_verified": True})

    def test_error_does_not_expose_identifiers(self):
        with self.assertRaises(SecretActivationReadinessError) as ctx:
            validate_activation_readiness(self.pending, self.active,
                replace(self.evidence, create_operation_id=OP2))
        self.assertNotIn(REF, str(ctx.exception))
        self.assertNotIn(OP2, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
