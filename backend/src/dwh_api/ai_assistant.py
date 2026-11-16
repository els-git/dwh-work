"""AI assistant API (I17 step 5): help by the user guide, report from a description, administration of its settings.

GET  /api/ai/status                 the model answers; reports are configured; what the session user may do
POST /api/ai/help                   question (+ optional screenshot, I17-07) -> answer from the guide sections
                                    the user may read (I17-08)
POST /api/ai/report                 description -> SQL over the whitelist objects allowed to the user -> rows
GET  /api/ai/admin/config           whitelist, reference columns, glossary, examples   (AI_ADMIN_MANAGE)
POST /api/ai/admin/report-objects | value-columns | glossary | examples   save one entry (AI_ADMIN_MANAGE)
GET  /api/ai/admin/requests         request journal                                     (AI_ADMIN_MANAGE)

The middleware requires AI_ASSISTANT_USE for /api/ai. The database checks every call again (package 016:
app.ai_session_user): the request journal is written only through app.ai_request_begin/_finish, the
whitelist seen by the user comes from app.ai_object_access. The user's role comes from the server session,
never from the client. Reports run as dwh_ai_ro (read-only, statement timeout) after the text and plan checks of
ai_sql_guard; the model gets column names, reference values, terms and examples, never report rows.
The journal keeps the question, the SQL and the outcome; no rows, no image.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
import time
from contextlib import closing
from typing import Any, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg import sql as pgsql
from pydantic import BaseModel, Field

from dwh_api import ai_llm
from dwh_api.ai_core import (DENIED_TEXT, MAX_ROWS, NO_HELP_TEXT, describe_report_data, help_prompt,
                             report_system, run_checked)
from dwh_api.ai_knowledge import KnowledgeIndex, UserScope
from dwh_api.ai_sql_guard import AllowedObject, SqlRejected, check_text, extract_sql
from dwh_api.auth import _load_auth_state
from dwh_api.config import get_api_config
from dwh_api.db import connect_ai_readonly as db_connect_ai_readonly, connect_control, connect_readonly
from dwh_api.integration_admin import _db_message, _raise_database_error, _session_token_hash, _set_request_context

config = get_api_config()
router = APIRouter(prefix="/api/ai", tags=["AI assistant"])

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\s]+)$")
IMAGE_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff", "webp": b"RIFF"}

_index: KnowledgeIndex | None = None


def knowledge() -> KnowledgeIndex:
    global _index
    if _index is None:
        _index = KnowledgeIndex.from_file()
    return _index


class HelpRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    image: str | None = Field(default=None, description="data:image/png|jpeg|webp;base64,... screenshot (I17-07)")


class ReportRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class ReportObjectSave(BaseModel):
    object_name: str
    title: str
    description: str | None = None
    required_permission_code: str
    is_active: bool = True


class ValueColumnSave(BaseModel):
    object_id: int
    column_name: str
    max_values: int = 50
    is_active: bool = True


class GlossarySave(BaseModel):
    term_id: int | None = None
    term: str
    meaning: str
    object_id: int | None = None
    is_active: bool = True


class ExampleSave(BaseModel):
    example_id: int | None = None
    object_id: int
    question: str
    sql_text: str
    is_active: bool = True


# ------------------------------------------------------------------ helpers

def user_scope(request: Request) -> UserScope:
    state = _load_auth_state(request)
    if state is None or state.user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return UserScope(state.user.account_level, frozenset(p.permission_code for p in state.permissions),
                     frozenset(f"{r.resource_type}:{r.resource_code}" for r in state.resources))


def _raise(exc: psycopg.Error) -> None:
    message = _db_message(exc)
    if "ai access denied" in message.lower():
        raise HTTPException(status_code=403, detail=message) from exc
    _raise_database_error(exc)


def call_db(request: Request, statement: str, params: tuple = (), fetch: Literal["one", "all", "none"] = "one") -> Any:
    """One app.ai_* call in its own committed transaction (dwh_app, SECURITY DEFINER functions)."""
    token_hash = _session_token_hash(request)
    try:
        with closing(connect_control(config)) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(cur, str(uuid4()))
                    cur.execute(statement, (token_hash, *params))
                    result = (cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except psycopg.Error as exc:
        _raise(exc)
    if fetch == "one":
        return None if result is None else list(result.values())[0]
    return result


def begin(request: Request, kind: str, question: str, image: bool, settings: ai_llm.LlmSettings | None) -> int:
    return int(call_db(request, "SELECT app.ai_request_begin(%s, %s, %s, %s, %s)",
                       (kind, question, image, settings.model_label if settings else None)))


def finish(request: Request, request_id: int, status: str, code: str | None = None, sql_text: str | None = None,
           object_ids: list[int] | None = None, rows: int | None = None, message: str | None = None) -> None:
    call_db(request, "SELECT app.ai_request_finish(%s, %s, %s, %s, %s, %s, %s, %s)",
            (request_id, status, code, sql_text, object_ids, rows, message), fetch="none")


def check_image(data_url: str | None) -> str | None:
    if not data_url:
        return None
    match = IMAGE_RE.match(data_url)
    if not match:
        raise HTTPException(status_code=422, detail="image must be a PNG, JPEG or WEBP data URL")
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="image is not valid base64") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="image is larger than 5 MB")
    if not raw.startswith(IMAGE_MAGIC[match.group(1)]):
        raise HTTPException(status_code=422, detail="image content does not match its type")
    return f"data:image/{match.group(1)};base64,{base64.b64encode(raw).decode()}"


def llm_settings() -> ai_llm.LlmSettings:
    try:
        return ai_llm.LlmSettings.from_env()
    except ai_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def ask_model(request: Request, request_id: int, settings: ai_llm.LlmSettings, system: str, user: str,
              image: str | None = None, max_tokens: int = 700) -> str:
    try:
        return ai_llm.chat(settings, system, user, image, max_tokens)
    except ai_llm.LlmBusy as exc:
        finish(request, request_id, "ERROR", "AI_BUSY", message=str(exc))
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ai_llm.LlmUnavailable as exc:
        finish(request, request_id, "ERROR", "AI_MODEL_UNAVAILABLE", message=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def connect_ai_readonly() -> psycopg.Connection:
    """dwh_ai_ro: reads schema dm only; read-only transactions and timeouts are also set on the role (016)."""
    password = os.environ.get("DWH_AI_DB_PASSWORD", "").strip()
    if not password:
        raise HTTPException(status_code=503, detail="reports are not configured (DWH_AI_DB_PASSWORD)")
    return db_connect_ai_readonly(config, password)


# ------------------------------------------------------------------ status

@router.get("/status")
def status(request: Request) -> dict:
    user = user_scope(request)
    ready = False
    try:
        ready = ai_llm.is_ready(ai_llm.LlmSettings.from_env())
    except ai_llm.LlmUnavailable:
        ready = False
    return {"model_ready": ready, "report_configured": bool(os.environ.get("DWH_AI_DB_PASSWORD", "").strip()),
            "can_help": "AI_ASSISTANT_USE" in user.permissions, "can_report": "AI_REPORT_RUN" in user.permissions,
            "can_admin": "AI_ADMIN_MANAGE" in user.permissions}


# ------------------------------------------------------------------ help

@router.post("/help")
def help_answer(payload: HelpRequest, request: Request) -> dict:
    user = user_scope(request)
    image = check_image(payload.image)
    settings = llm_settings()
    request_id = begin(request, "HELP", payload.question, image is not None, settings)
    found = knowledge().search(payload.question, user)
    if found.only_denied and image is None:
        finish(request, request_id, "DENIED", "AI_SECTION_NOT_ALLOWED", message="; ".join(found.denied_titles)[:4000])
        return {"request_id": request_id, "status": "DENIED", "answer": DENIED_TEXT, "sections": []}
    if not found.sections and image is None:
        finish(request, request_id, "NO_DATA", "AI_HELP_NOT_FOUND")
        return {"request_id": request_id, "status": "NO_DATA", "answer": NO_HELP_TEXT, "sections": []}
    system, user_text = help_prompt(found, payload.question)
    answer = ask_model(request, request_id, settings, system, user_text, image)
    finish(request, request_id, "OK", None, message=", ".join(s.number for s in found.sections)[:4000])
    return {"request_id": request_id, "status": "OK", "answer": answer,
            "sections": [{"number": s.number, "title": s.title} for s in found.sections]}


# ------------------------------------------------------------------ report

def report_context(request: Request) -> tuple[list[AllowedObject], list[str], str, set[str]]:
    """Objects allowed to the user, titles of the denied ones, the description for the model, all dm relations."""
    access = call_db(request, "SELECT * FROM app.ai_object_access(%s)", fetch="all")
    allowed = [AllowedObject(int(r["object_id"]), r["schema_name"], r["object_name"]) for r in access if r["is_allowed"]]
    denied = [r["title"] for r in access if not r["is_allowed"]]
    described = {int(r["object_id"]): r for r in access if r["is_allowed"]}
    with closing(connect_readonly(config)) as meta_conn, closing(connect_ai_readonly()) as values_conn:
        try:
            with meta_conn.cursor() as meta_cur, values_conn.cursor() as values_cur:
                description, dm_relations = describe_report_data(meta_cur, values_cur, allowed, described, denied)
        finally:
            meta_conn.rollback()
            values_conn.rollback()
    return allowed, denied, description, dm_relations


def run_report_sql(statement: str, allowed: list[AllowedObject]) -> tuple[list[str], list[dict], bool]:
    with closing(connect_ai_readonly()) as conn:
        try:
            with conn.cursor() as cur:
                return run_checked(cur, statement, allowed, MAX_ROWS)
        finally:
            conn.rollback()


@router.post("/report")
def report(payload: ReportRequest, request: Request) -> dict:
    user_scope(request)
    settings = llm_settings()
    request_id = begin(request, "REPORT", payload.question, False, settings)
    if not os.environ.get("DWH_AI_DB_PASSWORD", "").strip():
        finish(request, request_id, "ERROR", "AI_REPORT_NOT_CONFIGURED")
        raise HTTPException(status_code=503, detail="reports are not configured (DWH_AI_DB_PASSWORD)")
    allowed, denied, context, dm_relations = report_context(request)
    if not allowed:
        finish(request, request_id, "DENIED", "AI_OBJECT_NOT_ALLOWED", message="no reporting object for the role")
        return {"request_id": request_id, "status": "DENIED", "message": DENIED_TEXT}
    answer = ask_model(request, request_id, settings, report_system(context),
                       payload.question, max_tokens=600)
    sql_text = extract_sql(answer)
    try:
        statement, object_ids = check_text(sql_text, allowed, dm_relations)
    except SqlRejected as exc:
        outcome = {"AI_OBJECT_NOT_ALLOWED": "DENIED", "AI_NO_DATA": "NO_DATA"}.get(exc.code, "REFUSED")
        finish(request, request_id, outcome, exc.code, sql_text=sql_text[:20000], message=str(exc))
        if outcome == "DENIED":
            # The SQL may name a closed object: it stays in the journal for the administrator only.
            return {"request_id": request_id, "status": outcome, "code": exc.code, "message": DENIED_TEXT}
        return {"request_id": request_id, "status": outcome, "code": exc.code, "message": str(exc), "sql": sql_text}
    started = time.monotonic()
    try:
        columns, rows, truncated = run_report_sql(statement, allowed)
    except SqlRejected as exc:
        finish(request, request_id, "DENIED", exc.code, sql_text=statement, object_ids=object_ids, message=str(exc))
        return {"request_id": request_id, "status": "DENIED", "code": exc.code, "message": DENIED_TEXT}
    except psycopg.Error as exc:
        message = _db_message(exc)
        finish(request, request_id, "ERROR", "AI_SQL_ERROR", sql_text=statement, object_ids=object_ids,
               message=message)
        return {"request_id": request_id, "status": "ERROR", "code": "AI_SQL_ERROR", "message": message,
                "sql": statement}
    finish(request, request_id, "OK", "AI_REPORT_TRUNCATED" if truncated else None, sql_text=statement,
           object_ids=object_ids, rows=len(rows))
    return {"request_id": request_id, "status": "OK", "sql": statement, "columns": columns,
            "rows": jsonable_encoder(rows), "truncated": truncated,
            "seconds": round(time.monotonic() - started, 2)}


# ------------------------------------------------------------------ administration (checked in the database)

@router.get("/admin/config")
def admin_config(request: Request) -> dict:
    # The request journal list checks AI_ADMIN_MANAGE in the database before anything is returned.
    call_db(request, "SELECT count(*) FROM app.ai_request_log_list(%s, 1)")
    with closing(connect_readonly(config)) as conn:
        try:
            with conn.cursor() as cur:
                result = {}
                for key, table in (("report_objects", "ai_report_object"), ("value_columns", "ai_value_column"),
                                   ("glossary", "ai_glossary"), ("examples", "ai_example")):
                    cur.execute(pgsql.SQL("SELECT * FROM meta.{} ORDER BY 1").format(pgsql.Identifier(table)))
                    result[key] = cur.fetchall()
        finally:
            conn.rollback()
    return jsonable_encoder(result)


@router.get("/admin/requests")
def admin_requests(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    return jsonable_encoder(call_db(request, "SELECT * FROM app.ai_request_log_list(%s, %s)", (limit,), fetch="all"))


@router.post("/admin/report-objects")
def save_report_object(payload: ReportObjectSave, request: Request) -> dict:
    return {"object_id": call_db(request, "SELECT app.ai_report_object_save(%s, 'dm', %s, %s, %s, %s, %s)",
                                 (payload.object_name, payload.title, payload.description,
                                  payload.required_permission_code, payload.is_active))}


@router.post("/admin/value-columns")
def save_value_column(payload: ValueColumnSave, request: Request) -> dict:
    return {"value_column_id": call_db(request, "SELECT app.ai_value_column_save(%s, %s, %s, %s, %s)",
                                       (payload.object_id, payload.column_name, payload.max_values,
                                        payload.is_active))}


@router.post("/admin/glossary")
def save_glossary(payload: GlossarySave, request: Request) -> dict:
    return {"term_id": call_db(request, "SELECT app.ai_glossary_save(%s, %s, %s, %s, %s, %s)",
                               (payload.term_id, payload.term, payload.meaning, payload.object_id, payload.is_active))}


@router.post("/admin/examples")
def save_example(payload: ExampleSave, request: Request) -> dict:
    return {"example_id": call_db(request, "SELECT app.ai_example_save(%s, %s, %s, %s, %s, %s)",
                                  (payload.example_id, payload.object_id, payload.question, payload.sql_text,
                                   payload.is_active))}
