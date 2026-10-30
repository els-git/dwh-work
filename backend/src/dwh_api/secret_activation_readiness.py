"""Synthetic readiness checks, NOT permission to activate a real secret.

Evidence booleans are caller claims, not verified filesystem, SQL or audit
receipts. A future durable coordinator MUST authenticate and verify proofs.
This module performs no I/O, persistence, authorization or activation.
"""
from __future__ import annotations

from dataclasses import dataclass

from dwh_api.secret_binding_validation import (
    SecretBinding,
    SecretBindingValidationError,
    validate_transition,
)


class SecretActivationReadinessError(ValueError):
    """Sanitized failure; no identifiers or secrets included."""


@dataclass(frozen=True, slots=True)
class SyntheticActivationEvidence:
    secret_ref: str
    source_system_id: int
    create_operation_id: str
    encrypted_file_verified: bool
    pending_registry_commit_verified: bool
    attempt_audit_commit_verified: bool
    outcome_audit_commit_verified: bool


def validate_activation_readiness(
    before: object, after: object, evidence: object
) -> None:
    """Validate synthetic evidence for an intended PENDING -> ACTIVE step.

    Successful return means only that supplied values are internally
    consistent. It does NOT perform or authorize an actual activation.
    """
    try:
        validate_transition(before, after)
    except SecretBindingValidationError as exc:
        raise SecretActivationReadinessError("Activation not ready") from None

    if (
        not isinstance(before, SecretBinding)
        or not isinstance(after, SecretBinding)
        or before.state != "PENDING"
        or after.state != "ACTIVE"
        or not isinstance(evidence, SyntheticActivationEvidence)
        or type(evidence.secret_ref) is not str
        or evidence.secret_ref != before.secret_ref
        or type(evidence.source_system_id) is not int
        or evidence.source_system_id != before.source_system_id
        or type(evidence.create_operation_id) is not str
        or evidence.create_operation_id != before.create_operation_id
        or any(type(value) is not bool or value is not True for value in (
            evidence.encrypted_file_verified,
            evidence.pending_registry_commit_verified,
            evidence.attempt_audit_commit_verified,
            evidence.outcome_audit_commit_verified,
        ))
    ):
        raise SecretActivationReadinessError("Activation not ready")
