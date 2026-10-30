"""Structural checks for UNTRUSTED SecretStore reconciliation claims.

No claim authenticates file content, SQL COMMIT, audit or trusted identities.
Successful validation NEVER authorizes activation, deletion, retry or READ.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import re

from dwh_api.secret_binding_validation import (
    SecretBinding, SecretBindingValidationError, validate_binding,
)

ReceiptKind = Literal["FILE", "PENDING", "ATTEMPT", "OUTCOME"]
_KINDS = ("FILE", "PENDING", "ATTEMPT", "OUTCOME")
_CHECKSUM = re.compile(r"[0-9a-f]{64}\Z")


class ReconciliationEvidenceError(ValueError):
    """Sanitized failure of structural checks only."""


@dataclass(frozen=True, slots=True)
class ClaimedReceipt:
    kind: ReceiptKind
    secret_ref: str
    source_system_id: int
    create_operation_id: str
    committed: bool
    # Only FILE carries a non-secret SHA256 ciphertext digest.
    ciphertext_sha256: str | None = None
    # Only PENDING carries its claimed binding state.
    registry_state: str | None = None
    # Only ATTEMPT / OUTCOME carry an audit code.
    audit_code: str | None = None


def validate_claimed_evidence(binding: object, receipts: object) -> None:
    """Validate four caller-asserted receipts, never their authenticity.

    Fail closed for missing, duplicate, wrongly ordered, inconsistent or
    malformed claims. No conversion into permission or recovery decisions.
    """
    try:
        validate_binding(binding)
    except SecretBindingValidationError:
        raise ReconciliationEvidenceError("Unverified reconciliation evidence") from None
    if not isinstance(binding, SecretBinding) or not isinstance(receipts, (tuple, list)):
        raise ReconciliationEvidenceError("Unverified reconciliation evidence")
    if len(receipts) != 4:
        raise ReconciliationEvidenceError("Unverified reconciliation evidence")
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, ClaimedReceipt) or type(receipt.kind) is not str or receipt.kind != _KINDS[index]:
            raise ReconciliationEvidenceError("Unverified reconciliation evidence")
        if (type(receipt.secret_ref) is not str or receipt.secret_ref != binding.secret_ref
                or type(receipt.source_system_id) is not int
                or receipt.source_system_id != binding.source_system_id
                or type(receipt.create_operation_id) is not str
                or receipt.create_operation_id != binding.create_operation_id
                or type(receipt.committed) is not bool or receipt.committed is not True):
            raise ReconciliationEvidenceError("Unverified reconciliation evidence")
        if receipt.kind == "FILE":
            if (type(receipt.ciphertext_sha256) is not str
                    or _CHECKSUM.fullmatch(receipt.ciphertext_sha256) is None
                    or receipt.registry_state is not None or receipt.audit_code is not None):
                raise ReconciliationEvidenceError("Unverified reconciliation evidence")
        elif receipt.kind == "PENDING":
            if (receipt.ciphertext_sha256 is not None or receipt.registry_state != "PENDING"
                    or receipt.audit_code is not None):
                raise ReconciliationEvidenceError("Unverified reconciliation evidence")
        else:
            correct = "PERMITTED_ATTEMPT" if receipt.kind == "ATTEMPT" else "SUCCEEDED"
            if (receipt.ciphertext_sha256 is not None or receipt.registry_state is not None
                    or receipt.audit_code != correct):
                raise ReconciliationEvidenceError("Unverified reconciliation evidence")
