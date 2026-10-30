"""Disconnected validation of synthetic SecretStore binding metadata.

This is NOT a persistence, authorization, audit or publication mechanism.
A caller's supplied collection is not an authoritative database snapshot.
No secret values or filesystem operations are handled here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import uuid

BindingState = Literal["PENDING", "ACTIVE", "REVOKED"]
_ALLOWED = {"PENDING", "ACTIVE", "REVOKED"}
_NEXT = {"PENDING": "ACTIVE", "ACTIVE": "REVOKED"}
_MAX_BIGINT = 9223372036854775807


class SecretBindingValidationError(ValueError):
    """Sanitized metadata-validation failure."""


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


@dataclass(frozen=True, slots=True)
class SecretBinding:
    secret_ref: str
    source_system_id: int
    state: BindingState
    create_operation_id: str


def validate_binding(binding: object) -> None:
    """Validate one binding's shape, never claiming its authorization."""
    if not isinstance(binding, SecretBinding):
        raise SecretBindingValidationError("Invalid secret binding")
    if not _canonical_uuid(binding.secret_ref):
        raise SecretBindingValidationError("Invalid secret binding")
    source_id = binding.source_system_id
    if (isinstance(source_id, bool) or not isinstance(source_id, int)
            or not 0 < source_id <= _MAX_BIGINT):
        raise SecretBindingValidationError("Invalid secret binding")
    if not isinstance(binding.state, str) or binding.state not in _ALLOWED:
        raise SecretBindingValidationError("Invalid secret binding")
    if not _canonical_uuid(binding.create_operation_id):
        raise SecretBindingValidationError("Invalid secret binding")


def validate_registry(bindings: object) -> None:
    """Check duplicate refs and create-operation IDs in a supplied snapshot.

    Global uniqueness and transaction isolation MUST be enforced by durable
    storage in a later approved iteration, not by this check.
    """
    if not isinstance(bindings, (list, tuple)):
        raise SecretBindingValidationError("Invalid secret bindings")
    refs: set[str] = set()
    operations: set[str] = set()
    for binding in bindings:
        validate_binding(binding)
        if binding.secret_ref in refs or binding.create_operation_id in operations:
            raise SecretBindingValidationError("Conflicting secret binding")
        refs.add(binding.secret_ref)
        operations.add(binding.create_operation_id)


def validate_transition(before: object, after: object) -> None:
    """Require same immutable identity and PENDING->ACTIVE->REVOKED only."""
    validate_binding(before)
    validate_binding(after)
    if (before.secret_ref != after.secret_ref
            or before.source_system_id != after.source_system_id
            or before.create_operation_id != after.create_operation_id
            or _NEXT.get(before.state) != after.state):
        raise SecretBindingValidationError("Invalid binding transition")
