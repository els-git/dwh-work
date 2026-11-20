"""Credential of a source from SecretStore for the ETL (I04-05).

The ETL role (dwh_etl) journals a service READ in secret_store
(etl.source_secret_read_begin(code, request_id) / _finish, packages 007 and 026), then decrypts the
encrypted file with the master key from the DPAPI keyring (I02-05) in this
process. The value is returned to the caller only; it is never printed,
logged or stored. Every failure stops the ETL step (fail closed).

I02-03 (package 026): the password is given only for a RUNNING load request of an object of this source
(E03-E08, E10). The request id comes from the dispatcher (DWH_RUN_OBJECT_REQUEST_ID of the child process);
a refusal is journaled by the database as a DENIED READ and reported here by its code.

Requires backend/src and backend/packages on sys.path (the local launcher and
tools/run_etl.ps1 add them).
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

from dwh.config import get_dwh_config
from dwh.db import connect

SECRET_DIRECTORY_ENV = "DWH_SECRET_STORE_DIR"
REQUEST_ENV = "DWH_RUN_OBJECT_REQUEST_ID"


class SourceSecretError(RuntimeError):
    """Sanitized failure: never contains the credential."""


def _message(exc: psycopg.Error) -> str:
    diag = getattr(exc, "diag", None)
    return (getattr(diag, "message_primary", None) or "database error").strip()


def read_source_credential(source_code: str, *, request_id: int | None = None, open_connection=None,
                           key_loader=None, store_factory=None) -> str:
    directory_value = os.environ.get(SECRET_DIRECTORY_ENV, "")
    if not directory_value:
        raise SourceSecretError("SecretStore is not configured (DWH_SECRET_STORE_DIR)")
    if request_id is None:
        value = os.environ.get(REQUEST_ENV, "").strip()
        if not value.isdigit():
            raise SourceSecretError("The source password is read only for a load request of the dispatcher "
                                    f"({REQUEST_ENV} is not set)")
        request_id = int(value)
    if key_loader is None or store_factory is None:
        from dwh_api.secret_file_store import EncryptedFileSecretStore
        from dwh_api.secret_keyring import load_key
        key_loader = key_loader or load_key
        store_factory = store_factory or EncryptedFileSecretStore
    conn = (open_connection or (lambda: connect(get_dwh_config())))()
    try:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT operation_id, secret_ref, key_id, refusal "
                            "FROM etl.source_secret_read_begin(%s, %s)", (source_code, request_id))
                operation_id, secret_ref, key_id, refusal = cur.fetchone()
            conn.commit()  # a refusal is a DENIED READ in the journal: it is kept
            if refusal:
                raise SourceSecretError(f"SecretStore READ refused for {source_code}, request {request_id}: {refusal}")
        except psycopg.Error as exc:
            conn.rollback()
            raise SourceSecretError(f"SecretStore READ refused for {source_code}: {_message(exc)}") from None
        try:
            store = store_factory(crypto=key_loader(key_id), directory=Path(directory_value))
            value = store.read(secret_ref=str(secret_ref))
            succeeded = True
        except Exception:
            value = None
            succeeded = False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT etl.source_secret_read_finish(%s, %s)", (operation_id, succeeded))
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            raise SourceSecretError(f"SecretStore READ could not be finished: {_message(exc)}") from None
        if not succeeded:
            raise SourceSecretError(f"Credential of {source_code} could not be decrypted (key {key_id})")
        return value
    finally:
        conn.close()
