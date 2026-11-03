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
import re
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
from dwh_api.secret_key_provider import SecretKeyProviderError, load_crypto_from_environment


router = APIRouter(
    prefix="/api/admin/integrations",
    tags=["Administration - Integrations"],
)

config = get_api_config()

SECRET_DIRECTORY_ENV = "DWH_SECRET_STORE_DIR"
MASTER_KEY_ID_ENV = "DWH_CONNECTOR_MASTER_KEY_ID"
CREDENTIAL_MAX_LENGTH = 4096
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


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
    """Encrypted file store, its directory and the non-secret master-key id.

    Fails closed before any database or file operation when the server has
    no master key, no secret directory or no key id configured.
    """
    directory_value = os.environ.get(SECRET_DIRECTORY_ENV, "")
    key_id = os.environ.get(MASTER_KEY_ID_ENV, "")
    if not directory_value or not _KEY_ID_RE.fullmatch(key_id):
        raise CredentialStorageUnavailable()
    directory = Path(directory_value)
    try:
        store = EncryptedFileSecretStore(crypto=load_crypto_from_environment(), directory=directory)
    except (SecretKeyProviderError, SecretFileStoreError):
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
