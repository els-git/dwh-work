"""Explicit, interactive SecretStore administrative connection provider.

No API integration, unattended credentials, SQL schema changes, or implicit writes.
The privileged identity is reserved for a human-initiated operation. Writer
and readback connections are separate; the readback connection is forced read-only.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from getpass import getpass
from typing import Callable


class AdminConnectionStopped(Exception):
    """Safe error message; deliberately excludes database credentials and SQL."""


@dataclass(frozen=True)
class AdminTarget:
    host: str = '127.0.0.1'
    port: int = 5433
    dbname: str = 'dwh'
    user: str = 'dwh_admin'

    def validate(self):
        if (self.host != '127.0.0.1' or type(self.port) is not int
                or self.port != 5433 or self.dbname != 'dwh'
                or self.user != 'dwh_admin'):
            raise AdminConnectionStopped('Unapproved administrative target')


def _row_values(row):
    if isinstance(row, dict):
        return tuple(row.values())
    return tuple(row)


def _identity(conn, readonly):
    with conn.cursor() as cur:
        cur.execute('SHOW transaction_read_only')
        if _row_values(cur.fetchone())[0] != ('on' if readonly else 'off'):
            raise AdminConnectionStopped('Unexpected transaction mode')
        cur.execute('SELECT current_database(), current_user')
        if _row_values(cur.fetchone()) != ('dwh', 'dwh_admin'):
            raise AdminConnectionStopped('Unexpected administrative identity')


class InteractiveAdminConnections:
    """Supply PgDurableAdapter writer/readback factories to an approved operator.

    Password is entered once per human operation and remains only in this object;
    the operator MUST not log, serialize, expose or retain this object afterward.
    Factories do not execute any SecretStore writes themselves.
    """

    def __init__(self, target: AdminTarget, password: str, connector: Callable):
        target.validate()
        if not isinstance(password, str) or not password or not callable(connector):
            raise AdminConnectionStopped('Invalid administrative connection setup')
        self._target, self._password, self._connector = target, password, connector

    def _connect(self, *, readonly: bool):
        try:
            conn = self._connector(
                host=self._target.host, port=self._target.port,
                dbname=self._target.dbname, user=self._target.user,
                password=self._password, connect_timeout=5,
                options='-c default_transaction_read_only=on' if readonly else
                        '-c default_transaction_read_only=off',
            )
        except Exception:
            raise AdminConnectionStopped('Administrative connection refused') from None
        try:
            _identity(conn, readonly)
            return conn
        except Exception:
            try: conn.rollback()
            except Exception: pass
            try: conn.close()
            except Exception: pass
            raise AdminConnectionStopped('Administrative connection not verified') from None

    def writer(self):
        return self._connect(readonly=False)

    def reader(self):
        return self._connect(readonly=True)

    def verify_only(self):
        """Open and close independent sessions; no mutations."""
        for factory in (self.reader, self.writer):
            conn = factory()
            try:
                conn.rollback()
            finally:
                conn.close()


def run_preflight():
    """Manual operator command. Read-only inspection, no SecretStore DML."""
    try:
        import psycopg
        password = getpass('dwh_admin password (not echoed): ')
        provider = InteractiveAdminConnections(AdminTarget(), password, psycopg.connect)
        provider.verify_only()
    except Exception:
        raise AdminConnectionStopped('STOP: Administrative preflight failed') from None
    print('ADMIN_TARGET: dwh / dwh_admin')
    print('WRITER_IDENTITY: VERIFIED')
    print('INDEPENDENT_READONLY_SESSION: VERIFIED')
    print('SECRET_STORE_WRITES: NONE')
    print('RESULT: I02_03_ADMIN_PREFLIGHT_PASS')


if __name__ == '__main__':
    try:
        run_preflight()
    except AdminConnectionStopped as exc:
        raise SystemExit(str(exc)) from None
