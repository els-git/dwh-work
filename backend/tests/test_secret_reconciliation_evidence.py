"""No-I/O tests of UNTRUSTED reconciliation receipt structure."""
from __future__ import annotations

from dataclasses import replace
import unittest

from dwh_api.secret_binding_validation import SecretBinding
from dwh_api.secret_reconciliation_evidence import (
    ClaimedReceipt, ReconciliationEvidenceError, validate_claimed_evidence,
)

REF = "123e4567-e89b-42d3-a456-426614174000"
OTHER = "123e4567-e89b-42d3-a456-426614174001"
OP = "223e4567-e89b-42d3-a456-426614174000"
OTHER_OP = "223e4567-e89b-42d3-a456-426614174001"


class ReconciliationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.binding = SecretBinding(REF, 1, "PENDING", OP)
        def receipt(kind, **kwargs):
            return ClaimedReceipt(kind, REF, 1, OP, True, **kwargs)
        self.receipts = (
            receipt("FILE", ciphertext_sha256="a" * 64),
            receipt("PENDING", registry_state="PENDING"),
            receipt("ATTEMPT", audit_code="PERMITTED_ATTEMPT"),
            receipt("OUTCOME", audit_code="SUCCEEDED"),
        )

    def invalid(self, receipts=None, binding=None):
        with self.assertRaises(ReconciliationEvidenceError):
            validate_claimed_evidence(self.binding if binding is None else binding,
                                      self.receipts if receipts is None else receipts)

    def change(self, index, **fields):
        values = list(self.receipts)
        values[index] = replace(values[index], **fields)
        self.invalid(tuple(values))

    def test_consistent_claims_are_structurally_valid_only(self):
        self.assertIsNone(validate_claimed_evidence(self.binding, self.receipts))

    def test_list_accepted(self):
        self.assertIsNone(validate_claimed_evidence(self.binding, list(self.receipts)))

    def test_missing_receipt(self):
        self.invalid(self.receipts[:3])

    def test_extra_receipt(self):
        self.invalid(self.receipts + (self.receipts[0],))

    def test_duplicate_receipt(self):
        self.invalid((self.receipts[0], self.receipts[1], self.receipts[2], self.receipts[2]))

    def test_order_mismatch(self):
        self.invalid((self.receipts[1], self.receipts[0], self.receipts[2], self.receipts[3]))

    def test_wrong_ref(self):
        self.change(0, secret_ref=OTHER)

    def test_wrong_owner(self):
        self.change(1, source_system_id=2)

    def test_wrong_operation(self):
        self.change(2, create_operation_id=OTHER_OP)

    def test_false_commit(self):
        self.change(3, committed=False)

    def test_nonboolean_commit(self):
        self.change(2, committed=1)

    def test_invalid_checksum(self):
        self.change(0, ciphertext_sha256="sha256:invalid")

    def test_missing_checksum(self):
        self.change(0, ciphertext_sha256=None)

    def test_wrong_pending_state(self):
        self.change(1, registry_state="ACTIVE")

    def test_wrong_attempt_event(self):
        self.change(2, audit_code="DENIED")

    def test_wrong_outcome_event(self):
        self.change(3, audit_code="FAILED")

    def test_mixed_receipt_fields(self):
        self.change(3, ciphertext_sha256="b" * 64)

    def test_incorrect_input_types(self):
        self.invalid("claimed data")
        self.invalid(tuple([object()] + list(self.receipts[1:])))
        self.invalid(binding=object())

    def test_sanitized_message(self):
        with self.assertRaises(ReconciliationEvidenceError) as ctx:
            validate_claimed_evidence(self.binding,
                (replace(self.receipts[0], ciphertext_sha256="password123"),) + self.receipts[1:])
        self.assertNotIn("password", str(ctx.exception))
        self.assertNotIn(REF, str(ctx.exception))

    def test_revoked_not_accepted_as_pending(self):
        self.invalid(binding=replace(self.binding, state="REVOKED"), receipts=(
            self.receipts[0], replace(self.receipts[1], registry_state="REVOKED"),
            self.receipts[2], self.receipts[3]))
