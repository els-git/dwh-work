"""Isolated SecretStore DB adapter. No production wiring, no real credentials.
Each write opens a separate transaction; readback uses a distinct READ ONLY
connection. This is not filesystem or crash-durability evidence.
"""
from dataclasses import dataclass
from uuid import UUID

class PersistenceStopped(Exception):
    pass

@dataclass(frozen=True)
class DbOperation:
    operation_id: str
    secret_ref: str
    source_system_id: int

def _uuid(value):
    if type(value) is not str:
        raise PersistenceStopped('Invalid operation identifiers')
    try:
        u = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise PersistenceStopped('Invalid operation identifiers') from None
    if str(u) != value:
        raise PersistenceStopped('Invalid operation identifiers')
    return value

def _check(op):
    if not isinstance(op, DbOperation) or type(op.source_system_id) is not int or op.source_system_id <= 0:
        raise PersistenceStopped('Invalid trusted operation')
    _uuid(op.operation_id); _uuid(op.secret_ref)

def _close(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

class PgDurableAdapter:
    """Injected connection factories: writer is privileged; reader is read-only.
    Caller must authorize actor and trust operation ID before invoking. This adapter
    deliberately does not expose an ACTIVE transition or CREATE/REPLACE/READ API.
    """
    def __init__(self, writer_factory, readonly_factory):
        if not callable(writer_factory) or not callable(readonly_factory):
            raise PersistenceStopped('Invalid connection factories')
        self._writer = writer_factory
        self._reader = readonly_factory

    def _write(self, sql, args):
        conn = None
        try:
            conn = self._writer()
            with conn.cursor() as cur:
                cur.execute('SHOW transaction_read_only')
                if cur.fetchone()[0] != 'off':
                    raise PersistenceStopped('Writer not writable')
                cur.execute('SELECT current_database(), current_user')
                if tuple(cur.fetchone()) != ('dwh', 'dwh_admin'):
                    raise PersistenceStopped('Unexpected writer identity')
                cur.execute(sql, args)
            conn.commit()
        except Exception:
            if conn is not None:
                try: conn.rollback()
                except Exception: pass
            raise PersistenceStopped('Database write not confirmed') from None
        finally:
            _close(conn)

    def _verify(self, sql, args):
        conn = None
        try:
            conn = self._reader()
            with conn.cursor() as cur:
                cur.execute('SHOW transaction_read_only')
                if cur.fetchone()[0] != 'on':
                    raise PersistenceStopped('Reader not read-only')
                cur.execute('SELECT current_database()')
                if cur.fetchone()[0] != 'dwh':
                    raise PersistenceStopped('Unexpected database')
                cur.execute(sql, args)
                rows = cur.fetchall()
                if len(rows) != 1 or rows[0][0] != 1:
                    raise PersistenceStopped('Readback mismatch')
            conn.rollback()
        except Exception:
            if conn is not None:
                try: conn.rollback()
                except Exception: pass
            raise PersistenceStopped('Readback not confirmed') from None
        finally:
            _close(conn)

    def permitted_attempt(self, op):
        _check(op)
        self._write('INSERT INTO secret_store.operation_event '
                    '(operation_id,create_operation_id,source_system_id,phase,code) '
                    "VALUES (%s,%s,%s,'ATTEMPT','PERMITTED_ATTEMPT')",
                    (op.operation_id,op.operation_id,op.source_system_id))
        self._verify('SELECT count(*) FROM secret_store.operation_event '
                     "WHERE operation_id=%s AND source_system_id=%s AND phase='ATTEMPT' "
                     "AND code='PERMITTED_ATTEMPT'",(op.operation_id,op.source_system_id))

    def pending(self, op):
        _check(op)
        self._write('INSERT INTO secret_store.binding '
                    '(secret_ref,source_system_id,create_operation_id) VALUES (%s,%s,%s)',
                    (op.secret_ref,op.source_system_id,op.operation_id))
        self._verify('SELECT count(*) FROM secret_store.binding '
                     "WHERE secret_ref=%s AND source_system_id=%s AND create_operation_id=%s "
                     "AND state='PENDING'",(op.secret_ref,op.source_system_id,op.operation_id))

    def failed_outcome(self, op):
        _check(op)
        self._write('INSERT INTO secret_store.operation_event '
                    '(operation_id,create_operation_id,source_system_id,phase,code) '
                    "VALUES (%s,%s,%s,'OUTCOME','FAILED')",
                    (op.operation_id,op.operation_id,op.source_system_id))
        self._verify('SELECT count(*) FROM secret_store.operation_event '
                     "WHERE operation_id=%s AND source_system_id=%s AND phase='OUTCOME' "
                     "AND code='FAILED'",(op.operation_id,op.source_system_id))
