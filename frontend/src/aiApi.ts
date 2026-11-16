// I17: AI assistant API (/api/ai). The server decides what the session user may see (I17-08);
// the client only shows what it gets.

export interface AiStatus {
  model_ready: boolean
  report_configured: boolean
  can_help: boolean
  can_report: boolean
  can_admin: boolean
  average_answer_seconds: number | null
  average_answer_count: number
}

export interface AiHelpAnswer {
  request_id: number
  status: 'OK' | 'DENIED' | 'NO_DATA'
  code?: string
  answer: string
  sections: { number: string, title: string }[]
}

export type AiCell = string | number | boolean | null

export interface AiReportAnswer {
  request_id: number
  status: 'OK' | 'DENIED' | 'NO_DATA' | 'REFUSED' | 'ERROR'
  code?: string
  message?: string
  sql?: string
  columns?: string[]
  rows?: Record<string, AiCell>[]
  truncated?: boolean
  repaired?: boolean
}

export interface AiReportObject {
  object_id: number
  schema_name: string
  object_name: string
  title: string
  description: string | null
  required_permission_code: string
  is_active: boolean
}

export interface AiValueColumn {
  value_column_id: number
  object_id: number
  column_name: string
  max_values: number
  is_active: boolean
}

export interface AiGlossaryTerm {
  term_id: number
  term: string
  meaning: string
  object_id: number | null
  is_active: boolean
}

export interface AiExample {
  example_id: number
  object_id: number
  question: string
  sql_text: string
  is_active: boolean
}

export interface AiConfig {
  report_objects: AiReportObject[]
  value_columns: AiValueColumn[]
  glossary: AiGlossaryTerm[]
  examples: AiExample[]
}

export interface AiRequestLogRow {
  request_id: number
  username: string
  request_kind: 'HELP' | 'REPORT'
  question: string
  image_attached: boolean
  status: string
  result_code: string | null
  sql_text: string | null
  row_count: number | null
  message: string | null
  started_at: string
  finished_at: string | null
}


// An HTTP error with its status: the chat shows its own text for "busy", "unavailable" and "stopped".
export class AiHttpError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
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
    const detail = typeof payload?.detail === 'string' ? payload.detail : null
    throw new AiHttpError(response.status, payload?.message ?? detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}


function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body), signal })
}


export const getAiStatus = () => request<AiStatus>('/api/ai/status')

export const askAiHelp = (question: string, image: string | null, clientId: string, signal: AbortSignal) =>
  post<AiHelpAnswer>('/api/ai/help', { question, image, client_id: clientId }, signal)

export const askAiReport = (question: string, clientId: string, signal: AbortSignal) =>
  post<AiReportAnswer>('/api/ai/report', { question, client_id: clientId }, signal)

// The Stop button: the server stops the question at once and frees the model for the next one.
export const cancelAiQuestion = (clientId: string) =>
  post<{ stopped: boolean }>('/api/ai/cancel', { client_id: clientId })

export const getAiConfig = () => request<AiConfig>('/api/ai/admin/config')

export const getAiRequests = (limit = 100) => request<AiRequestLogRow[]>(`/api/ai/admin/requests?limit=${limit}`)

export const saveAiReportObject = (payload: {
  object_name: string, title: string, description: string | null, required_permission_code: string, is_active: boolean,
}) => post<{ object_id: number }>('/api/ai/admin/report-objects', payload)

export const saveAiValueColumn = (payload: {
  object_id: number, column_name: string, max_values: number, is_active: boolean,
}) => post<{ value_column_id: number }>('/api/ai/admin/value-columns', payload)

export const saveAiGlossary = (payload: {
  term_id: number | null, term: string, meaning: string, object_id: number | null, is_active: boolean,
}) => post<{ term_id: number }>('/api/ai/admin/glossary', payload)

export const saveAiExample = (payload: {
  example_id: number | null, object_id: number, question: string, sql_text: string, is_active: boolean,
}) => post<{ example_id: number }>('/api/ai/admin/examples', payload)
