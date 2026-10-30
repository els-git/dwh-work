"""Isolated PostgreSQL read-back verifier for SecretStore.

Read-only, independent connection; no write/activation/secret access.
A visible record is evidence of PostgreSQL visibility, not proof of the
writer's connection/transaction provenance, independent audit COMMIT,
or durability configuration (fsync/WAL/synchronous_commit).
"""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Callable, Mapping
import re
from uuid import UUID

class ReadbackDenied(Exception):
    """Sanitized read-back failure."""

@dataclass(frozen=True, slots=True)
class Readback:
    kind: str
    operation_id: str
    source_system_id: int
    state_or_code: str
    ciphertext_sha256: str | None = None

_ALLOWED = {('ATTEMPT','PERMITTED_ATTEMPT'), ('ATTEMPT','DENIED_ATTEMPT'),
            ('OUTCOME','SUCCEEDED'), ('OUTCOME','FAILED')}
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')

def _uuid(x: object) -> str:
    if type(x) is not str:
        raise ReadbackDenied('Unverified persisted evidence')
    try:
        u=UUID(x)
    except (ValueError, AttributeError):
        raise ReadbackDenied('Unverified persisted evidence') from None
    if str(u) != x:
        raise ReadbackDenied('Unverified persisted evidence')
    return x

def _row(r):
    if r is None: return None
    return tuple(r.values()) if isinstance(r, Mapping) else tuple(r)

def _read(connection_factory: Callable, sql: str, params: tuple):
    conn=None
    try:
        conn=connection_factory()
        if conn is None: raise ReadbackDenied('Unverified persisted evidence')
        with conn.cursor() as cur:
            cur.execute('SHOW transaction_read_only')
            result=_row(cur.fetchone())
            if result != ('on',):
                raise ReadbackDenied('Unverified persisted evidence')
            cur.execute(sql,params)
            first=_row(cur.fetchone())
            if cur.fetchone() is not None:
                raise ReadbackDenied('Unverified persisted evidence')
            return first
    except Exception:
        raise ReadbackDenied('Unverified persisted evidence') from None
    finally:
        if conn is not None:
            try: conn.rollback()
            finally: conn.close()

def verify_event(connection_factory: Callable, operation_id: str, source_system_id: int,
                 phase: str, code: str) -> Readback:
    """Fresh transaction read-back of one event in secret_store.operation_event."""
    op=_uuid(operation_id)
    if type(source_system_id) is not int or source_system_id <= 0 or (phase,code) not in _ALLOWED:
        raise ReadbackDenied('Unverified persisted evidence')
    sql=('SELECT operation_id::text, create_operation_id::text, source_system_id, phase, code '
         'FROM secret_store.operation_event '
         'WHERE operation_id = %s::uuid AND phase = %s AND source_system_id = %s')
    r=_read(connection_factory,sql,(op,phase,source_system_id))
    if r != (op,op,source_system_id,phase,code):
        raise ReadbackDenied('Unverified persisted evidence')
    return Readback(phase,op,source_system_id,code)

def verify_binding(connection_factory: Callable, secret_ref: str, operation_id: str,
                   source_system_id: int, state: str, ciphertext_sha256: str | None = None) -> Readback:
    """Fresh transaction read-back of the binding; not an activation authorization."""
    ref=_uuid(secret_ref);op=_uuid(operation_id)
    if (type(source_system_id) is not int or source_system_id <= 0
            or state not in ('PENDING','ACTIVE','REVOKED')
            or (ciphertext_sha256 is not None and
                (type(ciphertext_sha256) is not str or _DIGEST.fullmatch(ciphertext_sha256) is None))):
        raise ReadbackDenied('Unverified persisted evidence')
    sql=('SELECT secret_ref::text, create_operation_id::text, source_system_id, '
         'state, ciphertext_sha256 FROM secret_store.binding WHERE secret_ref = %s::uuid')
    r=_read(connection_factory,sql,(ref,))
    if r != (ref,op,source_system_id,state,ciphertext_sha256):
        raise ReadbackDenied('Unverified persisted evidence')
    return Readback('BINDING',op,source_system_id,state,ciphertext_sha256)
