export interface HealthResponse {
  status: string
  database_user: string
  database_name: string
  transaction_read_only: string
  integration_objects: number
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
