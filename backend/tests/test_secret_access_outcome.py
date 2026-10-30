"""Synthetic-only outcome-audit tests; no real keys, PostgreSQL or HTTP."""
import hashlib
import unittest
import uuid
from dwh_api.secret_access_boundary import (
    SecretAccessBoundary, SecretAccessDenied, SecretAccessFailure,
)

REF = "12345678-1234-4234-8234-123456789abc"
DIGEST = hashlib.sha256(REF.encode("ascii")).hexdigest()

class Store:
    def __init__(self, trace, fail=False):
        self.trace = trace
        self.fail = fail
    def create(self, *, secret_ref, value):
        self.trace.append("storage:create")
        if self.fail:
            raise RuntimeError("SECRET_VALUE_NEVER_LOGGED")
    def read(self, *, secret_ref):
        self.trace.append("storage:read")
        if self.fail:
            raise RuntimeError("SECRET_VALUE_NEVER_LOGGED")
        return "secret-value"

class TestOutcomeAudit(unittest.TestCase):
    def make_gate(self, *, denied=False, store_fail=False, fail_phase=None):
        trace = []
        def attempt(event):
            self.assertEqual(event.reference_fingerprint, DIGEST)
            self.assertEqual(event.actor_id, "123")
            trace.append("attempt:" + event.decision)
            if fail_phase == "attempt":
                raise ValueError("SECRET_VALUE_NEVER_LOGGED")
        def outcome(event):
            self.assertEqual(event.reference_fingerprint, DIGEST)
            trace.append("outcome:" + event.decision)
            if fail_phase == "outcome":
                raise ValueError("SECRET_VALUE_NEVER_LOGGED")
        gate = SecretAccessBoundary(
            store=Store(trace, fail=store_fail),
            authorize=lambda *_: not denied,
            audit=attempt, audit_outcome=outcome,
        )
        return gate, trace

    def test_create_success_order(self):
        gate, trace = self.make_gate()
        gate.create(actor_id="123", secret_ref=REF, value="secret-value")
        self.assertEqual(trace, ["attempt:PERMITTED_ATTEMPT", "storage:create", "outcome:SUCCEEDED"])

    def test_create_failed_storage_audited(self):
        gate, trace = self.make_gate(store_fail=True)
        with self.assertRaisesRegex(SecretAccessFailure, "Secret operation failed"):
            gate.create(actor_id="123", secret_ref=REF, value="secret-value")
        self.assertEqual(trace, ["attempt:PERMITTED_ATTEMPT", "storage:create", "outcome:FAILED"])

    def test_denied_creates_no_file_and_no_outcome(self):
        gate, trace = self.make_gate(denied=True)
        with self.assertRaises(SecretAccessDenied):
            gate.create(actor_id="123", secret_ref=REF, value="secret-value")
        self.assertEqual(trace, ["attempt:DENIED"])

    def test_failed_attempt_audit_prevents_create(self):
        gate, trace = self.make_gate(fail_phase="attempt")
        with self.assertRaisesRegex(SecretAccessFailure, "Secret access unavailable"):
            gate.create(actor_id="123", secret_ref=REF, value="secret-value")
        self.assertEqual(trace, ["attempt:PERMITTED_ATTEMPT"])

    def test_failed_outcome_audit_never_claims_success(self):
        gate, trace = self.make_gate(fail_phase="outcome")
        with self.assertRaisesRegex(SecretAccessFailure, "Secret access unavailable"):
            gate.create(actor_id="123", secret_ref=REF, value="secret-value")
        self.assertEqual(trace, ["attempt:PERMITTED_ATTEMPT", "storage:create", "outcome:SUCCEEDED"])

    def test_read_success_outcome(self):
        gate, trace = self.make_gate()
        self.assertEqual(gate.read(actor_id="123", secret_ref=REF), "secret-value")
        self.assertEqual(trace, ["attempt:PERMITTED_ATTEMPT", "storage:read", "outcome:SUCCEEDED"])

    def test_read_failed_storage_outcome(self):
        gate, trace = self.make_gate(store_fail=True)
        with self.assertRaisesRegex(SecretAccessFailure, "Secret operation failed"):
            gate.read(actor_id="123", secret_ref=REF)
        self.assertEqual(trace, ["attempt:PERMITTED_ATTEMPT", "storage:read", "outcome:FAILED"])

    def test_backward_compatible_without_outcome_callback(self):
        trace=[]
        gate=SecretAccessBoundary(
            store=Store(trace), authorize=lambda *_: True,
            audit=lambda e: trace.append("attempt:"+e.decision))
        gate.create(actor_id="123", secret_ref=REF, value="secret-value")
        self.assertEqual(trace, ["attempt:PERMITTED_ATTEMPT", "storage:create"])

    def test_invalid_auditor_fails_closed(self):
        with self.assertRaises(SecretAccessFailure):
            SecretAccessBoundary(store=Store([]), authorize=lambda *_: True,
                                 audit=lambda e: None, audit_outcome=True)

    def test_sanitized_exception_no_value(self):
        gate, _ = self.make_gate(fail_phase="outcome")
        with self.assertRaises(SecretAccessFailure) as raised:
            gate.create(actor_id="123", secret_ref=REF, value="secret-value")
        self.assertNotIn("secret-value", str(raised.exception))
        self.assertNotIn(REF, str(raised.exception))

if __name__ == "__main__":
    unittest.main()
