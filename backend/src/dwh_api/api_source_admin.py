"""I11: administration of API objects (package 033).

  POST   /api/admin/integrations/source-systems/{id}/api-objects             new API object (request code)
  GET    /api/admin/integrations/source-objects/{id}/api-request             draft and published request, credential state,
                                                                             cURL with *** instead of the credential
  PUT    /api/admin/integrations/source-objects/{id}/api-request             save the request into the draft
  POST   /api/admin/integrations/source-objects/{id}/api-request/import-curl cURL -> request (draft) + credential (SecretStore)
  PUT    /api/admin/integrations/source-objects/{id}/api-request/credential  set / replace the credential
  DELETE /api/admin/integrations/source-objects/{id}/api-request/credential  revoke it
  POST   /api/admin/integrations/source-objects/{id}/api-request/probe       first page: number of records and columns
                                                                             with suggested types (no values)
  POST   /api/admin/integrations/source-objects/{id}/api-request/structure   save the columns as the object's snapshot

The database checks the session (app.integration_admin_session_user). The credential is never returned.
A draft is created from the published version when the object has none (the mapping editor shows it).
"""

from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Body, HTTPException, Path as PathParam, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from dwh_api import api_request as ar
from dwh_api import http_gateway
from dwh_api.config import get_api_config
from dwh_api.db import connect_control
from dwh_api.integration_admin import _db_message, _raise_database_error, _session_token_hash, _set_request_context
from dwh_api.object_config_admin import ObjectConfigDocument, read_config, strategy_of
from dwh_api.secret_file_store import EncryptedFileSecretStore, SecretFileStoreError
from dwh_api.secret_keyring import KeyringUnavailable, load_key

router = APIRouter(prefix="/api/admin/integrations", tags=["Administration - Integrations"])
config = get_api_config()

STRUCTURE_TYPES = ("text", "bigint", "integer", "numeric", "boolean", "date", "timestamp with time zone",
                   "timestamp without time zone", "jsonb")
CREDENTIAL_MAX_LENGTH = 4000

_REJECTED = ("not an api", "api source", "request code", "already used", "no draft", "must not contain credentials",
             "invalid http request", "no active credential", "has no key id", "immutable", "not active",
             "structure must", "column needs", "not found")
_CONFLICT = ("in progress", "changed by another user")


def _raise(exc: psycopg.Error) -> None:
    lowered = _db_message(exc).lower()
    if any(t in lowered for t in _CONFLICT):
        raise HTTPException(status_code=409, detail=_db_message(exc)) from exc
    if any(t in lowered for t in _REJECTED):
        raise HTTPException(status_code=422, detail=_db_message(exc)) from exc
    if getattr(exc, "sqlstate", None) == "23514":
        raise HTTPException(status_code=422, detail="HTTP request is not valid (address or credential)") from exc
    _raise_database_error(exc)


def _execute(conn, request_id: str, sql: str, params: tuple) -> list[dict]:
    try:
        with conn.cursor() as cur:
            _set_request_context(cur, request_id)
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    except psycopg.Error as exc:
        conn.rollback()
        _raise(exc)


def _api_error(exc: ar.ApiRequestError) -> HTTPException:
    return HTTPException(status_code=422, detail=f"{exc.code}: {exc.message}")


# ------------------------------------------------------------------ models


class ApiObjectCreate(BaseModel):
    code: str = Field(min_length=1, max_length=63)
    description: str | None = Field(default=None, max_length=4000)


class ApiObjectCreated(BaseModel):
    source_object_id: int


class ApiRequestState(BaseModel):
    draft_version_id: int | None
    draft_request: dict[str, Any] | None
    published_version_id: int | None
    published_request: dict[str, Any] | None
    has_credential: bool
    credential_key_id: str | None
    curl: str | None
    allowed_hosts: list[str]


class ApiRequestSave(BaseModel):
    request: dict[str, Any]


class CurlImportBody(BaseModel):
    curl: str = Field(min_length=4, max_length=20000)


class CurlImportResult(BaseModel):
    request: dict[str, Any]
    credential_stored: bool
    notes: list[str]


class ProbeBody(BaseModel):
    request: dict[str, Any] | None = None


class ProbeColumn(BaseModel):
    name: str
    type: str
    nullable: bool


class ProbeResult(BaseModel):
    records: int
    more_pages: bool
    columns: list[ProbeColumn]
    type_choices: list[str]


class StructureColumn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: Literal["text", "bigint", "integer", "numeric", "boolean", "date", "timestamp with time zone",
                  "timestamp without time zone", "jsonb"]
    nullable: bool = True


class StructureBody(BaseModel):
    columns: list[StructureColumn] = Field(min_length=1, max_length=500)


class StructureResult(BaseModel):
    metadata_capture_id: int
    columns_captured: int


class CredentialState(BaseModel):
    has_credential: bool
    credential_key_id: str | None = None


# ------------------------------------------------------------------ operations


def request_state(conn, token_hash: str, request_id: str, source_object_id: int) -> dict:
    (row,) = _execute(conn, request_id, "SELECT * FROM app.integration_api_request_get(%s, %s)",
                      (token_hash, source_object_id))
    return row


def ensure_draft(conn, token_hash: str, request_id: str, source_object_id: int) -> None:
    """The request lives in the draft; without a draft one is made from the published version (same fields)."""
    cfg = read_config(conn, token_hash, request_id, source_object_id)
    if cfg.get("draft"):
        return
    current = (cfg.get("current") or {}).get("definition") or {}
    document = ObjectConfigDocument.model_validate({
        "target_schema": current.get("target_schema"), "target_table": current.get("target_table"),
        "fields": current.get("fields") or [], "key": current.get("key") or [], **strategy_of(current)}).model_dump()
    _execute(conn, request_id, "SELECT app.integration_object_config_save_draft(%s, %s, %s, %s::jsonb) AS revision",
             (token_hash, source_object_id, None, Jsonb(document)))


def save_request(conn, token_hash: str, request_id: str, source_object_id: int, request: dict) -> dict:
    try:
        doc = ar.normalize(request, draft=True)
    except ar.ApiRequestError as exc:
        raise _api_error(exc) from None
    ensure_draft(conn, token_hash, request_id, source_object_id)
    _execute(conn, request_id, "SELECT app.integration_api_request_save(%s, %s, %s::jsonb) AS version_id",
             (token_hash, source_object_id, Jsonb(doc)))
    return doc


def _storage() -> tuple[EncryptedFileSecretStore, Path, str]:
    from dwh_api.source_connection_admin import CredentialStorageUnavailable, credential_storage

    try:
        return credential_storage()
    except CredentialStorageUnavailable:
        raise HTTPException(status_code=503, detail="Credential storage is not configured on the server") from None


def store_credential(conn, token_hash: str, request_id: str, source_object_id: int, value: str,
                     storage=None) -> str:
    """CREATE/REPLACE as for a source: ATTEMPT + PENDING, encrypted file, OUTCOME + ACTIVE."""
    store, directory, key_id = storage or _storage()
    (row,) = _execute(conn, request_id, "SELECT operation_id, secret_ref FROM app.integration_object_secret_begin(%s, %s)",
                      (token_hash, source_object_id))
    operation_id, secret_ref = str(row["operation_id"]), str(row["secret_ref"])
    try:
        store.create(secret_ref=secret_ref, value=value)
        digest = hashlib.sha256((directory / f"{secret_ref}.bin").read_bytes()).hexdigest()
    except (SecretFileStoreError, OSError):
        _execute(conn, request_id, "SELECT app.integration_object_secret_complete(%s, %s, %s, false) AS state",
                 (token_hash, source_object_id, operation_id))
        raise HTTPException(status_code=500, detail="Credential could not be stored") from None
    try:
        _execute(conn, request_id, "SELECT app.integration_object_secret_complete(%s, %s, %s, true, %s, %s) AS state",
                 (token_hash, source_object_id, operation_id, digest, key_id))
    except HTTPException:
        raise HTTPException(status_code=500, detail="Credential stored but not activated; reconciliation required") from None
    return key_id


def read_credential(conn, token_hash: str, request_id: str, source_object_id: int, key_loader=None,
                    directory: Path | None = None) -> str:
    key_loader = key_loader or load_key
    if directory is None:
        from dwh_api.source_connection_admin import _secret_directory
        directory = _secret_directory()
    (read,) = _execute(conn, request_id, "SELECT * FROM app.integration_object_secret_read_begin(%s, %s)",
                       (token_hash, source_object_id))
    try:
        value = EncryptedFileSecretStore(crypto=key_loader(read["key_id"]), directory=directory).read(
            secret_ref=str(read["secret_ref"]))
    except (KeyringUnavailable, SecretFileStoreError, OSError):
        _execute(conn, request_id, "SELECT app.integration_object_secret_read_finish(%s, %s, %s, false) AS ok",
                 (token_hash, source_object_id, read["operation_id"]))
        raise HTTPException(status_code=503, detail="Credential could not be read on the server") from None
    _execute(conn, request_id, "SELECT app.integration_object_secret_read_finish(%s, %s, %s, true) AS ok",
             (token_hash, source_object_id, read["operation_id"]))
    return value


def probe(conn, token_hash: str, request_id: str, source_object_id: int, request: dict | None,
          get=None, credential_reader=None) -> ProbeResult:
    get = get or http_gateway.get
    credential_reader = credential_reader or read_credential
    state = request_state(conn, token_hash, request_id, source_object_id)
    raw = request or state["draft_request"] or state["published_request"]
    if not raw:
        raise HTTPException(status_code=422, detail="Save the HTTP request first")
    try:
        doc = ar.normalize(raw)
    except ar.ApiRequestError as exc:
        raise _api_error(exc) from None
    secret = None
    if doc["auth"]["kind"] != "NONE":
        if not state["has_credential"]:
            raise HTTPException(status_code=422, detail="API_CREDENTIAL_MISSING: set the credential of the object first")
        secret = credential_reader(conn, token_hash, request_id, source_object_id)
    records: list[dict] = []
    more = False
    try:
        pages = ar.read_pages(doc, secret, get=get, max_pages=1 if doc["pagination"]["kind"] == "NONE" else 2)
        records = next(pages)
        try:
            more = bool(next(pages))
        except StopIteration:
            more = False
        except ar.ApiRequestError as exc:
            if exc.code != "API_TOO_MANY_PAGES":
                raise
            more = True
    except ar.ApiRequestError as exc:
        raise _api_error(exc) from None
    finally:
        secret = None
    columns = ar.infer_columns(records)
    return ProbeResult(records=len(records), more_pages=more, columns=[ProbeColumn(**c) for c in columns],
                       type_choices=list(STRUCTURE_TYPES))


def _connect_and(request: Request, operation, *args):
    token_hash = _session_token_hash(request)
    with closing(connect_control(config)) as conn:
        return operation(conn, token_hash, str(uuid4()), *args)


# ------------------------------------------------------------------ routes


@router.post("/source-systems/{source_system_id}/api-objects", response_model=ApiObjectCreated)
def post_api_object(request: Request, payload: ApiObjectCreate, source_system_id: int = PathParam(ge=1)) -> ApiObjectCreated:
    def op(conn, token_hash, request_id):
        (row,) = _execute(conn, request_id, "SELECT app.integration_api_object_create(%s, %s, %s, %s) AS id",
                          (token_hash, source_system_id, payload.code, payload.description))
        return ApiObjectCreated(source_object_id=row["id"])
    return _connect_and(request, op)


@router.get("/source-objects/{source_object_id}/api-request", response_model=ApiRequestState)
def get_api_request(request: Request, source_object_id: int = PathParam(ge=1)) -> ApiRequestState:
    def op(conn, token_hash, request_id):
        row = request_state(conn, token_hash, request_id, source_object_id)
        shown = row["draft_request"] or row["published_request"]
        try:
            curl = ar.export_curl(shown) if shown else None
        except ar.ApiRequestError:
            curl = None
        return ApiRequestState(draft_version_id=row["draft_version_id"], draft_request=row["draft_request"],
                               published_version_id=row["published_version_id"],
                               published_request=row["published_request"], has_credential=row["has_credential"],
                               credential_key_id=row["credential_key_id"], curl=curl,
                               allowed_hosts=sorted(http_gateway.GatewaySettings.from_env().allowed_hosts))
    return _connect_and(request, op)


@router.put("/source-objects/{source_object_id}/api-request", response_model=ApiRequestSave)
def put_api_request(request: Request, payload: ApiRequestSave, source_object_id: int = PathParam(ge=1)) -> ApiRequestSave:
    return _connect_and(request, lambda c, t, r: ApiRequestSave(request=save_request(c, t, r, source_object_id,
                                                                                      payload.request)))


@router.post("/source-objects/{source_object_id}/api-request/import-curl", response_model=CurlImportResult)
def post_import_curl(request: Request, payload: CurlImportBody, source_object_id: int = PathParam(ge=1)) -> CurlImportResult:
    try:
        imported = ar.import_curl(payload.curl)
    except ar.ApiRequestError as exc:
        raise _api_error(exc) from None
    storage = _storage() if imported.secret else None

    def op(conn, token_hash, request_id):
        doc = save_request(conn, token_hash, request_id, source_object_id, imported.request)
        stored = False
        if imported.secret:
            store_credential(conn, token_hash, request_id, source_object_id, imported.secret, storage)
            stored = True
        return CurlImportResult(request=doc, credential_stored=stored, notes=imported.notes)
    try:
        return _connect_and(request, op)
    finally:
        imported.secret = None


@router.put("/source-objects/{source_object_id}/api-request/credential", response_model=CredentialState)
def put_api_credential(request: Request, payload: Any = Body(...), source_object_id: int = PathParam(ge=1)) -> CredentialState:
    # validated by hand: FastAPI validation errors would echo the value
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value or len(value) > CREDENTIAL_MAX_LENGTH:
        raise HTTPException(status_code=422, detail=f"Credential must be 1 to {CREDENTIAL_MAX_LENGTH} characters")
    storage = _storage()
    key_id = _connect_and(request, lambda c, t, r: store_credential(c, t, r, source_object_id, value, storage))
    return CredentialState(has_credential=True, credential_key_id=key_id)


@router.delete("/source-objects/{source_object_id}/api-request/credential", response_model=CredentialState)
def delete_api_credential(request: Request, source_object_id: int = PathParam(ge=1)) -> CredentialState:
    _connect_and(request, lambda c, t, r: _execute(c, r, "SELECT app.integration_object_secret_revoke(%s, %s) AS ok",
                                                    (t, source_object_id)))
    return CredentialState(has_credential=False)


@router.post("/source-objects/{source_object_id}/api-request/probe", response_model=ProbeResult)
def post_api_probe(request: Request, payload: ProbeBody, source_object_id: int = PathParam(ge=1)) -> ProbeResult:
    return _connect_and(request, lambda c, t, r: probe(c, t, r, source_object_id, payload.request))


@router.post("/source-objects/{source_object_id}/api-request/structure", response_model=StructureResult)
def post_api_structure(request: Request, payload: StructureBody, source_object_id: int = PathParam(ge=1)) -> StructureResult:
    names = [c.name for c in payload.columns]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=422, detail="Column names must be unique")
    columns = [{"column_name": c.name, "formatted_type": c.type, "is_nullable": c.nullable} for c in payload.columns]

    def op(conn, token_hash, request_id):
        (row,) = _execute(conn, request_id, "SELECT * FROM app.integration_api_object_capture(%s, %s, %s::jsonb)",
                          (token_hash, source_object_id, Jsonb(columns)))
        return StructureResult(metadata_capture_id=row["metadata_capture_id"], columns_captured=row["columns_captured"])
    return _connect_and(request, op)
