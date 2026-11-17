// I19: DM mart builder API (package 018, backend dm_builder_admin.py). Every change is checked again by the
// database (session, rights, validation); the client only sends the definition and shows the answer.

export type DmSourceKind = 'GRAIN' | 'SAME_KEY' | 'CHILD' | 'LOOKUP'
export type DmAggregate = 'NONE' | 'SUM' | 'COUNT' | 'COUNT_DISTINCT' | 'MIN' | 'MAX' | 'AVG'
export type DmOperator = '=' | '<>' | '>' | '>=' | '<' | '<=' | 'IN' | 'IS NULL' | 'IS NOT NULL' | 'LIKE'
export type DmFilterValue = string | number | boolean | (string | number | boolean)[] | null

export interface DmEntityKey {
  entity_key_id: number
  entity_id: number
  table_name: string
  column_name: string
  type_family: string
  is_active: boolean
}

export interface DmEntity {
  entity_id: number
  entity_code: string
  title: string
  is_active: boolean
  keys: DmEntityKey[]
}

export interface DmOdsTable {
  table_name: string
  estimated_rows: number
  columns: { name: string, type: string }[]
}

export interface DmCatalog {
  entities: DmEntity[]
  ods_tables: DmOdsTable[]
  field_types: string[]
  aggregates: string[]
  operators: string[]
}

export interface DmVersionSummary {
  version_id: number
  mart_id: number
  version_no: number
  status: 'DRAFT' | 'PUBLISHED' | 'SUPERSEDED'
  grain_entity_id: number | null
  ddl_status: 'NONE' | 'PROPOSED' | 'APPLIED' | 'REJECTED'
  updated_at: string
  published_at: string | null
}

export interface DmMart {
  mart_id: number
  mart_code: string
  title: string
  description: string | null
  required_permission_code: string
  ai_enabled: boolean
  auto_after_ods: boolean
  is_active: boolean
  versions: DmVersionSummary[]
}

export interface DmMartSave {
  mart_id: number | null
  mart_code: string | null
  title: string
  description: string | null
  required_permission_code: string
  ai_enabled: boolean
  auto_after_ods: boolean
  is_active: boolean
}

export interface DmSourceDef {
  alias: string
  table: string
  kind: DmSourceKind
  key_column?: string
  ref_alias?: string | null
  ref_column?: string | null
  lookup_column?: string | null
}

export interface DmFieldSourceDef {
  alias: string
  column: string
  aggregate: DmAggregate
}

export interface DmFieldDef {
  name: string
  title: string | null
  type: string
  is_key: boolean
  sources: DmFieldSourceDef[]
}

export interface DmFilterDef {
  alias: string
  column: string
  operator: DmOperator
  value: DmFilterValue
}

export interface DmDefinition {
  grain_entity_id: number | null
  sources: DmSourceDef[]
  fields: DmFieldDef[]
  filters: DmFilterDef[]
}

export interface DmVersionDetail {
  version: DmVersionSummary & { mart_code: string, ddl_text: string | null, ddl_sha256: string | null }
  definition: DmDefinition
  fill_sql: string | null
  fill_sql_error: string | null
  missing: string[]
}

export interface DmMartStatus {
  mart_id: number
  mart_code: string
  title: string
  description: string | null
  ai_enabled: boolean
  auto_after_ods: boolean
  is_active: boolean
  published_version_id: number | null
  published_version_no: number | null
  published_at: string | null
  draft_version_no: number | null
  draft_ddl_status: string | null
  table_exists: boolean
  last_run_id: number | null
  last_run_status: 'RUNNING' | 'SUCCESS' | 'FAILED' | null
  last_run_started_at: string | null
  last_run_finished_at: string | null
  last_run_rows: number | null
  last_run_error_code: string | null
  last_run_error_message: string | null
  last_run_diagnostic: { examples?: { key: string, rows: number }[], missing?: string[] } | null
  last_request_id: number | null
  last_request_trigger: 'MANUAL' | 'AFTER_ODS' | null
  last_request_status: string | null
  last_request_by: string | null
  last_request_at: string | null
  last_request_result: string | null
  has_active_request: boolean
}


function errorText(payload: { message?: string, detail?: unknown } | null, fallback: string): string {
  if (typeof payload?.message === 'string') {
    return payload.message
  }
  if (typeof payload?.detail === 'string') {
    return payload.detail
  }
  // FastAPI validation errors: a list of {loc, msg}.
  if (Array.isArray(payload?.detail) && payload.detail.length > 0) {
    const first = payload.detail[0] as { loc?: unknown[], msg?: string }
    return `${(first.loc ?? []).join('.')}: ${first.msg ?? fallback}`
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  if (init?.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    let payload: { message?: string, detail?: unknown } | null
    try {
      payload = await response.json() as { message?: string, detail?: unknown }
    } catch {
      payload = null
    }
    throw new Error(errorText(payload, `${response.status} ${response.statusText}`))
  }
  return response.json() as Promise<T>
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
}

const BASE = '/api/admin/integrations/dm'

export const getDmCatalog = () => request<DmCatalog>(`${BASE}/catalog`)
export const saveDmEntity = (body: { entity_id: number | null, entity_code: string | null, title: string, is_active: boolean }) =>
  post<{ entity_id: number }>(`${BASE}/entities`, body)
export const saveDmEntityKey = (entityId: number, body: { table_name: string, column_name: string, is_active: boolean }) =>
  post<{ entity_key_id: number }>(`${BASE}/entities/${entityId}/keys`, body)
export const getDmMarts = () => request<DmMart[]>(`${BASE}/marts`)
export const saveDmMart = (body: DmMartSave) => post<{ mart_id: number }>(`${BASE}/marts`, body)
export const createDmVersion = (martId: number) => post<{ version_id: number }>(`${BASE}/marts/${martId}/versions`)
export const getDmVersion = (versionId: number) => request<DmVersionDetail>(`${BASE}/versions/${versionId}`)
export const saveDmVersion = (versionId: number, definition: DmDefinition) =>
  post<{ version_id: number, problems: string[] }>(`${BASE}/versions/${versionId}`, { definition })
export const discardDmVersion = (versionId: number) => post<{ discarded: boolean }>(`${BASE}/versions/${versionId}/discard`)
export const proposeDmDdl = (versionId: number) =>
  post<{ ddl_text: string, ddl_sha256: string }>(`${BASE}/versions/${versionId}/ddl-propose`)
export const rejectDmDdl = (versionId: number) => post<{ rejected: boolean }>(`${BASE}/versions/${versionId}/ddl-reject`)
export const publishDmVersion = (versionId: number) => post<{ published: boolean }>(`${BASE}/versions/${versionId}/publish`)
export const createDmRunRequest = (martId: number) => post<{ request_id: number }>(`${BASE}/run-requests`, { mart_id: martId })
export const cancelDmRunRequest = (requestId: number) => post<{ cancelled: boolean }>(`${BASE}/run-requests/${requestId}/cancel`)
export const getDmMartStatus = () => request<DmMartStatus[]>('/api/integrations/loads/marts')


// Field type of the builder for a column type of the catalog (format_type spelling).
export function fieldTypeFor(columnType: string): string {
  const varchar = /^character varying\((\d+)\)$/.exec(columnType)
  if (varchar) {
    return `varchar(${varchar[1]})`
  }
  const numeric = /^numeric\((\d+),(\d+)\)$/.exec(columnType)
  if (numeric) {
    return `numeric(${numeric[1]},${numeric[2]})`
  }
  const map: Record<string, string> = {
    'timestamp without time zone': 'timestamp',
    'timestamp with time zone': 'timestamptz',
    'character varying': 'text',
    character: 'text',
  }
  if (map[columnType]) {
    return map[columnType]
  }
  return ['text', 'bigint', 'integer', 'smallint', 'numeric', 'date', 'boolean'].includes(columnType) ? columnType : 'text'
}
