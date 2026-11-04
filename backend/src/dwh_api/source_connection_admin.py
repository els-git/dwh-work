"""Connection settings and the credential of a DATABASE/FILE source (I03).

All database work goes through the app.integration_source_connection_* and
app.integration_source_secret_* functions (package 005), which check the DWH
session, write the audit trail and journal every credential operation in
secret_store. The credential value is encrypted here and written to the
encrypted file store; it never reaches PostgreSQL, a response or a log.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import closing
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Body, HTTPException, Path as PathParam, Request
from pydantic import BaseModel, Field

from dwh_api.config import get_api_config
from dwh_api.db import connect_control
from dwh_api.integration_admin import (
    _db_message,
    _raise_database_error,
    _session_token_hash,
    _set_request_context,
)
from dwh_api.secret_file_store import EncryptedFileSecretStore, SecretFileStoreError
from dwh_api.connector_contract import ConnectorError, SecretValue
from dwh_api.postgres_connector import ADAPTER as POSTGRES_ADAPTER, DISCOVER_LIMIT, PostgresConnectorError
from dwh_api.secret_keyring import KeyringUnavailable, load_active_key, load_key


router = APIRouter(
    prefix="/api/admin/integrations",
    tags=["Administration - Integrations"],
)

config = get_api_config()

SECRET_DIRECTORY_ENV = "DWH_SECRET_STORE_DIR"
CREDENTIAL_MAX_LENGTH = 4096


# ============================================================
# Models
# ============================================================


class SourceConnection(BaseModel):
    source_system_id: int
    source_kind: str
    connection_config: dict[str, Any]
    status: str
    row_version: int
    created_at: str
    updated_at: str
    credential_state: str
    credential_key_id: str | None
    credential_activated_at: str | None
    last_test_at: str | None = None
    last_test_ok: bool | None = None
    last_test_code: str | None = None
    last_test_server_version: str | None = None


class SourceConnectionResponse(BaseModel):
    connection: SourceConnection | None


class SourceConnectionSaveRequest(BaseModel):
    source_kind: Literal["DATABASE", "FILE"]
    connection_config: dict[str, Any] = Field(default_factory=dict)
    expected_row_version: int | None = Field(default=None, ge=1)


class SourceConnectionStatusRequest(BaseModel):
    status: Literal["DRAFT", "DISABLED"]
    expected_row_version: int = Field(ge=1)


class RowVersionResponse(BaseModel):
    row_version: int


class CredentialResponse(BaseModel):
    credential_state: str
    credential_key_id: str | None


class ConnectionTestResponse(BaseModel):
    ok: bool
    code: str
    message: str
    status: str
    row_version: int
    server_version: str | None


class SourceObjectItem(BaseModel):
    schema_name: str
    name: str
    object_type: str


class SourceObjectsResponse(BaseModel):
    objects: list[SourceObjectItem]
    truncated: bool


class SnapshotResponse(BaseModel):
    metadata_capture_id: int
    objects_captured: int
    columns_captured: int
    server_version: str


# ============================================================
# Errors
# ============================================================


class CredentialStorageUnavailable(Exception):
    """Credential storage is not configured; nothing was attempted."""


_CONFLICT_MESSAGES = (
    "changed by another user",
    "in progress",
)
_REJECTED_MESSAGES = (
    "must not contain secrets",
    "api sources keep",
    "requires a successful connection test",
    "save the connection settings",
    "is disabled",
    "has no active credential",
    "has no key id",
    "immutable",
)


def _raise_connection_error(exc: psycopg.Error) -> None:
    lowered = _db_message(exc).lower()
    if any(text in lowered for text in _CONFLICT_MESSAGES):
        raise HTTPException(status_code=409, detail=_db_message(exc)) from exc
    if any(text in lowered for text in _REJECTED_MESSAGES):
        raise HTTPException(status_code=422, detail=_db_message(exc)) from exc
    _raise_database_error(exc)


# ============================================================
# Credential storage
# ============================================================


def credential_storage() -> tuple[EncryptedFileSecretStore, Path, str]:
    """Encrypted file store, its directory and the id of the active key.

    The active master key comes from the DPAPI keyring (I02-05), never from
    .env. Fails closed before any database or file operation when the
    secret directory, the keyring or the active key is unavailable (K04).
    """
    directory_value = os.environ.get(SECRET_DIRECTORY_ENV, "")
    if not directory_value:
        raise CredentialStorageUnavailable()
    directory = Path(directory_value)
    try:
        crypto, key_id = load_active_key()
        store = EncryptedFileSecretStore(crypto=crypto, directory=directory)
    except (KeyringUnavailable, SecretFileStoreError):
        raise CredentialStorageUnavailable() from None
    return store, directory, key_id


def _credential_value(payload: Any) -> str:
    # Validated by hand: FastAPI validation errors would echo the input value.
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value or len(value) > CREDENTIAL_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Credential value must be a string of 1 to {CREDENTIAL_MAX_LENGTH} characters",
        )
    return value


def store_source_credential(
    connect,
    token_hash: str,
    request_id: str,
    source_system_id: int,
    value: str,
    store: EncryptedFileSecretStore,
    directory: Path,
    key_id: str,
) -> CredentialResponse:
    """CREATE or REPLACE the source credential.

    1. secret_begin commits ATTEMPT + PENDING (journal before any file).
    2. The value is encrypted into <secret_ref>.bin (never overwritten).
    3. secret_complete commits OUTCOME and activates the binding.
    A file failure is recorded as a FAILED outcome. If step 3 cannot be
    recorded after the file exists, the binding stays PENDING and needs
    reconciliation; the operation is not retried automatically.
    """
    with closing(connect(config)) as conn:
        try:
            with conn.cursor() as cur:
                _set_request_context(cur, request_id)
                cur.execute(
                    "SELECT operation_id, secret_ref FROM app.integration_source_secret_begin(%s, %s)",
                    (token_hash, source_system_id),
                )
                row = cur.fetchone()
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            _raise_connection_error(exc)
        operation_id = str(row["operation_id"])
        secret_ref = str(row["secret_ref"])

        try:
            store.create(secret_ref=secret_ref, value=value)
            digest = hashlib.sha256((directory / f"{secret_ref}.bin").read_bytes()).hexdigest()
        except (SecretFileStoreError, OSError):
            _finish(conn, token_hash, request_id, source_system_id, operation_id, False, None, None)
            raise HTTPException(status_code=500, detail="Credential could not be stored") from None

        try:
            _finish(conn, token_hash, request_id, source_system_id, operation_id, True, digest, key_id)
        except HTTPException:
            raise HTTPException(
                status_code=500,
                detail="Credential stored but not activated; reconciliation required",
            ) from None
    return CredentialResponse(credential_state="ACTIVE", credential_key_id=key_id)


def _finish(conn, token_hash, request_id, source_system_id, operation_id, succeeded, digest, key_id) -> None:
    try:
        with conn.cursor() as cur:
            _set_request_context(cur, request_id)
            cur.execute(
                "SELECT app.integration_source_secret_complete(%s, %s, %s, %s, %s, %s) AS state",
                (token_hash, source_system_id, operation_id, succeeded, digest, key_id),
            )
            cur.fetchone()
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        _raise_connection_error(exc)


# ============================================================
# Endpoints
# ============================================================


def _call(request: Request, sql: str, params: tuple) -> dict | None:
    token_hash = _session_token_hash(request)
    with closing(connect_control(config)) as conn:
        try:
            with conn.cursor() as cur:
                _set_request_context(cur, str(uuid4()))
                cur.execute(sql, (token_hash, *params))
                row = cur.fetchone()
            conn.commit()
            return row
        except psycopg.Error as exc:
            conn.rollback()
            _raise_connection_error(exc)


@router.get(
    "/source-systems/{source_system_id}/connection",
    response_model=SourceConnectionResponse,
)
def get_source_connection(
    request: Request,
    source_system_id: int = PathParam(ge=1),
) -> SourceConnectionResponse:
    row = _call(
        request,
        "SELECT * FROM app.integration_source_connection_get(%s, %s)",
        (source_system_id,),
    )
    if row is None:
        return SourceConnectionResponse(connection=None)
    return SourceConnectionResponse(
        connection=SourceConnection(
            source_system_id=row["source_system_id"],
            source_kind=row["source_kind"],
            connection_config=row["connection_config"] or {},
            status=row["status"],
            row_version=row["row_version"],
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
            credential_state=row["secret_state"],
            credential_key_id=row["secret_key_id"],
            credential_activated_at=(
                row["secret_activated_at"].isoformat() if row["secret_activated_at"] else None
            ),
            last_test_at=row["last_test_at"].isoformat() if row.get("last_test_at") else None,
            last_test_ok=row.get("last_test_ok"),
            last_test_code=row.get("last_test_code"),
            last_test_server_version=row.get("last_test_server_version"),
        )
    )


@router.put(
    "/source-systems/{source_system_id}/connection",
    response_model=RowVersionResponse,
)
def save_source_connection(
    payload: SourceConnectionSaveRequest,
    request: Request,
    source_system_id: int = PathParam(ge=1),
) -> RowVersionResponse:
    row = _call(
        request,
        "SELECT app.integration_source_connection_save(%s, %s, %s, %s::jsonb, %s) AS row_version",
        (
            source_system_id,
            payload.source_kind,
            Jsonb(payload.connection_config),
            payload.expected_row_version,
        ),
    )
    return RowVersionResponse(row_version=row["row_version"])


@router.post(
    "/source-systems/{source_system_id}/connection/status",
    response_model=RowVersionResponse,
)
def set_source_connection_status(
    payload: SourceConnectionStatusRequest,
    request: Request,
    source_system_id: int = PathParam(ge=1),
) -> RowVersionResponse:
    row = _call(
        request,
        "SELECT app.integration_source_connection_set_status(%s, %s, %s, %s) AS row_version",
        (source_system_id, payload.expected_row_version, payload.status),
    )
    return RowVersionResponse(row_version=row["row_version"])


@router.put(
    "/source-systems/{source_system_id}/connection/credential",
    response_model=CredentialResponse,
)
def set_source_credential(
    request: Request,
    source_system_id: int = PathParam(ge=1),
    payload: Any = Body(...),
) -> CredentialResponse:
    value = _credential_value(payload)
    token_hash = _session_token_hash(request)
    try:
        store, directory, key_id = credential_storage()
    except CredentialStorageUnavailable:
        raise HTTPException(status_code=503, detail="Credential storage is not configured on the server") from None
    return store_source_credential(
        connect_control, token_hash, str(uuid4()), source_system_id, value, store, directory, key_id
    )


@router.delete(
    "/source-systems/{source_system_id}/connection/credential",
    response_model=CredentialResponse,
)
def revoke_source_credential(
    request: Request,
    source_system_id: int = PathParam(ge=1),
) -> CredentialResponse:
    _call(
        request,
        "SELECT app.integration_source_secret_revoke(%s, %s) AS revoked",
        (source_system_id,),
    )
    return CredentialResponse(credential_state="NONE", credential_key_id=None)


# ============================================================
# I04: connection test, source objects, schema snapshot
# ============================================================


def _execute(conn, request_id: str, sql: str, params: tuple):
    """One statement in its own committed transaction; DB errors mapped to HTTP."""
    try:
        with conn.cursor() as cur:
            _set_request_context(cur, request_id)
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    except psycopg.Error as exc:
        conn.rollback()
        _raise_connection_error(exc)


def _secret_directory() -> Path:
    value = os.environ.get(SECRET_DIRECTORY_ENV, "")
    if not value:
        raise HTTPException(status_code=503, detail="Credential storage is not configured on the server")
    return Path(value)


def read_source_credential(conn, token_hash: str, request_id: str, source_system_id: int,
                           key_loader=load_key) -> tuple[dict, int, SecretValue]:
    """Settings, row_version and the decrypted credential of a PostgreSQL source.

    The session is checked by the database first; the credential READ is
    journaled in secret_store (ATTEMPT before decrypting, OUTCOME after).
    """
    rows = _execute(conn, request_id,
                    "SELECT * FROM app.integration_source_connection_get(%s, %s)", (token_hash, source_system_id))
    if not rows:
        raise HTTPException(status_code=422, detail="Save the connection settings of the source first")
    connection = rows[0]
    source = _execute(conn, request_id,
                      "SELECT source_type FROM meta.source_system WHERE source_system_id = %s", (source_system_id,))
    if not source or str(source[0]["source_type"]).strip().upper() != "POSTGRESQL" or connection["source_kind"] != "DATABASE":
        raise HTTPException(status_code=422, detail="Connection test is available for PostgreSQL sources")
    directory = _secret_directory()
    (read,) = _execute(conn, request_id,
                       "SELECT * FROM app.integration_source_secret_read_begin(%s, %s)", (token_hash, source_system_id))
    try:
        store = EncryptedFileSecretStore(crypto=key_loader(read["key_id"]), directory=directory)
        credential = SecretValue(store.read(secret_ref=str(read["secret_ref"])))
    except (KeyringUnavailable, SecretFileStoreError, ConnectorError, OSError):
        _execute(conn, request_id, "SELECT app.integration_source_secret_read_finish(%s, %s, %s, false) AS ok",
                 (token_hash, source_system_id, read["operation_id"]))
        raise HTTPException(status_code=503, detail="Credential could not be read on the server") from None
    _execute(conn, request_id, "SELECT app.integration_source_secret_read_finish(%s, %s, %s, true) AS ok",
             (token_hash, source_system_id, read["operation_id"]))
    return connection["connection_config"] or {}, read["row_version"], credential


def test_source_connection(conn, token_hash: str, request_id: str, source_system_id: int,
                           adapter=POSTGRES_ADAPTER, key_loader=load_key) -> ConnectionTestResponse:
    config_, row_version, credential = read_source_credential(conn, token_hash, request_id, source_system_id, key_loader)
    result = adapter.test_connection(config_, credential)
    del credential
    (row,) = _execute(conn, request_id,
                      "SELECT * FROM app.integration_source_connection_record_test(%s, %s, %s, %s, %s, %s)",
                      (token_hash, source_system_id, row_version, result.ok, result.code, result.server_version))
    return ConnectionTestResponse(ok=result.ok, code=result.code, message=result.message, status=row["status"],
                                  row_version=row["row_version"], server_version=result.server_version)


def list_source_objects(conn, token_hash: str, request_id: str, source_system_id: int,
                        adapter=POSTGRES_ADAPTER, key_loader=load_key) -> SourceObjectsResponse:
    config_, _, credential = read_source_credential(conn, token_hash, request_id, source_system_id, key_loader)
    try:
        found = adapter.discover(config_, credential, limit=DISCOVER_LIMIT + 1)
    except PostgresConnectorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    finally:
        del credential
    return SourceObjectsResponse(
        objects=[SourceObjectItem(schema_name=o.schema, name=o.name, object_type=o.object_type)
                 for o in found[:DISCOVER_LIMIT]],
        truncated=len(found) > DISCOVER_LIMIT,
    )


def capture_source_snapshot(conn, token_hash: str, request_id: str, source_system_id: int,
                            adapter=POSTGRES_ADAPTER, key_loader=load_key) -> SnapshotResponse:
    config_, _, credential = read_source_credential(conn, token_hash, request_id, source_system_id, key_loader)
    registered = _execute(conn, request_id,
                          "SELECT source_object_id, source_schema, source_object FROM meta.source_object"
                          " WHERE source_system_id = %s AND is_active ORDER BY source_object_id", (source_system_id,))
    if not registered:
        del credential
        raise HTTPException(status_code=422, detail="Register at least one active object of the source first")
    try:
        version, found = adapter.describe(config_, credential,
                                          [(r["source_schema"], r["source_object"]) for r in registered])
    except PostgresConnectorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    finally:
        del credential
    missing = [f'{r["source_schema"]}.{r["source_object"]}' for r in registered
               if (r["source_schema"], r["source_object"]) not in found]
    if missing:
        raise HTTPException(status_code=422, detail="Objects not found in the source: " + ", ".join(missing[:20]))
    payload = [
        {"source_object_id": r["source_object_id"], **found[(r["source_schema"], r["source_object"])]}
        for r in registered
    ]
    (row,) = _execute(conn, request_id,
                      "SELECT * FROM app.integration_source_metadata_capture(%s, %s, %s, %s, %s::jsonb)",
                      (token_hash, source_system_id, config_.get("database"), version, Jsonb(payload)))
    return SnapshotResponse(metadata_capture_id=row["metadata_capture_id"], objects_captured=row["objects_captured"],
                            columns_captured=row["columns_captured"], server_version=version)


@router.post(
    "/source-systems/{source_system_id}/connection/test",
    response_model=ConnectionTestResponse,
)
def post_source_connection_test(
    request: Request,
    source_system_id: int = PathParam(ge=1),
) -> ConnectionTestResponse:
    token_hash = _session_token_hash(request)
    with closing(connect_control(config)) as conn:
        return test_source_connection(conn, token_hash, str(uuid4()), source_system_id)


@router.get(
    "/source-systems/{source_system_id}/connection/objects",
    response_model=SourceObjectsResponse,
)
def get_source_objects(
    request: Request,
    source_system_id: int = PathParam(ge=1),
) -> SourceObjectsResponse:
    token_hash = _session_token_hash(request)
    with closing(connect_control(config)) as conn:
        return list_source_objects(conn, token_hash, str(uuid4()), source_system_id)


@router.post(
    "/source-systems/{source_system_id}/connection/snapshot",
    response_model=SnapshotResponse,
)
def post_source_snapshot(
    request: Request,
    source_system_id: int = PathParam(ge=1),
) -> SnapshotResponse:
    token_hash = _session_token_hash(request)
    with closing(connect_control(config)) as conn:
        return capture_source_snapshot(conn, token_hash, str(uuid4()), source_system_id)

