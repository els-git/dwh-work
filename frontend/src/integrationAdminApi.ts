export interface IntegrationSourceSystem {
  source_system_id: number
  code: string
  name: string
  source_type: string
  description: string | null
  is_active: boolean
  created_at: string
  updated_at: string
  object_count: number
}


export interface IntegrationSourceObject {
  source_object_id: number
  source_system_id: number
  source_system_code: string
  source_system_name: string
  source_schema: string
  source_object: string
  object_type: string
  load_strategy: string
  watermark_column: string | null
  target_schema: string | null
  target_object: string | null
  load_order: number
  is_active: boolean
  description: string | null
  created_at: string
  updated_at: string
}


export interface SourceSystemCreatePayload {
  code: string
  name: string
  source_type: string
  description: string | null
}


export interface SourceSystemUpdatePayload {
  name: string
  source_type: string
  description: string | null
}


export interface SourceObjectCreatePayload {
  source_system_id: number
  source_schema: string
  source_object: string
  object_type: string
  load_strategy: string
  watermark_column: string | null
  target_schema: string | null
  target_object: string | null
  load_order: number
  description: string | null
}


export interface SourceObjectUpdatePayload {
  object_type: string
  load_strategy: string
  watermark_column: string | null
  target_schema: string | null
  target_object: string | null
  load_order: number
  description: string | null
}


interface ApiErrorResponse {
  message?: string
  detail?: string
}


async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const headers =
    new Headers(
      init?.headers,
    )

  headers.set(
    'Accept',
    'application/json',
  )

  if (init?.body !== undefined) {
    headers.set(
      'Content-Type',
      'application/json',
    )
  }

  const response =
    await fetch(
      path,
      {
        ...init,
        headers,
      },
    )

  if (!response.ok) {
    let payload: ApiErrorResponse | null

    try {
      payload =
        await response.json() as ApiErrorResponse
    } catch {
      payload = null
    }

    throw new Error(
      payload?.message
      ?? payload?.detail
      ?? `${response.status} ${response.statusText}`,
    )
  }

  return response.json() as Promise<T>
}


export function getIntegrationSourceSystems():
Promise<IntegrationSourceSystem[]> {
  return apiRequest<IntegrationSourceSystem[]>(
    '/api/admin/integrations/source-systems',
  )
}


export function createIntegrationSourceSystem(
  payload: SourceSystemCreatePayload,
): Promise<unknown> {
  return apiRequest<unknown>(
    '/api/admin/integrations/source-systems',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}


export function updateIntegrationSourceSystem(
  sourceSystemId: number,
  payload: SourceSystemUpdatePayload,
): Promise<unknown> {
  return apiRequest<unknown>(
    '/api/admin/integrations/source-systems/'
    + encodeURIComponent(
      String(sourceSystemId),
    ),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}


export function setIntegrationSourceSystemActive(
  sourceSystemId: number,
  isActive: boolean,
): Promise<unknown> {
  return apiRequest<unknown>(
    '/api/admin/integrations/source-systems/'
    + encodeURIComponent(
      String(sourceSystemId),
    )
    + '/'
    + (
      isActive
        ? 'enable'
        : 'disable'
    ),
    {
      method: 'POST',
    },
  )
}


export function getIntegrationSourceObjects(
  sourceSystemId?: number,
): Promise<IntegrationSourceObject[]> {
  const query =
    sourceSystemId === undefined
      ? ''
      : (
          '?source_system_id='
          + encodeURIComponent(
            String(sourceSystemId),
          )
        )

  return apiRequest<IntegrationSourceObject[]>(
    '/api/admin/integrations/source-objects'
    + query,
  )
}


export function createIntegrationSourceObject(
  payload: SourceObjectCreatePayload,
): Promise<unknown> {
  return apiRequest<unknown>(
    '/api/admin/integrations/source-objects',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}


export function updateIntegrationSourceObject(
  sourceObjectId: number,
  payload: SourceObjectUpdatePayload,
): Promise<unknown> {
  return apiRequest<unknown>(
    '/api/admin/integrations/source-objects/'
    + encodeURIComponent(
      String(sourceObjectId),
    ),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}


export function setIntegrationSourceObjectActive(
  sourceObjectId: number,
  isActive: boolean,
): Promise<unknown> {
  return apiRequest<unknown>(
    '/api/admin/integrations/source-objects/'
    + encodeURIComponent(
      String(sourceObjectId),
    )
    + '/'
    + (
      isActive
        ? 'enable'
        : 'disable'
    ),
    {
      method: 'POST',
    },
  )
}
// ============================================================
// Contracts
// ============================================================

export interface IntegrationContract {
  contract_id: number
  contract_root_id: number
  version_no: number
  source_system_code: string
  source_schema: string
  source_object: string
  stg_table: string
  source_column: string
  target_schema: string
  target_object: string
  target_column: string
  expected_pg_type: string
  is_required: boolean
  allow_blank: boolean
  is_active: boolean
  description: string | null
  valid_from: string
  valid_to: string | null
  supersedes_contract_id: number | null
  created_by: string | null
  change_reason: string | null
  created_at: string
}


export interface IntegrationContractCreatePayload {
  source_system_code: string
  source_schema: string
  source_object: string
  stg_table: string
  source_column: string
  target_schema: string
  target_object: string
  target_column: string
  expected_pg_type: string
  is_required: boolean
  allow_blank: boolean
  is_active: boolean
  description: string | null
  change_reason: string | null
}


export interface IntegrationContractUpdatePayload {
  target_schema: string
  target_object: string
  target_column: string
  expected_pg_type: string
  is_required: boolean
  allow_blank: boolean
  is_active: boolean
  description: string | null
  change_reason: string | null
}


export function getIntegrationContracts(
  sourceSystemCode?: string,
): Promise<IntegrationContract[]> {
  const query =
    sourceSystemCode
      ? (
          '?source_system_code='
          + encodeURIComponent(
            sourceSystemCode,
          )
        )
      : ''

  return apiRequest<IntegrationContract[]>(
    '/api/admin/integrations/contracts'
    + query,
  )
}


export function createIntegrationContract(
  payload: IntegrationContractCreatePayload,
): Promise<{ contract_id: number }> {
  return apiRequest<{ contract_id: number }>(
    '/api/admin/integrations/contracts',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}


export function updateIntegrationContract(
  contractId: number,
  payload: IntegrationContractUpdatePayload,
): Promise<{ contract_id: number }> {
  return apiRequest<{ contract_id: number }>(
    '/api/admin/integrations/contracts/'
    + encodeURIComponent(
      String(contractId),
    ),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}


export function setIntegrationContractActive(
  contractId: number,
  isActive: boolean,
): Promise<{
  contract_id: number
  is_active: boolean
}> {
  return apiRequest<{
    contract_id: number
    is_active: boolean
  }>(
    '/api/admin/integrations/contracts/'
    + encodeURIComponent(
      String(contractId),
    )
    + '/'
    + (
      isActive
        ? 'enable'
        : 'disable'
    ),
    {
      method: 'POST',
      body: JSON.stringify({
        change_reason: null,
      }),
    },
  )
}


// ============================================================
// Mappings
// ============================================================

export interface IntegrationMappingSourceAdmin {
  source_layer: string
  source_schema: string
  source_object: string
  source_column: string
  source_role: string
  ordinal_position: number
}


export interface IntegrationMappingAdmin {
  mapping_id: number
  mapping_key: string
  version_no: number
  source_system_code: string
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
  is_active: boolean
  valid_from: string
  valid_to: string | null
  supersedes_mapping_id: number | null
  created_by: string | null
  change_reason: string | null
  created_at: string
  definition_hash: string
  sources: IntegrationMappingSourceAdmin[]
}


export interface IntegrationMappingSourcePayload {
  source_layer: string
  source_schema: string
  source_object: string
  source_column: string
  source_role: string
}


export interface IntegrationMappingCreatePayload {
  source_system_code: string
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
  is_active: boolean
  sources: IntegrationMappingSourcePayload[]
  change_reason: string | null
}


export interface IntegrationMappingUpdatePayload {
  transform_name: string
  mapping_position: number
  stg_schema: string
  stg_table: string
  mapping_type: string
  transform_expression: string | null
  expected_pg_type: string | null
  is_active: boolean
  sources: IntegrationMappingSourcePayload[]
  change_reason: string | null
}


export function getIntegrationAdminMappings(
  sourceSystemCode?: string,
): Promise<IntegrationMappingAdmin[]> {
  const query =
    sourceSystemCode
      ? (
          '?source_system_code='
          + encodeURIComponent(
            sourceSystemCode,
          )
        )
      : ''

  return apiRequest<IntegrationMappingAdmin[]>(
    '/api/admin/integrations/mappings'
    + query,
  )
}


export function createIntegrationMapping(
  payload: IntegrationMappingCreatePayload,
): Promise<{ mapping_id: number }> {
  return apiRequest<{ mapping_id: number }>(
    '/api/admin/integrations/mappings',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}


export function updateIntegrationMapping(
  mappingId: number,
  payload: IntegrationMappingUpdatePayload,
): Promise<{ mapping_id: number }> {
  return apiRequest<{ mapping_id: number }>(
    '/api/admin/integrations/mappings/'
    + encodeURIComponent(
      String(mappingId),
    ),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}


export function setIntegrationMappingActive(
  mappingId: number,
  isActive: boolean,
): Promise<{
  mapping_id: number
  is_active: boolean
}> {
  return apiRequest<{
    mapping_id: number
    is_active: boolean
  }>(
    '/api/admin/integrations/mappings/'
    + encodeURIComponent(
      String(mappingId),
    )
    + '/'
    + (
      isActive
        ? 'enable'
        : 'disable'
    ),
    {
      method: 'POST',
      body: JSON.stringify({
        change_reason: null,
      }),
    },
  )
}


// ============================================================
// DQ rules
// ============================================================

export interface IntegrationDqRuleAdmin {
  rule_id: number
  rule_root_id: number
  rule_key: string
  rule_code: string
  version_no: number
  rule_name: string
  severity: string
  category: string
  source_object: string | null
  target_object: string | null
  target_column: string | null
  description: string | null
  issue_limit: number
  is_active: boolean
  valid_from: string
  valid_to: string | null
  supersedes_rule_id: number | null
  created_by: string | null
  change_reason: string | null
  created_at: string
}


export interface IntegrationDqRuleCreatePayload {
  rule_code: string
  rule_name: string
  severity: string
  category: string
  source_object: string | null
  target_object: string | null
  target_column: string | null
  description: string | null
  issue_limit: number
  is_active: boolean
  change_reason: string | null
}


export interface IntegrationDqRuleUpdatePayload {
  rule_name: string
  severity: string
  category: string
  source_object: string | null
  target_object: string | null
  target_column: string | null
  description: string | null
  issue_limit: number
  is_active: boolean
  change_reason: string | null
}


export function getIntegrationAdminDqRules():
Promise<IntegrationDqRuleAdmin[]> {
  return apiRequest<IntegrationDqRuleAdmin[]>(
    '/api/admin/integrations/dq-rules',
  )
}


export function createIntegrationDqRule(
  payload: IntegrationDqRuleCreatePayload,
): Promise<{ rule_id: number }> {
  return apiRequest<{ rule_id: number }>(
    '/api/admin/integrations/dq-rules',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}


export function updateIntegrationDqRule(
  ruleId: number,
  payload: IntegrationDqRuleUpdatePayload,
): Promise<{ rule_id: number }> {
  return apiRequest<{ rule_id: number }>(
    '/api/admin/integrations/dq-rules/'
    + encodeURIComponent(
      String(ruleId),
    ),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}


export function setIntegrationDqRuleActive(
  ruleId: number,
  isActive: boolean,
): Promise<{
  rule_id: number
  is_active: boolean
}> {
  return apiRequest<{
    rule_id: number
    is_active: boolean
  }>(
    '/api/admin/integrations/dq-rules/'
    + encodeURIComponent(
      String(ruleId),
    )
    + '/'
    + (
      isActive
        ? 'enable'
        : 'disable'
    ),
    {
      method: 'POST',
      body: JSON.stringify({
        change_reason: null,
      }),
    },
  )
}

// ============================================================
// Run Control
// ============================================================

export interface IntegrationRunCandidate {
  batch_run_id: number
  business_date: string | null
  pipeline_code: string
}


export interface IntegrationRunRequestAdmin {
  run_request_id: number
  task_code: string
  source_batch_run_id: number
  load_mode: string | null
  status: string
  requested_by_user_id: number
  requested_by_username: string
  requested_at: string
  claimed_by: string | null
  claimed_at: string | null
  started_at: string | null
  finished_at: string | null
  result_batch_run_id: number | null
  result_pipeline_code: string | null
  result_batch_status: string | null
  result_dq_run_id: number | null
  result_dq_status: string | null
  message: string | null
  error_message: string | null
}


export interface IntegrationRunRequestCreatePayload {
  task_code: string
  source_batch_run_id: number
  load_mode: string | null
}


export interface IntegrationRunRequestCancelPayload {
  reason: string | null
}


export function getIntegrationRunCandidates(
  taskCode: string,
  limit = 200,
): Promise<IntegrationRunCandidate[]> {
  const params =
    new URLSearchParams()

  params.set(
    'task_code',
    taskCode,
  )

  params.set(
    'limit',
    String(limit),
  )

  return apiRequest<IntegrationRunCandidate[]>(
    '/api/admin/integrations/run-candidates?'
    + params.toString(),
  )
}


export function getIntegrationRunRequests(
  status?: string,
  limit = 100,
): Promise<IntegrationRunRequestAdmin[]> {
  const params =
    new URLSearchParams()

  if (status) {
    params.set(
      'status',
      status,
    )
  }

  params.set(
    'limit',
    String(limit),
  )

  return apiRequest<IntegrationRunRequestAdmin[]>(
    '/api/admin/integrations/run-requests?'
    + params.toString(),
  )
}


export function createIntegrationRunRequest(
  payload: IntegrationRunRequestCreatePayload,
): Promise<{ id: number }> {
  return apiRequest<{ id: number }>(
    '/api/admin/integrations/run-requests',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}


export function cancelIntegrationRunRequest(
  runRequestId: number,
  payload: IntegrationRunRequestCancelPayload,
): Promise<unknown> {
  return apiRequest<unknown>(
    '/api/admin/integrations/run-requests/'
    + encodeURIComponent(
      String(runRequestId),
    )
    + '/cancel',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}


// ============================================================
// History / Errors
// ============================================================

export interface IntegrationHistoryEtlRun {
  batch_run_id: number
  source_batch_run_id: number
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


export interface IntegrationHistoryEtlError {
  error_id: number
  batch_run_id: number | null
  job_run_id: number | null
  job_id: number | null
  source_object_id: number | null
  source_system_code: string | null
  source_system_name: string | null
  source_schema: string | null
  source_object: string | null
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


export interface IntegrationHistoryDqRun {
  dq_run_id: number
  source_batch_run_id: number
  business_date: string | null
  status: string
  started_at: string
  finished_at: string | null
  created_by: string | null
  message: string | null
}


export interface IntegrationHistoryDqIssue {
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


export function getIntegrationHistoryEtlRuns(
  limit = 100,
): Promise<IntegrationHistoryEtlRun[]> {
  return apiRequest<IntegrationHistoryEtlRun[]>(
    '/api/admin/integrations/history/etl-runs'
    + '?limit='
    + encodeURIComponent(
      String(limit),
    ),
  )
}


export function getIntegrationHistoryEtlErrors(
  limit = 100,
  batchRunId?: number,
): Promise<IntegrationHistoryEtlError[]> {
  const params =
    new URLSearchParams()

  params.set(
    'limit',
    String(limit),
  )

  if (batchRunId !== undefined) {
    params.set(
      'batch_run_id',
      String(batchRunId),
    )
  }

  return apiRequest<IntegrationHistoryEtlError[]>(
    '/api/admin/integrations/history/etl-errors?'
    + params.toString(),
  )
}


export function getIntegrationHistoryDqRuns(
  limit = 100,
): Promise<IntegrationHistoryDqRun[]> {
  return apiRequest<IntegrationHistoryDqRun[]>(
    '/api/admin/integrations/history/dq-runs'
    + '?limit='
    + encodeURIComponent(
      String(limit),
    ),
  )
}


export function getIntegrationHistoryDqIssues(
  limit = 100,
): Promise<IntegrationHistoryDqIssue[]> {
  return apiRequest<IntegrationHistoryDqIssue[]>(
    '/api/admin/integrations/history/dq-issues'
    + '?limit='
    + encodeURIComponent(
      String(limit),
    ),
  )
}



// Source connection and credential (I03). The credential is write-only: the
// API never returns it, only its state and the non-secret key id.

export type SourceConnectionKind = 'DATABASE' | 'FILE'

export type SourceConnectionStatus = 'DRAFT' | 'READY' | 'DISABLED'

export type SourceCredentialState = 'NONE' | 'PENDING' | 'ACTIVE'

export interface SourceConnection {
  source_system_id: number
  source_kind: SourceConnectionKind
  connection_config: Record<string, unknown>
  status: SourceConnectionStatus
  row_version: number
  created_at: string
  updated_at: string
  credential_state: SourceCredentialState
  credential_key_id: string | null
  credential_activated_at: string | null
  last_test_at?: string | null
  last_test_ok?: boolean | null
  last_test_code?: string | null
  last_test_server_version?: string | null
}


export interface SourceCredentialResult {
  credential_state: SourceCredentialState
  credential_key_id: string | null
}


function sourceConnectionPath(
  sourceSystemId: number,
): string {
  return '/api/admin/integrations/source-systems/'
    + encodeURIComponent(String(sourceSystemId))
    + '/connection'
}


export async function getSourceConnection(
  sourceSystemId: number,
): Promise<SourceConnection | null> {
  const result =
    await apiRequest<{ connection: SourceConnection | null }>(
      sourceConnectionPath(sourceSystemId),
    )

  return result.connection
}


export function saveSourceConnection(
  sourceSystemId: number,
  payload: {
    source_kind: SourceConnectionKind
    connection_config: Record<string, unknown>
    expected_row_version: number | null
  },
): Promise<{ row_version: number }> {
  return apiRequest<{ row_version: number }>(
    sourceConnectionPath(sourceSystemId),
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}


export function setSourceConnectionStatus(
  sourceSystemId: number,
  status: 'DRAFT' | 'DISABLED',
  expectedRowVersion: number,
): Promise<{ row_version: number }> {
  return apiRequest<{ row_version: number }>(
    sourceConnectionPath(sourceSystemId) + '/status',
    {
      method: 'POST',
      body: JSON.stringify({
        status,
        expected_row_version: expectedRowVersion,
      }),
    },
  )
}


export function setSourceCredential(
  sourceSystemId: number,
  value: string,
): Promise<SourceCredentialResult> {
  return apiRequest<SourceCredentialResult>(
    sourceConnectionPath(sourceSystemId) + '/credential',
    {
      method: 'PUT',
      body: JSON.stringify({ value }),
    },
  )
}


export function revokeSourceCredential(
  sourceSystemId: number,
): Promise<SourceCredentialResult> {
  return apiRequest<SourceCredentialResult>(
    sourceConnectionPath(sourceSystemId) + '/credential',
    {
      method: 'DELETE',
    },
  )
}


export interface SourceConnectionTestResult {
  ok: boolean
  code: string
  message: string
  status: SourceConnectionStatus
  row_version: number
  server_version: string | null
}


export interface SourceObjectItem {
  schema_name: string
  name: string
  object_type: string
}


export interface SourceObjectsResult {
  objects: SourceObjectItem[]
  truncated: boolean
}


export interface SourceSnapshotResult {
  metadata_capture_id: number
  objects_captured: number
  columns_captured: number
  server_version: string
  objects_with_drift: number | null
}


export function testSourceConnection(
  sourceSystemId: number,
): Promise<SourceConnectionTestResult> {
  return apiRequest<SourceConnectionTestResult>(
    sourceConnectionPath(sourceSystemId) + '/test',
    {
      method: 'POST',
    },
  )
}


export function listSourceObjects(
  sourceSystemId: number,
): Promise<SourceObjectsResult> {
  return apiRequest<SourceObjectsResult>(
    sourceConnectionPath(sourceSystemId) + '/objects',
  )
}


export function captureSourceSnapshot(
  sourceSystemId: number,
): Promise<SourceSnapshotResult> {
  return apiRequest<SourceSnapshotResult>(
    sourceConnectionPath(sourceSystemId) + '/snapshot',
    {
      method: 'POST',
    },
  )
}


// ============================================================
// I05: field mapping configuration of a source object
// ============================================================


export type ConversionKind = 'NONE' | 'SAFE' | 'CONFIRMED'

export type WritePolicy = 'AUTHORITATIVE' | 'INSERT_ONLY' | 'NO_WRITE'

export type Compatibility = 'SAME' | 'SAFE' | 'CONDITIONAL' | 'INCOMPATIBLE'


export interface ObjectConfigField {
  target_column: string
  source_column: string | null
  source_type: string
  target_type: string
  conversion: ConversionKind
  write_policy: WritePolicy
  allow_explicit_null: boolean
}


export type LoadStrategy = 'FULL' | 'INCREMENTAL'


// I07: load strategy of a version. Saved definitions carry it nested in "strategy";
// a draft is sent with the flat keys.
export interface ObjectLoadStrategy {
  load_strategy: LoadStrategy
  change_column: string | null
  overlap_minutes: number
  delete_flag_column: string | null
}


export interface ObjectConfigDefinition extends Partial<ObjectLoadStrategy> {
  target_schema: 'stg' | 'ods' | null
  target_table: string | null
  fields: ObjectConfigField[]
  key: string[]
  strategy?: Partial<ObjectLoadStrategy> | null
}


export interface ObjectConfigIssue {
  severity: 'ERROR' | 'WARNING'
  code: string
  column?: string
  source_column?: string
  [detail: string]: unknown
}


export interface ObjectConfigDriftChange {
  change: 'ADDED' | 'REMOVED' | 'TYPE_CHANGED' | 'SNAPSHOT_MISSING'
  source_column?: string
  mapped?: boolean
  before?: string
  after?: string
}


export interface ObjectConfigProposal {
  proposal_id: number
  change_kind: 'CREATE_TABLE' | 'ADD_COLUMNS'
  target_schema: string
  target_table: string
  status: 'PROPOSED' | 'APPLIED' | 'REJECTED'
  created_at: string
  ddl: string
}


export interface ObjectConfigState {
  object: {
    source_object_id: number
    source_system_id: number
    source_schema: string
    source_object: string
    object_type: string
    is_active: boolean
    legacy: boolean
  }
  current: {
    version_id: number
    version_no: number
    status: 'READY' | 'DRIFT'
    status_details: { changes?: ObjectConfigDriftChange[] }
    published_at: string
    definition_hash: string
    definition: ObjectConfigDefinition
  } | null
  draft: {
    version_id: number
    version_no: number
    draft_revision: number
    definition_hash: string | null
    updated_at: string
    definition: ObjectConfigDefinition
    last_validation: {
      ok: boolean
      issues: ObjectConfigIssue[]
      draft_revision: number
      definition_hash: string
      created_at: string
    } | null
  } | null
  proposals: ObjectConfigProposal[]
}


export interface SnapshotColumn {
  name: string
  position: number
  formatted_type: string
  type: string
  nullable: boolean
  primary_key: boolean
}


export interface TargetColumn {
  name: string
  formatted_type: string
  type: string
  not_null: boolean
  has_default: boolean
}


export interface TargetTable {
  schema: 'stg' | 'ods'
  table: string
  columns: TargetColumn[]
}


export interface ObjectConfigResponse {
  config: ObjectConfigState
  snapshot: {
    metadata_capture_id: number
    captured_at: string | null
    columns: SnapshotColumn[]
  } | null
  targets: TargetTable[]
  type_choices: string[]
  compatibility: Record<string, Compatibility>
}


function objectConfigPath(
  sourceObjectId: number,
): string {
  return '/api/admin/integrations/source-objects/'
    + encodeURIComponent(String(sourceObjectId))
    + '/config'
}


export function getObjectConfig(
  sourceObjectId: number,
): Promise<ObjectConfigResponse> {
  return apiRequest<ObjectConfigResponse>(
    objectConfigPath(sourceObjectId),
  )
}


export function saveObjectConfigDraft(
  sourceObjectId: number,
  expectedRevision: number | null,
  config: ObjectConfigDefinition,
): Promise<{ draft_revision: number }> {
  return apiRequest<{ draft_revision: number }>(
    objectConfigPath(sourceObjectId) + '/draft',
    {
      method: 'PUT',
      body: JSON.stringify({
        expected_revision: expectedRevision,
        config,
      }),
    },
  )
}


export function validateObjectConfig(
  sourceObjectId: number,
  draftRevision: number,
): Promise<{ ok: boolean, issues: ObjectConfigIssue[], draft_revision: number }> {
  return apiRequest<{ ok: boolean, issues: ObjectConfigIssue[], draft_revision: number }>(
    objectConfigPath(sourceObjectId) + '/validate',
    {
      method: 'POST',
      body: JSON.stringify({ draft_revision: draftRevision }),
    },
  )
}


export function publishObjectConfig(
  sourceObjectId: number,
  draftRevision: number,
): Promise<{ version_no: number, status: string }> {
  return apiRequest<{ version_no: number, status: string }>(
    objectConfigPath(sourceObjectId) + '/publish',
    {
      method: 'POST',
      body: JSON.stringify({ draft_revision: draftRevision }),
    },
  )
}


export function checkObjectConfigDrift(
  sourceObjectId: number,
): Promise<{ status: string, changes: ObjectConfigDriftChange[] }> {
  return apiRequest<{ status: string, changes: ObjectConfigDriftChange[] }>(
    objectConfigPath(sourceObjectId) + '/drift-check',
    {
      method: 'POST',
    },
  )
}


export function createObjectConfigProposal(
  sourceObjectId: number,
  draftRevision: number,
): Promise<{ proposal_id: number, change_kind: string, ddl: string }> {
  return apiRequest<{ proposal_id: number, change_kind: string, ddl: string }>(
    objectConfigPath(sourceObjectId) + '/proposal',
    {
      method: 'POST',
      body: JSON.stringify({ draft_revision: draftRevision }),
    },
  )
}


export function rejectTargetProposal(
  proposalId: number,
): Promise<{ rejected: boolean }> {
  return apiRequest<{ rejected: boolean }>(
    '/api/admin/integrations/target-proposals/'
      + encodeURIComponent(String(proposalId))
      + '/reject',
    {
      method: 'POST',
    },
  )
}
