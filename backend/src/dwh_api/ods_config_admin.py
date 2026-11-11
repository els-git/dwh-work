"""ODS configuration of a source object (I06-05, package 012).

Versions, drafts, publication and change proposals of the ODS line are stored only through
app.integration_ods_config_* / app.integration_ods_proposal_create, which check the DWH session
and write the audit trail. This module adds what the database cannot do itself: the validation
of a draft against the STG table of the object and the ODS table (types, white list rules of
each transformation, key, required columns) and the DDL text of a change proposal for a new
ODS table or new columns. DDL is never executed here (decision O5).
"""

from __future__ import annotations

import re
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Path as PathParam, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from dwh_api import field_types
from dwh_api.config import get_api_config
from dwh_api.db import connect_control
from dwh_api.integration_admin import _session_token_hash
from dwh_api.object_config_admin import (
    NAME_PATTERN,
    TYPE_CHOICES,
    TYPE_PATTERN,
    DraftRevisionRequest,
    DraftSaveResponse,
    ProposalResponse,
    PublishResponse,
    ValidationResponse,
    _execute,
    _find_target,
    _issue,
    build_proposal,
    compatibility_matrix,
    has_errors,
    read_targets,
)

router = APIRouter(
    prefix="/api/admin/integrations",
    tags=["Administration - Integrations"],
)

config = get_api_config()

FIELD_LIMIT = 1000
TRANSFORMS = ("COPY", "CAST", "TRIM", "UPPER", "LOWER", "EMPTY_TO_NULL", "DEFAULT", "CONSTANT",
              "DATE_FROM_TEXT", "LOAD_RUN_ID", "LOADED_AT")
NO_STG_COLUMN = ("CONSTANT", "LOAD_RUN_ID", "LOADED_AT")
WITH_PARAM = ("DEFAULT", "CONSTANT", "DATE_FROM_TEXT")
TECHNICAL = ("LOAD_RUN_ID", "LOADED_AT")
TEXT_TRANSFORMS = ("TRIM", "UPPER", "LOWER", "EMPTY_TO_NULL")
DATE_FORMATS = ("YYYY-MM-DD", "DD.MM.YYYY", "YYYYMMDD")


# ============================================================
# Models
# ============================================================


class OdsFieldConfig(BaseModel):
    target_column: str = Field(pattern=NAME_PATTERN)
    transform: Literal["COPY", "CAST", "TRIM", "UPPER", "LOWER", "EMPTY_TO_NULL", "DEFAULT", "CONSTANT",
                       "DATE_FROM_TEXT", "LOAD_RUN_ID", "LOADED_AT"] = "COPY"
    stg_column: str | None = Field(default=None, pattern=NAME_PATTERN)
    stg_type: str | None = Field(default=None, pattern=TYPE_PATTERN)
    target_type: str = Field(pattern=TYPE_PATTERN)
    conversion: Literal["NONE", "SAFE", "CONFIRMED"] = "NONE"
    param: str | None = Field(default=None, min_length=1, max_length=200)
    write_policy: Literal["AUTHORITATIVE", "INSERT_ONLY"] = "AUTHORITATIVE"
    allow_explicit_null: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "OdsFieldConfig":
        # The same rules as the CHECK constraints of meta.object_ods_field (package 012).
        if (self.transform in NO_STG_COLUMN) != (self.stg_column is None):
            raise ValueError("STG column is required for this transformation and forbidden for constants")
        if (self.stg_column is None) != (self.stg_type is None):
            raise ValueError("STG column and STG type go together")
        if (self.transform in WITH_PARAM) != (self.param is not None):
            raise ValueError("A value is required for DEFAULT, CONSTANT and DATE_FROM_TEXT only")
        if self.transform == "DATE_FROM_TEXT" and self.param not in DATE_FORMATS:
            raise ValueError("Date format is not in the list")
        if self.transform in TECHNICAL and self.write_policy != "INSERT_ONLY":
            raise ValueError("Technical fields are INSERT_ONLY")
        return self


class OdsConfigDocument(BaseModel):
    target_table: str | None = Field(default=None, pattern=NAME_PATTERN)
    fields: list[OdsFieldConfig] = Field(default_factory=list, max_length=FIELD_LIMIT)
    key: list[str] = Field(default_factory=list, max_length=32)


class OdsDraftSaveRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    config: OdsConfigDocument


class OdsConfigResponse(BaseModel):
    config: dict[str, Any]
    stg_columns: list[dict[str, Any]] | None
    targets: list[dict[str, Any]]
    type_choices: list[str]
    transforms: list[str]
    date_formats: list[str]
    compatibility: dict[str, str]


# ============================================================
# Validation (pure functions)
# ============================================================


def value_fits(value: str, type_name: str) -> bool:
    """A constant (DEFAULT / CONSTANT) can be stored in the target type."""
    base, a, b = field_types.parse(type_name)
    try:
        if base in ("INTEGER", "BIGINT"):
            if not re.fullmatch(r"[+-]?\d+", value):
                return False
            limit = 2 ** 31 if base == "INTEGER" else 2 ** 63
            return -limit <= int(value) < limit
        if base in ("NUMERIC", "FLOAT"):
            number = Decimal(value)
            if base == "NUMERIC" and a is not None:
                digits = len(number.quantize(Decimal(1).scaleb(-(b or 0))).as_tuple().digits)
                return digits <= a
            return number.is_finite()
        if base == "BOOLEAN":
            return value.lower() in ("true", "false")
        if base == "DATE":
            date.fromisoformat(value)
            return True
        if base == "TEXT":
            return a is None or len(value) <= a
    except (ValueError, InvalidOperation):
        return False
    return False


def validate_ods(definition: dict, stg: dict | None, stg_columns: list[dict] | None, target: dict | None) -> list[dict]:
    """Issues of an ODS draft; ERROR blocks publication, WARNING does not."""
    issues: list[dict] = []
    fields = definition.get("fields") or []
    key = definition.get("key") or []
    if stg is None:
        issues.append(_issue("ERROR", "STG_NOT_PUBLISHED"))
    if not definition.get("target_table"):
        issues.append(_issue("ERROR", "TARGET_NOT_SELECTED"))
    elif target is None:
        issues.append(_issue("ERROR", "TARGET_TABLE_MISSING"))
    if not fields:
        issues.append(_issue("ERROR", "NO_FIELDS"))
    stg_by_name = {c["name"]: c for c in (stg_columns or [])}
    target_columns = {c["name"]: c for c in (target or {}).get("columns", [])}
    by_target: dict[str, dict] = {}
    used_stg: set[str] = set()
    for f in fields:
        column = f["target_column"]
        if column in by_target:
            issues.append(_issue("ERROR", "DUPLICATE_TARGET_COLUMN", column))
            continue
        by_target[column] = f
        transform, stg_column, target_type = f["transform"], f.get("stg_column"), f["target_type"]
        base = field_types.parse(target_type)[0]
        if stg_column:
            used_stg.add(stg_column)
            actual = stg_by_name.get(stg_column)
            if stg_columns is not None and actual is None:
                issues.append(_issue("ERROR", "STG_COLUMN_MISSING", column, source_column=stg_column))
            elif actual is not None and actual["type"] != f.get("stg_type"):
                issues.append(_issue("ERROR", "STG_TYPE_CHANGED", column, source_column=stg_column,
                                     expected=f.get("stg_type"), actual=actual["type"]))
        if target is not None:
            actual = target_columns.get(column)
            if actual is None:
                issues.append(_issue("ERROR", "TARGET_COLUMN_MISSING", column))
            elif actual["type"] != target_type:
                issues.append(_issue("ERROR", "TARGET_TYPE_MISMATCH", column, expected=target_type, actual=actual["type"]))
        stg_type = f.get("stg_type")
        stg_base = field_types.parse(stg_type)[0] if stg_type else None
        if transform in ("COPY", "DEFAULT") and stg_type and stg_type != target_type \
                and field_types.compatibility(stg_type, target_type) != field_types.SAFE:
            issues.append(_issue("ERROR", "TRANSFORM_NEEDS_CAST", column, source_type=stg_type, target_type=target_type))
        if transform == "CAST" and stg_type:
            compat = field_types.compatibility(stg_type, target_type)
            if compat == field_types.INCOMPATIBLE:
                issues.append(_issue("ERROR", "TYPE_INCOMPATIBLE", column, source_type=stg_type, target_type=target_type))
            elif compat == field_types.CONDITIONAL and f.get("conversion") != "CONFIRMED":
                issues.append(_issue("ERROR", "CONVERSION_NOT_CONFIRMED", column, source_type=stg_type,
                                     target_type=target_type))
        if transform in TEXT_TRANSFORMS and (stg_base != "TEXT" or base != "TEXT"):
            issues.append(_issue("ERROR", "TRANSFORM_NEEDS_TEXT", column))
        if transform == "DATE_FROM_TEXT" and (stg_base != "TEXT" or base != "DATE"):
            issues.append(_issue("ERROR", "DATE_FROM_TEXT_TYPES", column))
        if transform in ("DEFAULT", "CONSTANT") and f.get("param") is not None and not value_fits(f["param"], target_type):
            issues.append(_issue("ERROR", "VALUE_INVALID", column, target_type=target_type))
        if transform == "LOAD_RUN_ID" and base not in ("BIGINT", "NUMERIC"):
            issues.append(_issue("ERROR", "TECHNICAL_TYPE", column, target_type=target_type))
        if transform == "LOADED_AT" and base not in ("TIMESTAMP", "TIMESTAMPTZ"):
            issues.append(_issue("ERROR", "TECHNICAL_TYPE", column, target_type=target_type))
    if fields and not key:
        issues.append(_issue("ERROR", "KEY_EMPTY"))
    for part in key:
        f = by_target.get(part)
        if f is None:
            issues.append(_issue("ERROR", "KEY_FIELD_MISSING", part))
        elif not f.get("stg_column") or f.get("allow_explicit_null") or f["transform"] in ("DEFAULT", "EMPTY_TO_NULL"):
            issues.append(_issue("ERROR", "KEY_NOT_FROM_STG", part))
    for name, column in target_columns.items():
        if column["not_null"] and not column["has_default"] and name not in by_target:
            issues.append(_issue("ERROR", "TARGET_REQUIRED_UNMAPPED", name))
    for name in stg_by_name:
        if name not in used_stg:
            issues.append(_issue("WARNING", "STG_COLUMN_UNMAPPED", source_column=name))
    return issues


# ============================================================
# Operations
# ============================================================


def read_ods_config(conn, token_hash: str, request_id: str, source_object_id: int) -> dict:
    (row,) = _execute(conn, request_id, "SELECT app.integration_ods_config_get(%s, %s) AS config",
                      (token_hash, source_object_id))
    return row["config"]


def _stg_columns(targets: list[dict], stg: dict | None) -> list[dict] | None:
    table = _find_target(targets, "stg", stg["table"]) if stg else None
    return table["columns"] if table else None


def _draft(cfg: dict, draft_revision: int) -> dict:
    draft = cfg.get("draft")
    if not draft or draft.get("draft_revision") != draft_revision:
        raise HTTPException(status_code=409, detail="Draft not found or changed by another user")
    return draft["definition"]


def load_ods_config(conn, token_hash: str, request_id: str, source_object_id: int) -> OdsConfigResponse:
    cfg = read_ods_config(conn, token_hash, request_id, source_object_id)
    targets = read_targets(conn, request_id)
    stg_columns = _stg_columns(targets, cfg.get("stg"))
    ods_targets = [t for t in targets if t["schema"] == "ods"]
    source_types = {c["type"] for c in stg_columns or []}
    target_types = {c["type"] for t in ods_targets for c in t["columns"]}
    for part in ("current", "draft"):
        for f in ((cfg.get(part) or {}).get("definition") or {}).get("fields", []):
            if f.get("stg_type"):
                source_types.add(f["stg_type"])
            target_types.add(f["target_type"])
    return OdsConfigResponse(config=cfg, stg_columns=stg_columns, targets=ods_targets, type_choices=list(TYPE_CHOICES),
                             transforms=list(TRANSFORMS), date_formats=list(DATE_FORMATS),
                             compatibility=compatibility_matrix(source_types, target_types))


def save_ods_draft(conn, token_hash: str, request_id: str, source_object_id: int,
                   payload: OdsDraftSaveRequest) -> DraftSaveResponse:
    (row,) = _execute(conn, request_id,
                      "SELECT app.integration_ods_config_save_draft(%s, %s, %s, %s::jsonb) AS revision",
                      (token_hash, source_object_id, payload.expected_revision, Jsonb(payload.config.model_dump())))
    return DraftSaveResponse(draft_revision=row["revision"])


def _validate(conn, token_hash: str, request_id: str, source_object_id: int, draft_revision: int):
    cfg = read_ods_config(conn, token_hash, request_id, source_object_id)
    definition = _draft(cfg, draft_revision)
    targets = read_targets(conn, request_id)
    target = _find_target(targets, "ods", definition.get("target_table"))
    return cfg, definition, target, validate_ods(definition, cfg.get("stg"), _stg_columns(targets, cfg.get("stg")),
                                                 target)


def validate_ods_draft(conn, token_hash: str, request_id: str, source_object_id: int,
                       draft_revision: int) -> ValidationResponse:
    _, _, _, issues = _validate(conn, token_hash, request_id, source_object_id, draft_revision)
    ok = not has_errors(issues)
    _execute(conn, request_id, "SELECT app.integration_ods_config_record_validation(%s, %s, %s, %s, %s::jsonb) AS id",
             (token_hash, source_object_id, draft_revision, ok, Jsonb(issues)))
    return ValidationResponse(ok=ok, issues=issues, draft_revision=draft_revision)


def publish_ods_draft(conn, token_hash: str, request_id: str, source_object_id: int,
                      draft_revision: int) -> PublishResponse:
    # The STG and ODS tables may have changed since the validation: check again before publishing.
    _, _, _, issues = _validate(conn, token_hash, request_id, source_object_id, draft_revision)
    if has_errors(issues):
        raise HTTPException(status_code=422, detail="Validation is outdated or failed; check the draft again")
    (row,) = _execute(conn, request_id, "SELECT app.integration_ods_config_publish(%s, %s, %s) AS version_no",
                      (token_hash, source_object_id, draft_revision))
    return PublishResponse(version_no=row["version_no"], status="READY")


def create_ods_proposal(conn, token_hash: str, request_id: str, source_object_id: int,
                        draft_revision: int) -> ProposalResponse:
    cfg, definition, target, _ = _validate(conn, token_hash, request_id, source_object_id, draft_revision)
    proposal_definition = {**definition, "target_schema": "ods"}
    stg = cfg.get("stg") or {}
    label = f'ODS of object {source_object_id} (from stg.{stg.get("table", "?")})'
    change_kind, ddl = build_proposal(proposal_definition, target, label, task="I06-05")
    (row,) = _execute(conn, request_id,
                      "SELECT app.integration_ods_proposal_create(%s, %s, %s, %s, %s) AS proposal_id",
                      (token_hash, source_object_id, change_kind, definition["target_table"], ddl))
    return ProposalResponse(proposal_id=row["proposal_id"], change_kind=change_kind, target_schema="ods",
                            target_table=definition["target_table"], ddl=ddl)


# ============================================================
# Routes
# ============================================================


def _run(request: Request, operation, *args):
    token_hash = _session_token_hash(request)
    with closing(connect_control(config)) as conn:
        return operation(conn, token_hash, str(uuid4()), *args)


@router.get("/source-objects/{source_object_id}/ods-config", response_model=OdsConfigResponse)
def get_ods_config(request: Request, source_object_id: int = PathParam(ge=1)) -> OdsConfigResponse:
    return _run(request, load_ods_config, source_object_id)


@router.put("/source-objects/{source_object_id}/ods-config/draft", response_model=DraftSaveResponse)
def put_ods_config_draft(request: Request, source_object_id: int = PathParam(ge=1),
                         payload: OdsDraftSaveRequest = Body(...)) -> DraftSaveResponse:
    return _run(request, save_ods_draft, source_object_id, payload)


@router.post("/source-objects/{source_object_id}/ods-config/validate", response_model=ValidationResponse)
def post_ods_config_validate(request: Request, source_object_id: int = PathParam(ge=1),
                             payload: DraftRevisionRequest = Body(...)) -> ValidationResponse:
    return _run(request, validate_ods_draft, source_object_id, payload.draft_revision)


@router.post("/source-objects/{source_object_id}/ods-config/publish", response_model=PublishResponse)
def post_ods_config_publish(request: Request, source_object_id: int = PathParam(ge=1),
                            payload: DraftRevisionRequest = Body(...)) -> PublishResponse:
    return _run(request, publish_ods_draft, source_object_id, payload.draft_revision)


@router.post("/source-objects/{source_object_id}/ods-config/proposal", response_model=ProposalResponse)
def post_ods_config_proposal(request: Request, source_object_id: int = PathParam(ge=1),
                             payload: DraftRevisionRequest = Body(...)) -> ProposalResponse:
    return _run(request, create_ods_proposal, source_object_id, payload.draft_revision)
