"""I12: administration of file objects (package 034).

  POST /api/admin/integrations/source-systems/{id}/file-objects          new file object (code)
  GET  /api/admin/integrations/source-objects/{id}/file-config           draft and published settings, folder
  PUT  /api/admin/integrations/source-objects/{id}/file-config           save the settings into the draft
  POST /api/admin/integrations/source-objects/{id}/file-config/check     files of the folder by the mask: date, records,
                                                                         what the load would do; columns with suggested
                                                                         types of the newest file (no values)
  POST /api/admin/integrations/source-objects/{id}/file-config/structure save the columns as the object's snapshot
  GET  /api/admin/integrations/source-objects/{id}/files                 register of the files of the object

The database checks the session (app.integration_admin_session_user). Files are only read (F1): the folder is
<DWH_FILE_ROOT>/<code of the source>, never a path from the request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from dwh_api import api_request as ar
from dwh_api import file_gateway as fg
from dwh_api.api_source_admin import (STRUCTURE_TYPES, ProbeColumn, StructureBody, StructureResult, _connect_and,
                                      _execute, ensure_draft)

router = APIRouter(prefix="/api/admin/integrations", tags=["Administration - Integrations"])

CHECK_FILES = 20


def _file_error(exc: fg.FileSourceError) -> HTTPException:
    return HTTPException(status_code=422, detail=f"{exc.code}: {exc.message}")


class FileObjectCreate(BaseModel):
    code: str = Field(min_length=1, max_length=63)
    description: str | None = Field(default=None, max_length=4000)


class FileObjectCreated(BaseModel):
    source_object_id: int


class FileConfigState(BaseModel):
    draft_version_id: int | None
    draft_config: dict[str, Any] | None
    published_version_id: int | None
    published_config: dict[str, Any] | None
    source_code: str
    folder: str
    folder_exists: bool
    max_file_mb: int


class FileConfigSave(BaseModel):
    config: dict[str, Any]


class FileCheckBody(BaseModel):
    config: dict[str, Any] | None = None


class CheckedFile(BaseModel):
    name: str
    size: int
    modified_at: datetime
    actual_at: datetime | None
    records: int | None
    verdict: str  # LOAD, NOT_ACTUAL, NO_DATE, BROKEN, WAIT, LOADED_BEFORE, TOO_LARGE
    message: str | None = None


class FileCheckResult(BaseModel):
    folder: str
    mask: str
    files_total: int
    files: list[CheckedFile]
    last_loaded_at: datetime | None
    sample_file: str | None
    columns: list[ProbeColumn]
    type_choices: list[str]


class RegisteredFile(BaseModel):
    file_id: int
    file_name: str
    file_size: int
    sha256: str
    actual_at: datetime | None
    status: str
    run_id: int | None
    rows_read: int | None
    attempts: int
    message: str | None
    first_seen_at: datetime
    updated_at: datetime


def config_state(conn, token_hash: str, request_id: str, source_object_id: int) -> dict:
    (row,) = _execute(conn, request_id, "SELECT * FROM app.integration_file_config_get(%s, %s)",
                      (token_hash, source_object_id))
    return row


def _folder_of(code: str) -> tuple[str, bool]:
    path = fg.file_root() / code
    try:
        fg.source_folder(code)
        return str(path), True
    except fg.FileSourceError:
        return str(path), False


def save_config(conn, token_hash: str, request_id: str, source_object_id: int, config: dict) -> dict:
    try:
        doc = fg.normalize(config, draft=True)
    except fg.FileSourceError as exc:
        raise _file_error(exc) from None
    ensure_draft(conn, token_hash, request_id, source_object_id)
    _execute(conn, request_id, "SELECT app.integration_file_config_save(%s, %s, %s::jsonb) AS version_id",
             (token_hash, source_object_id, Jsonb(doc)))
    return doc


def registry(conn, token_hash: str, request_id: str, source_object_id: int, limit: int = 100) -> list[dict]:
    return _execute(conn, request_id, "SELECT * FROM app.integration_file_registry(%s, %s, %s)",
                    (token_hash, source_object_id, limit))


def check_files(conn, token_hash: str, request_id: str, source_object_id: int, config: dict | None,
                now: float | None = None) -> FileCheckResult:
    """What the next load would do with the files of the folder (the newest CHECK_FILES by modification time)."""
    import time

    state = config_state(conn, token_hash, request_id, source_object_id)
    raw = config or state["draft_config"] or state["published_config"]
    if not raw:
        raise HTTPException(status_code=422, detail="Save the file settings first")
    try:
        cfg = fg.normalize(raw)
        folder = fg.source_folder(state["source_code"])
        entries = fg.list_files(folder, cfg["mask"])
    except fg.FileSourceError as exc:
        raise _file_error(exc) from None
    known = {r["sha256"]: r for r in registry(conn, token_hash, request_id, source_object_id, 1000)}
    loaded = [r["actual_at"] for r in known.values() if r["status"] == "LOADED" and r["actual_at"]]
    last = max(loaded) if loaded else None
    now = time.time() if now is None else now
    shown = sorted(entries, key=lambda e: e.mtime, reverse=True)[:CHECK_FILES]
    files: list[CheckedFile] = []
    sample: tuple[datetime, str, list[dict]] | None = None
    for entry in shown:
        item = CheckedFile(name=entry.name, size=entry.size, actual_at=None, records=None, verdict="LOAD",
                           modified_at=datetime.fromtimestamp(entry.mtime, timezone.utc))
        try:
            body, sha = fg.read_file(entry)
        except fg.FileSourceError as exc:
            item.verdict, item.message = ("TOO_LARGE" if exc.code == "FILE_TOO_LARGE" else "BROKEN"), exc.message
            files.append(item)
            continue
        try:
            parsed = fg.parse_file(body, cfg)
        except fg.FileSourceError as exc:
            waiting = exc.code == "FILE_BROKEN" and now - entry.mtime < cfg["wait_seconds"]
            item.verdict, item.message = ("WAIT" if waiting else "BROKEN"), f"{exc.code}: {exc.message}"
            files.append(item)
            continue
        item.actual_at, item.records = parsed.actual_at, len(parsed.records)
        if sha in known and known[sha]["status"] == "LOADED":
            item.verdict = "LOADED_BEFORE"
        elif parsed.actual_at is None:
            item.verdict = "NO_DATE"
            item.message = f"No date at '{cfg['date_path']}'" + (
                f" (value '{parsed.date_text[:40]}')" if parsed.date_text else "")
        elif last is not None and parsed.actual_at <= last:
            item.verdict = "NOT_ACTUAL"
        if parsed.records and (sample is None or (parsed.actual_at or datetime.min.replace(tzinfo=timezone.utc))
                               > sample[0]):
            sample = (parsed.actual_at or datetime.min.replace(tzinfo=timezone.utc), entry.name, parsed.records[:1000])
        files.append(item)
    if cfg["mode"] == "LATEST":
        candidates = [f for f in files if f.verdict == "LOAD"]
        newest = max(candidates, key=lambda f: (f.actual_at, f.name), default=None)
        for f in candidates:
            if f is not newest:
                f.verdict, f.message = "NOT_ACTUAL", "A newer file would be loaded"
    columns = ar.infer_columns(sample[2]) if sample else []
    return FileCheckResult(folder=str(folder), mask=cfg["mask"], files_total=len(entries), files=files,
                           last_loaded_at=last, sample_file=sample[1] if sample else None,
                           columns=[ProbeColumn(**c) for c in columns], type_choices=list(STRUCTURE_TYPES))


# ------------------------------------------------------------------ routes


@router.post("/source-systems/{source_system_id}/file-objects", response_model=FileObjectCreated)
def post_file_object(request: Request, payload: FileObjectCreate,
                     source_system_id: int = PathParam(ge=1)) -> FileObjectCreated:
    def op(conn, token_hash, request_id):
        (row,) = _execute(conn, request_id, "SELECT app.integration_file_object_create(%s, %s, %s, %s) AS id",
                          (token_hash, source_system_id, payload.code, payload.description))
        return FileObjectCreated(source_object_id=row["id"])
    return _connect_and(request, op)


@router.get("/source-objects/{source_object_id}/file-config", response_model=FileConfigState)
def get_file_config(request: Request, source_object_id: int = PathParam(ge=1)) -> FileConfigState:
    def op(conn, token_hash, request_id):
        row = config_state(conn, token_hash, request_id, source_object_id)
        folder, exists = _folder_of(row["source_code"])
        return FileConfigState(draft_version_id=row["draft_version_id"], draft_config=row["draft_config"],
                               published_version_id=row["published_version_id"],
                               published_config=row["published_config"], source_code=row["source_code"],
                               folder=folder, folder_exists=exists, max_file_mb=fg.max_bytes() // 2**20)
    return _connect_and(request, op)


@router.put("/source-objects/{source_object_id}/file-config", response_model=FileConfigSave)
def put_file_config(request: Request, payload: FileConfigSave, source_object_id: int = PathParam(ge=1)) -> FileConfigSave:
    return _connect_and(request, lambda c, t, r: FileConfigSave(config=save_config(c, t, r, source_object_id,
                                                                                    payload.config)))


@router.post("/source-objects/{source_object_id}/file-config/check", response_model=FileCheckResult)
def post_file_check(request: Request, payload: FileCheckBody, source_object_id: int = PathParam(ge=1)) -> FileCheckResult:
    return _connect_and(request, lambda c, t, r: check_files(c, t, r, source_object_id, payload.config))


@router.post("/source-objects/{source_object_id}/file-config/structure", response_model=StructureResult)
def post_file_structure(request: Request, payload: StructureBody,
                        source_object_id: int = PathParam(ge=1)) -> StructureResult:
    names = [c.name for c in payload.columns]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=422, detail="Column names must be unique")
    columns = [{"column_name": c.name, "formatted_type": c.type, "is_nullable": c.nullable} for c in payload.columns]

    def op(conn, token_hash, request_id):
        (row,) = _execute(conn, request_id, "SELECT * FROM app.integration_file_object_capture(%s, %s, %s::jsonb)",
                          (token_hash, source_object_id, Jsonb(columns)))
        return StructureResult(metadata_capture_id=row["metadata_capture_id"], columns_captured=row["columns_captured"])
    return _connect_and(request, op)


@router.get("/source-objects/{source_object_id}/files", response_model=list[RegisteredFile])
def get_files(request: Request, source_object_id: int = PathParam(ge=1),
              limit: int = Query(default=100, ge=1, le=1000)) -> list[RegisteredFile]:
    return _connect_and(request, lambda c, t, r: [RegisteredFile(**row) for row in registry(c, t, r, source_object_id,
                                                                                             limit)])
