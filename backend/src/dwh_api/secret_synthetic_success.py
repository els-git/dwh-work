"""Isolated synthetic-only CREATE composition: encrypted file -> independent DB readbacks.
No DWH authorization, ACL attestation, secure key delivery, durable directory sync,
crash recovery, or production wiring. Never pass real credentials to this component.
"""
from __future__ import annotations
import hashlib
import stat
from pathlib import Path
from dwh_api.secret_create_coordinator import Request, Receipt, CreateCoordinator, CoordinationStopped
from dwh_api.secret_pg_durable_adapter import DbOperation

class SyntheticFileStep:
    """Only accepts pre-approved synthetic value from a provider; no production secrets."""
    def __init__(self, encrypted_store, directory: Path, synthetic_value_provider):
        if not callable(synthetic_value_provider) or not isinstance(directory, Path):
            raise CoordinationStopped('Invalid synthetic file dependencies')
        self._store=encrypted_store
        self._directory=directory
        self._value=synthetic_value_provider

    def create_durable(self, request: Request) -> str:
        try:
            # UUID validation and symlink checks are repeated by underlying file store.
            from uuid import UUID
            ref=request.secret_ref
            if type(ref) is not str or str(UUID(ref)) != ref:
                raise ValueError()
            if not self._directory.is_dir() or self._directory.is_symlink():
                raise ValueError()
            value=self._value()
            if type(value) is not str:
                raise ValueError()
            self._store.create(secret_ref=ref,value=value)
            p=self._directory/(ref+'.bin')
            before=p.lstat()
            if not stat.S_ISREG(before.st_mode) or p.is_symlink():
                raise ValueError()
            first=p.read_bytes()
            second=p.read_bytes()
            if not first or first != second or len(first)>16*1024*1024:
                raise ValueError()
            after=p.lstat()
            if before.st_ino != after.st_ino or before.st_size != after.st_size:
                raise ValueError()
            return hashlib.sha256(first).hexdigest()
        except Exception:
            raise CoordinationStopped('Encrypted file evidence not confirmed') from None

class PgSuccessBridge:
    """Compose existing PG COMMIT/readback methods; no fabrication on failure."""
    def __init__(self, pg_adapter, success_extension):
        self._db=pg_adapter
        self._success=success_extension

    @staticmethod
    def _op(req):
        return DbOperation(req.operation_id,req.secret_ref,req.source_system_id)

    @staticmethod
    def _receipt(req,phase):
        return Receipt(phase,req.operation_id,req.secret_ref,req.source_system_id,True,'db-readback:'+phase)

    def commit_attempt(self,req):
        try: self._db.permitted_attempt(self._op(req))
        except Exception: raise CoordinationStopped('ATTEMPT unavailable') from None
        return self._receipt(req,'ATTEMPT')

    def commit_pending(self,req):
        try: self._db.pending(self._op(req))
        except Exception: raise CoordinationStopped('PENDING unavailable') from None
        return self._receipt(req,'PENDING')

    def commit_outcome(self,req,file_digest):
        try: self._success.successful_outcome(self._op(req),file_digest)
        except Exception: raise CoordinationStopped('OUTCOME unavailable') from None
        return self._receipt(req,'OUTCOME')

    def activate(self,req,file_digest):
        # True is internal to this composition, not an externally supplied receipt.
        # Cannot prove filesystem directory durability or secure identity.
        try: self._success.activate_verified(self._op(req),file_digest,file_is_verified=True)
        except Exception: raise CoordinationStopped('ACTIVE unavailable') from None
        return self._receipt(req,'ACTIVE')

    def commit_failure(self,req):
        try: self._db.failed_outcome(self._op(req))
        except Exception: raise CoordinationStopped('FAILED OUTCOME unavailable') from None
        return self._receipt(req,'OUTCOME_FAILED')


def compose_synthetic_create(pg_adapter, success_extension, encrypted_store, directory, synthetic_value_provider):
    bridge=PgSuccessBridge(pg_adapter,success_extension)
    file_step=SyntheticFileStep(encrypted_store,directory,synthetic_value_provider)
    return CreateCoordinator(bridge,bridge,file_step)
