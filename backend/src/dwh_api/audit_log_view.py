"""I13-05: the administrator views the audit journal and the journal of source password operations.

Reading goes only through the functions of package 029 (dwh_app has no rights on the journal tables):
  GET /api/admin/audit/events             app.audit_log_page       (audit.security_event, masked details)
  GET /api/admin/audit/filters            app.audit_log_filters    (values for the filter lists)
  GET /api/admin/audit/secret-operations  app.secret_journal_page  (secret_store.operation_event + source code)
Only SUPERADMIN / ADMIN (plan Zh2): the router checks the level, the database checks the session again.
The first page of a request is written by the database as AUDIT_LOG_VIEWED / SECRET_JOURNAL_VIEWED (Zh6),
so the calls run on the control connection and are committed. No export (Zh5).
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from dwh_api.auth import AuthState
from dwh_api.config import get_api_config
from dwh_api.db import connect_control
from dwh_api.integration_admin import _db_message, _session_token_hash
from dwh_api.server_load import require_server_admin

router = APIRouter(prefix="/api/admin/audit", tags=["audit-log"])

PAGE_MAX = 200


def _raise(exc: psycopg.Error) -> None:
    message = _db_message(exc)
    lowered = message.lower()
    if "authentication required" in lowered or "invalid authentication session" in lowered:
        raise HTTPException(status_code=401, detail="Authentication required") from exc
    if "access denied" in lowered or "password change required" in lowered:
        raise HTTPException(status_code=403, detail=message) from exc
    raise HTTPException(status_code=500, detail="Journal is not available") from exc


def _call(request: Request, statement: str, params: tuple) -> dict[str, Any]:
    token_hash = _session_token_hash(request)
    try:
        with closing(connect_control(get_api_config())) as conn:
            try:
                row = conn.execute(statement, (token_hash, *params)).fetchone()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except psycopg.Error as exc:
        _raise(exc)
    value = row["result"] if isinstance(row, dict) else row[0]
    return value or {}


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@router.get("/events")
def audit_events(request: Request,
                 date_from: datetime | None = Query(default=None, alias="from"),
                 date_to: datetime | None = Query(default=None, alias="to"),
                 event_type: str | None = Query(default=None, max_length=100),
                 actor: str | None = Query(default=None, max_length=100),
                 entity: str | None = Query(default=None, max_length=200),
                 outcome: str | None = Query(default=None, max_length=30),
                 before_id: int | None = Query(default=None, ge=1),
                 limit: int = Query(default=100, ge=1, le=PAGE_MAX),
                 _state: AuthState = Depends(require_server_admin)) -> dict[str, Any]:
    return _call(request, "SELECT app.audit_log_page(%s, %s, %s, %s, %s, %s, %s, %s, %s) AS result",
                 (date_from, date_to, _text(event_type), _text(actor), _text(entity), _text(outcome), before_id, limit))


@router.get("/filters")
def audit_filters(request: Request, _state: AuthState = Depends(require_server_admin)) -> dict[str, Any]:
    return _call(request, "SELECT app.audit_log_filters(%s) AS result", ())


@router.get("/secret-operations")
def secret_operations(request: Request,
                      source_system_id: int | None = Query(default=None, ge=1),
                      date_from: datetime | None = Query(default=None, alias="from"),
                      date_to: datetime | None = Query(default=None, alias="to"),
                      operation_type: str | None = Query(default=None, pattern="^(CREATE|REPLACE|REVOKE|READ)$"),
                      before_id: int | None = Query(default=None, ge=1),
                      limit: int = Query(default=100, ge=1, le=PAGE_MAX),
                      _state: AuthState = Depends(require_server_admin)) -> dict[str, Any]:
    return _call(request, "SELECT app.secret_journal_page(%s, %s, %s, %s, %s, %s, %s) AS result",
                 (source_system_id, date_from, date_to, operation_type, before_id, limit))
