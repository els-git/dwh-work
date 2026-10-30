"""Side-effect-free recovery classification tests using synthetic metadata."""
from __future__ import annotations

from dataclasses import replace
import unittest

from dwh_api.secret_binding_validation import SecretBinding
from dwh_api.secret_recovery_decision import (
    RecoveryDecision,
    SyntheticRecoverySnapshot,
    decide_recovery,
)

REF = "123e4567-e89b-42d3-a456-426614174000"
OP = "223e4567-e89b-42d3-a456-426614174000"


class RecoveryDecisionTests(unittest.TestCase):
    def setUp(self):
        self.binding = SecretBinding(REF, 5, "PENDING", OP)
        self.snapshot = SyntheticRecoverySnapshot(REF, 5, OP, True, True, True, True)

    def test_full_pending_never_automatically_activates(self):
        self.assertEqual(decide_recovery(self.binding, self.snapshot), RecoveryDecision.REQUIRE_RECONCILIATION)

    def test_empty_pending_requires_reconciliation(self):
        snapshot = replace(self.snapshot, encrypted_file_observed=False,
                           pending_registry_commit_observed=False,
                           attempt_audit_commit_observed=False,
                           outcome_audit_commit_observed=False)
        self.assertEqual(decide_recovery(self.binding, snapshot), RecoveryDecision.REQUIRE_RECONCILIATION)

    def test_unknown_observation_requires_reconciliation(self):
        snapshot = replace(self.snapshot, encrypted_file_observed=None,
                           outcome_audit_commit_observed=None)
        self.assertEqual(decide_recovery(self.binding, snapshot), RecoveryDecision.REQUIRE_RECONCILIATION)

    def test_missing_file_requires_reconciliation(self):
        snapshot = replace(self.snapshot, encrypted_file_observed=False)
        self.assertEqual(decide_recovery(self.binding, snapshot), RecoveryDecision.REQUIRE_RECONCILIATION)

    def test_missing_outcome_requires_reconciliation(self):
        snapshot = replace(self.snapshot, outcome_audit_commit_observed=False)
        self.assertEqual(decide_recovery(self.binding, snapshot), RecoveryDecision.REQUIRE_RECONCILIATION)

    def test_same_operation_retry_does_not_publish(self):
        result1 = decide_recovery(self.binding, self.snapshot)
        result2 = decide_recovery(self.binding, self.snapshot)
        self.assertEqual(result1, result2)
        self.assertEqual(result1, RecoveryDecision.REQUIRE_RECONCILIATION)

    def test_active_consistent_is_not_reactivated(self):
        self.assertEqual(decide_recovery(replace(self.binding, state="ACTIVE"), self.snapshot), RecoveryDecision.ALREADY_ACTIVE)

    def test_active_incomplete_denied(self):
        snapshot = replace(self.snapshot, outcome_audit_commit_observed=None)
        self.assertEqual(decide_recovery(replace(self.binding, state="ACTIVE"), snapshot), RecoveryDecision.DENY)

    def test_revoked_denied(self):
        self.assertEqual(decide_recovery(replace(self.binding, state="REVOKED"), self.snapshot), RecoveryDecision.DENY)

    def test_ref_mismatch_denied(self):
        self.assertEqual(decide_recovery(self.binding, replace(self.snapshot, secret_ref="123e4567-e89b-42d3-a456-426614174001")), RecoveryDecision.DENY)

    def test_source_mismatch_denied(self):
        self.assertEqual(decide_recovery(self.binding, replace(self.snapshot, source_system_id=4)), RecoveryDecision.DENY)

    def test_operation_mismatch_denied(self):
        self.assertEqual(decide_recovery(self.binding, replace(self.snapshot, create_operation_id="223e4567-e89b-42d3-a456-426614174001")), RecoveryDecision.DENY)

    def test_non_boolean_flag_denied(self):
        for value in (1, "true", [], object()):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(decide_recovery(self.binding, replace(self.snapshot, encrypted_file_observed=value)), RecoveryDecision.DENY)

    def test_outcome_without_attempt_denied(self):
        self.assertEqual(decide_recovery(self.binding, replace(self.snapshot, attempt_audit_commit_observed=False)), RecoveryDecision.DENY)

    def test_file_without_pending_denied(self):
        self.assertEqual(decide_recovery(self.binding, replace(self.snapshot, pending_registry_commit_observed=False)), RecoveryDecision.DENY)

    def test_invalid_record_denied(self):
        self.assertEqual(decide_recovery(replace(self.binding, secret_ref="bad"), self.snapshot), RecoveryDecision.DENY)
        self.assertEqual(decide_recovery(None, self.snapshot), RecoveryDecision.DENY)
        self.assertEqual(decide_recovery(self.binding, None), RecoveryDecision.DENY)


if __name__ == "__main__":
    unittest.main()
