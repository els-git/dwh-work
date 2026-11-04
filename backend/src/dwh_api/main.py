from __future__ import annotations

from dwh_api.auth import (
    authorize_api_access,
    router as auth_router,
)

from contextlib import closing
from typing import Any

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from dwh_api.config import get_api_config
from dwh_api.db import connect_readonly
from dwh_api.module_catalog import router as module_catalog_router
from dwh_api.integration_admin import router as integration_admin_router
from dwh_api.source_connection_admin import router as source_connection_admin_router
from dwh_api.role_catalog import router as role_catalog_router
from dwh_api.access_control import router as access_control_router
from dwh_api.user_catalog import router as user_catalog_router
from dwh_api.models import (
    ApiError,
    ApiMetadata,
    DqIssue,
    DqResult,
    DqRun,
    EtlError,
    EtlJobRun,
    EtlRun,
    HealthResponse,
    IntegrationDqRule,
    IntegrationLatestJob,
    IntegrationMapping,
    IntegrationObject,
    IntegrationOverview,
    SourceColumn,
    ValidationContract,
)


from dwh_api.models import RetailReportingOverview

from dwh_api.reporting_export import (
    ExportColumn,
    build_export,
)

app = FastAPI(
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


def get_integration(
    source_object_id: int,
) -> dict:
    row = fetch_one(
        """
        SELECT *
        FROM meta.v_integration_control_object
        WHERE source_object_id = %s
        """,
        (
            source_object_id,
        ),
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Integration object not found"
            ),
        )

    return row


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
# Integration objects
# ============================================================


@app.get(
    "/api/integrations",
    tags=["Integrations"],
    response_model=list[IntegrationObject],
)
def integrations() -> list[dict]:
    return fetch_all(
        """
        SELECT
            source_object_id,

            source_system_code,
            source_schema,
            source_object,
            object_type,

            load_strategy,
            watermark_column,

            stg_schema,
            stg_table,

            load_order,
            integration_active,
            description,

            source_columns,
            pk_columns,

            contracted_columns,
            required_columns,
            active_contracts,

            mapped_source_columns,

            total_mappings,
            direct_mappings,
            cast_mappings,
            derived_mappings,
            constant_mappings,
            system_mappings,

            metadata_captured_at

        FROM meta.v_integration_control_object

        ORDER BY
            load_order,
            source_object_id
        """
    )


@app.get(
    "/api/integrations/{source_object_id}",
    tags=["Integrations"],
    response_model=IntegrationObject,
)
def integration_detail(
    source_object_id: int,
) -> dict:
    return get_integration(
        source_object_id
    )


# ============================================================
# Source physical structure
# ============================================================


@app.get(
    "/api/integrations/{source_object_id}/structure",
    tags=["Integrations"],
    response_model=list[SourceColumn],
)
def integration_structure(
    source_object_id: int,
) -> list[dict]:

    integration = get_integration(
        source_object_id
    )

    return fetch_all(
        """
        SELECT
            ordinal_position,

            source_column,
            source_type,

            is_nullable,
            is_primary_key,
            is_unique_key,
            is_identity,
            is_generated,

            metadata_captured_at

        FROM meta.v_integration_control_column

        WHERE source_system_code = %s
          AND source_schema = %s
          AND source_object = %s

        ORDER BY ordinal_position
        """,
        (
            integration[
                "source_system_code"
            ],
            integration[
                "source_schema"
            ],
            integration[
                "source_object"
            ],
        ),
    )


# ============================================================
# Technical validation contracts
# ============================================================


@app.get(
    "/api/integrations/{source_object_id}/validations",
    tags=["Integrations"],
    response_model=list[ValidationContract],
)
def integration_validations(
    source_object_id: int,
) -> list[dict]:

    integration = get_integration(
        source_object_id
    )

    return fetch_all(
        """
        SELECT
            ordinal_position,

            source_column,
            source_type,

            contract_id,
            contract_root_id,
            contract_version,

            expected_pg_type,

            is_required,
            allow_blank,
            contract_active,

            contract_target_schema,
            contract_target_object,
            contract_target_column,

            contract_valid_from

        FROM meta.v_integration_control_column

        WHERE source_system_code = %s
          AND source_schema = %s
          AND source_object = %s
          AND contract_id IS NOT NULL

        ORDER BY
            ordinal_position,
            contract_id
        """,
        (
            integration[
                "source_system_code"
            ],
            integration[
                "source_schema"
            ],
            integration[
                "source_object"
            ],
        ),
    )


# ============================================================
# STG -> ODS mappings
# ============================================================


@app.get(
    "/api/integrations/{source_object_id}/mappings/raw",
    tags=["Integrations"],
    include_in_schema=False,
)
def integration_mappings_raw(
    source_object_id: int,
) -> list[dict]:

    integration = get_integration(
        source_object_id
    )

    return fetch_all(
        """
        SELECT
            mapping_id,
            mapping_key,
            version_no,

            mapping_stage,

            transform_name,
            mapping_position,

            source_layer,
            source_position,

            source_schema,
            source_object,
            source_column,

            stg_schema,
            stg_table,

            target_schema,
            target_object,
            target_column,

            mapping_type,
            transform_expression,
            expected_pg_type,

            definition_hash,

            valid_from,
            created_by,
            change_reason

        FROM meta.v_integration_control_mapping

        WHERE source_system_code = %s
          AND stg_schema = %s
          AND stg_table = %s

        ORDER BY
            mapping_position,
            source_position NULLS LAST,
            mapping_id
        """,
        (
            integration[
                "source_system_code"
            ],
            integration[
                "stg_schema"
            ],
            integration[
                "stg_table"
            ],
        ),
    )


def group_mapping_rows(
    rows: list[dict],
) -> list[dict]:
    """
    Convert mapping/source-reference rows into one
    API object per mapping rule.

    Example:
        response_ms
            sources:
                - rq_date
                - rs_date
    """

    grouped: dict[int, dict] = {}
    mapping_order: list[int] = []

    for row in rows:
        mapping_id = int(
            row["mapping_id"]
        )

        if mapping_id not in grouped:
            grouped[mapping_id] = {
                "mapping_id":
                    mapping_id,

                "mapping_key":
                    row["mapping_key"],

                "version_no":
                    row["version_no"],

                "mapping_stage":
                    row["mapping_stage"],

                "transform_name":
                    row["transform_name"],

                "mapping_position":
                    row["mapping_position"],

                "stg_schema":
                    row["stg_schema"],

                "stg_table":
                    row["stg_table"],

                "target_schema":
                    row["target_schema"],

                "target_object":
                    row["target_object"],

                "target_column":
                    row["target_column"],

                "mapping_type":
                    row["mapping_type"],

                "transform_expression":
                    row["transform_expression"],

                "expected_pg_type":
                    row["expected_pg_type"],

                "definition_hash":
                    row["definition_hash"],

                "valid_from":
                    row["valid_from"],

                "created_by":
                    row["created_by"],

                "change_reason":
                    row["change_reason"],

                "sources": [],
            }

            mapping_order.append(
                mapping_id
            )

        if row["source_layer"] is not None:
            grouped[
                mapping_id
            ]["sources"].append(
                {
                    "source_layer":
                        row["source_layer"],

                    "source_position":
                        row[
                            "source_position"
                        ],

                    "source_schema":
                        row[
                            "source_schema"
                        ],

                    "source_object":
                        row[
                            "source_object"
                        ],

                    "source_column":
                        row[
                            "source_column"
                        ],
                }
            )

    return [
        grouped[mapping_id]
        for mapping_id
        in mapping_order
    ]


@app.get(
    "/api/integrations/{source_object_id}/mappings",
    tags=["Integrations"],
    response_model=list[IntegrationMapping],
)
def integration_mappings_grouped(
    source_object_id: int,
) -> list[IntegrationMapping]:

    raw_rows = integration_mappings_raw(
        source_object_id
    )

    grouped_rows = group_mapping_rows(
        raw_rows
    )

    return [
        IntegrationMapping.model_validate(
            row
        )
        for row in grouped_rows
    ]

@app.get(
    "/api/integrations/{source_object_id}/dq-rules",
    tags=["Integrations"],
    response_model=list[IntegrationDqRule],
)
def integration_dq_rules(
    source_object_id: int,
) -> list[dict]:

    get_integration(
        source_object_id
    )

    return fetch_all(
        """
        SELECT
            source_object_id,

            rule_id,
            rule_root_id,
            rule_key,
            version_no,

            rule_code,
            rule_name,

            severity,
            category,

            rule_source_object,
            rule_target_object,
            target_column,

            description,
            issue_limit,

            relation_role,

            valid_from,
            created_by,
            change_reason

        FROM meta.v_integration_control_dq_rule

        WHERE source_object_id = %s

        ORDER BY
            CASE relation_role
                WHEN 'SOURCE' THEN 1
                WHEN 'BOTH' THEN 2
                WHEN 'TARGET' THEN 3
                ELSE 4
            END,
            rule_id
        """,
        (
            source_object_id,
        ),
    )


@app.get(
    "/api/integrations/{source_object_id}/overview",
    tags=["Integrations"],
    response_model=IntegrationOverview,
)
def integration_overview(
    source_object_id: int,
) -> IntegrationOverview:

    integration = get_integration(
        source_object_id
    )

    target = fetch_one(
        """
        SELECT DISTINCT
            target_schema AS ods_schema,
            target_object AS ods_object

        FROM meta.v_current_mapping

        WHERE source_system_code = %s
          AND mapping_stage = 'STG_TO_ODS'
          AND stg_schema = %s
          AND stg_table = %s
          AND is_active = true
        """,
        (
            integration[
                "source_system_code"
            ],
            integration[
                "stg_schema"
            ],
            integration[
                "stg_table"
            ],
        ),
    )

    if target is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "ODS target is not configured "
                "for integration"
            ),
        )

    dq_summary = fetch_one(
        """
        SELECT
            COUNT(*) AS dq_rules_total,

            COUNT(*) FILTER (
                WHERE severity = 'ERROR'
            ) AS dq_error_rules,

            COUNT(*) FILTER (
                WHERE severity = 'WARNING'
            ) AS dq_warning_rules

        FROM meta.v_integration_control_dq_rule

        WHERE source_object_id = %s
        """,
        (
            source_object_id,
        ),
    )

    issue_summary = fetch_one(
        """
        SELECT COUNT(*) AS open_dq_issues

        FROM dq.issue i

        JOIN meta.v_integration_control_dq_rule r
          ON r.rule_id = i.rule_id
         AND r.source_object_id = %s

        WHERE i.issue_status = 'OPEN'
        """,
        (
            source_object_id,
        ),
    )

    error_summary = fetch_one(
        """
        SELECT COUNT(*) AS technical_errors

        FROM etl.v_integration_error

        WHERE source_object_id = %s
        """,
        (
            source_object_id,
        ),
    )

    latest_job = fetch_one(
        """
        SELECT
            batch_run_id,
            root_batch_run_id,
            attempt_no,
            run_mode,

            batch_status,

            job_run_id,
            job_status,

            rows_read,
            rows_inserted,
            rows_updated,
            rows_rejected,
            error_count,

            started_at,
            finished_at

        FROM etl.v_integration_job_history

        WHERE target_schema = %s
          AND target_object = %s

        ORDER BY
            batch_run_id DESC,
            job_run_id DESC

        LIMIT 1
        """,
        (
            target["ods_schema"],
            target["ods_object"],
        ),
    )

    latest_dq = fetch_one(
        """
        SELECT
            dq_run_id,
            source_batch_run_id,
            business_date,

            status,

            rules_total,
            rules_passed,
            rules_warned,
            rules_failed,

            started_at,
            finished_at,

            created_by,
            message,

            frozen_rule_versions,
            issues_total,
            issues_open

        FROM dq.v_integration_dq_history

        ORDER BY dq_run_id DESC

        LIMIT 1
        """
    )

    return IntegrationOverview(
        integration=IntegrationObject.model_validate(
            integration
        ),

        ods_schema=target[
            "ods_schema"
        ],
        ods_object=target[
            "ods_object"
        ],

        dq_rules_total=int(
            dq_summary[
                "dq_rules_total"
            ]
        ),

        dq_error_rules=int(
            dq_summary[
                "dq_error_rules"
            ]
        ),

        dq_warning_rules=int(
            dq_summary[
                "dq_warning_rules"
            ]
        ),

        open_dq_issues=int(
            issue_summary[
                "open_dq_issues"
            ]
        ),

        technical_errors=int(
            error_summary[
                "technical_errors"
            ]
        ),

        latest_job=(
            IntegrationLatestJob.model_validate(
                latest_job
            )
            if latest_job is not None
            else None
        ),

        latest_dq_run=(
            DqRun.model_validate(
                latest_dq
            )
            if latest_dq is not None
            else None
        ),
    )

@app.get(
    "/api/integrations/{source_object_id}/runs",
    tags=["Integrations"],
    response_model=list[EtlJobRun],
)
def integration_runs(
    source_object_id: int,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
) -> list[dict]:

    get_integration(
        source_object_id
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

        FROM etl.v_integration_object_job_history

        WHERE source_object_id = %s

        ORDER BY
            batch_run_id DESC,
            job_run_id DESC

        LIMIT %s
        """,
        (
            source_object_id,
            limit,
        ),
    )


@app.get(
    "/api/integrations/{source_object_id}/errors",
    tags=["Integrations"],
    response_model=list[EtlError],
)
def integration_errors(
    source_object_id: int,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
) -> list[dict]:

    get_integration(
        source_object_id
    )

    return fetch_all(
        """
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

        WHERE source_object_id = %s

        ORDER BY
            occurred_at DESC,
            error_id DESC

        LIMIT %s
        """,
        (
            source_object_id,
            limit,
        ),
    )


@app.get(
    "/api/integrations/{source_object_id}/issues",
    tags=["Integrations"],
    response_model=list[DqIssue],
)
def integration_issues(
    source_object_id: int,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    issue_status: str | None = None,
) -> list[dict]:

    get_integration(
        source_object_id
    )

    params: list[Any] = [
        source_object_id,
    ]

    status_sql = ""

    if issue_status is not None:
        status_sql = (
            "AND issue_status = %s"
        )

        params.append(
            issue_status.upper()
        )

    params.append(
        limit
    )

    sql = f"""
        SELECT
            dq_issue_id,

            dq_run_id,
            dq_result_id,
            rule_id,

            rule_code,
            rule_name,
            category,

            source_batch_run_id,

            severity,
            issue_status,

            source_system,
            source_schema,
            source_object,

            source_pk,
            application_id,
            applicant_id,

            column_name,
            bad_value,
            expected_value,

            error_code,
            error_message,

            created_at,

            resolved_at,
            resolved_by,
            resolution_comment

        FROM dq.v_integration_object_issue_history

        WHERE source_object_id = %s

        {status_sql}

        ORDER BY
            created_at DESC,
            dq_issue_id DESC

        LIMIT %s
    """

    return fetch_all(
        sql,
        tuple(params),
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
# DQ runs
# ============================================================


@app.get(
    "/api/dq/runs",
    tags=["DQ"],
    response_model=list[DqRun],
)
def dq_runs(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    source_batch_run_id: int | None = None,
    status: str | None = None,
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
            dq_run_id,
            source_batch_run_id,
            business_date,

            status,

            rules_total,
            rules_passed,
            rules_warned,
            rules_failed,

            started_at,
            finished_at,

            created_by,
            message,

            frozen_rule_versions,

            issues_total,
            issues_open

        FROM dq.v_integration_dq_history

        {where_sql}

        ORDER BY dq_run_id DESC

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
    "/api/dq/runs/{dq_run_id}/results",
    tags=["DQ"],
    response_model=list[DqResult],
)
def dq_run_results(
    dq_run_id: int,
) -> list[dict]:

    run = fetch_one(
        """
        SELECT dq_run_id
        FROM dq.run
        WHERE dq_run_id = %s
        """,
        (
            dq_run_id,
        ),
    )

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="DQ run not found",
        )

    return fetch_all(
        """
        SELECT
            res.dq_result_id,
            res.dq_run_id,

            res.rule_id,
            r.version_no AS rule_version,

            r.rule_code,
            r.rule_name,

            r.severity,
            r.category,
            r.target_column,

            res.status,

            res.checked_rows,
            res.failed_rows,

            res.metric_value,
            res.expected_value,

            COUNT(
                i.dq_issue_id
            )::bigint AS issue_count,

            res.details,
            res.checked_at

        FROM dq.result res

        JOIN dq.rule r
          ON r.rule_id = res.rule_id

        LEFT JOIN dq.issue i
          ON i.dq_result_id =
             res.dq_result_id

        WHERE res.dq_run_id = %s

        GROUP BY
            res.dq_result_id,
            res.dq_run_id,

            res.rule_id,
            r.version_no,

            r.rule_code,
            r.rule_name,

            r.severity,
            r.category,
            r.target_column,

            res.status,

            res.checked_rows,
            res.failed_rows,

            res.metric_value,
            res.expected_value,

            res.details,
            res.checked_at

        ORDER BY
            res.rule_id
        """,
        (
            dq_run_id,
        ),
    )

# ============================================================
# DQ row-level issues
# ============================================================


@app.get(
    "/api/dq/issues",
    tags=["DQ"],
    response_model=list[DqIssue],
)
def dq_issues(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    dq_run_id: int | None = None,
    issue_status: str | None = None,
    source_schema: str | None = None,
    source_object: str | None = None,
) -> list[dict]:

    conditions: list[str] = []
    params: list[Any] = []

    if dq_run_id is not None:
        conditions.append(
            "dq_run_id = %s"
        )
        params.append(
            dq_run_id
        )

    if issue_status is not None:
        conditions.append(
            "issue_status = %s"
        )
        params.append(
            issue_status.upper()
        )

    if source_schema is not None:
        conditions.append(
            "source_schema = %s"
        )
        params.append(
            source_schema
        )

    if source_object is not None:
        conditions.append(
            "source_object = %s"
        )
        params.append(
            source_object
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
            dq_issue_id,

            dq_run_id,
            dq_result_id,
            rule_id,

            rule_code,
            rule_name,
            category,

            source_batch_run_id,

            severity,
            issue_status,

            source_system,
            source_schema,
            source_object,

            source_pk,
            application_id,
            applicant_id,

            column_name,
            bad_value,
            expected_value,

            error_code,
            error_message,

            created_at,

            resolved_at,
            resolved_by,
            resolution_comment

        FROM dq.v_integration_issue

        {where_sql}

        ORDER BY
            created_at DESC,
            dq_issue_id DESC

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
# Reporting
# ============================================================


@app.get(
    "/api/reporting/retail/overview",
    tags=["Reporting"],
    response_model=RetailReportingOverview,
)
def retail_reporting_overview() -> RetailReportingOverview:
    summary = fetch_one(
        """
        SELECT
            COUNT(*)::bigint
                AS applications,

            COUNT(requested_amount)::bigint
                AS requested_amount_rows,

            COUNT(chosen_amount)::bigint
                AS chosen_amount_rows,

            COUNT(final_amount)::bigint
                AS final_amount_rows,

            COALESCE(
                SUM(requested_amount),
                0
            )::double precision
                AS requested_amount_total,

            COALESCE(
                SUM(chosen_amount),
                0
            )::double precision
                AS chosen_amount_total,

            COALESCE(
                SUM(final_amount),
                0
            )::double precision
                AS final_amount_total

        FROM dm.v_retail_application_current
        """
    )

    if summary is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Retail reporting summary "
                "could not be calculated"
            ),
        )

    by_status = fetch_all(
        """
        SELECT
            status_id::text AS id,
            NULL::text AS code,
            status_name_ru AS name,
            COUNT(*)::bigint AS applications
        FROM dm.v_retail_application_current
        GROUP BY
            status_id,
            status_name_ru
        ORDER BY
            applications DESC,
            status_id
        """
    )

    by_stage = fetch_all(
        """
        SELECT
            stage_id::text AS id,
            NULL::text AS code,
            stage_name_ru AS name,
            COUNT(*)::bigint AS applications
        FROM dm.v_retail_application_current
        GROUP BY
            stage_id,
            stage_name_ru
        ORDER BY
            applications DESC,
            stage_id
        """
    )

    by_channel = fetch_all(
        """
        SELECT
            sales_channel_id::text AS id,
            sales_channel_code::text AS code,
            sales_channel_name_ru AS name,
            COUNT(*)::bigint AS applications
        FROM dm.v_retail_application_current
        GROUP BY
            sales_channel_id,
            sales_channel_code,
            sales_channel_name_ru
        ORDER BY
            applications DESC,
            sales_channel_id
        """
    )

    by_product = fetch_all(
        """
        SELECT
            requested_product_id::text AS id,
            requested_product_code::text AS code,
            requested_product_name_ru AS name,
            COUNT(*)::bigint AS applications
        FROM dm.v_retail_application_current
        GROUP BY
            requested_product_id,
            requested_product_code,
            requested_product_name_ru
        ORDER BY
            applications DESC,
            requested_product_id
        """
    )

    return RetailReportingOverview(
        **summary,
        by_status=by_status,
        by_stage=by_stage,
        by_channel=by_channel,
        by_product=by_product,
    )

@app.get(
    "/api/reporting/retail/export",
    tags=["Reporting"],
)
def retail_reporting_export(
    report: str = Query(
        pattern=(
            "^(status|stage|channel|product)$"
        ),
    ),
    file_format: str = Query(
        alias="format",
        pattern="^(txt|pdf|xlsx)$",
    ),
) -> Response:

    if report == "status":
        report_title = "Retail Applications - Status"

        columns = [
            ExportColumn(
                key="id",
                label="ID",
                width=24,
            ),
            ExportColumn(
                key="name",
                label="Name",
                width=46,
            ),
            ExportColumn(
                key="applications",
                label="Applications",
                width=16,
            ),
        ]

        rows = fetch_all(
            """
            SELECT
                status_id::text
                    AS id,

                status_name_ru
                    AS name,

                COUNT(*)::bigint
                    AS applications

            FROM dm.v_retail_application_current

            GROUP BY
                status_id,
                status_name_ru

            ORDER BY
                applications DESC,
                status_id
            """
        )

    elif report == "stage":
        report_title = "Retail Applications - Stage"

        columns = [
            ExportColumn(
                key="id",
                label="ID",
                width=28,
            ),
            ExportColumn(
                key="name",
                label="Name",
                width=52,
            ),
            ExportColumn(
                key="applications",
                label="Applications",
                width=16,
            ),
        ]

        rows = fetch_all(
            """
            SELECT
                stage_id::text
                    AS id,

                stage_name_ru
                    AS name,

                COUNT(*)::bigint
                    AS applications

            FROM dm.v_retail_application_current

            GROUP BY
                stage_id,
                stage_name_ru

            ORDER BY
                applications DESC,
                stage_id
            """
        )

    elif report == "channel":
        report_title = (
            "Retail Applications - Sales Channel"
        )

        columns = [
            ExportColumn(
                key="id",
                label="ID",
                width=12,
            ),
            ExportColumn(
                key="code",
                label="Code",
                width=12,
            ),
            ExportColumn(
                key="name",
                label="Name",
                width=48,
            ),
            ExportColumn(
                key="applications",
                label="Applications",
                width=16,
            ),
        ]

        rows = fetch_all(
            """
            SELECT
                sales_channel_id::text
                    AS id,

                sales_channel_code::text
                    AS code,

                sales_channel_name_ru
                    AS name,

                COUNT(*)::bigint
                    AS applications

            FROM dm.v_retail_application_current

            GROUP BY
                sales_channel_id,
                sales_channel_code,
                sales_channel_name_ru

            ORDER BY
                applications DESC,
                sales_channel_id
            """
        )

    else:
        report_title = (
            "Retail Applications - "
            "Bank Pre-approved Product"
        )

        columns = [
            ExportColumn(
                key="id",
                label="ID",
                width=12,
            ),
            ExportColumn(
                key="code",
                label="Code",
                width=12,
            ),
            ExportColumn(
                key="name",
                label="Name",
                width=52,
            ),
            ExportColumn(
                key="applications",
                label="Applications",
                width=16,
            ),
        ]

        rows = fetch_all(
            """
            SELECT
                requested_product_id::text
                    AS id,

                requested_product_code::text
                    AS code,

                requested_product_name_ru
                    AS name,

                COUNT(*)::bigint
                    AS applications

            FROM dm.v_retail_application_current

            GROUP BY
                requested_product_id,
                requested_product_code,
                requested_product_name_ru

            ORDER BY
                applications DESC,
                requested_product_id
            """
        )

    payload, media_type, filename = (
        build_export(
            file_format=file_format,
            report_code=report,
            report_title=report_title,
            columns=columns,
            rows=rows,
        )
    )

    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
            "Cache-Control": "no-store",
        },
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
app.include_router(role_catalog_router)
app.include_router(access_control_router)
app.include_router(user_catalog_router)