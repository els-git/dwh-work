"""Pure classification of caller-supplied, synthetic SecretStore recovery state.

No proof is authenticated here. In particular, RECONCILE never authorizes
activation, file deletion, retries of storage writes, or service secret READ.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from dwh_api.secret_binding_validation import (
    SecretBinding,
    SecretBindingValidationError,
    validate_binding,
)


class RecoveryDecision(str, Enum):
    REQUIRE_RECONCILIATION = "REQUIRE_RECONCILIATION"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class SyntheticRecoverySnapshot:
    """Untrusted synthetic observations, NOT durable receipts."""

    secret_ref: str
    source_system_id: int
    create_operation_id: str
    encrypted_file_observed: bool | None
    pending_registry_commit_observed: bool | None
    attempt_audit_commit_observed: bool | None
    outcome_audit_commit_observed: bool | None


def decide_recovery(binding: object, snapshot: object) -> RecoveryDecision:
    """Classify; never mutate, grant access, or publish a secret.

    None means unknown. Incomplete consistent PENDING observations require an
    independent trusted reconciliation, never an automatic transition.
    Inconsistency, invalid metadata and revoked entries are denied.
    """
    try:
        validate_binding(binding)
    except SecretBindingValidationError:
        return RecoveryDecision.DENY

    if not isinstance(binding, SecretBinding) or not isinstance(snapshot, SyntheticRecoverySnapshot):
        return RecoveryDecision.DENY

    if (
        type(snapshot.secret_ref) is not str
        or snapshot.secret_ref != binding.secret_ref
        or type(snapshot.source_system_id) is not int
        or snapshot.source_system_id != binding.source_system_id
        or type(snapshot.create_operation_id) is not str
        or snapshot.create_operation_id != binding.create_operation_id
    ):
        return RecoveryDecision.DENY

    flags = (
        snapshot.encrypted_file_observed,
        snapshot.pending_registry_commit_observed,
        snapshot.attempt_audit_commit_observed,
        snapshot.outcome_audit_commit_observed,
    )
    if any(type(value) not in (bool, type(None)) for value in flags):
        return RecoveryDecision.DENY

    file_seen, pending_seen, attempt_seen, outcome_seen = flags

    # A committed outcome requires a committed attempt. A file or outcome
    # cannot belong to a record whose PENDING commit was explicitly absent.
    if outcome_seen is True and attempt_seen is False:
        return RecoveryDecision.DENY
    if pending_seen is False and (file_seen is True or outcome_seen is True):
        return RecoveryDecision.DENY

    if binding.state == "REVOKED":
        return RecoveryDecision.DENY
    if binding.state == "ACTIVE":
        # A synthetic snapshot cannot prove activation. Even in the internally
        # consistent case, ALREADY_ACTIVE never permits a new transition.
        if all(value is True for value in flags):
            return RecoveryDecision.ALREADY_ACTIVE
        return RecoveryDecision.DENY
    if binding.state == "PENDING":
        # No caller-supplied booleans, including all True, permit activation.
        return RecoveryDecision.REQUIRE_RECONCILIATION
    return RecoveryDecision.DENY
