"""AI assistant API (I17 step 5): help by the user guide, report from a description, administration of its settings.

GET  /api/ai/status                 the model answers; reports are configured; what the session user may do
POST /api/ai/help                   question (+ optional screenshot, I17-07) -> answer from the guide sections
                                    the user may read (I17-08)
POST /api/ai/report                 description -> SQL over the whitelist objects allowed to the user -> rows
POST /api/ai/diagnose               I17-10: diagnosis of a load error / object / mart (AI_ASSISTANT_USE + ETL_VIEW):
                                    the server collects a dossier of facts (ai_diagnosis), the model writes the
                                    diagnosis; card_only returns the instant card without the model
Help and report take the last messages of the dialogue (I17-10, at most 10, kept in the browser only).
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
import threading
import time
from collections import deque
from contextlib import closing
from typing import Any, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg import sql as pgsql
from pydantic import BaseModel, Field

from dwh_api import ai_diagnosis, ai_llm
from dwh_api.ai_core import (DENIED_TEXT, MAX_ROWS, NO_HELP_TEXT, TRANSLATE_SYSTEM, describe_report_data,
                             fold_text_equality, help_prompt, repair_message, report_system, report_text_columns,
                             run_checked, script_answer, trim_found)
from dwh_api.ai_knowledge import KnowledgeIndex, UserScope, is_russian
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


CLIENT_ID = r"^[A-Za-z0-9-]{8,64}$"


HISTORY_MESSAGES = 10
HISTORY_MESSAGE_CHARS = 2000
HISTORY_TOTAL_CHARS = 12000


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=20000)


class HelpRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    image: str | None = Field(default=None, description="data:image/png|jpeg|webp;base64,... screenshot (I17-07)")
    client_id: str | None = Field(default=None, pattern=CLIENT_ID, description="id for the Stop button")
    history: list[HistoryItem] = Field(default_factory=list, max_length=40, description="I17-10: earlier messages")
    mode: Literal["script", "ai"] = Field(default="script", description="I17-12: script answers without the model")


class ReportRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    client_id: str | None = Field(default=None, pattern=CLIENT_ID, description="id for the Stop button")
    history: list[HistoryItem] = Field(default_factory=list, max_length=40, description="I17-10: earlier messages")


class DiagnoseRequest(BaseModel):
    kind: Literal["error", "object", "mart"]
    id: int = Field(ge=1, description="error_id, source_object_id or mart_id")
    question: str | None = Field(default=None, max_length=2000)
    language: Literal["ru", "en", "az"] = "ru"
    client_id: str | None = Field(default=None, pattern=CLIENT_ID, description="id for the Stop button")
    card_only: bool = False


def dialogue(items: list[HistoryItem]) -> list[tuple[str, str]]:
    """The last messages of the dialogue, cut to the limits (newest kept), oldest first; empty texts dropped."""
    kept: list[tuple[str, str]] = []
    total = 0
    for item in reversed(items[-HISTORY_MESSAGES:]):
        text = item.text.strip()[:HISTORY_MESSAGE_CHARS]
        if not text:
            continue
        if total + len(text) > HISTORY_TOTAL_CHARS:
            break
        kept.append((item.role, text))
        total += len(text)
    return list(reversed(kept))


def last_user_text(history: list[tuple[str, str]]) -> str:
    return next((text for role, text in reversed(history) if role == "user"), "")


class CancelRequest(BaseModel):
    client_id: str = Field(pattern=CLIENT_ID)


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


# Duration of the last model answers (seconds): the chat shows their average while it waits. Kept in the
# memory of the API process (one worker); after a restart the average is empty until the first answers.
RECENT_ANSWERS = 10
_recent_seconds: deque[float] = deque(maxlen=RECENT_ANSWERS)
_recent_lock = threading.Lock()


def record_answer_time(seconds: float) -> None:
    with _recent_lock:
        _recent_seconds.append(seconds)


def average_answer_time() -> tuple[float | None, int]:
    with _recent_lock:
        values = list(_recent_seconds)
    return (round(sum(values) / len(values), 1) if values else None), len(values)


# Questions that can be stopped: (session, client id) -> token. Only the session that asked can stop it.
_running: dict[tuple[str, str], ai_llm.CancelToken] = {}
_running_lock = threading.Lock()


class Stoppable:
    """Registers the question of one request for the Stop button while it runs."""

    def __init__(self, request: Request, client_id: str | None):
        self.key = (_session_token_hash(request), client_id) if client_id else None
        self.token = ai_llm.CancelToken()

    def __enter__(self) -> ai_llm.CancelToken:
        if self.key:
            with _running_lock:
                _running[self.key] = self.token
        return self.token

    def __exit__(self, *exc: object) -> None:
        if self.key:
            with _running_lock:
                if _running.get(self.key) is self.token:
                    del _running[self.key]


def ask_model(request: Request, request_id: int, settings: ai_llm.LlmSettings, system: str, user: str,
              image: str | None = None, max_tokens: int = 700, cancel: ai_llm.CancelToken | None = None,
              timed: bool = True, history: list[tuple[str, str]] | None = None) -> str:
    started = time.monotonic()
    try:
        answer = ai_llm.chat(settings, system, user, image, max_tokens, cancel, history)
        if timed:
            record_answer_time(time.monotonic() - started)
        return answer
    except ai_llm.LlmCancelled as exc:
        finish(request, request_id, "ERROR", "AI_CANCELLED", message="stopped by the user")
        raise HTTPException(status_code=409, detail="AI_CANCELLED") from exc
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
    average, count = average_answer_time()
    return {"model_ready": ready, "report_configured": bool(os.environ.get("DWH_AI_DB_PASSWORD", "").strip()),
            "average_answer_seconds": average, "average_answer_count": count,
            "can_help": "AI_ASSISTANT_USE" in user.permissions, "can_report": "AI_REPORT_RUN" in user.permissions,
            "can_admin": "AI_ADMIN_MANAGE" in user.permissions,
            # I17-10: diagnosis of load errors needs the right to see them (ETL_VIEW).
            "can_diagnose": "ETL_VIEW" in user.permissions,
            # I17-09: a report can be saved to "Reporting" (folders need REPORT_VIEW, saving AI_REPORT_RUN).
            "can_save_reports": {"AI_REPORT_RUN", "REPORT_VIEW"} <= user.permissions}


# ------------------------------------------------------------------ help

@router.post("/cancel")
def cancel(payload: CancelRequest, request: Request) -> dict:
    """The Stop button: stops the own running question of this session (waiting or answering)."""
    user_scope(request)
    with _running_lock:
        token = _running.get((_session_token_hash(request), payload.client_id))
    if token is not None:
        token.cancel()
    return {"stopped": token is not None}


HELP_MAX_TOKENS = 1200
# Help questions keep only the last two question/answer pairs: the earlier ones slow the model down.
HELP_HISTORY_MESSAGES = 4


def help_by_script(payload: HelpRequest, request: Request, user: UserScope) -> dict:
    """I17-12: the answer without the model (R1, R5): the matching lines of the guide sections the role may read.
    A question that is not in Russian is searched as it is (R2): the guide is Russian, the model is not asked."""
    request_id = begin(request, "HELP", payload.question, False, None)
    # Only the question itself is searched: the words of the previous question would answer an unrelated new
    # question with the previous section. A follow-up ("and how to delete it?") is the model's job (button).
    index = knowledge()
    found = index.search(payload.question, user)
    base = {"request_id": request_id, "mode": "SCRIPT", "foreign": not is_russian(payload.question)}
    if found.only_denied:
        finish(request, request_id, "DENIED", "AI_SECTION_NOT_ALLOWED", message="; ".join(found.denied_titles)[:4000])
        return {**base, "status": "DENIED", "code": "AI_SECTION_NOT_ALLOWED", "answer": DENIED_TEXT, "sections": []}
    if not found.sections:
        finish(request, request_id, "NO_DATA", "AI_HELP_NOT_FOUND", message="script")
        return {**base, "status": "NO_DATA", "code": "AI_HELP_NOT_FOUND", "answer": NO_HELP_TEXT, "sections": []}
    answer, used = script_answer(found, payload.question, index.weight)
    if not used:
        finish(request, request_id, "NO_DATA", "AI_HELP_NOT_FOUND", message="script")
        return {**base, "status": "NO_DATA", "code": "AI_HELP_NOT_FOUND", "answer": NO_HELP_TEXT, "sections": []}
    finish(request, request_id, "OK", "AI_HELP_SCRIPT", message=", ".join(s.number for s in used)[:4000])
    return {**base, "status": "OK", "answer": answer,
            "sections": [{"number": s.number, "title": s.title} for s in used],
            "closed_nearby": found.closed_nearby > 0}


@router.post("/help")
def help_answer(payload: HelpRequest, request: Request) -> dict:
    user = user_scope(request)
    image = check_image(payload.image)
    if image is None and payload.mode == "script":  # I17-12 (R3): a screenshot always goes to the model
        return help_by_script(payload, request, user)
    settings = llm_settings()
    request_id = begin(request, "HELP", payload.question, image is not None, settings)
    history = dialogue(payload.history)[-HELP_HISTORY_MESSAGES:]
    with Stoppable(request, payload.client_id) as token:
        translated = None
        if not is_russian(payload.question):
            # The guide is in Russian: a question in another language is translated for the search only;
            # the answer is given in the language of the question.
            translated = ask_model(request, request_id, settings, TRANSLATE_SYSTEM, payload.question,
                                   max_tokens=150, cancel=token, timed=False).strip()[:1000] or None
        # A follow-up question ("and how do I delete it?") is searched together with the previous question.
        found = knowledge().search(f"{payload.question} {translated or ''} {last_user_text(history)}", user)
        if found.only_denied and image is None:
            finish(request, request_id, "DENIED", "AI_SECTION_NOT_ALLOWED",
                   message="; ".join(found.denied_titles)[:4000])
            return {"request_id": request_id, "status": "DENIED", "code": "AI_SECTION_NOT_ALLOWED",
                    "answer": DENIED_TEXT, "sections": []}
        if not found.sections and image is None:
            finish(request, request_id, "NO_DATA", "AI_HELP_NOT_FOUND")
            return {"request_id": request_id, "status": "NO_DATA", "code": "AI_HELP_NOT_FOUND",
                    "answer": NO_HELP_TEXT, "sections": []}
        screen_map = knowledge().screen_map(user) if image else None
        found = trim_found(found, f"{payload.question} {translated or ''} {last_user_text(history)}")
        system, user_text = help_prompt(found, payload.question, screen_map, translated)
        # Help answers are longer than reports and Azerbaijani/English text takes more tokens: 1200, not 700.
        answer = ask_model(request, request_id, settings, system, user_text, image, max_tokens=HELP_MAX_TOKENS,
                           cancel=token, history=history)
    finish(request, request_id, "OK", None, message=(", ".join(s.number for s in found.sections)
                                                     + (f"; history {len(history)}" if history else ""))[:4000])
    return {"request_id": request_id, "status": "OK", "answer": answer,
            "sections": [{"number": s.number, "title": s.title} for s in found.sections],
            "closed_nearby": found.closed_nearby > 0}


# ------------------------------------------------------------------ report

def report_context(request: Request) -> tuple[list[AllowedObject], list[str], str, set[str], set[str]]:
    """Objects allowed to the user, titles of the denied ones, the description for the model, all dm relations,
    the text columns of the allowed objects."""
    access = call_db(request, "SELECT * FROM app.ai_object_access(%s)", fetch="all")
    allowed = [AllowedObject(int(r["object_id"]), r["schema_name"], r["object_name"]) for r in access if r["is_allowed"]]
    denied = [r["title"] for r in access if not r["is_allowed"]]
    described = {int(r["object_id"]): r for r in access if r["is_allowed"]}
    with closing(connect_readonly(config)) as meta_conn, closing(connect_ai_readonly()) as values_conn:
        try:
            with meta_conn.cursor() as meta_cur, values_conn.cursor() as values_cur:
                description, dm_relations = describe_report_data(meta_cur, values_cur, allowed, described, denied)
                text_columns = report_text_columns(meta_cur, allowed)
        finally:
            meta_conn.rollback()
            values_conn.rollback()
    return allowed, denied, description, dm_relations, text_columns


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
    allowed, denied, context, dm_relations, text_columns = report_context(request)
    if not allowed:
        finish(request, request_id, "DENIED", "AI_OBJECT_NOT_ALLOWED", message="no reporting object for the role")
        return {"request_id": request_id, "status": "DENIED", "code": "AI_OBJECT_NOT_ALLOWED", "message": DENIED_TEXT}
    with Stoppable(request, payload.client_id) as token:
        return report_attempts(request, request_id, settings, payload.question, allowed, context, dm_relations, token,
                               dialogue(payload.history), text_columns=text_columns)


def report_attempts(request: Request, request_id: int, settings: ai_llm.LlmSettings, question: str,
                    allowed: list[AllowedObject], context: str, dm_relations: set[str],
                    token: ai_llm.CancelToken, history: list[tuple[str, str]] | None = None,
                    text_columns: set[str] | None = None) -> dict:
    user_text = question
    started = time.monotonic()
    for attempt in (1, 2):
        # The dialogue (earlier questions and their SQL) lets the user refine a report: "and now by month".
        answer = ask_model(request, request_id, settings, report_system(context), user_text, max_tokens=600,
                           cancel=token, history=history)
        sql_text = extract_sql(answer)
        try:
            statement, object_ids = check_text(sql_text, allowed, dm_relations)
            # names typed by the user are compared without regard to case (ƏSGƏROV = Əsgərov)
            statement = fold_text_equality(statement, text_columns or set())
        except SqlRejected as exc:
            outcome = {"AI_OBJECT_NOT_ALLOWED": "DENIED", "AI_NO_DATA": "NO_DATA"}.get(exc.code, "REFUSED")
            finish(request, request_id, outcome, exc.code, sql_text=sql_text[:20000], message=str(exc))
            if outcome == "DENIED":
                # The SQL may name a closed object: it stays in the journal for the administrator only.
                return {"request_id": request_id, "status": outcome, "code": exc.code, "message": DENIED_TEXT}
            return {"request_id": request_id, "status": outcome, "code": exc.code, "message": str(exc),
                    "sql": sql_text}
        try:
            columns, rows, truncated = run_report_sql(statement, allowed)
        except SqlRejected as exc:
            finish(request, request_id, "DENIED", exc.code, sql_text=statement, object_ids=object_ids,
                   message=str(exc))
            return {"request_id": request_id, "status": "DENIED", "code": exc.code, "message": DENIED_TEXT}
        except psycopg.Error as exc:
            message = _db_message(exc)
            if attempt == 1:
                # One self-repair: the model gets its query and the PostgreSQL error, the new query is checked again.
                user_text = repair_message(question, statement, message)
                continue
            finish(request, request_id, "ERROR", "AI_SQL_ERROR", sql_text=statement, object_ids=object_ids,
                   message=message)
            return {"request_id": request_id, "status": "ERROR", "code": "AI_SQL_ERROR", "message": message,
                    "sql": statement}
        code = "AI_REPORT_TRUNCATED" if truncated else ("AI_REPORT_REPAIRED" if attempt == 2 else None)
        finish(request, request_id, "OK", code, sql_text=statement, object_ids=object_ids, rows=len(rows),
               message="SQL repaired after a PostgreSQL error" if attempt == 2 else None)
        return {"request_id": request_id, "status": "OK", "sql": statement, "columns": columns,
                "rows": jsonable_encoder(rows), "truncated": truncated, "repaired": attempt == 2,
                "seconds": round(time.monotonic() - started, 2)}
    raise HTTPException(status_code=500, detail="report attempts exhausted")


# ------------------------------------------------------------------ diagnosis of load errors (I17-10)

DIAGNOSE_MAX_TOKENS = 900


@router.post("/diagnose")
def diagnose(payload: DiagnoseRequest, request: Request) -> dict:
    user = user_scope(request)
    if "ETL_VIEW" not in user.permissions:
        raise HTTPException(status_code=403, detail="AI_DIAGNOSE_NOT_ALLOWED: ETL_VIEW is required")
    with closing(connect_readonly(config)) as conn:
        try:
            if payload.kind == "mart":
                dossier = ai_diagnosis.mart_dossier(conn, payload.id)
            elif payload.kind == "object":
                dossier = ai_diagnosis.error_dossier(conn, _session_token_hash(request), object_id=payload.id)
            else:
                dossier = ai_diagnosis.error_dossier(conn, _session_token_hash(request), error_id=payload.id)
        except ai_diagnosis.DiagnosisError as exc:
            raise HTTPException(status_code=404, detail=f"AI_DIAGNOSE_NOT_FOUND: {exc}") from exc
        finally:
            conn.rollback()
    card = ai_diagnosis.card(dossier, payload.language)
    if payload.card_only:
        return {"status": "CARD", "card": card}
    settings = llm_settings()
    subject = (f"mart {dossier.mart_code}" if dossier.kind == "MART" else f"error {dossier.error_id}")
    # The journal keeps what was analysed, not the dossier (values of the data stay out of it).
    request_id = begin(request, "HELP", f"[diagnose {subject}] {payload.question or ''}".strip()[:4000], False, settings)
    system, text = ai_diagnosis.prompt(dossier, payload.language, payload.question)
    with Stoppable(request, payload.client_id) as token:
        answer = ask_model(request, request_id, settings, system, text, max_tokens=DIAGNOSE_MAX_TOKENS, cancel=token)
    finish(request, request_id, "OK", None, message=f"diagnose {subject} code {dossier.code or '-'}")
    return {"request_id": request_id, "status": "OK", "card": card, "answer": answer,
            "facts": len(dossier.facts), "unavailable": dossier.unavailable}


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
