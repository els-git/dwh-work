"""Isolated PG successful-outcome and activation extension.

No production wiring. Trusted verified file digest and user authorization MUST
be established outside this adapter; a string digest alone is not file evidence.
"""
from dataclasses import dataclass
import re
from dwh_api.secret_pg_durable_adapter import PgDurableAdapter, DbOperation, _check, PersistenceStopped

_DIGEST = re.compile(r"[a-f0-9]{64}\Z")

class PgSuccessExtension:
    def __init__(self, adapter: PgDurableAdapter):
        if type(adapter) is not PgDurableAdapter:
            raise PersistenceStopped('Invalid persistence adapter')
        self._db = adapter

    @staticmethod
    def _validate(op, digest):
        _check(op)
        if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
            raise PersistenceStopped('Invalid encrypted file digest')

    def successful_outcome(self, op: DbOperation, digest: str):
        self._validate(op, digest)
        # Caller must independently authenticate the file and readback checksum.
        self._db._write(
            'INSERT INTO secret_store.operation_event '
            '(operation_id,create_operation_id,source_system_id,phase,code) '
            "VALUES (%s,%s,%s,'OUTCOME','SUCCEEDED')",
            (op.operation_id,op.operation_id,op.source_system_id))
        self._db._verify(
            'SELECT count(*) FROM secret_store.operation_event '
            "WHERE operation_id=%s AND create_operation_id=%s AND source_system_id=%s "
            "AND phase='OUTCOME' AND code='SUCCEEDED'",
            (op.operation_id,op.operation_id,op.source_system_id))

    def activate_verified(self, op: DbOperation, digest: str, *, file_is_verified: bool):
        self._validate(op, digest)
        # Never accept truthy strings, unknown flags or fabricated receipts.
        if file_is_verified is not True:
            raise PersistenceStopped('Encrypted file verification required')
        # Conditional UPDATE ensures binding is PENDING for exactly this operation.
        # Rowcount=1 and subsequent independent readback are mandatory.
        conn = None
        try:
            conn = self._db._writer()
            with conn.cursor() as cur:
                cur.execute('SHOW transaction_read_only')
                if cur.fetchone()[0] != 'off':
                    raise PersistenceStopped('Writer not writable')
                cur.execute('SELECT current_database(), current_user')
                if tuple(cur.fetchone()) != ('dwh','dwh_admin'):
                    raise PersistenceStopped('Unexpected writer identity')
                cur.execute(
                    "UPDATE secret_store.binding SET state='ACTIVE', "
                    'activated_at=clock_timestamp(),ciphertext_sha256=%s '
                    "WHERE secret_ref=%s AND source_system_id=%s AND create_operation_id=%s "
                    "AND state='PENDING' AND EXISTS (SELECT 1 FROM secret_store.operation_event e "
                    "WHERE e.operation_id=%s AND e.source_system_id=%s "
                    "AND e.phase='OUTCOME' AND e.code='SUCCEEDED')",
                    (digest,op.secret_ref,op.source_system_id,op.operation_id,
                     op.operation_id,op.source_system_id))
                if cur.rowcount != 1:
                    raise PersistenceStopped('Activation preconditions not met')
            conn.commit()
        except Exception:
            if conn is not None:
                try: conn.rollback()
                except Exception: pass
            raise PersistenceStopped('Activation not confirmed; reconciliation required') from None
        finally:
            if conn is not None:
                try: conn.close()
                except Exception: pass
        self._db._verify(
            'SELECT count(*) FROM secret_store.binding '
            "WHERE secret_ref=%s AND source_system_id=%s AND create_operation_id=%s "
            "AND state='ACTIVE' AND ciphertext_sha256=%s AND activated_at IS NOT NULL",
            (op.secret_ref,op.source_system_id,op.operation_id,digest))
