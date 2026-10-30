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


class ApiMetadata(ApiModel):
    api_name: str
    api_version: str
    mode: str

    database_user: str

    integration_objects: int
    mapping_rules: int
    validation_contracts: int
    dq_rules: int


class ApiError(ApiModel):
    error: str
    message: str
    request_id: str
    path: str
    details: Any | None = None


class IntegrationObject(ApiModel):
    source_system_code: str
    source_schema: str
    source_object: str
    object_type: str

    source_columns: int
    pk_columns: int

    contracted_columns: int
    required_columns: int
    active_contracts: int

    mappings: int | None = None
    mapped_source_columns: int

    direct_mappings: int
    cast_mappings: int
    derived_mappings: int
    system_mappings: int

    metadata_captured_at: datetime

    source_object_id: int

    load_strategy: str
    watermark_column: str | None

    stg_schema: str
    stg_table: str

    load_order: int
    integration_active: bool

    description: str | None

    total_mappings: int
    constant_mappings: int


class SourceColumn(ApiModel):
    ordinal_position: int

    source_column: str
    source_type: str

    is_nullable: bool
    is_primary_key: bool
    is_unique_key: bool
    is_identity: bool
    is_generated: bool

    metadata_captured_at: datetime


class ValidationContract(ApiModel):
    ordinal_position: int

    source_column: str
    source_type: str

    contract_id: int
    contract_root_id: int
    contract_version: int

    expected_pg_type: str

    is_required: bool
    allow_blank: bool
    contract_active: bool

    contract_target_schema: str
    contract_target_object: str
    contract_target_column: str

    contract_valid_from: datetime


class MappingSource(ApiModel):
    source_layer: str
    source_position: int | None

    source_schema: str
    source_object: str
    source_column: str


class IntegrationMapping(ApiModel):
    mapping_id: int
    mapping_key: str
    version_no: int

    mapping_stage: str

    transform_name: str
    mapping_position: int

    stg_schema: str
    stg_table: str

    target_schema: str
    target_object: str
    target_column: str

    mapping_type: str
    transform_expression: str | None
    expected_pg_type: str | None

    definition_hash: str

    valid_from: datetime

    created_by: str | None
    change_reason: str | None

    sources: list[MappingSource]


class EtlRun(ApiModel):
    batch_run_id: int
    source_batch_run_id: int

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
    source_batch_run_id: int

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


class DqRun(ApiModel):
    dq_run_id: int
    source_batch_run_id: int

    business_date: date | None
    status: str

    rules_total: int
    rules_passed: int
    rules_warned: int
    rules_failed: int

    started_at: datetime
    finished_at: datetime | None

    created_by: str | None
    message: str | None

    frozen_rule_versions: int
    issues_total: int
    issues_open: int


class DqIssue(ApiModel):
    dq_issue_id: int

    dq_run_id: int
    dq_result_id: int
    rule_id: int

    rule_code: str
    rule_name: str
    category: str

    source_batch_run_id: int

    severity: str
    issue_status: str

    source_system: str
    source_schema: str
    source_object: str

    source_pk: str | None
    application_id: int | None
    applicant_id: int | None

    column_name: str | None
    bad_value: str | None
    expected_value: str | None

    error_code: str | None
    error_message: str | None

    created_at: datetime

    resolved_at: datetime | None
    resolved_by: str | None
    resolution_comment: str | None

class IntegrationDqRule(ApiModel):
    source_object_id: int

    rule_id: int
    rule_root_id: int
    rule_key: str
    version_no: int

    rule_code: str
    rule_name: str

    severity: str
    category: str

    rule_source_object: str
    rule_target_object: str
    target_column: str | None

    description: str | None
    issue_limit: int

    relation_role: str

    valid_from: datetime

    created_by: str | None
    change_reason: str | None


class IntegrationLatestJob(ApiModel):
    batch_run_id: int
    root_batch_run_id: int | None
    attempt_no: int | None
    run_mode: str | None

    batch_status: str

    job_run_id: int
    job_status: str

    rows_read: int | None
    rows_inserted: int | None
    rows_updated: int | None
    rows_rejected: int | None
    error_count: int | None

    started_at: datetime | None
    finished_at: datetime | None


class IntegrationOverview(ApiModel):
    integration: IntegrationObject

    ods_schema: str
    ods_object: str

    dq_rules_total: int
    dq_error_rules: int
    dq_warning_rules: int

    open_dq_issues: int
    technical_errors: int

    latest_job: IntegrationLatestJob | None
    latest_dq_run: DqRun | None

class DqResult(ApiModel):
    dq_result_id: int
    dq_run_id: int

    rule_id: int
    rule_version: int

    rule_code: str
    rule_name: str

    severity: str
    category: str
    target_column: str | None

    status: str

    checked_rows: int | None
    failed_rows: int

    metric_value: float | None
    expected_value: float | None

    issue_count: int

    details: str | None
    checked_at: datetime

# ============================================================
# Reporting
# ============================================================


class ReportingDimensionItem(ApiModel):
    id: str | None
    code: str | None = None
    name: str | None
    applications: int


class RetailReportingOverview(ApiModel):
    applications: int

    requested_amount_rows: int
    chosen_amount_rows: int
    final_amount_rows: int

    requested_amount_total: float
    chosen_amount_total: float
    final_amount_total: float

    by_status: list[ReportingDimensionItem]
    by_stage: list[ReportingDimensionItem]
    by_channel: list[ReportingDimensionItem]
    by_product: list[ReportingDimensionItem]