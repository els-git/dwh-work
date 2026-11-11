"""Field mapping configuration of a source object (I05, package 008).

Versions, drafts, publication and change proposals are stored only through the
app.integration_object_config_* / app.integration_target_proposal_* functions,
which check the DWH session and write the audit trail. This module adds what
the database cannot do itself: normalized types and the compatibility matrix
(field_types), the validation of a draft against the latest schema snapshot and
the target table, schema drift, and the DDL text of a change proposal. DDL is
never executed here (decision M5).
"""

from __future__ import annotations

from contextlib import closing
from typing import Any, Literal
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Body, HTTPException, Path as PathParam, Request
from pydantic import BaseModel, Field

from dwh_api import field_types
from dwh_api.config import get_api_config
from dwh_api.db import connect_control
from dwh_api.integration_admin import (
    _db_message,
    _raise_database_error,
    _session_token_hash,
    _set_request_context,
)


router = APIRouter(
    prefix="/api/admin/integrations",
    tags=["Administration - Integrations"],
)

config = get_api_config()

TARGET_SCHEMAS = ("stg", "ods")
NAME_PATTERN = r"^[a-z_][a-z0-9_]{0,62}$"
TYPE_PATTERN = r"^[A-Z]+(\([0-9]{1,4}(,[0-9]{1,4})?\))?$"
FIELD_LIMIT = 1000
TYPE_CHOICES = ("TEXT", "INTEGER", "BIGINT", "NUMERIC", "FLOAT", "BOOLEAN", "DATE", "TIME",
                "TIMESTAMP", "TIMESTAMPTZ", "UUID", "JSON", "BYTES")


# ============================================================
# Models
# ============================================================


class FieldConfig(BaseModel):
    target_column: str = Field(pattern=NAME_PATTERN)
    source_column: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: str = Field(pattern=TYPE_PATTERN)
    target_type: str = Field(pattern=TYPE_PATTERN)
    conversion: Literal["NONE", "SAFE", "CONFIRMED"] = "NONE"
    write_policy: Literal["AUTHORITATIVE", "INSERT_ONLY", "NO_WRITE"] = "AUTHORITATIVE"
    allow_explicit_null: bool = False


class ObjectConfigDocument(BaseModel):
    target_schema: Literal["stg", "ods"] | None = None
    target_table: str | None = Field(default=None, pattern=NAME_PATTERN)
    fields: list[FieldConfig] = Field(default_factory=list, max_length=FIELD_LIMIT)
    key: list[str] = Field(default_factory=list, max_length=32)
    # I07 (package 011): load strategy of the version.
    load_strategy: Literal["FULL", "INCREMENTAL"] = "FULL"
    change_column: str | None = Field(default=None, pattern=NAME_PATTERN)
    overlap_minutes: int = Field(default=10, ge=0, le=10080)
    delete_flag_column: str | None = Field(default=None, pattern=NAME_PATTERN)


class DraftSaveRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    config: ObjectConfigDocument


class DraftRevisionRequest(BaseModel):
    draft_revision: int = Field(ge=1)


class DraftSaveResponse(BaseModel):
    draft_revision: int


class ValidationResponse(BaseModel):
    ok: bool
    issues: list[dict[str, Any]]
    draft_revision: int


class PublishResponse(BaseModel):
    version_no: int
    status: str


class DriftResponse(BaseModel):
    status: str
    changes: list[dict[str, Any]]


class ProposalResponse(BaseModel):
    proposal_id: int
    change_kind: str
    target_schema: str
    target_table: str
    ddl: str


class RejectResponse(BaseModel):
    rejected: bool


class ObjectConfigResponse(BaseModel):
    config: dict[str, Any]
    snapshot: dict[str, Any] | None
    targets: list[dict[str, Any]]
    type_choices: list[str]
    compatibility: dict[str, str]


# ============================================================
# Errors
# ============================================================


_CONFLICT_MESSAGES = (
    "changed by another user",
    "hash mismatch",
    "not in force or changed",
)
_REJECTED_MESSAGES = (
    "legacy crm",
    "invalid configuration",
    "publish requires",
    "target table",
    "target columns do not exist",
    "business key",
    "key fields",
    "already authoritative",
    "save a draft",
    "already exists",
    "has no fields",
    "not active",
    "not open",
    # I06-05 (package 012): ODS configuration.
    "stg configuration",
    "stg columns do not exist",
    "invalid ods configuration",
    "save an ods draft",
)


def _raise_config_error(exc: psycopg.Error) -> None:
    lowered = _db_message(exc).lower()
    if any(text in lowered for text in _CONFLICT_MESSAGES):
        raise HTTPException(status_code=409, detail=_db_message(exc)) from exc
    if any(text in lowered for text in _REJECTED_MESSAGES):
        raise HTTPException(status_code=422, detail=_db_message(exc)) from exc
    _raise_database_error(exc)


def _execute(conn, request_id: str, sql: str, params: tuple) -> list[dict]:
    """One statement in its own committed transaction; DB errors mapped to HTTP."""
    try:
        with conn.cursor() as cur:
            _set_request_context(cur, request_id)
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    except psycopg.Error as exc:
        conn.rollback()
        _raise_config_error(exc)
        raise  # pragma: no cover - _raise_config_error always raises


# ============================================================
# Reading: configuration, snapshot, targets
# ============================================================


_SNAPSHOT_SQL = """
SELECT mc.metadata_capture_id, mc.finished_at, c.column_name, c.ordinal_position, c.formatted_type,
       c.is_nullable, c.is_primary_key
  FROM meta.source_object_snapshot os
  JOIN meta.metadata_capture mc ON mc.metadata_capture_id = os.metadata_capture_id
  JOIN meta.source_column_snapshot c ON c.object_snapshot_id = os.object_snapshot_id
 WHERE os.source_object_id = %s
   AND os.metadata_capture_id = (
       SELECT max(os2.metadata_capture_id)
         FROM meta.source_object_snapshot os2
         JOIN meta.metadata_capture mc2 ON mc2.metadata_capture_id = os2.metadata_capture_id
        WHERE os2.source_object_id = %s AND mc2.status = 'SUCCESS')
 ORDER BY c.ordinal_position
"""

# Catalog only (no data): dwh_app has no USAGE on stg/ods, pg_catalog is readable.
_TARGETS_SQL = """
SELECT n.nspname AS schema_name, t.relname AS table_name, a.attname AS column_name, a.attnum,
       format_type(a.atttypid, a.atttypmod) AS formatted_type, a.attnotnull,
       (a.atthasdef OR a.attidentity <> '' OR a.attgenerated <> '') AS has_default
  FROM pg_class t
  JOIN pg_namespace n ON n.oid = t.relnamespace
  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
 WHERE n.nspname = ANY(%s) AND t.relkind IN ('r', 'p')
 ORDER BY n.nspname, t.relname, a.attnum
"""


def read_config(conn, token_hash: str, request_id: str, source_object_id: int) -> dict:
    """Configuration document of package 008; checks the session first."""
    (row,) = _execute(conn, request_id, "SELECT app.integration_object_config_get(%s, %s) AS config",
                      (token_hash, source_object_id))
    return row["config"]


def read_snapshot(conn, request_id: str, source_object_id: int) -> dict | None:
    rows = _execute(conn, request_id, _SNAPSHOT_SQL, (source_object_id, source_object_id))
    if not rows:
        return None
    return {
        "metadata_capture_id": rows[0]["metadata_capture_id"],
        "captured_at": rows[0]["finished_at"].isoformat() if rows[0]["finished_at"] else None,
        "columns": [
            {
                "name": r["column_name"],
                "position": r["ordinal_position"],
                "formatted_type": r["formatted_type"],
                "type": field_types.normalize_postgres(r["formatted_type"]),
                "nullable": bool(r["is_nullable"]),
                "primary_key": bool(r["is_primary_key"]),
            }
            for r in rows
        ],
    }


def read_targets(conn, request_id: str) -> list[dict]:
    tables: dict[tuple[str, str], dict] = {}
    for r in _execute(conn, request_id, _TARGETS_SQL, (list(TARGET_SCHEMAS),)):
        table = tables.setdefault((r["schema_name"], r["table_name"]), {
            "schema": r["schema_name"], "table": r["table_name"], "columns": []})
        table["columns"].append({
            "name": r["column_name"],
            "formatted_type": r["formatted_type"],
            "type": field_types.normalize_postgres(r["formatted_type"]),
            "not_null": bool(r["attnotnull"]),
            "has_default": bool(r["has_default"]),
        })
    return list(tables.values())


def _find_target(targets: list[dict], schema: str | None, table: str | None) -> dict | None:
    return next((t for t in targets if t["schema"] == schema and t["table"] == table), None)


def compatibility_matrix(source_types: set[str], target_types: set[str]) -> dict[str, str]:
    targets = set(target_types) | set(TYPE_CHOICES)
    return {f"{s}|{t}": field_types.compatibility(s, t) for s in source_types for t in targets}


# ============================================================
# Validation, drift, change proposals (pure functions)
# ============================================================


def _issue(severity: str, code: str, column: str | None = None, **extra: Any) -> dict:
    issue: dict[str, Any] = {"severity": severity, "code": code}
    if column is not None:
        issue["column"] = column
    issue.update(extra)
    return issue


def validate_definition(definition: dict, snapshot: dict | None, target: dict | None) -> list[dict]:
    """Issues of a draft; ERROR blocks publication, WARNING does not."""
    issues: list[dict] = []
    fields = definition.get("fields") or []
    key = definition.get("key") or []
    if not definition.get("target_schema") or not definition.get("target_table"):
        issues.append(_issue("ERROR", "TARGET_NOT_SELECTED"))
    elif target is None:
        issues.append(_issue("ERROR", "TARGET_TABLE_MISSING"))
    if snapshot is None:
        issues.append(_issue("ERROR", "SNAPSHOT_MISSING"))
    if not fields:
        issues.append(_issue("ERROR", "NO_FIELDS"))
    source_columns = {c["name"]: c for c in (snapshot or {}).get("columns", [])}
    target_columns = {c["name"]: c for c in (target or {}).get("columns", [])}
    used_sources: dict[str, str] = {}
    by_target = {}
    for f in fields:
        column = f["target_column"]
        if column in by_target:
            issues.append(_issue("ERROR", "DUPLICATE_TARGET_COLUMN", column))
            continue
        by_target[column] = f
        source = f.get("source_column")
        if source:
            if source in used_sources and f["write_policy"] != "NO_WRITE":
                issues.append(_issue("WARNING", "SOURCE_COLUMN_REUSED", column, source_column=source))
            used_sources.setdefault(source, column)
            if snapshot is not None and source not in source_columns:
                issues.append(_issue("ERROR", "SOURCE_COLUMN_MISSING", column, source_column=source))
            elif snapshot is not None and source_columns[source]["type"] != f["source_type"]:
                issues.append(_issue("ERROR", "SOURCE_TYPE_CHANGED", column, source_column=source,
                                     expected=f["source_type"], actual=source_columns[source]["type"]))
        if target is not None:
            actual = target_columns.get(column)
            if actual is None:
                issues.append(_issue("ERROR", "TARGET_COLUMN_MISSING", column))
            elif actual["type"] != f["target_type"]:
                issues.append(_issue("ERROR", "TARGET_TYPE_MISMATCH", column,
                                     expected=f["target_type"], actual=actual["type"]))
            elif source and f["write_policy"] != "NO_WRITE" and actual["not_null"] \
                    and source_columns.get(source, {}).get("nullable"):
                issues.append(_issue("WARNING", "NULLABLE_TO_NOT_NULL", column, source_column=source))
        if f["write_policy"] == "NO_WRITE" and not source:
            continue
        compat = field_types.compatibility(f["source_type"], f["target_type"])
        if compat == field_types.INCOMPATIBLE:
            issues.append(_issue("ERROR", "TYPE_INCOMPATIBLE", column,
                                 source_type=f["source_type"], target_type=f["target_type"]))
        elif compat == field_types.CONDITIONAL and f["conversion"] != "CONFIRMED":
            issues.append(_issue("ERROR", "CONVERSION_NOT_CONFIRMED", column,
                                 source_type=f["source_type"], target_type=f["target_type"]))
        elif compat != field_types.CONDITIONAL and f["conversion"] != field_types.conversion_for(compat, False):
            issues.append(_issue("ERROR", "CONVERSION_MISMATCH", column, expected=field_types.conversion_for(compat, False)))
    if fields and not key:
        issues.append(_issue("ERROR", "KEY_EMPTY"))
    for part in key:
        f = by_target.get(part)
        if f is None:
            issues.append(_issue("ERROR", "KEY_FIELD_MISSING", part))
            continue
        if f["write_policy"] == "NO_WRITE" or not f.get("source_column"):
            issues.append(_issue("ERROR", "KEY_NOT_WRITTEN", part))
        if f.get("allow_explicit_null"):
            issues.append(_issue("ERROR", "KEY_NULL_ALLOWED", part))
        if source_columns.get(f.get("source_column") or "", {}).get("nullable"):
            issues.append(_issue("WARNING", "KEY_SOURCE_NULLABLE", part))
    for name, column in target_columns.items():
        mapped = by_target.get(name)
        if column["not_null"] and not column["has_default"] and (mapped is None or mapped["write_policy"] == "NO_WRITE"):
            issues.append(_issue("ERROR", "TARGET_REQUIRED_UNMAPPED", name))
    if snapshot is not None:
        for name in source_columns:
            if name not in used_sources:
                issues.append(_issue("WARNING", "SOURCE_COLUMN_UNMAPPED", source_column=name))
    issues.extend(validate_strategy(definition, by_target))
    return issues


CHANGE_COLUMN_TYPES = ("DATE", "TIMESTAMP", "TIMESTAMPTZ", "INTEGER", "BIGINT")


def strategy_of(definition: dict) -> dict:
    """Load strategy of a definition (nested "strategy" from the database or flat keys of a draft)."""
    raw = definition.get("strategy") or definition
    return {
        "load_strategy": raw.get("load_strategy") or "FULL",
        "change_column": raw.get("change_column"),
        "overlap_minutes": 10 if raw.get("overlap_minutes") is None else int(raw.get("overlap_minutes")),
        "delete_flag_column": raw.get("delete_flag_column"),
    }


def _written(f: dict | None) -> bool:
    return bool(f and f.get("source_column") and f["write_policy"] != "NO_WRITE")


def validate_strategy(definition: dict, by_target: dict) -> list[dict]:
    """I07: the same rules as the database checks at publication (package 011)."""
    issues: list[dict] = []
    strategy = strategy_of(definition)
    change = strategy["change_column"]
    flag = strategy["delete_flag_column"]
    if strategy["load_strategy"] == "INCREMENTAL" and not change:
        issues.append(_issue("ERROR", "CHANGE_COLUMN_REQUIRED"))
    if change:
        f = by_target.get(change)
        if not _written(f):
            issues.append(_issue("ERROR", "CHANGE_COLUMN_NOT_MAPPED", change))
        elif (field_types.parse(f["target_type"])[0] not in CHANGE_COLUMN_TYPES
              or field_types.parse(f["source_type"])[0] not in CHANGE_COLUMN_TYPES):
            # The engine compares the source column with the watermark in the source type.
            issues.append(_issue("ERROR", "CHANGE_COLUMN_TYPE", change, target_type=f["target_type"]))
    if flag:
        f = by_target.get(flag)
        if not _written(f):
            issues.append(_issue("ERROR", "DELETE_FLAG_NOT_MAPPED", flag))
        elif f["target_type"] != "BOOLEAN":
            issues.append(_issue("ERROR", "DELETE_FLAG_TYPE", flag, target_type=f["target_type"]))
    return issues


def has_errors(issues: list[dict]) -> bool:
    return any(i["severity"] == "ERROR" for i in issues)


def baseline_of(snapshot: dict) -> dict:
    """Source structure recorded at publication; drift is measured against it."""
    return {
        "metadata_capture_id": snapshot["metadata_capture_id"],
        "columns": {c["name"]: c["type"] for c in snapshot["columns"]},
    }


def drift_changes(baseline: dict | None, published: dict, snapshot: dict | None) -> list[dict]:
    """Changes of the source structure since publication (empty = no drift)."""
    if snapshot is None:
        return [{"change": "SNAPSHOT_MISSING"}]
    current = {c["name"]: c["type"] for c in snapshot["columns"]}
    before = (baseline or {}).get("columns")
    if not isinstance(before, dict):
        # No baseline recorded: compare with the source columns of the published fields.
        before = {f["source_column"]: f["source_type"] for f in published.get("fields", []) if f.get("source_column")}
        added: list[str] = []
    else:
        added = [name for name in current if name not in before]
    mapped = {f["source_column"] for f in published.get("fields", []) if f.get("source_column")}
    changes: list[dict] = []
    for name, type_ in before.items():
        if name not in current:
            changes.append({"change": "REMOVED", "source_column": name, "mapped": name in mapped})
        elif current[name] != type_:
            changes.append({"change": "TYPE_CHANGED", "source_column": name, "mapped": name in mapped,
                            "before": type_, "after": current[name]})
    for name in added:
        changes.append({"change": "ADDED", "source_column": name, "mapped": False, "after": current[name]})
    return changes


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def build_proposal(definition: dict, target: dict | None, source_label: str, task: str = "I05") -> tuple[str, str]:
    """(change_kind, DDL) for a missing target table or missing target columns."""
    schema, table = definition.get("target_schema"), definition.get("target_table")
    if not schema or not table:
        raise HTTPException(status_code=422, detail="Target table not selected")
    fields = definition.get("fields") or []
    if not fields:
        raise HTTPException(status_code=422, detail="Configuration has no fields")
    key = definition.get("key") or []
    try:
        types = {f["target_column"]: field_types.postgres_type(f["target_type"]) for f in fields}
    except ValueError:
        raise HTTPException(status_code=422, detail="Target type has no PostgreSQL column type") from None
    header = f"-- {task} change proposal for {source_label}; apply only as a database package (E0 -> E1 -> E2).\n"
    if target is None:
        if not key:
            raise HTTPException(status_code=422, detail="Business key not selected")
        lines = [f"  {_quote(c)} {t}{' NOT NULL' if c in key else ''}" for c, t in types.items()]
        lines.append(f"  CONSTRAINT {_quote(('pk_' + table)[:63])} PRIMARY KEY ({', '.join(_quote(k) for k in key)})")
        ddl = f"{header}CREATE TABLE {_quote(schema)}.{_quote(table)} (\n" + ",\n".join(lines) + "\n);\n"
        return "CREATE_TABLE", ddl
    existing = {c["name"] for c in target["columns"]}
    missing = [c for c in types if c not in existing]
    if not missing:
        raise HTTPException(status_code=422, detail="Target table already has every column of the configuration")
    ddl = header + "".join(f"ALTER TABLE {_quote(schema)}.{_quote(table)} ADD COLUMN {_quote(c)} {types[c]};\n"
                           for c in missing)
    return "ADD_COLUMNS", ddl


# ============================================================
# Operations
# ============================================================


def _draft_definition(cfg: dict, draft_revision: int) -> dict:
    draft = cfg.get("draft")
    if not draft or draft.get("draft_revision") != draft_revision:
        raise HTTPException(status_code=409, detail="Draft not found or changed by another user")
    return draft["definition"]


def load_object_config(conn, token_hash: str, request_id: str, source_object_id: int) -> ObjectConfigResponse:
    cfg = read_config(conn, token_hash, request_id, source_object_id)
    snapshot = read_snapshot(conn, request_id, source_object_id)
    targets = read_targets(conn, request_id)
    source_types = {c["type"] for c in (snapshot or {}).get("columns", [])}
    target_types = {c["type"] for t in targets for c in t["columns"]}
    for part in ("current", "draft"):
        for f in ((cfg.get(part) or {}).get("definition") or {}).get("fields", []):
            source_types.add(f["source_type"])
            target_types.add(f["target_type"])
    return ObjectConfigResponse(config=cfg, snapshot=snapshot, targets=targets, type_choices=list(TYPE_CHOICES),
                                compatibility=compatibility_matrix(source_types, target_types))


def save_draft(conn, token_hash: str, request_id: str, source_object_id: int,
               payload: DraftSaveRequest) -> DraftSaveResponse:
    document = payload.config.model_dump()
    (row,) = _execute(conn, request_id,
                      "SELECT app.integration_object_config_save_draft(%s, %s, %s, %s::jsonb) AS revision",
                      (token_hash, source_object_id, payload.expected_revision, Jsonb(document)))
    return DraftSaveResponse(draft_revision=row["revision"])


def _validate(conn, token_hash: str, request_id: str, source_object_id: int, draft_revision: int):
    cfg = read_config(conn, token_hash, request_id, source_object_id)
    definition = _draft_definition(cfg, draft_revision)
    snapshot = read_snapshot(conn, request_id, source_object_id)
    target = _find_target(read_targets(conn, request_id), definition.get("target_schema"),
                          definition.get("target_table"))
    return definition, snapshot, target, validate_definition(definition, snapshot, target)


def validate_draft(conn, token_hash: str, request_id: str, source_object_id: int,
                   draft_revision: int) -> ValidationResponse:
    _, _, _, issues = _validate(conn, token_hash, request_id, source_object_id, draft_revision)
    ok = not has_errors(issues)
    _execute(conn, request_id, "SELECT app.integration_object_config_record_validation(%s, %s, %s, %s, %s::jsonb) AS id",
             (token_hash, source_object_id, draft_revision, ok, Jsonb(issues)))
    return ValidationResponse(ok=ok, issues=issues, draft_revision=draft_revision)


def publish_draft(conn, token_hash: str, request_id: str, source_object_id: int,
                  draft_revision: int) -> PublishResponse:
    # The stored validation is re-checked by the database; the snapshot and the target
    # may have changed since, so the API checks them again before publishing.
    _, snapshot, _, issues = _validate(conn, token_hash, request_id, source_object_id, draft_revision)
    if has_errors(issues):
        raise HTTPException(status_code=422, detail="Validation is outdated or failed; check the draft again")
    (row,) = _execute(conn, request_id, "SELECT app.integration_object_config_publish(%s, %s, %s) AS version_no",
                      (token_hash, source_object_id, draft_revision))
    cfg = read_config(conn, token_hash, request_id, source_object_id)
    current = cfg["current"]
    (status,) = _execute(conn, request_id,
                         "SELECT app.integration_object_config_set_drift(%s, %s, %s, %s, %s::jsonb) AS status",
                         (token_hash, source_object_id, current["version_id"], False,
                          Jsonb({"baseline": baseline_of(snapshot), "changes": []})))
    return PublishResponse(version_no=row["version_no"], status=status["status"])


def check_drift(conn, token_hash: str, request_id: str, source_object_id: int) -> DriftResponse:
    cfg = read_config(conn, token_hash, request_id, source_object_id)
    current = cfg.get("current")
    if not current:
        raise HTTPException(status_code=422, detail="Object has no published configuration")
    details = current.get("status_details") or {}
    baseline = details.get("baseline")
    snapshot = read_snapshot(conn, request_id, source_object_id)
    changes = drift_changes(baseline, current["definition"], snapshot)
    new_details: dict[str, Any] = {"changes": changes}
    if baseline:
        new_details["baseline"] = baseline
    if snapshot is not None:
        new_details["checked_capture_id"] = snapshot["metadata_capture_id"]
    (row,) = _execute(conn, request_id,
                      "SELECT app.integration_object_config_set_drift(%s, %s, %s, %s, %s::jsonb) AS status",
                      (token_hash, source_object_id, current["version_id"], bool(changes), Jsonb(new_details)))
    return DriftResponse(status=row["status"], changes=changes)


def create_proposal(conn, token_hash: str, request_id: str, source_object_id: int,
                    draft_revision: int) -> ProposalResponse:
    cfg = read_config(conn, token_hash, request_id, source_object_id)
    definition = _draft_definition(cfg, draft_revision)
    target = _find_target(read_targets(conn, request_id), definition.get("target_schema"),
                          definition.get("target_table"))
    obj = cfg["object"]
    label = f'object {obj["source_object_id"]} ({obj["source_schema"]}.{obj["source_object"]})'
    change_kind, ddl = build_proposal(definition, target, label)
    (row,) = _execute(conn, request_id,
                      "SELECT app.integration_target_proposal_create(%s, %s, %s, %s, %s, %s) AS proposal_id",
                      (token_hash, source_object_id, change_kind, definition["target_schema"],
                       definition["target_table"], ddl))
    return ProposalResponse(proposal_id=row["proposal_id"], change_kind=change_kind,
                            target_schema=definition["target_schema"], target_table=definition["target_table"], ddl=ddl)


def drift_after_snapshot(conn, token_hash: str, request_id: str, source_system_id: int) -> int:
    """Re-check drift of every published object of the source after a new snapshot."""
    rows = _execute(conn, request_id,
                    "SELECT source_object_id FROM meta.source_object WHERE source_system_id = %s AND is_active"
                    " ORDER BY source_object_id", (source_system_id,))
    drifted = 0
    for r in rows:
        try:
            result = check_drift(conn, token_hash, request_id, r["source_object_id"])
        except HTTPException as exc:
            if exc.status_code == 422:
                continue  # no published configuration
            raise
        drifted += result.status == "DRIFT"
    return drifted


# ============================================================
# Routes
# ============================================================


def _run(request: Request, operation, *args):
    token_hash = _session_token_hash(request)
    with closing(connect_control(config)) as conn:
        return operation(conn, token_hash, str(uuid4()), *args)


@router.get("/source-objects/{source_object_id}/config", response_model=ObjectConfigResponse)
def get_object_config(request: Request, source_object_id: int = PathParam(ge=1)) -> ObjectConfigResponse:
    return _run(request, load_object_config, source_object_id)


@router.put("/source-objects/{source_object_id}/config/draft", response_model=DraftSaveResponse)
def put_object_config_draft(request: Request, source_object_id: int = PathParam(ge=1),
                            payload: DraftSaveRequest = Body(...)) -> DraftSaveResponse:
    return _run(request, save_draft, source_object_id, payload)


@router.post("/source-objects/{source_object_id}/config/validate", response_model=ValidationResponse)
def post_object_config_validate(request: Request, source_object_id: int = PathParam(ge=1),
                                payload: DraftRevisionRequest = Body(...)) -> ValidationResponse:
    return _run(request, validate_draft, source_object_id, payload.draft_revision)


@router.post("/source-objects/{source_object_id}/config/publish", response_model=PublishResponse)
def post_object_config_publish(request: Request, source_object_id: int = PathParam(ge=1),
                               payload: DraftRevisionRequest = Body(...)) -> PublishResponse:
    return _run(request, publish_draft, source_object_id, payload.draft_revision)


@router.post("/source-objects/{source_object_id}/config/drift-check", response_model=DriftResponse)
def post_object_config_drift_check(request: Request, source_object_id: int = PathParam(ge=1)) -> DriftResponse:
    return _run(request, check_drift, source_object_id)


@router.post("/source-objects/{source_object_id}/config/proposal", response_model=ProposalResponse)
def post_object_config_proposal(request: Request, source_object_id: int = PathParam(ge=1),
                                payload: DraftRevisionRequest = Body(...)) -> ProposalResponse:
    return _run(request, create_proposal, source_object_id, payload.draft_revision)


@router.post("/target-proposals/{proposal_id}/reject", response_model=RejectResponse)
def post_target_proposal_reject(request: Request, proposal_id: int = PathParam(ge=1)) -> RejectResponse:
    def reject(conn, token_hash, request_id):
        (row,) = _execute(conn, request_id, "SELECT app.integration_target_proposal_reject(%s, %s) AS rejected",
                          (token_hash, proposal_id))
        return RejectResponse(rejected=bool(row["rejected"]))
    return _run(request, reject)
