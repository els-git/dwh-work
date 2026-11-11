"""Run requests of source objects and the state of every object (I08, package 015).

GET  /api/integrations/loads/objects                       state of every object (tab "Objects"; INTEGRATION_VIEW,
                                                            read-only connection, view etl.v_object_load_status)
POST /api/admin/integrations/object-run-requests           create a request: RUN, CONTINUE_ODS or FULL_RERUN
POST /api/admin/integrations/object-run-requests/{id}/cancel   cancel a QUEUED request

Requests are only written through app.integration_object_run_request_create/_cancel: the database checks the
session and the right to run (the same check as the CRM run requests), the object state, and writes the audit
trail. The dispatcher executes the requests (etl/src/object_request_worker.py); nothing runs here.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Path as PathParam, Request
from pydantic import BaseModel

from dwh_api.config import get_api_config
from dwh_api.db import connect_control, connect_readonly
from dwh_api.integration_admin import _db_message, _raise_database_error, _session_token_hash, _set_request_context

config = get_api_config()

view_router = APIRouter(prefix="/api/integrations/loads", tags=["Data loads"])
admin_router = APIRouter(prefix="/api/admin/integrations", tags=["Data loads"])

RUN_KINDS = ("RUN", "CONTINUE_ODS", "FULL_RERUN")

# Messages of the request functions that describe the state of the object, not a server failure.
_CONFLICT = ("already has an active run request", "only queued run requests can be cancelled")
_REJECTED = ("source object not found", "source object is not active", "legacy crm sources",
             "source system is not active", "no published ready configuration", "no published ready ods configuration",
             "continue is possible only", "unsupported run kind", "run request does not exist")

_STATUS_SQL = """
SELECT * FROM etl.v_object_load_status
 ORDER BY is_legacy, source_system_code, source_schema, source_object
"""


class ObjectRunRequestCreate(BaseModel):
    source_object_id: int
    run_kind: Literal["RUN", "CONTINUE_ODS", "FULL_RERUN"]


class ObjectRunRequestCreated(BaseModel):
    request_id: int


class ObjectRunRequestCancelled(BaseModel):
    cancelled: bool


def _raise_request_error(exc: psycopg.Error) -> None:
    message = _db_message(exc)
    lowered = message.lower()
    if any(text in lowered for text in _CONFLICT):
        raise HTTPException(status_code=409, detail=message) from exc
    if any(text in lowered for text in _REJECTED):
        raise HTTPException(status_code=422, detail=message) from exc
    _raise_database_error(exc)


def _call(request: Request, sql: str, params: tuple) -> Any:
    token_hash = _session_token_hash(request)
    request_id = str(uuid4())
    try:
        with closing(connect_control(config)) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(cur, request_id)
                    cur.execute(sql, (token_hash, *params))
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except psycopg.Error as exc:
        _raise_request_error(exc)
    if row is None:
        raise HTTPException(status_code=500, detail="No result returned")
    return list(row.values())[0] if isinstance(row, dict) else row[0]


@view_router.get("/objects")
def object_load_status() -> list[dict]:
    with closing(connect_readonly(config)) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(_STATUS_SQL)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.rollback()


@admin_router.post("/object-run-requests", response_model=ObjectRunRequestCreated, status_code=201)
def create_object_run_request(payload: ObjectRunRequestCreate, request: Request) -> ObjectRunRequestCreated:
    request_id = _call(request, "SELECT app.integration_object_run_request_create(%s, %s, %s)",
                       (payload.source_object_id, payload.run_kind))
    return ObjectRunRequestCreated(request_id=int(request_id))


@admin_router.post("/object-run-requests/{request_id}/cancel", response_model=ObjectRunRequestCancelled)
def cancel_object_run_request(request: Request, request_id: int = PathParam(ge=1)) -> ObjectRunRequestCancelled:
    return ObjectRunRequestCancelled(
        cancelled=bool(_call(request, "SELECT app.integration_object_run_request_cancel(%s, %s)", (request_id,))))
