from __future__ import annotations

import hashlib
from contextlib import closing
from datetime import datetime
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


class RunRequestCreateRequest(BaseModel):
    task_code: str = Field(
        min_length=1,
        max_length=32,
    )

    source_batch_run_id: int = Field(
        ge=1,
    )

    load_mode: str | None = Field(
        default=None,
        max_length=16,
    )


class RunRequestCancelRequest(BaseModel):
    reason: str | None = Field(
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
# Run requests
# ============================================================


@router.get(
    "/run-candidates",
)
def list_run_candidates(
    task_code: str = Query(
        min_length=1,
        max_length=32,
    ),
    limit: int = Query(
        default=200,
        ge=1,
        le=1000,
    ),
) -> list[dict]:
    normalized_task = _normalize_code(
        task_code
    )

    allowed_tasks = {
        "VALIDATE_STG",
        "TRANSFORM_ODS",
        "REFRESH_MARTS",
        "RUN_DQ",
    }

    if normalized_task not in allowed_tasks:
        raise HTTPException(
            status_code=422,
            detail="Unsupported integration run task",
        )

    if normalized_task == "REFRESH_MARTS":
        sql = """
            SELECT
                batch_run_id,
                business_date,
                pipeline_code
            FROM etl.v_integration_run_history
            WHERE status = 'SUCCESS'
              AND pipeline_code = 'ODS_TRANSFORM'
            ORDER BY batch_run_id DESC
            LIMIT %s
        """

        return _history_fetch_all(
            sql,
            (
                limit,
            ),
        )

    sql = """
        WITH ranked_source_batches AS (
            SELECT
                source_batch_run_id
                    AS batch_run_id,
                business_date,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        source_batch_run_id
                    ORDER BY
                        batch_run_id DESC
                ) AS row_no
            FROM etl.v_integration_run_history
            WHERE status = 'SUCCESS'
              AND source_batch_run_id IS NOT NULL
              AND source_batch_run_id > 0
        )
        SELECT
            batch_run_id,
            business_date,
            'SOURCE_BATCH'::text
                AS pipeline_code
        FROM ranked_source_batches
        WHERE row_no = 1
        ORDER BY batch_run_id DESC
        LIMIT %s
    """

    return _history_fetch_all(
        sql,
        (
            limit,
        ),
    )


@router.get(
    "/run-requests",
)
def list_run_requests(
    status: str | None = Query(
        default=None,
        max_length=16,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []

    if status is not None:
        conditions.append(
            "status = %s"
        )
        params.append(
            status.strip().upper()
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
        SELECT *
        FROM etl.v_integration_run_request
        {where_sql}
        ORDER BY
            requested_at DESC,
            run_request_id DESC
        LIMIT %s
    """

    try:
        with closing(
            connect_control(config)
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    tuple(params),
                )

                return cur.fetchall()

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable run request list state"
    )


@router.post(
    "/run-requests",
    response_model=IdResponse,
    status_code=201,
)
def create_run_request(
    payload: RunRequestCreateRequest,
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
                            app.integration_run_request_create(
                                %s,
                                %s,
                                %s,
                                %s
                            ) AS run_request_id
                        """,
                        (
                            token_hash,
                            _normalize_code(
                                payload.task_code
                            ),
                            payload.source_batch_run_id,
                            (
                                _normalize_code(
                                    payload.load_mode
                                )
                                if payload.load_mode
                                else None
                            ),
                        ),
                    )

                    row = cur.fetchone()

                    if row is None:
                        raise RuntimeError(
                            "Run request id was not returned"
                        )

                conn.commit()

                return IdResponse(
                    id=row["run_request_id"]
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable run request create state"
    )


@router.post(
    "/run-requests/{run_request_id}/cancel",
    response_model=ChangedResponse,
)
def cancel_run_request(
    payload: RunRequestCancelRequest,
    request: Request,
    run_request_id: int = Path(
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
                            app.integration_run_request_cancel(
                                %s,
                                %s,
                                %s
                            ) AS changed
                        """,
                        (
                            token_hash,
                            run_request_id,
                            _nullable_text(
                                payload.reason
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
        "Unreachable run request cancel state"
    )

# === INTEGRATION CONTRACT / MAPPING / DQ ADMIN ===

from contextlib import closing as _ia_closing
from typing import Any as _IAAny

from fastapi import (
    Path as _IAPath,
    Query as _IAQuery,
)
from pydantic import (
    BaseModel as _IABaseModel,
    Field as _IAField,
)


# ------------------------------------------------------------
# Request models
# ------------------------------------------------------------

class IntegrationContractCreateRequest(
    _IABaseModel
):
    source_system_code: str = _IAField(
        min_length=1,
        max_length=128,
    )
    source_schema: str = _IAField(
        min_length=1,
        max_length=128,
    )
    source_object: str = _IAField(
        min_length=1,
        max_length=256,
    )
    stg_table: str = _IAField(
        min_length=1,
        max_length=256,
    )
    source_column: str = _IAField(
        min_length=1,
        max_length=256,
    )

    target_schema: str = _IAField(
        min_length=1,
        max_length=128,
    )
    target_object: str = _IAField(
        min_length=1,
        max_length=256,
    )
    target_column: str = _IAField(
        min_length=1,
        max_length=256,
    )

    expected_pg_type: str = _IAField(
        min_length=1,
        max_length=256,
    )

    is_required: bool
    allow_blank: bool
    is_active: bool = True

    description: str | None = _IAField(
        default=None,
        max_length=4000,
    )
    change_reason: str | None = _IAField(
        default=None,
        max_length=4000,
    )


class IntegrationContractUpdateRequest(
    _IABaseModel
):
    target_schema: str = _IAField(
        min_length=1,
        max_length=128,
    )
    target_object: str = _IAField(
        min_length=1,
        max_length=256,
    )
    target_column: str = _IAField(
        min_length=1,
        max_length=256,
    )

    expected_pg_type: str = _IAField(
        min_length=1,
        max_length=256,
    )

    is_required: bool
    allow_blank: bool
    is_active: bool = True

    description: str | None = _IAField(
        default=None,
        max_length=4000,
    )
    change_reason: str | None = _IAField(
        default=None,
        max_length=4000,
    )


class IntegrationActiveStateRequest(
    _IABaseModel
):
    change_reason: str | None = _IAField(
        default=None,
        max_length=4000,
    )


class IntegrationMappingSourceRequest(
    _IABaseModel
):
    source_layer: str = _IAField(
        min_length=1,
        max_length=32,
    )
    source_schema: str = _IAField(
        min_length=1,
        max_length=128,
    )
    source_object: str = _IAField(
        min_length=1,
        max_length=256,
    )
    source_column: str = _IAField(
        min_length=1,
        max_length=256,
    )
    source_role: str = _IAField(
        default="INPUT",
        min_length=1,
        max_length=64,
    )


class IntegrationMappingCreateRequest(
    _IABaseModel
):
    source_system_code: str = _IAField(
        min_length=1,
        max_length=128,
    )
    mapping_stage: str = _IAField(
        min_length=1,
        max_length=64,
    )
    transform_name: str = _IAField(
        min_length=1,
        max_length=256,
    )
    mapping_position: int = _IAField(
        ge=1,
    )

    stg_schema: str = _IAField(
        min_length=1,
        max_length=128,
    )
    stg_table: str = _IAField(
        min_length=1,
        max_length=256,
    )

    target_schema: str = _IAField(
        min_length=1,
        max_length=128,
    )
    target_object: str = _IAField(
        min_length=1,
        max_length=256,
    )
    target_column: str = _IAField(
        min_length=1,
        max_length=256,
    )

    mapping_type: str = _IAField(
        min_length=1,
        max_length=64,
    )

    transform_expression: str | None = None
    expected_pg_type: str | None = _IAField(
        default=None,
        max_length=256,
    )

    is_active: bool = True

    sources: list[
        IntegrationMappingSourceRequest
    ] = _IAField(
        default_factory=list,
    )

    change_reason: str | None = _IAField(
        default=None,
        max_length=4000,
    )


class IntegrationMappingUpdateRequest(
    _IABaseModel
):
    transform_name: str = _IAField(
        min_length=1,
        max_length=256,
    )
    mapping_position: int = _IAField(
        ge=1,
    )

    stg_schema: str = _IAField(
        min_length=1,
        max_length=128,
    )
    stg_table: str = _IAField(
        min_length=1,
        max_length=256,
    )

    mapping_type: str = _IAField(
        min_length=1,
        max_length=64,
    )

    transform_expression: str | None = None
    expected_pg_type: str | None = _IAField(
        default=None,
        max_length=256,
    )

    is_active: bool = True

    sources: list[
        IntegrationMappingSourceRequest
    ] = _IAField(
        default_factory=list,
    )

    change_reason: str | None = _IAField(
        default=None,
        max_length=4000,
    )


class IntegrationDqRuleCreateRequest(
    _IABaseModel
):
    rule_code: str = _IAField(
        min_length=1,
        max_length=128,
    )
    rule_name: str = _IAField(
        min_length=1,
        max_length=256,
    )
    severity: str = _IAField(
        min_length=1,
        max_length=32,
    )
    category: str = _IAField(
        min_length=1,
        max_length=128,
    )

    source_object: str | None = _IAField(
        default=None,
        max_length=512,
    )
    target_object: str | None = _IAField(
        default=None,
        max_length=512,
    )
    target_column: str | None = _IAField(
        default=None,
        max_length=256,
    )

    description: str | None = _IAField(
        default=None,
        max_length=4000,
    )

    issue_limit: int = _IAField(
        default=1000,
        ge=1,
    )

    is_active: bool = True

    change_reason: str | None = _IAField(
        default=None,
        max_length=4000,
    )


class IntegrationDqRuleUpdateRequest(
    _IABaseModel
):
    rule_name: str = _IAField(
        min_length=1,
        max_length=256,
    )
    severity: str = _IAField(
        min_length=1,
        max_length=32,
    )
    category: str = _IAField(
        min_length=1,
        max_length=128,
    )

    source_object: str | None = _IAField(
        default=None,
        max_length=512,
    )
    target_object: str | None = _IAField(
        default=None,
        max_length=512,
    )
    target_column: str | None = _IAField(
        default=None,
        max_length=256,
    )

    description: str | None = _IAField(
        default=None,
        max_length=4000,
    )

    issue_limit: int = _IAField(
        default=1000,
        ge=1,
    )

    is_active: bool = True

    change_reason: str | None = _IAField(
        default=None,
        max_length=4000,
    )


# ------------------------------------------------------------
# Shared DB helpers
# ------------------------------------------------------------

def _ia_read_all(
    sql: str,
    params: tuple = (),
) -> list[dict]:
    try:
        with _ia_closing(
            connect_control(
                config
            )
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    params,
                )
                return list(
                    cur.fetchall()
                )

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable integration admin read state"
    )


def _ia_mutate_id(
    *,
    request: Request,
    sql: str,
    params: tuple,
    result_column: str,
) -> int:
    token_hash = _session_token_hash(
        request
    )

    try:
        with _ia_closing(
            connect_control(
                config
            )
        ) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        sql,
                        (
                            token_hash,
                            *params,
                        ),
                    )

                    row = cur.fetchone()

                    if row is None:
                        raise RuntimeError(
                            "Database mutation returned no result"
                        )

                    value = row[
                        result_column
                    ]

                    if value is None:
                        raise RuntimeError(
                            "Database mutation returned null id"
                        )

                conn.commit()

                return int(
                    value
                )

            except Exception:
                conn.rollback()
                raise

    except psycopg.Error as exc:
        _raise_database_error(
            exc
        )

    raise RuntimeError(
        "Unreachable integration admin mutation state"
    )


def _ia_mapping_sources_json(
    sources: list[
        IntegrationMappingSourceRequest
    ],
) -> str:
    import json

    return json.dumps(
        [
            source.model_dump()
            for source in sources
        ],
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )


# ------------------------------------------------------------
# Contracts
# ------------------------------------------------------------

@router.get(
    "/contracts",
)
def list_integration_contracts(
    source_system_code: str | None = _IAQuery(
        default=None,
    ),
    source_schema: str | None = _IAQuery(
        default=None,
    ),
    source_object: str | None = _IAQuery(
        default=None,
    ),
    include_inactive: bool = _IAQuery(
        default=True,
    ),
) -> list[dict]:
    return _ia_read_all(
        """
        SELECT
            contract_id,
            contract_root_id,
            version_no,
            source_system_code,
            source_schema,
            source_object,
            stg_table,
            source_column,
            target_schema,
            target_object,
            target_column,
            expected_pg_type,
            is_required,
            allow_blank,
            is_active,
            description,
            valid_from,
            valid_to,
            supersedes_contract_id,
            created_by,
            change_reason,
            created_at
        FROM meta.v_current_column_contract
        WHERE (
            %s::text IS NULL
            OR source_system_code = upper(btrim(%s::text))
        )
          AND (
            %s::text IS NULL
            OR source_schema = btrim(%s::text)
        )
          AND (
            %s::text IS NULL
            OR source_object = btrim(%s::text)
        )
          AND (
            %s::boolean = true
            OR is_active = true
        )
        ORDER BY
            source_system_code,
            source_schema,
            source_object,
            source_column
        """,
        (
            source_system_code,
            source_system_code,
            source_schema,
            source_schema,
            source_object,
            source_object,
            include_inactive,
        ),
    )


@router.post(
    "/contracts",
)
def create_integration_contract(
    payload: IntegrationContractCreateRequest,
    request: Request,
) -> dict:
    contract_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_contract_create(
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
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                ) AS contract_id
        """,
        params=(
            payload.source_system_code,
            payload.source_schema,
            payload.source_object,
            payload.stg_table,
            payload.source_column,
            payload.target_schema,
            payload.target_object,
            payload.target_column,
            payload.expected_pg_type,
            payload.is_required,
            payload.allow_blank,
            payload.is_active,
            payload.description,
            payload.change_reason,
        ),
        result_column="contract_id",
    )

    return {
        "contract_id": contract_id,
    }


@router.put(
    "/contracts/{contract_id}",
)
def update_integration_contract(
    payload: IntegrationContractUpdateRequest,
    request: Request,
    contract_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    new_contract_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_contract_version(
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
                ) AS contract_id
        """,
        params=(
            contract_id,
            payload.target_schema,
            payload.target_object,
            payload.target_column,
            payload.expected_pg_type,
            payload.is_required,
            payload.allow_blank,
            payload.is_active,
            payload.description,
            payload.change_reason,
        ),
        result_column="contract_id",
    )

    return {
        "contract_id": new_contract_id,
    }


def _ia_set_contract_active(
    *,
    request: Request,
    contract_id: int,
    is_active: bool,
    change_reason: str | None,
) -> dict:
    new_contract_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_contract_set_active(
                    %s,
                    %s,
                    %s,
                    %s
                ) AS contract_id
        """,
        params=(
            contract_id,
            is_active,
            change_reason,
        ),
        result_column="contract_id",
    )

    return {
        "contract_id": new_contract_id,
        "is_active": is_active,
    }


@router.post(
    "/contracts/{contract_id}/enable",
)
def enable_integration_contract(
    payload: IntegrationActiveStateRequest,
    request: Request,
    contract_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    return _ia_set_contract_active(
        request=request,
        contract_id=contract_id,
        is_active=True,
        change_reason=payload.change_reason,
    )


@router.post(
    "/contracts/{contract_id}/disable",
)
def disable_integration_contract(
    payload: IntegrationActiveStateRequest,
    request: Request,
    contract_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    return _ia_set_contract_active(
        request=request,
        contract_id=contract_id,
        is_active=False,
        change_reason=payload.change_reason,
    )


# ------------------------------------------------------------
# Mappings
# ------------------------------------------------------------

@router.get(
    "/mappings",
)
def list_integration_mappings(
    source_system_code: str | None = _IAQuery(
        default=None,
    ),
    mapping_stage: str | None = _IAQuery(
        default=None,
    ),
    target_schema: str | None = _IAQuery(
        default=None,
    ),
    target_object: str | None = _IAQuery(
        default=None,
    ),
    include_inactive: bool = _IAQuery(
        default=True,
    ),
) -> list[dict]:
    return _ia_read_all(
        """
        SELECT
            m.mapping_id,
            m.mapping_key,
            m.version_no,
            m.source_system_code,
            m.mapping_stage,
            m.transform_name,
            m.mapping_position,
            m.stg_schema,
            m.stg_table,
            m.target_schema,
            m.target_object,
            m.target_column,
            m.mapping_type,
            m.transform_expression,
            m.expected_pg_type,
            m.is_active,
            m.valid_from,
            m.valid_to,
            m.supersedes_mapping_id,
            m.created_by,
            m.change_reason,
            m.created_at,
            m.definition_hash,
            coalesce(
                jsonb_agg(
                    jsonb_build_object(
                        'source_layer',
                        r.source_layer,
                        'source_schema',
                        r.source_schema,
                        'source_object',
                        r.source_object,
                        'source_column',
                        r.source_column,
                        'source_role',
                        r.source_role,
                        'ordinal_position',
                        r.ordinal_position
                    )
                    ORDER BY r.ordinal_position
                ) FILTER (
                    WHERE r.mapping_source_ref_id IS NOT NULL
                ),
                '[]'::jsonb
            ) AS sources
        FROM meta.v_current_mapping m
        LEFT JOIN meta.mapping_source_ref r
          ON r.mapping_id = m.mapping_id
        WHERE (
            %s::text IS NULL
            OR m.source_system_code = upper(btrim(%s::text))
        )
          AND (
            %s::text IS NULL
            OR m.mapping_stage = upper(btrim(%s::text))
        )
          AND (
            %s::text IS NULL
            OR m.target_schema = btrim(%s::text)
        )
          AND (
            %s::text IS NULL
            OR m.target_object = btrim(%s::text)
        )
          AND (
            %s::boolean = true
            OR m.is_active = true
        )
        GROUP BY
            m.mapping_id,
            m.mapping_key,
            m.version_no,
            m.source_system_code,
            m.mapping_stage,
            m.transform_name,
            m.mapping_position,
            m.stg_schema,
            m.stg_table,
            m.target_schema,
            m.target_object,
            m.target_column,
            m.mapping_type,
            m.transform_expression,
            m.expected_pg_type,
            m.is_active,
            m.valid_from,
            m.valid_to,
            m.supersedes_mapping_id,
            m.created_by,
            m.change_reason,
            m.created_at,
            m.definition_hash
        ORDER BY
            m.source_system_code,
            m.mapping_stage,
            m.target_schema,
            m.target_object,
            m.mapping_position,
            m.target_column
        """,
        (
            source_system_code,
            source_system_code,
            mapping_stage,
            mapping_stage,
            target_schema,
            target_schema,
            target_object,
            target_object,
            include_inactive,
        ),
    )


@router.post(
    "/mappings",
)
def create_integration_mapping(
    payload: IntegrationMappingCreateRequest,
    request: Request,
) -> dict:
    mapping_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_mapping_create(
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
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s
                ) AS mapping_id
        """,
        params=(
            payload.source_system_code,
            payload.mapping_stage,
            payload.transform_name,
            payload.mapping_position,
            payload.stg_schema,
            payload.stg_table,
            payload.target_schema,
            payload.target_object,
            payload.target_column,
            payload.mapping_type,
            payload.transform_expression,
            payload.expected_pg_type,
            payload.is_active,
            _ia_mapping_sources_json(
                payload.sources
            ),
            payload.change_reason,
        ),
        result_column="mapping_id",
    )

    return {
        "mapping_id": mapping_id,
    }


@router.put(
    "/mappings/{mapping_id}",
)
def update_integration_mapping(
    payload: IntegrationMappingUpdateRequest,
    request: Request,
    mapping_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    new_mapping_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_mapping_version(
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
                    %s::jsonb,
                    %s
                ) AS mapping_id
        """,
        params=(
            mapping_id,
            payload.transform_name,
            payload.mapping_position,
            payload.stg_schema,
            payload.stg_table,
            payload.mapping_type,
            payload.transform_expression,
            payload.expected_pg_type,
            payload.is_active,
            _ia_mapping_sources_json(
                payload.sources
            ),
            payload.change_reason,
        ),
        result_column="mapping_id",
    )

    return {
        "mapping_id": new_mapping_id,
    }


def _ia_set_mapping_active(
    *,
    request: Request,
    mapping_id: int,
    is_active: bool,
    change_reason: str | None,
) -> dict:
    new_mapping_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_mapping_set_active(
                    %s,
                    %s,
                    %s,
                    %s
                ) AS mapping_id
        """,
        params=(
            mapping_id,
            is_active,
            change_reason,
        ),
        result_column="mapping_id",
    )

    return {
        "mapping_id": new_mapping_id,
        "is_active": is_active,
    }


@router.post(
    "/mappings/{mapping_id}/enable",
)
def enable_integration_mapping(
    payload: IntegrationActiveStateRequest,
    request: Request,
    mapping_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    return _ia_set_mapping_active(
        request=request,
        mapping_id=mapping_id,
        is_active=True,
        change_reason=payload.change_reason,
    )


@router.post(
    "/mappings/{mapping_id}/disable",
)
def disable_integration_mapping(
    payload: IntegrationActiveStateRequest,
    request: Request,
    mapping_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    return _ia_set_mapping_active(
        request=request,
        mapping_id=mapping_id,
        is_active=False,
        change_reason=payload.change_reason,
    )


# ------------------------------------------------------------
# DQ rules
# ------------------------------------------------------------

@router.get(
    "/dq-rules",
)
def list_integration_dq_rules(
    severity: str | None = _IAQuery(
        default=None,
    ),
    category: str | None = _IAQuery(
        default=None,
    ),
    include_inactive: bool = _IAQuery(
        default=True,
    ),
) -> list[dict]:
    return _ia_read_all(
        """
        SELECT
            rule_id,
            rule_root_id,
            rule_key,
            rule_code,
            version_no,
            rule_name,
            severity,
            category,
            source_object,
            target_object,
            target_column,
            description,
            issue_limit,
            is_active,
            valid_from,
            valid_to,
            supersedes_rule_id,
            created_by,
            change_reason,
            created_at
        FROM dq.v_current_rule
        WHERE (
            %s::text IS NULL
            OR severity = upper(btrim(%s::text))
        )
          AND (
            %s::text IS NULL
            OR category = upper(btrim(%s::text))
        )
          AND (
            %s::boolean = true
            OR is_active = true
        )
        ORDER BY
            rule_code
        """,
        (
            severity,
            severity,
            category,
            category,
            include_inactive,
        ),
    )


@router.post(
    "/dq-rules",
)
def create_integration_dq_rule(
    payload: IntegrationDqRuleCreateRequest,
    request: Request,
) -> dict:
    rule_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_dq_rule_create(
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
                    %s,
                    %s
                ) AS rule_id
        """,
        params=(
            payload.rule_code,
            payload.rule_name,
            payload.severity,
            payload.category,
            payload.source_object,
            payload.target_object,
            payload.target_column,
            payload.description,
            payload.issue_limit,
            payload.is_active,
            payload.change_reason,
        ),
        result_column="rule_id",
    )

    return {
        "rule_id": rule_id,
    }


@router.put(
    "/dq-rules/{rule_id}",
)
def update_integration_dq_rule(
    payload: IntegrationDqRuleUpdateRequest,
    request: Request,
    rule_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    new_rule_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_dq_rule_version(
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
                    %s,
                    %s
                ) AS rule_id
        """,
        params=(
            rule_id,
            payload.rule_name,
            payload.severity,
            payload.category,
            payload.source_object,
            payload.target_object,
            payload.target_column,
            payload.description,
            payload.issue_limit,
            payload.is_active,
            payload.change_reason,
        ),
        result_column="rule_id",
    )

    return {
        "rule_id": new_rule_id,
    }


def _ia_set_dq_rule_active(
    *,
    request: Request,
    rule_id: int,
    is_active: bool,
    change_reason: str | None,
) -> dict:
    new_rule_id = _ia_mutate_id(
        request=request,
        sql="""
            SELECT
                app.integration_dq_rule_set_active(
                    %s,
                    %s,
                    %s,
                    %s
                ) AS rule_id
        """,
        params=(
            rule_id,
            is_active,
            change_reason,
        ),
        result_column="rule_id",
    )

    return {
        "rule_id": new_rule_id,
        "is_active": is_active,
    }


@router.post(
    "/dq-rules/{rule_id}/enable",
)
def enable_integration_dq_rule(
    payload: IntegrationActiveStateRequest,
    request: Request,
    rule_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    return _ia_set_dq_rule_active(
        request=request,
        rule_id=rule_id,
        is_active=True,
        change_reason=payload.change_reason,
    )


@router.post(
    "/dq-rules/{rule_id}/disable",
)
def disable_integration_dq_rule(
    payload: IntegrationActiveStateRequest,
    request: Request,
    rule_id: int = _IAPath(
        ge=1,
    ),
) -> dict:
    return _ia_set_dq_rule_active(
        request=request,
        rule_id=rule_id,
        is_active=False,
        change_reason=payload.change_reason,
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


@router.get(
    "/history/dq-runs",
)
def integration_history_dq_runs(
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
            dq_run_id,
            source_batch_run_id,
            business_date,
            status,
            started_at,
            finished_at,
            created_by,
            message
        FROM dq.run
        {where_sql}
        ORDER BY dq_run_id DESC
        LIMIT %s
    """

    return _history_fetch_all(
        sql,
        tuple(params),
    )


@router.get(
    "/history/dq-issues",
)
def integration_history_dq_issues(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    dq_run_id: int | None = Query(
        default=None,
        ge=1,
    ),
    issue_status: str | None = Query(
        default=None,
        max_length=32,
    ),
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []

    if dq_run_id is not None:
        conditions.append(
            "i.dq_run_id = %s"
        )
        params.append(
            dq_run_id
        )

    if issue_status is not None:
        conditions.append(
            "i.issue_status = %s"
        )
        params.append(
            issue_status.strip().upper()
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
            i.dq_issue_id,
            i.dq_run_id,
            i.dq_result_id,
            i.rule_id,
            r.rule_code,
            r.rule_name,
            r.category,
            i.source_batch_run_id,
            i.severity,
            i.issue_status,
            i.source_system,
            i.source_schema,
            i.source_object,
            i.source_pk,
            i.application_id,
            i.applicant_id,
            i.column_name,
            i.bad_value,
            i.expected_value,
            i.error_code,
            i.error_message,
            i.created_at,
            i.resolved_at,
            i.resolved_by,
            i.resolution_comment
        FROM dq.issue i
        JOIN dq.rule r
          ON r.rule_id = i.rule_id
        {where_sql}
        ORDER BY
            i.created_at DESC,
            i.dq_issue_id DESC
        LIMIT %s
    """

    return _history_fetch_all(
        sql,
        tuple(params),
    )

