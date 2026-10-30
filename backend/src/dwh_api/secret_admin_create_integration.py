"""Opt-in, synthetic-only admin CREATE composition; never exposes an API endpoint.

Operator supplies the already authenticated/authorized Request, encrypted store,
synthetic value supplier and verified administrative connection provider. The
caller must serialize operations and audit authorization separately. No implicit
connections, passwords, key generation, or database writes on construction.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from dwh_api.secret_create_coordinator import CreateCoordinator, CoordinationStopped, Receipt, Request
from dwh_api.secret_file_store import EncryptedFileSecretStore
from dwh_api.secret_pg_durable_adapter import DbOperation, PgDurableAdapter
from dwh_api.secret_pg_success_extension import PgSuccessExtension
from dwh_api.secret_windows_durable import WindowsDurableFileCreate


class AdminCreateStopped(Exception):
    pass


def _canonical(ref):
    try:
        if type(ref) is not str or str(UUID(ref)) != ref:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AdminCreateStopped('Invalid trusted operation') from None


def _op(req):
    if not isinstance(req, Request) or type(req.source_system_id) is not int or req.source_system_id <= 0:
        raise AdminCreateStopped('Invalid trusted operation')
    _canonical(req.operation_id)
    _canonical(req.secret_ref)
    return DbOperation(req.operation_id, req.secret_ref, req.source_system_id)


class _VerifiedFile:
    def __init__(self, store, synthetic_value_supplier):
        if not isinstance(store, EncryptedFileSecretStore) or not callable(synthetic_value_supplier):
            raise AdminCreateStopped('Invalid file dependencies')
        self._store = store
        self._supplier = synthetic_value_supplier
        self._writer = WindowsDurableFileCreate(store)
        self._proof = None

    def create_durable(self, req):
        _op(req)
        if self._proof is not None:
            raise AdminCreateStopped('File proof already consumed')
        value = self._supplier()
        if type(value) is not str or not value:
            raise AdminCreateStopped('Invalid synthetic value')
        try:
            digest = self._writer.create_and_verify(secret_ref=req.secret_ref, value=value)
        except Exception:
            raise AdminCreateStopped('Durable file verification failed') from None
        self._proof = (req.operation_id, req.secret_ref, req.source_system_id, digest)
        return digest

    def verify_for_activation(self, req, digest):
        if self._proof != (req.operation_id, req.secret_ref, req.source_system_id, digest):
            raise AdminCreateStopped('Untrusted file evidence')
        path = self._store._directory / (req.secret_ref + '.bin')
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
                raise ValueError()
            # Direct hash check blocks unnoticed post-outcome ciphertext changes.
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError()
        except Exception:
            raise AdminCreateStopped('File no longer matches durable evidence') from None
        self._proof = None


class _AdminBridge:
    def __init__(self, db, success, file_step):
        self._db, self._success, self._file = db, success, file_step

    @staticmethod
    def _receipt(req, phase):
        return Receipt(phase, req.operation_id, req.secret_ref,
                       req.source_system_id, True, 'independent-db-readback:' + phase)

    def commit_attempt(self, req):
        try:
            self._db.permitted_attempt(_op(req))
        except Exception:
            raise AdminCreateStopped('ATTEMPT unconfirmed') from None
        return self._receipt(req, 'ATTEMPT')

    def commit_pending(self, req):
        try:
            self._db.pending(_op(req))
        except Exception:
            raise AdminCreateStopped('PENDING unconfirmed') from None
        return self._receipt(req, 'PENDING')

    def commit_outcome(self, req, digest):
        # DB success outcome follows the file's native flush acknowledgment.
        if self._file._proof != (req.operation_id, req.secret_ref, req.source_system_id, digest):
            raise AdminCreateStopped('OUTCOME missing file proof')
        try:
            self._success.successful_outcome(_op(req), digest)
        except Exception:
            raise AdminCreateStopped('OUTCOME unconfirmed') from None
        return self._receipt(req, 'OUTCOME')

    def activate(self, req, digest):
        self._file.verify_for_activation(req, digest)
        try:
            self._success.activate_verified(_op(req), digest, file_is_verified=True)
        except Exception:
            raise AdminCreateStopped('ACTIVE unconfirmed') from None
        return self._receipt(req, 'ACTIVE')

    def commit_failure(self, req):
        try:
            self._db.failed_outcome(_op(req))
        except Exception:
            raise AdminCreateStopped('Failure audit unconfirmed') from None
        return self._receipt(req, 'OUTCOME_FAILED')


def compose_admin_synthetic_create(*, connections, encrypted_store, synthetic_value_supplier):
    """Build an inert coordinator. No actions until caller invokes .create(request).

    Only for operator-approved synthetic values. Caller must independently verify
    DWH authorization, trusted identity, scope, key custody, and ACL before use.
    """
    if not all(callable(getattr(connections, name, None)) for name in ('writer', 'reader')):
        raise AdminCreateStopped('Administrative connections not available')
    files = _VerifiedFile(encrypted_store, synthetic_value_supplier)
    db = PgDurableAdapter(connections.writer, connections.reader)
    bridge = _AdminBridge(db, PgSuccessExtension(db), files)
    return CreateCoordinator(bridge, bridge, files)
