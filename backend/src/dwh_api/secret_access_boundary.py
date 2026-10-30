"""Disconnected security boundary for operations on connector secrets.

The caller MUST supply a verifier implemented using existing DWH authorization
and a DURABLE audit sink. Never replace these with permissive callbacks in
production. The access gate itself creates no roles, route, database rows, or
logs, and it does not read actual secret or master-key settings at import time.

This module records an authorization DECISION before storage. An OPTIONAL
result-audit callback can also record SUCCEEDED/FAILED after the operation;
its absence preserves the previous disconnected/test-only behaviour.

A result-audit failure after CREATE may leave an encrypted file on disk, so
this callback is NOT a durable commit/publication protocol. Real independent
audit transactions, server-owned reference binding, pending-state recovery,
filesystem ACLs and service identity remain mandatory before any API/ETL use.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

SecretOperation = Literal["CREATE", "READ"]
Decision = Literal["DENIED", "PERMITTED_ATTEMPT", "SUCCEEDED", "FAILED"]
_ACTOR_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)


class SecretAccessDenied(Exception):
    """Sanitized denial; never embeds credentials, object paths or callbacks."""


class SecretAccessFailure(Exception):
    """Sanitized failure; hides sensitive details from dependency exceptions."""


@dataclass(frozen=True, slots=True)
class SecretAccessEvent:
    actor_id: str
    operation: SecretOperation
    decision: Decision
    reference_fingerprint: str


class SecretStorage(Protocol):
    def create(self, *, secret_ref: str, value: str) -> None: ...
    def read(self, *, secret_ref: str) -> str: ...


class SecretAccessBoundary:
    """Fail-closed barrier; callers supply real DWH authorization and audit."""

    __slots__ = ("_store", "_authorize", "_audit", "_audit_outcome")

    def __init__(
        self,
        *,
        store: SecretStorage,
        authorize: Callable[[str, SecretOperation, str], bool],
        audit: Callable[[SecretAccessEvent], None],
        audit_outcome: Callable[[SecretAccessEvent], None] | None = None,
    ) -> None:
        if audit_outcome is not None and not callable(audit_outcome):
            raise SecretAccessFailure("Secret access unavailable")
        if not callable(authorize) or not callable(audit):
            raise SecretAccessFailure("Secret access unavailable")
        if not callable(getattr(store, "create", None)) or not callable(
            getattr(store, "read", None)
        ):
            raise SecretAccessFailure("Secret access unavailable")
        self._store = store
        self._authorize = authorize
        self._audit = audit
        self._audit_outcome = audit_outcome

    def __repr__(self) -> str:
        return "SecretAccessBoundary(<sensitive configuration hidden>)"

    @staticmethod
    def _validate(actor_id: str, secret_ref: str) -> str:
        if not isinstance(actor_id, str) or _ACTOR_RE.fullmatch(actor_id) is None:
            raise SecretAccessDenied("Secret access denied")
        if not isinstance(secret_ref, str):
            raise SecretAccessDenied("Secret access denied")
        try:
            canonical = str(uuid.UUID(secret_ref))
        except (TypeError, ValueError, AttributeError):
            raise SecretAccessDenied("Secret access denied") from None
        if canonical != secret_ref:
            raise SecretAccessDenied("Secret access denied")
        return hashlib.sha256(secret_ref.encode("ascii")).hexdigest()

    def _check(self, *, actor_id: str, secret_ref: str, operation: SecretOperation) -> None:
        fingerprint = self._validate(actor_id, secret_ref)
        try:
            allowed = self._authorize(actor_id, operation, secret_ref) is True
        except Exception:
            allowed = False
        event = SecretAccessEvent(
            actor_id=actor_id,
            operation=operation,
            decision="PERMITTED_ATTEMPT" if allowed else "DENIED",
            reference_fingerprint=fingerprint,
        )
        try:
            self._audit(event)
        except Exception:
            raise SecretAccessFailure("Secret access unavailable") from None
        if not allowed:
            raise SecretAccessDenied("Secret access denied")

    def _result(self, *, actor_id: str, secret_ref: str,
                operation: SecretOperation, decision: Decision) -> None:
        """Optional final event; no claim of atomic DB/file finalization."""
        if self._audit_outcome is None:
            return
        fingerprint = self._validate(actor_id, secret_ref)
        try:
            self._audit_outcome(SecretAccessEvent(
                actor_id=actor_id,
                operation=operation,
                decision=decision,
                reference_fingerprint=fingerprint,
            ))
        except Exception:
            raise SecretAccessFailure("Secret access unavailable") from None

    def create(self, *, actor_id: str, secret_ref: str, value: str) -> None:
        self._check(actor_id=actor_id, secret_ref=secret_ref, operation="CREATE")
        try:
            self._store.create(secret_ref=secret_ref, value=value)
        except Exception:
            self._result(actor_id=actor_id, secret_ref=secret_ref,
                         operation="CREATE", decision="FAILED")
            raise SecretAccessFailure("Secret operation failed") from None
        self._result(actor_id=actor_id, secret_ref=secret_ref,
                     operation="CREATE", decision="SUCCEEDED")

    def read(self, *, actor_id: str, secret_ref: str) -> str:
        self._check(actor_id=actor_id, secret_ref=secret_ref, operation="READ")
        try:
            value = self._store.read(secret_ref=secret_ref)
            if not isinstance(value, str):
                raise ValueError("Invalid storage response")
        except Exception:
            self._result(actor_id=actor_id, secret_ref=secret_ref,
                         operation="READ", decision="FAILED")
            raise SecretAccessFailure("Secret operation failed") from None
        self._result(actor_id=actor_id, secret_ref=secret_ref,
                     operation="READ", decision="SUCCEEDED")
        return value
