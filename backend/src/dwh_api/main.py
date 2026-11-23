from __future__ import annotations

from dwh_api.auth import (
    authorize_api_access,
    router as auth_router,
)

from contextlib import asynccontextmanager, closing
from typing import Any

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dwh_api.config import get_api_config
from dwh_api.db import connect_readonly
from dwh_api.module_catalog import router as module_catalog_router
from dwh_api.integration_admin import router as integration_admin_router
from dwh_api.source_connection_admin import router as source_connection_admin_router
from dwh_api.object_config_admin import router as object_config_admin_router
from dwh_api.ods_config_admin import router as ods_config_admin_router
from dwh_api.object_run_admin import admin_router as object_run_admin_router, view_router as object_load_view_router
from dwh_api.role_catalog import router as role_catalog_router
from dwh_api.access_control import router as access_control_router
from dwh_api.user_catalog import router as user_catalog_router
from dwh_api.ai_assistant import router as ai_assistant_router
from dwh_api.saved_reports import router as saved_reports_router
from dwh_api.mart_view import router as mart_view_router
from dwh_api.dm_builder_admin import admin_router as dm_builder_admin_router, view_router as dm_mart_view_router
from dwh_api.server_load import router as server_load_router, start_sampling as start_server_load_sampling
from dwh_api.models import (
    ApiError,
    ApiMetadata,
    EtlError,
    EtlJobRun,
    EtlRun,
    HealthResponse,
)

@asynccontextmanager
async def _lifespan(_app):
    start_server_load_sampling()  # I13-13: the load history is collected from the start of the API
    yield


app = FastAPI(
    lifespan=_lifespan,
    title="DWH API",
    version="0.3.0",
    description=(
        "DWH Control Center API."
    ),
)

config = get_api_config()


@app.exception_handler(HTTPException)
async def api_http_error(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    request_id = str(uuid4())

    if isinstance(exc.detail, str):
        message = exc.detail
        details = None
    else:
        details = exc.detail

        if (
            isinstance(exc.detail, dict)
            and isinstance(
                exc.detail.get("message"),
                str,
            )
        ):
            message = exc.detail["message"]
        else:
            message = "Request failed"

    payload = ApiError(
        error="http_error",
        message=message,
        request_id=request_id,
        path=request.url.path,
        details=details,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=payload.model_dump(
            mode="json"
        ),
    )


@app.exception_handler(RequestValidationError)
async def api_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    request_id = str(uuid4())

    payload = ApiError(
        error="validation_error",
        message="Request validation failed",
        request_id=request_id,
        path=request.url.path,
        details=exc.errors(),
    )

    return JSONResponse(
        status_code=422,
        content=payload.model_dump(
            mode="json"
        ),
    )

@app.exception_handler(Exception)
async def api_internal_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    request_id = str(uuid4())

    print(
        "API INTERNAL ERROR "
        f"request_id={request_id} "
        f"path={request.url.path} "
        f"type={type(exc).__name__}"
    )

    payload = ApiError(
        error="internal_error",
        message="Internal server error",
        request_id=request_id,
        path=request.url.path,
    )

    return JSONResponse(
        status_code=500,
        content=payload.model_dump(
            mode="json"
        ),
    )


# ============================================================
# Internal helpers
# ============================================================


def fetch_one(
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict | None:
    with closing(
        connect_readonly(config)
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                params,
            )
            return cur.fetchone()


def fetch_all(
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict]:
    with closing(
        connect_readonly(config)
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                params,
            )
            return cur.fetchall()



# ============================================================
# System
# ============================================================


@app.get(
    "/api/health",
    tags=["System"],
    response_model=HealthResponse,
)
def health() -> dict:
    try:
        row = fetch_one(
            """
            SELECT
                current_user
                    AS database_user,

                current_database()
                    AS database_name,

                current_setting(
                    'transaction_read_only'
                ) AS transaction_read_only,

                (
                    SELECT COUNT(*)
                    FROM
                        meta.v_integration_control_object
                ) AS integration_objects
            """
        )

        return {
            "status": "ok",
            **row,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "DWH database connection failed: "
                f"{type(exc).__name__}"
            ),
        ) from exc


@app.get(
    "/api/meta",
    tags=["System"],
    response_model=ApiMetadata,
)
def api_metadata() -> ApiMetadata:

    row = fetch_one(
        """
        SELECT
            current_user
                AS database_user,

            (
                SELECT COUNT(*)
                FROM meta.v_integration_control_object
            ) AS integration_objects,

            (
                SELECT COUNT(*)
                FROM meta.v_current_mapping
                WHERE is_active = true
            ) AS mapping_rules,

            (
                SELECT COUNT(*)
                FROM meta.v_current_column_contract
                WHERE is_active = true
            ) AS validation_contracts,

            (
                SELECT COUNT(*)
                FROM dq.v_current_rule
                WHERE is_active = true
            ) AS dq_rules
        """
    )

    return ApiMetadata(
        api_name=app.title,
        api_version=app.version,
        mode="read_only",
        **row,
    )

# ============================================================
# ETL run history
# ============================================================


@app.get(
    "/api/etl/runs",
    tags=["ETL"],
    response_model=list[EtlRun],
)
def etl_runs(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    source_batch_run_id: int | None = None,
    status: str | None = None,
    run_mode: str | None = None,
) -> list[dict]:

    conditions: list[str] = []
    params: list[Any] = []

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
            status.upper()
        )

    if run_mode is not None:
        conditions.append(
            "run_mode = %s"
        )
        params.append(
            run_mode.upper()
        )

    where_sql = ""

    if conditions:
        where_sql = (
            "WHERE "
            + " AND ".join(
                conditions
            )
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

    params.append(
        limit
    )

    return fetch_all(
        sql,
        tuple(params),
    )


@app.get(
    "/api/etl/runs/{batch_run_id}/jobs",
    tags=["ETL"],
    response_model=list[EtlJobRun],
)
def etl_run_jobs(
    batch_run_id: int,
) -> list[dict]:

    batch = fetch_one(
        """
        SELECT batch_run_id
        FROM etl.v_integration_run_history
        WHERE batch_run_id = %s
        """,
        (
            batch_run_id,
        ),
    )

    if batch is None:
        raise HTTPException(
            status_code=404,
            detail="ODS batch not found",
        )

    return fetch_all(
        """
        SELECT
            batch_run_id,
            source_batch_run_id,

            root_batch_run_id,
            attempt_no,
            run_mode,
            batch_status,

            job_id,
            job_code,
            execution_order,

            target_schema,
            target_object,

            job_run_id,
            job_status,

            reused_from_job_run_id,
            skip_reason,

            rows_read,
            rows_inserted,
            rows_updated,
            rows_rejected,
            error_count,

            started_at,
            finished_at,

            message

        FROM etl.v_integration_job_history

        WHERE batch_run_id = %s

        ORDER BY
            execution_order,
            job_run_id
        """,
        (
            batch_run_id,
        ),
    )


# ============================================================
# ETL technical errors
# ============================================================


@app.get(
    "/api/etl/errors",
    tags=["ETL"],
    response_model=list[EtlError],
)
def etl_errors(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    batch_run_id: int | None = None,
    source_object_id: int | None = None,
) -> list[dict]:

    conditions: list[str] = []
    params: list[Any] = []

    if batch_run_id is not None:
        conditions.append(
            "batch_run_id = %s"
        )
        params.append(
            batch_run_id
        )

    if source_object_id is not None:
        conditions.append(
            "source_object_id = %s"
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

    sql = f"""
        SELECT
            error_id,

            batch_run_id,
            job_run_id,
            job_id,
            source_object_id,

            error_stage,
            error_code,
            error_message,
            error_detail,

            record_key,

            source_pk,
            application_id,
            applicant_id,

            column_name,
            bad_value,
            expected_value,

            sql_state,
            exception_type,
            diagnostic_context,

            occurred_at

        FROM etl.v_integration_error

        {where_sql}

        ORDER BY
            occurred_at DESC,
            error_id DESC

        LIMIT %s
    """

    params.append(
        limit
    )

    return fetch_all(
        sql,
        tuple(params),
    )


# ============================================================
# API authorization enforcement
# ============================================================


@app.middleware("http")
async def authorization_middleware(
    request: Request,
    call_next,
):
    path = request.url.path


    # Public technical/authentication endpoints.
    if (
        path == "/api/health"
        or path == "/api/meta"
        or path.startswith("/api/auth/")
    ):
        return await call_next(
            request
        )


    permissions: tuple[str, ...] = ()
    resource_required = False
    admin_level_required = False


    if path.startswith("/api/admin/integrations"):
        permissions = (
            "ADMIN_INTEGRATION_MANAGE",
        )
        admin_level_required = True


    elif path.startswith("/api/admin/modules"):
        permissions = (
            "ADMIN_MODULE_MANAGE",
        )
        admin_level_required = True


    elif path.startswith("/api/admin/roles"):
        permissions = (
            "ADMIN_ROLE_MANAGE",
        )
        admin_level_required = True


    elif path.startswith("/api/admin/access"):
        permissions = (
            "ADMIN_ACCESS_MANAGE",
        )
        admin_level_required = True

    elif path.startswith("/api/admin/users"):
        permissions = (
            "ADMIN_USER_MANAGE",
        )
        admin_level_required = True


    elif path.startswith("/api/integrations"):
        permissions = (
            "INTEGRATION_VIEW",
        )
        resource_required = True


    elif path.startswith("/api/etl"):
        permissions = (
            "ETL_VIEW",
        )
        resource_required = True


    elif path.startswith("/api/dq"):
        permissions = (
            "DQ_VIEW",
        )
        resource_required = True


    # I17: AI assistant; every call is checked again in the database (package 016).
    elif path.startswith("/api/ai/"):
        permissions = (
            "AI_ASSISTANT_USE",
        )


    # I17-09: saved reports; every call is checked again in the database (package 017).
    elif path.startswith("/api/reports/saved/"):
        permissions = (
            "REPORT_VIEW",
        )
        resource_required = True


    elif path.startswith(
        "/api/reporting/retail/export"
    ):
        permissions = (
            "REPORT_VIEW",
            "REPORT_EXPORT",
        )
        resource_required = True


    elif path.startswith(
        "/api/reporting/retail"
    ):
        permissions = (
            "REPORT_VIEW",
        )
        resource_required = True


    # Unknown API endpoints are left to FastAPI
    # so normal 404 behaviour is preserved.
    if not permissions:
        return await call_next(
            request
        )


    try:

        if resource_required:

            auth_state = authorize_api_access(
                request,
                permissions=permissions,
                resource_type="MODULE",
                resource_code="CDP",
            )

        else:

            auth_state = authorize_api_access(
                request,
                permissions=permissions,
            )


        if (
            auth_state.user is not None
            and auth_state.user.must_change_password
        ):
            raise HTTPException(
                status_code=403,
                detail="Password change required",
            )

        if admin_level_required:

            if (
                auth_state.user is None
                or auth_state.user.account_level
                not in (
                    "SUPERADMIN",
                    "ADMIN",
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Administrator access required"
                    ),
                )


    except HTTPException as exc:

        request_id = str(
            uuid4()
        )

        payload = ApiError(
            error=(
                "unauthorized"
                if exc.status_code == 401
                else "forbidden"
            ),
            message=str(
                exc.detail
            ),
            request_id=request_id,
            path=path,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(
                mode="json"
            ),
            headers={
                "Cache-Control": "no-store",
            },
        )


    return await call_next(
        request
    )


# Authentication / authorization routes.
app.include_router(auth_router)
app.include_router(module_catalog_router)
app.include_router(integration_admin_router)
app.include_router(source_connection_admin_router)
app.include_router(object_config_admin_router)
app.include_router(ods_config_admin_router)
app.include_router(object_run_admin_router)
app.include_router(object_load_view_router)
app.include_router(role_catalog_router)
app.include_router(access_control_router)
app.include_router(user_catalog_router)
app.include_router(ai_assistant_router)
app.include_router(saved_reports_router)
app.include_router(mart_view_router)
app.include_router(dm_builder_admin_router)
app.include_router(dm_mart_view_router)
app.include_router(server_load_router)  # I13-13: server load for administrators
