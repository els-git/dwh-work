export interface HealthResponse {
  status: string
  database_user: string
  database_name: string
  transaction_read_only: string
  integration_objects: number
}

export interface IntegrationObject {
  source_system_code: string
  source_schema: string
  source_object: string
  object_type: string

  source_columns: number
  pk_columns: number

  contracted_columns: number
  required_columns: number
  active_contracts: number

  mappings: number | null
  mapped_source_columns: number

  direct_mappings: number
  cast_mappings: number
  derived_mappings: number
  system_mappings: number
  constant_mappings: number

  metadata_captured_at: string

  source_object_id: number

  load_strategy: string
  watermark_column: string | null

  stg_schema: string
  stg_table: string

  load_order: number
  integration_active: boolean

  description: string | null

  total_mappings: number
}

export interface SourceColumn {
  ordinal_position: number

  source_column: string
  source_type: string

  is_nullable: boolean
  is_primary_key: boolean
  is_unique_key: boolean
  is_identity: boolean
  is_generated: boolean

  metadata_captured_at: string
}

export interface ValidationContract {
  ordinal_position: number

  source_column: string
  source_type: string

  contract_id: number
  contract_root_id: number
  contract_version: number

  expected_pg_type: string

  is_required: boolean
  allow_blank: boolean
  contract_active: boolean

  contract_target_schema: string
  contract_target_object: string
  contract_target_column: string

  contract_valid_from: string
}

export interface MappingSource {
  source_layer: string
  source_position: number | null

  source_schema: string
  source_object: string
  source_column: string
}

export interface IntegrationMapping {
  mapping_id: number
  mapping_key: string
  version_no: number

  mapping_stage: string

  transform_name: string
  mapping_position: number

  stg_schema: string
  stg_table: string

  target_schema: string
  target_object: string
  target_column: string

  mapping_type: string
  transform_expression: string | null
  expected_pg_type: string | null

  definition_hash: string
  valid_from: string

  created_by: string | null
  change_reason: string | null

  sources: MappingSource[]
}

export interface IntegrationDqRule {
  source_object_id: number

  rule_id: number
  rule_root_id: number
  rule_key: string
  version_no: number

  rule_code: string
  rule_name: string

  severity: string
  category: string

  rule_source_object: string
  rule_target_object: string
  target_column: string | null

  description: string | null
  issue_limit: number

  relation_role: string

  valid_from: string

  created_by: string | null
  change_reason: string | null
}

export interface IntegrationLatestJob {
  batch_run_id: number
  root_batch_run_id: number | null
  attempt_no: number | null
  run_mode: string | null

  batch_status: string

  job_run_id: number
  job_status: string

  rows_read: number | null
  rows_inserted: number | null
  rows_updated: number | null
  rows_rejected: number | null
  error_count: number | null

  started_at: string | null
  finished_at: string | null
}

export interface DqRun {
  dq_run_id: number
  source_batch_run_id: number

  business_date: string | null
  status: string

  rules_total: number
  rules_passed: number
  rules_warned: number
  rules_failed: number

  started_at: string
  finished_at: string | null

  created_by: string | null
  message: string | null

  frozen_rule_versions: number
  issues_total: number
  issues_open: number
}

export interface IntegrationOverview {
  integration: IntegrationObject

  ods_schema: string
  ods_object: string

  dq_rules_total: number
  dq_error_rules: number
  dq_warning_rules: number

  open_dq_issues: number
  technical_errors: number

  latest_job: IntegrationLatestJob | null
  latest_dq_run: DqRun | null
}

export interface EtlJobRun {
  batch_run_id: number
  source_batch_run_id: number | null

  root_batch_run_id: number | null
  attempt_no: number | null
  run_mode: string | null

  batch_status: string

  job_id: number
  job_code: string
  execution_order: number

  target_schema: string
  target_object: string

  job_run_id: number
  job_status: string

  reused_from_job_run_id: number | null
  skip_reason: string | null

  rows_read: number | null
  rows_inserted: number | null
  rows_updated: number | null
  rows_rejected: number | null
  error_count: number | null

  started_at: string | null
  finished_at: string | null

  message: string | null
}


export interface EtlError {
  error_id: number

  batch_run_id: number | null
  job_run_id: number | null
  job_id: number | null
  source_object_id: number | null

  error_stage: string | null
  error_code: string | null
  error_message: string | null
  error_detail: string | null

  record_key: unknown | null

  source_pk: string | null
  application_id: number | null
  applicant_id: number | null

  column_name: string | null
  bad_value: string | null
  expected_value: string | null

  sql_state: string | null
  exception_type: string | null
  diagnostic_context: unknown | null

  occurred_at: string
}


export interface DqIssue {
  dq_issue_id: number

  dq_run_id: number
  dq_result_id: number
  rule_id: number

  rule_code: string
  rule_name: string
  category: string

  source_batch_run_id: number

  severity: string
  issue_status: string

  source_system: string
  source_schema: string
  source_object: string

  source_pk: string | null
  application_id: number | null
  applicant_id: number | null

  column_name: string | null
  bad_value: string | null
  expected_value: string | null

  error_code: string | null
  error_message: string | null

  created_at: string

  resolved_at: string | null
  resolved_by: string | null
  resolution_comment: string | null
}

export interface EtlRun {
  batch_run_id: number
  source_batch_run_id: number | null

  pipeline_code: string
  run_mode: string | null

  root_batch_run_id: number | null
  resume_from_batch_run_id: number | null
  attempt_no: number | null

  preflight_batch_run_id: number | null

  business_date: string | null
  trigger_type: string
  status: string

  started_at: string
  finished_at: string | null

  created_by: string | null
  message: string | null

  contract_versions: number
  mapping_versions: number

  jobs_success: number
  jobs_failed: number
  jobs_skipped: number
  jobs_pending: number
}

export interface DqResult {
  dq_result_id: number
  dq_run_id: number

  rule_id: number
  rule_version: number

  rule_code: string
  rule_name: string

  severity: string
  category: string
  target_column: string | null

  status: string

  checked_rows: number | null
  failed_rows: number

  metric_value: number | null
  expected_value: number | null

  issue_count: number

  details: string | null
  checked_at: string
}

export interface ReportingDimensionItem {
  id: string | null
  code: string | null
  name: string | null
  applications: number
}


export interface RetailReportingOverview {
  applications: number

  requested_amount_rows: number
  chosen_amount_rows: number
  final_amount_rows: number

  requested_amount_total: number
  chosen_amount_total: number
  final_amount_total: number

  by_status: ReportingDimensionItem[]
  by_stage: ReportingDimensionItem[]
  by_channel: ReportingDimensionItem[]
  by_product: ReportingDimensionItem[]
}