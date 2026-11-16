"""Saved reports of the AI assistant in "Reporting" (I17-09 step 3; package 017, decisions O1-O5 of 16.11.2026).

Folders are personal (owner only) or shared (everyone with REPORT_VIEW). A saved report keeps the description and the
checked SQL of the user's own successful report request (copied by the database, never taken from the client) and
no rows. Opening a report runs its SQL again with the rights of the viewer: the database gives the SQL only when the
viewer's role may read every whitelist object it uses, and the backend repeats the text and plan checks of the
assistant before the query runs as dwh_ai_ro. Every run is logged in app.ai_request_log (kind SAVED_REPORT).
All rights are checked in the database (functions report_folder_* / saved_report_*); the API passes the session only.
"""
from __future__ import annotations

import os
from contextlib import closing
from typing import Any, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from dwh_api.ai_core import DENIED_TEXT, MAX_ROWS, run_checked
from dwh_api.ai_sql_guard import AllowedObject, SqlRejected, check_text
from dwh_api.config import get_api_config
from dwh_api.db import connect_ai_readonly as db_connect_ai_readonly, connect_control, connect_readonly
from dwh_api.integration_admin import _db_message, _raise_database_error, _session_token_hash, _set_request_context

config = get_api_config()
router = APIRouter(prefix="/api/reports/saved", tags=["Saved reports"])


class FolderSave(BaseModel):
    folder_name: str = Field(min_length=1, max_length=200)
    visibility: Literal["PERSONAL", "SHARED"]


class ReportSave(BaseModel):
    request_id: int
    folder_id: int
    title: str = Field(default="", max_length=200)


class ReportUpdate(BaseModel):
    folder_id: int
    title: str = Field(min_length=1, max_length=200)


def _raise(exc: psycopg.Error) -> None:
    message = _db_message(exc)
    lower = message.lower()
    if "access denied" in lower or "only the owner" in lower:
        raise HTTPException(status_code=403, detail=message) from exc
    if "not found" in lower:
        raise HTTPException(status_code=404, detail=message) from exc
    if ("not empty" in lower or "duplicate key" in lower or "only an own successful report" in lower
            or "visibility" in lower):
        raise HTTPException(status_code=409, detail=message) from exc
    _raise_database_error(exc)


def call_db(request: Request, statement: str, params: tuple = (),
            fetch: Literal["one", "all", "none"] = "one") -> Any:
    """One call of a 017 function in its own committed transaction (dwh_app, SECURITY DEFINER)."""
    token_hash = _session_token_hash(request)
    try:
        with closing(connect_control(config)) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(cur, str(uuid4()))
                    cur.execute(statement, (token_hash, *params))
                    result = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except psycopg.Error as exc:
        _raise(exc)
    if fetch == "one":
        return None if result is None else result
    return result


# ------------------------------------------------------------------ folders

@router.get("/folders")
def folders(request: Request) -> list[dict]:
    return jsonable_encoder(call_db(request, "SELECT * FROM app.report_folder_list(%s)", fetch="all"))


@router.post("/folders")
def folder_create(payload: FolderSave, request: Request) -> dict:
    row = call_db(request, "SELECT app.report_folder_save(%s, NULL, %s, %s) AS folder_id",
                  (payload.folder_name.strip(), payload.visibility))
    return {"folder_id": row["folder_id"]}


@router.post("/folders/{folder_id}")
def folder_update(folder_id: int, payload: FolderSave, request: Request) -> dict:
    call_db(request, "SELECT app.report_folder_save(%s, %s, %s, %s)",
            (folder_id, payload.folder_name.strip(), payload.visibility), fetch="none")
    return {"folder_id": folder_id}


@router.post("/folders/{folder_id}/delete")
def folder_delete(folder_id: int, request: Request) -> dict:
    call_db(request, "SELECT app.report_folder_delete(%s, %s)", (folder_id,), fetch="none")
    return {"deleted": True}


# ------------------------------------------------------------------ reports

@router.get("/reports")
def reports(request: Request) -> list[dict]:
    return jsonable_encoder(call_db(request, "SELECT * FROM app.saved_report_list(%s)", fetch="all"))


@router.post("/reports")
def report_save(payload: ReportSave, request: Request) -> dict:
    row = call_db(request, "SELECT app.saved_report_save(%s, %s, %s, %s) AS report_id",
                  (payload.request_id, payload.folder_id, payload.title.strip()))
    return {"report_id": row["report_id"]}


@router.post("/reports/{report_id}")
def report_update(report_id: int, payload: ReportUpdate, request: Request) -> dict:
    call_db(request, "SELECT app.saved_report_update(%s, %s, %s, %s)",
            (report_id, payload.folder_id, payload.title.strip()), fetch="none")
    return {"report_id": report_id}


@router.post("/reports/{report_id}/delete")
def report_delete(report_id: int, request: Request) -> dict:
    call_db(request, "SELECT app.saved_report_delete(%s, %s)", (report_id,), fetch="none")
    return {"deleted": True}


def _finish(request: Request, request_id: int, status: str, code: str | None, rows: int | None,
            message: str | None) -> None:
    call_db(request, "SELECT app.saved_report_run_finish(%s, %s, %s, %s, %s, %s)",
            (request_id, status, code, rows, message), fetch="none")


def _dm_relations() -> set[str]:
    with closing(connect_readonly(config)) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'dm' AND c.relkind IN ('r', 'v', 'm', 'p', 'f')")
                return {r["relname"] for r in cur.fetchall()}
        finally:
            conn.rollback()


@router.post("/reports/{report_id}/run")
def report_run(report_id: int, request: Request) -> dict:
    password = os.environ.get("DWH_AI_DB_PASSWORD", "").strip()
    if not password:
        raise HTTPException(status_code=503, detail="reports are not configured (DWH_AI_DB_PASSWORD)")
    begun = call_db(request, "SELECT * FROM app.saved_report_run_begin(%s, %s)", (report_id,))
    request_id = int(begun["request_id"])
    if not begun["is_allowed"]:
        # The database has already logged the run as DENIED and gave no SQL.
        return {"request_id": request_id, "status": "DENIED", "code": "AI_OBJECT_NOT_ALLOWED", "message": DENIED_TEXT}
    objects = call_db(request, "SELECT * FROM app.saved_report_allowed_objects(%s)", fetch="all")
    allowed = [AllowedObject(int(r["object_id"]), r["schema_name"], r["object_name"]) for r in objects]
    try:
        statement, _ = check_text(begun["sql_text"], allowed, _dm_relations())
        with closing(db_connect_ai_readonly(config, password)) as conn:
            try:
                with conn.cursor() as cur:
                    columns, rows, truncated = run_checked(cur, statement, allowed, MAX_ROWS)
            finally:
                conn.rollback()
    except SqlRejected as exc:
        status = "DENIED" if exc.code == "AI_OBJECT_NOT_ALLOWED" else "REFUSED"
        _finish(request, request_id, status, exc.code, 0, str(exc))
        return {"request_id": request_id, "status": status, "code": exc.code,
                "message": DENIED_TEXT if status == "DENIED" else str(exc)}
    except psycopg.Error as exc:
        message = _db_message(exc)
        _finish(request, request_id, "ERROR", "AI_SQL_ERROR", 0, message)
        return {"request_id": request_id, "status": "ERROR", "code": "AI_SQL_ERROR", "message": message}
    _finish(request, request_id, "OK", "AI_REPORT_TRUNCATED" if truncated else None, len(rows), None)
    return {"request_id": request_id, "status": "OK", "sql": statement, "columns": columns,
            "rows": jsonable_encoder(rows), "truncated": truncated}
