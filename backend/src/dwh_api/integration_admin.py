from __future__ import annotations

import hashlib
from contextlib import closing
from uuid import uuid4

import psycopg
from fastapi import (
    APIRouter,
    HTTPException,
    Path,
    Query,
    Request,
)
from pydantic import BaseModel, Field

from dwh_api.auth import SESSION_COOKIE_NAME
from dwh_api.config import get_api_config
from dwh_api.db import connect_control, connect_readonly


router = APIRouter(
    prefix="/api/admin/integrations",
    tags=["Administration - Integrations"],
)

config = get_api_config()


# ============================================================
# Models
# ============================================================


class SourceSystemCreateRequest(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=64,
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    source_type: str = Field(
        min_length=1,
        max_length=100,
    )

    description: str | None = Field(
        default=None,
        max_length=4000,
    )


class SourceSystemUpdateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=255,
    )

    source_type: str = Field(
        min_length=1,
        max_length=100,
    )

    description: str | None = Field(
        default=None,
        max_length=4000,
    )


class SourceObjectCreateRequest(BaseModel):
    source_system_id: int = Field(
        ge=1,
    )

    source_schema: str = Field(
        min_length=1,
        max_length=255,
    )

    source_object: str = Field(
        min_length=1,
        max_length=255,
    )

    object_type: str = Field(
        min_length=1,
        max_length=32,
    )

    load_strategy: str = Field(
        min_length=1,
        max_length=64,
    )

    watermark_column: str | None = Field(
        default=None,
        max_length=255,
    )

    target_schema: str | None = Field(
        default=None,
        max_length=255,
    )

    target_object: str | None = Field(
        default=None,
        max_length=255,
    )

    load_order: int = Field(
        default=100,
        ge=1,
    )

    description: str | None = Field(
        default=None,
        max_length=4000,
    )


class SourceObjectUpdateRequest(BaseModel):
    object_type: str = Field(
        min_length=1,
        max_length=32,
    )

    load_strategy: str = Field(
        min_length=1,
        max_length=64,
    )

    watermark_column: str | None = Field(
        default=None,
        max_length=255,
    )

    target_schema: str | None = Field(
        default=None,
        max_length=255,
    )

    target_object: str | None = Field(
        default=None,
        max_length=255,
    )

    load_order: int = Field(
        ge=1,
    )

    description: str | None = Field(
        default=None,
        max_length=4000,
    )


class IdResponse(BaseModel):
    id: int


class ChangedResponse(BaseModel):
    changed: bool


# ============================================================
# Helpers
# ============================================================


def _session_token_hash(
    request: Request,
) -> str:
    token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _normalize_code(
    value: str,
) -> str:
    return value.strip().upper()


def _nullable_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()

    return normalized or None


def _db_message(
    exc: psycopg.Error,
) -> str:
    diag = getattr(
        exc,
        "diag",
        None,
    )

    if diag is not None:
        message = getattr(
            diag,
            "message_primary",
            None,
        )

        if message:
            return str(message)

    text = str(exc).strip()

    if not text:
        return "Database operation failed"

    return text.splitlines()[0]


def _raise_database_error(
    exc: psycopg.Error,
) -> None:
    message = _db_message(
        exc
    )

    lowered = message.lower()

    if (
        "authentication required" in lowered
        or "invalid authentication session" in lowered
    ):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        ) from exc

    if (
        "access denied" in lowered
        or "permission" in lowered
    ):
        raise HTTPException(
            status_code=403,
            detail=message,
        ) from exc

    if (
        "does not exist" in lowered
        or "not found" in lowered
    ):
        raise HTTPException(
            status_code=404,
            detail=message,
        ) from exc

    if (
        "already exists" in lowered
        or "duplicate" in lowered
        or "only queued" in lowered
        or "requires" in lowered
        or "must be" in lowered
        or "invalid" in lowered
        or "unsupported" in lowered
        or "load mode" in lowered
    ):
        raise HTTPException(
            status_code=422,
            detail=message,
        ) from exc

    raise HTTPException(
        status_code=500,
        detail="Integration administration operation failed",
    ) from exc


def _set_request_context(
    cur,
    request_id: str,
) -> None:
    cur.execute(
        """
        SELECT set_config(
            'dwh.request_id',
            %s,
            true
        )
        """,
        (
            request_id,
        ),
    )


# ============================================================
# Source systems
# ============================================================


@router.get(
    "/source-systems",
)
def list_source_systems(
    request: Request,
) -> list[dict]:
    token_hash = _session_token_hash(
        request
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.integration_source_system_list(
                        %s
                    )
                    ORDER BY code
                    """,
                    (
                        token_hash,
                    ),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source system list state"
    )


@router.post(
    "/source-systems",
    response_model=IdResponse,
    status_code=201,
)
def create_source_system(
    payload: SourceSystemCreateRequest,
    request: Request,
) -> IdResponse:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.integration_source_system_create(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS source_system_id
                        """,
                        (
                            token_hash,
                            _normalize_code(
                                payload.code
                            ),
                            payload.name.strip(),
                            payload.source_type.strip(),
                            _nullable_text(
                                payload.description
                            ),
                        ),
                    )

                    row = cur.fetchone()

                    if row is None:
                        raise RuntimeError(
                            "Source system id was not returned"
                        )

                conn.commit()

                return IdResponse(
                    id=row["source_system_id"]
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source system create state"
    )


@router.put(
    "/source-systems/{source_system_id}",
    response_model=ChangedResponse,
)
def update_source_system(
    payload: SourceSystemUpdateRequest,
    request: Request,
    source_system_id: int = Path(
        ge=1,
    ),
) -> ChangedResponse:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.integration_source_system_update(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            source_system_id,
                            payload.name.strip(),
                            payload.source_type.strip(),
                            _nullable_text(
                                payload.description
                            ),
                        ),
                    )

                    row = cur.fetchone()

                conn.commit()

                return ChangedResponse(
                    changed=bool(
                        row
                        and row["changed"]
                    )
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source system update state"
    )


def _set_source_system_active(
    *,
    request: Request,
    source_system_id: int,
    is_active: bool,
) -> ChangedResponse:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.integration_source_system_set_active(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            source_system_id,
                            is_active,
                        ),
                    )

                    row = cur.fetchone()

                conn.commit()

                return ChangedResponse(
                    changed=bool(
                        row
                        and row["changed"]
                    )
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source system state"
    )


@router.post(
    "/source-systems/{source_system_id}/enable",
    response_model=ChangedResponse,
)
def enable_source_system(
    request: Request,
    source_system_id: int = Path(
        ge=1,
    ),
) -> ChangedResponse:
    return _set_source_system_active(
        request=request,
        source_system_id=source_system_id,
        is_active=True,
    )


@router.post(
    "/source-systems/{source_system_id}/disable",
    response_model=ChangedResponse,
)
def disable_source_system(
    request: Request,
    source_system_id: int = Path(
        ge=1,
    ),
) -> ChangedResponse:
    return _set_source_system_active(
        request=request,
        source_system_id=source_system_id,
        is_active=False,
    )


# ============================================================
# Source objects
# ============================================================


@router.get(
    "/source-objects",
)
def list_source_objects(
    request: Request,
    source_system_id: int | None = Query(
        default=None,
        ge=1,
    ),
) -> list[dict]:
    token_hash = _session_token_hash(
        request
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.integration_source_object_list(
                        %s,
                        %s
                    )
                    ORDER BY
                        source_system_code,
                        load_order,
                        source_object_id
                    """,
                    (
                        token_hash,
                        source_system_id,
                    ),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source object list state"
    )


@router.post(
    "/source-objects",
    response_model=IdResponse,
    status_code=201,
)
def create_source_object(
    payload: SourceObjectCreateRequest,
    request: Request,
) -> IdResponse:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.integration_source_object_create(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS source_object_id
                        """,
                        (
                            token_hash,
                            payload.source_system_id,
                            payload.source_schema.strip(),
                            payload.source_object.strip(),
                            _normalize_code(
                                payload.object_type
                            ),
                            _normalize_code(
                                payload.load_strategy
                            ),
                            _nullable_text(
                                payload.watermark_column
                            ),
                            _nullable_text(
                                payload.target_schema
                            ),
                            _nullable_text(
                                payload.target_object
                            ),
                            payload.load_order,
                            _nullable_text(
                                payload.description
                            ),
                        ),
                    )

                    row = cur.fetchone()

                    if row is None:
                        raise RuntimeError(
                            "Source object id was not returned"
                        )

                conn.commit()

                return IdResponse(
                    id=row["source_object_id"]
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source object create state"
    )


@router.put(
    "/source-objects/{source_object_id}",
    response_model=ChangedResponse,
)
def update_source_object(
    payload: SourceObjectUpdateRequest,
    request: Request,
    source_object_id: int = Path(
        ge=1,
    ),
) -> ChangedResponse:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.integration_source_object_update(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            source_object_id,
                            _normalize_code(
                                payload.object_type
                            ),
                            _normalize_code(
                                payload.load_strategy
                            ),
                            _nullable_text(
                                payload.watermark_column
                            ),
                            _nullable_text(
                                payload.target_schema
                            ),
                            _nullable_text(
                                payload.target_object
                            ),
                            payload.load_order,
                            _nullable_text(
                                payload.description
                            ),
                        ),
                    )

                    row = cur.fetchone()

                conn.commit()

                return ChangedResponse(
                    changed=bool(
                        row
                        and row["changed"]
                    )
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source object update state"
    )


def _set_source_object_active(
    *,
    request: Request,
    source_object_id: int,
    is_active: bool,
) -> ChangedResponse:
    token_hash = _session_token_hash(
        request
    )

    request_id = str(
        uuid4()
    )

    try:
        with closing(
            connect_control(config)
        ) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(
                        cur,
                        request_id,
                    )

                    cur.execute(
                        """
                        SELECT
                            app.integration_source_object_set_active(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            source_object_id,
                            is_active,
                        ),
                    )

                    row = cur.fetchone()

                conn.commit()

                return ChangedResponse(
                    changed=bool(
                        row
                        and row["changed"]
                    )
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable source object state"
    )


@router.post(
    "/source-objects/{source_object_id}/enable",
    response_model=ChangedResponse,
)
def enable_source_object(
    request: Request,
    source_object_id: int = Path(
        ge=1,
    ),
) -> ChangedResponse:
    return _set_source_object_active(
        request=request,
        source_object_id=source_object_id,
        is_active=True,
    )


@router.post(
    "/source-objects/{source_object_id}/disable",
    response_model=ChangedResponse,
)
def disable_source_object(
    request: Request,
    source_object_id: int = Path(
        ge=1,
    ),
) -> ChangedResponse:
    return _set_source_object_active(
        request=request,
        source_object_id=source_object_id,
        is_active=False,
    )


# ============================================================
# Integration history / errors read-only administration
# ============================================================


def _history_fetch_all(
    sql: str,
    params: tuple[object, ...],
) -> list[dict]:
    try:
        with closing(
            connect_readonly(config)
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    params,
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable integration history state"
    )


@router.get(
    "/history/etl-runs",
)
def integration_history_etl_runs(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    source_batch_run_id: int | None = Query(
        default=None,
        ge=1,
    ),
    status: str | None = Query(
        default=None,
        max_length=32,
    ),
    pipeline_code: str | None = Query(
        default=None,
        max_length=64,
    ),
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []

    if source_batch_run_id is not None:
        conditions.append(
            "source_batch_run_id = %s"
        )
        params.append(
            source_batch_run_id
        )

    if status is not None:
        conditions.append(
            "status = %s"
        )
        params.append(
            status.strip().upper()
        )

    if pipeline_code is not None:
        conditions.append(
            "pipeline_code = %s"
        )
        params.append(
            pipeline_code.strip().upper()
        )

    where_sql = ""

    if conditions:
        where_sql = (
            "WHERE "
            + " AND ".join(
                conditions
            )
        )

    params.append(
        limit
    )

    sql = f"""
        SELECT
            batch_run_id,
            source_batch_run_id,
            pipeline_code,
            run_mode,
            root_batch_run_id,
            resume_from_batch_run_id,
            attempt_no,
            preflight_batch_run_id,
            business_date,
            trigger_type,
            status,
            started_at,
            finished_at,
            created_by,
            message,
            contract_versions,
            mapping_versions,
            jobs_success,
            jobs_failed,
            jobs_skipped,
            jobs_pending
        FROM etl.v_integration_run_history
        {where_sql}
        ORDER BY batch_run_id DESC
        LIMIT %s
    """

    return _history_fetch_all(
        sql,
        tuple(params),
    )


@router.get(
    "/history/etl-errors",
)
def integration_history_etl_errors(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    batch_run_id: int | None = Query(
        default=None,
        ge=1,
    ),
    source_object_id: int | None = Query(
        default=None,
        ge=1,
    ),
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []

    if batch_run_id is not None:
        conditions.append(
            "e.batch_run_id = %s"
        )
        params.append(
            batch_run_id
        )

    if source_object_id is not None:
        conditions.append(
            "e.source_object_id = %s"
        )
        params.append(
            source_object_id
        )

    where_sql = ""

    if conditions:
        where_sql = (
            "WHERE "
            + " AND ".join(
                conditions
            )
        )

    params.append(
        limit
    )

    sql = f"""
        SELECT
            e.error_id,
            e.batch_run_id,
            e.job_run_id,
            e.job_id,
            e.source_object_id,

            ss.code AS source_system_code,
            ss.name AS source_system_name,
            so.source_schema,
            so.source_object,

            e.error_stage,
            e.error_code,
            e.error_message,
            e.error_detail,

            e.record_key,
            e.source_pk,
            e.application_id,
            e.applicant_id,

            e.column_name,
            e.bad_value,
            e.expected_value,

            e.sql_state,
            e.exception_type,
            e.diagnostic_context,
            e.occurred_at

        FROM etl.v_integration_error e

        LEFT JOIN meta.source_object so
          ON so.source_object_id =
             e.source_object_id

        LEFT JOIN meta.source_system ss
          ON ss.source_system_id =
             so.source_system_id

        {where_sql}

        ORDER BY
            e.occurred_at DESC,
            e.error_id DESC

        LIMIT %s
    """

    return _history_fetch_all(
        sql,
        tuple(params),
    )


