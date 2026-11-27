from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )


class HealthResponse(ApiModel):
    status: str
    database_user: str
    database_name: str
    transaction_read_only: str
    integration_objects: int


class ApiError(ApiModel):
    error: str
    message: str
    request_id: str
    path: str
    details: Any | None = None


class EtlRun(ApiModel):
    batch_run_id: int
    source_batch_run_id: int | None

    pipeline_code: str
    run_mode: str | None

    root_batch_run_id: int | None
    resume_from_batch_run_id: int | None
    attempt_no: int | None

    preflight_batch_run_id: int | None

    business_date: date | None
    trigger_type: str
    status: str

    started_at: datetime
    finished_at: datetime | None

    created_by: str | None
    message: str | None

    contract_versions: int
    mapping_versions: int

    jobs_success: int
    jobs_failed: int
    jobs_skipped: int
    jobs_pending: int


class EtlJobRun(ApiModel):
    batch_run_id: int
    source_batch_run_id: int | None

    root_batch_run_id: int | None
    attempt_no: int | None
    run_mode: str | None

    batch_status: str

    job_id: int
    job_code: str
    execution_order: int

    target_schema: str
    target_object: str

    job_run_id: int
    job_status: str

    reused_from_job_run_id: int | None
    skip_reason: str | None

    rows_read: int | None
    rows_inserted: int | None
    rows_updated: int | None
    rows_rejected: int | None
    error_count: int | None

    started_at: datetime | None
    finished_at: datetime | None

    message: str | None


class EtlError(ApiModel):
    error_id: int

    batch_run_id: int | None
    job_run_id: int | None
    job_id: int | None
    source_object_id: int | None

    error_stage: str | None
    error_code: str | None
    error_message: str | None
    error_detail: str | None

    record_key: Any | None

    source_pk: str | None
    application_id: int | None
    applicant_id: int | None

    column_name: str | None
    bad_value: str | None
    expected_value: str | None

    sql_state: str | None
    exception_type: str | None
    diagnostic_context: Any | None

    occurred_at: datetime


# ============================================================
# Reporting
# ============================================================


