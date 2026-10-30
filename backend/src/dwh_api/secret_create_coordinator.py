"""Isolated SecretStore CREATE coordinator. Not connected to production adapters.

All receipts MUST be supplied by trusted adapters that verify durable commits.
This module cannot itself authenticate receipts or establish an independent COMMIT.
On uncertainty it fails closed; it never automatically recovers/activates.
"""
from dataclasses import dataclass
from typing import Protocol


class CoordinationStopped(Exception):
    """Safe, non-disclosing refusal."""


@dataclass(frozen=True)
class Request:
    secret_ref: str
    source_system_id: int
    operation_id: str


@dataclass(frozen=True)
class Receipt:
    phase: str
    operation_id: str
    secret_ref: str
    source_system_id: int
    committed: bool
    evidence_id: str


class DurableAudit(Protocol):
    def commit_attempt(self, request: Request) -> Receipt: ...
    def commit_outcome(self, request: Request, file_digest: str) -> Receipt: ...
    def commit_failure(self, request: Request) -> Receipt: ...


class BindingRegistry(Protocol):
    def commit_pending(self, request: Request) -> Receipt: ...
    def activate(self, request: Request, file_digest: str) -> Receipt: ...


class EncryptedFileWriter(Protocol):
    def create_durable(self, request: Request) -> str: ...


def _receipt(receipt: Receipt, req: Request, phase: str) -> None:
    if (not isinstance(receipt, Receipt) or receipt.phase != phase
            or receipt.operation_id != req.operation_id
            or receipt.secret_ref != req.secret_ref
            or receipt.source_system_id != req.source_system_id
            or receipt.committed is not True
            or not isinstance(receipt.evidence_id, str)
            or not receipt.evidence_id.strip()):
        raise CoordinationStopped('Unverified or mismatched durable receipt')


def _digest(value: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        raise CoordinationStopped('Invalid encrypted file digest')
    return value


class CreateCoordinator:
    """One-shot CREATE: ATTEMPT, PENDING, FILE, OUTCOME, ACTIVE.

    Caller must supply trusted operation/source authorization and serialized
    operation ownership. Failure can leave PENDING or an encrypted orphan:
    recovery/reconciliation is intentionally a separate manual protocol.
    """

    def __init__(self, audit: DurableAudit, registry: BindingRegistry,
                 files: EncryptedFileWriter):
        self._audit, self._registry, self._files = audit, registry, files

    def create(self, request: Request) -> Receipt:
        if (not isinstance(request, Request) or not isinstance(request.secret_ref, str)
                or not request.secret_ref or not isinstance(request.operation_id, str)
                or not request.operation_id or type(request.source_system_id) is not int
                or request.source_system_id <= 0):
            raise CoordinationStopped('Invalid trusted request')
        attempt_done = False
        outcome_done = False
        try:
            _receipt(self._audit.commit_attempt(request), request, 'ATTEMPT')
            attempt_done = True
            _receipt(self._registry.commit_pending(request), request, 'PENDING')
            digest = _digest(self._files.create_durable(request))
            _receipt(self._audit.commit_outcome(request, digest), request, 'OUTCOME')
            outcome_done = True
            result = self._registry.activate(request, digest)
            _receipt(result, request, 'ACTIVE')
            return result
        except Exception:
            # A failed outcome is best effort only. Never label an uncertain
            # operation as definitively failed or compensate by activating.
            if attempt_done and not outcome_done:
                try:
                    _receipt(self._audit.commit_failure(request), request, 'OUTCOME_FAILED')
                except Exception:
                    pass
            raise CoordinationStopped('Secret creation incomplete; manual reconciliation required') from None
