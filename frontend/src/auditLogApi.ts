// I13-05: journals for SUPERADMIN / ADMIN (GET /api/admin/audit/*). The server reads them through the functions
// of package 029, hides secret values in the details and records every first page as a view; no export.

export interface AuditEvent {
  id: number
  occurred_at: string
  actor_user_id: number | null
  actor_username: string | null
  actor_account_level: string | null
  event_type: string
  entity_type: string
  entity_key: string | null
  /** I13-15 (package 031): name from the catalogs at view time; absent before 031 */
  entity_name?: string | null
  /** I13-15: the object has no catalog row any more, or the mart is deleted */
  entity_deleted?: boolean
  action: string
  outcome: string
  source: string
  request_id: string | null
  details: unknown
}

export interface SecretOperation {
  event_id: number
  occurred_at: string
  source_system_id: number
  source_code: string
  source_name: string
  operation_type: string
  phase: string
  code: string
  actor_kind: string | null
  actor_ref: string | null
  operation_id: string
}

export interface JournalPage<T> {
  rows: T[]
  has_more: boolean
  next_before_id: number | null
}

export interface AuditFilters {
  event_types: string[]
  outcomes: string[]
  entity_types: string[]
}

export interface AuditEventQuery {
  from?: string
  to?: string
  event_type?: string
  actor?: string
  entity?: string
  outcome?: string
  before_id?: number
}

export interface SecretOperationQuery {
  source_system_id?: number
  from?: string
  to?: string
  operation_type?: string
  before_id?: number
}

async function get<T>(path: string, params: Record<string, string | number | undefined>): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') {
      query.set(key, String(value))
    }
  }
  const text = query.toString()
  const response = await fetch(text ? `${path}?${text}` : path, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json() as { message?: string, detail?: string }
      message = payload.message ?? payload.detail ?? message
    } catch {
      // the status line is enough
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const getAuditEvents = (query: AuditEventQuery) =>
  get<JournalPage<AuditEvent>>('/api/admin/audit/events', { ...query, limit: 100 })

export const getAuditFilters = () => get<AuditFilters>('/api/admin/audit/filters', {})

export const getSecretOperations = (query: SecretOperationQuery) =>
  get<JournalPage<SecretOperation>>('/api/admin/audit/secret-operations', { ...query, limit: 100 })
