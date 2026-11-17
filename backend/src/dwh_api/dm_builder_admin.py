"""DM mart builder: catalog of keys, marts, versions, DDL proposals, run requests (I19 step 5; package 018).

Administration (ADMIN_INTEGRATION_MANAGE, checked by the middleware and again by the database session check):
  GET  /api/admin/integrations/dm/catalog                    entities with keys, ODS tables with columns
  POST /api/admin/integrations/dm/entities                   create / change an entity
  POST /api/admin/integrations/dm/entities/{id}/keys         key column of an ODS table for the entity
  GET  /api/admin/integrations/dm/marts                      marts with their versions
  POST /api/admin/integrations/dm/marts                      create / change a mart
  POST /api/admin/integrations/dm/marts/{id}/versions        new draft (copy of the published version)
  GET  /api/admin/integrations/dm/versions/{id}              definition of a version, DDL proposal, fill SQL preview
  POST /api/admin/integrations/dm/versions/{id}              save the whole definition of a draft -> problems
  POST /api/admin/integrations/dm/versions/{id}/discard | ddl-propose | ddl-reject
  GET  /api/admin/integrations/dm/versions/{id}/preview      what publication does: DDL, changes, impact (019)
  POST /api/admin/integrations/dm/versions/{id}/publish      {confirm}: publish; a new structure is built aside
                                                             and swapped in by the dispatcher (019)
  POST /api/admin/integrations/dm/marts/{id}/delete          {confirm}: drop the mart table, deactivate (019)
  POST /api/admin/integrations/dm/run-requests               manual run request of a mart
  POST /api/admin/integrations/dm/run-requests/{id}/cancel   cancel a QUEUED request
State (INTEGRATION_VIEW):
  GET  /api/integrations/loads/marts                         etl.v_dm_mart_status

Every change goes through the app.dm_* functions of 018 and app.mart_builder_* of 019 (session, validation,
audit). The DDL text is built by the database from the whitelisted field types and executed by the database
functions of 019 only for tables of the builder's marts; no SQL text from the client is executed.
"""
from __future__ import annotations

import json
from contextlib import closing
from typing import Any, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Path as PathParam, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from dwh_api import dm_sql
from dwh_api.config import get_api_config
from dwh_api.db import connect_control, connect_readonly
from dwh_api.integration_admin import _db_message, _raise_database_error, _session_token_hash, _set_request_context

config = get_api_config()

admin_router = APIRouter(prefix="/api/admin/integrations/dm", tags=["DM marts"])
view_router = APIRouter(prefix="/api/integrations/loads", tags=["DM marts"])

NAME = r"^[a-z_][a-z0-9_]{0,62}$"
ALIAS = r"^[a-z][a-z0-9_]{0,30}$"
FIELD_TYPES = ("text", "bigint", "integer", "smallint", "numeric", "date", "timestamp", "timestamptz", "boolean")

_NOT_FOUND = ("mart not found", "entity not found", "mart version not found", "run request does not exist")
_CONFLICT = ("already has", "already exists", "only a draft", "only queued", "duplicate key", "already applied",
             "cannot be changed", "is applied", "being built", "mart is deleted", "foreign key")
_REJECTED = ("not compatible", "invalid", "not valid", "does not match", "no active key", "no published version",
             "is not active", "inactive", "violates check constraint", "violates not-null", "too large",
             "no proposed ddl", "can be proposed only", "already has this structure", "permission", "not found")


# ------------------------------------------------------------------ models

class EntitySave(BaseModel):
    entity_id: int | None = None
    entity_code: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,59}$")
    title: str = Field(min_length=1, max_length=200)
    is_active: bool = True


class EntityKeySave(BaseModel):
    table_name: str = Field(pattern=NAME)
    column_name: str = Field(pattern=NAME)
    is_active: bool = True


class MartSave(BaseModel):
    mart_id: int | None = None
    mart_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,49}$")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    required_permission_code: str = Field(default="REPORT_VIEW", pattern=r"^[A-Z][A-Z0-9_]*$")
    ai_enabled: bool = False
    auto_after_ods: bool = True
    is_active: bool = True


class SourceDef(BaseModel):
    alias: str = Field(pattern=ALIAS)
    table: str = Field(pattern=NAME)
    kind: Literal["GRAIN", "SAME_KEY", "CHILD", "LOOKUP"]
    ref_alias: str | None = Field(default=None, pattern=ALIAS)
    ref_column: str | None = Field(default=None, pattern=NAME)
    lookup_column: str | None = Field(default=None, pattern=NAME)


class FieldSourceDef(BaseModel):
    alias: str = Field(pattern=ALIAS)
    column: str = Field(pattern=NAME)
    aggregate: Literal["NONE", "SUM", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "AVG"] = "NONE"


class FieldDef(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    title: str | None = Field(default=None, max_length=200)
    type: str = Field(pattern=r"^(text|bigint|integer|smallint|numeric|numeric\([1-9][0-9]?,[0-9][0-9]?\)|date|timestamp"
                              r"|timestamptz|boolean|varchar\([1-9][0-9]{0,3}\))$")
    is_key: bool = False
    sources: list[FieldSourceDef] = Field(min_length=1, max_length=9)


class FilterDef(BaseModel):
    alias: str = Field(pattern=ALIAS)
    column: str = Field(pattern=NAME)
    operator: Literal["=", "<>", ">", ">=", "<", "<=", "IN", "IS NULL", "IS NOT NULL", "LIKE"]
    value: Any = None


class VersionDefinition(BaseModel):
    grain_entity_id: int | None = None
    sources: list[SourceDef] = Field(default_factory=list, max_length=30)
    fields: list[FieldDef] = Field(default_factory=list, max_length=300)
    filters: list[FilterDef] = Field(default_factory=list, max_length=50)


class VersionSave(BaseModel):
    definition: VersionDefinition


class Confirm(BaseModel):
    confirm: bool = False


class RunRequestCreate(BaseModel):
    mart_id: int


# ------------------------------------------------------------------ database calls

def _raise(exc: psycopg.Error) -> None:
    message = _db_message(exc)
    lower = message.lower()
    if "access denied" in lower:
        raise HTTPException(status_code=403, detail=message) from exc
    if any(t in lower for t in _CONFLICT):
        raise HTTPException(status_code=409, detail=message) from exc
    if any(t in lower for t in _NOT_FOUND):
        raise HTTPException(status_code=404, detail=message) from exc
    if any(t in lower for t in _REJECTED):
        raise HTTPException(status_code=422, detail=message) from exc
    _raise_database_error(exc)


def call_db(request: Request, statement: str, params: tuple = (), fetch: Literal["one", "all"] = "one") -> Any:
    """One app.dm_* call in its own committed transaction (dwh_app, SECURITY DEFINER)."""
    token_hash = _session_token_hash(request)
    try:
        with closing(connect_control(config)) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(cur, str(uuid4()))
                    cur.execute(statement, (token_hash, *params))
                    result = cur.fetchone() if fetch == "one" else cur.fetchall()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except psycopg.Error as exc:
        _raise(exc)
    return result


def _value(row: Any) -> Any:
    return list(row.values())[0] if isinstance(row, dict) else row[0]


def read(statement: str, params: tuple = ()) -> list[dict[str, Any]]:
    with closing(connect_readonly(config)) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(statement, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.rollback()


# ------------------------------------------------------------------ catalog

@admin_router.get("/catalog")
def catalog() -> dict:
    entities = read("SELECT entity_id, entity_code, title, is_active, updated_at FROM meta.dm_entity ORDER BY entity_code")
    keys = read("SELECT entity_key_id, entity_id, table_name, column_name, type_family, is_active, updated_at "
                "FROM meta.dm_entity_key ORDER BY entity_id, table_name")
    for e in entities:
        e["keys"] = [k for k in keys if k["entity_id"] == e["entity_id"]]
    # Structure only, from the catalog (dwh_app does not read the ODS data).
    columns = read("SELECT c.relname AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS "
                   "data_type, c.reltuples::bigint AS estimated_rows FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                   "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'ods' AND c.relkind IN ('r', 'p', 'v', 'm') "
                   "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY c.relname, a.attnum")
    tables: dict[str, dict[str, Any]] = {}
    for c in columns:
        t = tables.setdefault(c["table_name"], {"table_name": c["table_name"],
                                                "estimated_rows": max(int(c["estimated_rows"] or 0), 0), "columns": []})
        t["columns"].append({"name": c["column_name"], "type": c["data_type"]})
    return jsonable_encoder({"entities": entities, "ods_tables": list(tables.values()),
                             "field_types": list(FIELD_TYPES), "aggregates": list(dm_sql.AGGREGATES),
                             "operators": list(dm_sql.OPERATORS)})


@admin_router.post("/entities")
def entity_save(payload: EntitySave, request: Request) -> dict:
    row = call_db(request, "SELECT app.dm_entity_save(%s, %s, %s, %s, %s)",
                  (payload.entity_id, payload.entity_code, payload.title.strip(), payload.is_active))
    return {"entity_id": int(_value(row))}


@admin_router.post("/entities/{entity_id}/keys")
def entity_key_save(payload: EntityKeySave, request: Request, entity_id: int = PathParam(ge=1)) -> dict:
    row = call_db(request, "SELECT app.dm_entity_key_save(%s, %s, %s, %s, %s)",
                  (entity_id, payload.table_name, payload.column_name, payload.is_active))
    return {"entity_key_id": int(_value(row))}


# ------------------------------------------------------------------ marts and versions

@admin_router.get("/marts")
def marts() -> list[dict]:
    # Deleted marts (019) are not shown: their code stays reserved, their history stays in the journals.
    rows = read("SELECT * FROM meta.dm_mart WHERE deleted_at IS NULL ORDER BY mart_code")
    versions = read("SELECT version_id, mart_id, version_no, status, grain_entity_id, ddl_status, build_status, "
                    "build_message, created_at, updated_at, published_at FROM meta.dm_mart_version "
                    "ORDER BY mart_id, version_no DESC")
    for m in rows:
        m["versions"] = [v for v in versions if v["mart_id"] == m["mart_id"]]
    return jsonable_encoder(rows)


@admin_router.post("/marts")
def mart_save(payload: MartSave, request: Request) -> dict:
    row = call_db(request, "SELECT app.dm_mart_save(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                  (payload.mart_id, payload.mart_code, payload.title.strip(), payload.description,
                   payload.required_permission_code, payload.ai_enabled, payload.auto_after_ods, payload.is_active))
    return {"mart_id": int(_value(row))}


@admin_router.post("/marts/{mart_id}/versions")
def version_create(request: Request, mart_id: int = PathParam(ge=1)) -> dict:
    return {"version_id": int(_value(call_db(request, "SELECT app.dm_mart_version_create(%s, %s)", (mart_id,))))}


def definition_document(defn: dm_sql.Definition, grain_entity_id: int | None) -> dict[str, Any]:
    """Stored version in the shape of the save request."""
    sources = []
    for s in defn.sources:
        item: dict[str, Any] = {"alias": s.alias, "table": s.table, "kind": s.kind}
        if s.kind == "LOOKUP":
            item.update(ref_alias=s.ref_alias, ref_column=s.ref_column, lookup_column=s.key_column)
        else:
            item["key_column"] = s.key_column
        sources.append(item)
    return {"grain_entity_id": grain_entity_id, "sources": sources,
            "fields": [{"name": f.name, "title": f.title, "type": f.data_type, "is_key": f.is_key,
                        "sources": [{"alias": x.alias, "column": x.column, "aggregate": x.aggregate} for x in f.sources]}
                       for f in defn.fields],
            "filters": [{"alias": x.alias, "column": x.column, "operator": x.operator, "value": x.value}
                        for x in defn.filters]}


@admin_router.get("/versions/{version_id}")
def version_get(version_id: int = PathParam(ge=1)) -> dict:
    with closing(connect_readonly(config)) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT v.*, m.mart_code FROM meta.dm_mart_version v JOIN meta.dm_mart m ON m.mart_id = v.mart_id "
                            "WHERE v.version_id = %s", (version_id,))
                head = cur.fetchone()
                if head is None:
                    raise HTTPException(status_code=404, detail="Mart version not found")
                head = dict(head)
                defn = dm_sql.load_definition(cur, version_id)
                types = dm_sql.load_column_types(cur, defn.tables())
        finally:
            conn.rollback()
    preview: str | None = None
    preview_error: str | None = None
    missing = dm_sql.missing_columns(defn, types) if defn.sources else []
    if missing:
        preview_error = "ODS objects are missing: " + ", ".join(missing[:20])
    elif defn.sources and defn.fields:
        try:
            text, params = dm_sql.fill_sql(defn, types)
            preview = dm_sql.render(text, params)
        except dm_sql.DefinitionError as exc:
            preview_error = str(exc)
    return jsonable_encoder({"version": head, "definition": definition_document(defn, head["grain_entity_id"]),
                             "fill_sql": preview, "fill_sql_error": preview_error, "missing": missing})


@admin_router.post("/versions/{version_id}")
def version_save(payload: VersionSave, request: Request, version_id: int = PathParam(ge=1)) -> dict:
    document = payload.definition.model_dump(exclude_none=False)
    for f in document["filters"]:
        if f["operator"] == "IN" and not isinstance(f["value"], list):
            raise HTTPException(status_code=422, detail="IN needs a list of values")
        if f["operator"] in ("IS NULL", "IS NOT NULL"):
            f["value"] = None
    row = call_db(request, "SELECT app.dm_mart_version_save(%s, %s, %s)", (version_id, Jsonb(document)))
    problems = _value(row) or []
    return {"version_id": version_id, "problems": list(problems)}


@admin_router.post("/versions/{version_id}/discard")
def version_discard(request: Request, version_id: int = PathParam(ge=1)) -> dict:
    call_db(request, "SELECT app.dm_mart_version_discard(%s, %s)", (version_id,))
    return {"discarded": True}


@admin_router.post("/versions/{version_id}/ddl-propose")
def ddl_propose(request: Request, version_id: int = PathParam(ge=1)) -> dict:
    row = call_db(request, "SELECT * FROM app.dm_mart_ddl_propose(%s, %s)", (version_id,))
    row = dict(row) if isinstance(row, dict) else {"ddl_text": row[0], "ddl_sha256": row[1]}
    return {"ddl_text": row["ddl_text"], "ddl_sha256": row["ddl_sha256"]}


@admin_router.post("/versions/{version_id}/ddl-reject")
def ddl_reject(request: Request, version_id: int = PathParam(ge=1)) -> dict:
    call_db(request, "SELECT app.dm_mart_ddl_reject(%s, %s)", (version_id,))
    return {"rejected": True}


def _json(row: Any) -> dict:
    value = _value(row)
    return json.loads(value) if isinstance(value, str) else dict(value)


@admin_router.get("/versions/{version_id}/preview")
def version_preview(request: Request, version_id: int = PathParam(ge=1)) -> dict:
    return jsonable_encoder(_json(call_db(request, "SELECT app.mart_builder_preview(%s, %s)", (version_id,))))


@admin_router.post("/versions/{version_id}/publish")
def version_publish(request: Request, payload: Confirm | None = None, version_id: int = PathParam(ge=1)) -> dict:
    """PUBLISHED (same structure), BUILDING (built aside by the dispatcher) or NEEDS_CONFIRM with the impact."""
    confirm = bool(payload and payload.confirm)
    return jsonable_encoder(_json(call_db(request, "SELECT app.mart_builder_publish(%s, %s, %s)", (version_id, confirm))))


@admin_router.post("/marts/{mart_id}/delete")
def mart_delete(request: Request, payload: Confirm | None = None, mart_id: int = PathParam(ge=1)) -> dict:
    confirm = bool(payload and payload.confirm)
    return jsonable_encoder(_json(call_db(request, "SELECT app.mart_builder_delete(%s, %s, %s)", (mart_id, confirm))))


# ------------------------------------------------------------------ runs

@admin_router.post("/run-requests", status_code=201)
def run_request_create(payload: RunRequestCreate, request: Request) -> dict:
    return {"request_id": int(_value(call_db(request, "SELECT app.dm_mart_run_request_create(%s, %s)",
                                             (payload.mart_id,))))}


@admin_router.post("/run-requests/{request_id}/cancel")
def run_request_cancel(request: Request, request_id: int = PathParam(ge=1)) -> dict:
    call_db(request, "SELECT app.dm_mart_run_request_cancel(%s, %s)", (request_id,))
    return {"cancelled": True}


@view_router.get("/marts")
def mart_status() -> list[dict]:
    rows = read("SELECT * FROM etl.v_dm_mart_status WHERE NOT is_deleted ORDER BY mart_code")
    for r in rows:
        if isinstance(r.get("last_run_diagnostic"), str):
            r["last_run_diagnostic"] = json.loads(r["last_run_diagnostic"])
    return jsonable_encoder(rows)
