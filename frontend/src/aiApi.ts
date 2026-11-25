import type { AiScreenLabel } from './screenLabels'

// I17: AI assistant API (/api/ai). The server decides what the session user may see (I17-08);
// the client only shows what it gets.

export interface AiStatus {
  model_ready: boolean
  report_configured: boolean
  can_help: boolean
  can_report: boolean
  can_admin: boolean
  average_answer_seconds: number | null
  can_save_reports?: boolean
  // I17-10: diagnosis of load errors (ETL_VIEW).
  can_diagnose?: boolean
  average_answer_count: number
}

export interface AiHelpAnswer {
  request_id: number
  status: 'OK' | 'DENIED' | 'NO_DATA'
  code?: string
  answer: string
  sections: { number: string, title: string }[]
  // Part of the subject is described in guide sections closed to the role (shown in the interface language).
  closed_nearby?: boolean
  // I17-12: SCRIPT - the lines of the guide without the model; AI - the model's answer.
  mode?: 'SCRIPT' | 'AI'
  // I17-12: the question is not in Russian, the guide (and the script answer) is.
  foreign?: boolean
  ai_may_help?: boolean
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

// I17-10: the last messages of the dialogue go with the question (the server keeps none of them).
export interface AiHistoryItem {
  role: 'user' | 'assistant'
  text: string
}

// I17-12: screen - the selected menu item and tabs when a screenshot was taken (interface language and Russian).
export const askAiHelp = (question: string, image: string | null, clientId: string, signal: AbortSignal,
  history: AiHistoryItem[] = [], mode: 'script' | 'ai' = 'script', screen: AiScreenLabel[] = []) =>
  post<AiHelpAnswer>('/api/ai/help', { question, image, client_id: clientId, history, mode, screen }, signal)

export const askAiReport = (question: string, clientId: string, signal: AbortSignal, history: AiHistoryItem[] = []) =>
  post<AiReportAnswer>('/api/ai/report', { question, client_id: clientId, history }, signal)

// I17-10: diagnosis of a load error, the last error of an object or the last failed run of a mart.
export type AiDiagnoseKind = 'error' | 'object' | 'mart'

export interface AiDiagnosisCard {
  code: string | null
  // I17-12: the code is described in the knowledge base - the card is the answer, the model only on request.
  known?: boolean
  sqlstate: string | null
  title: string
  action: string
  who: 'USER' | 'SOURCE_OWNER' | 'ADMIN'
  where: string[]
  error_id: number | null
  object_id: number | null
  mart_code: string | null
}

export interface AiDiagnosis {
  status: 'CARD' | 'OK'
  card: AiDiagnosisCard
  answer?: string
  request_id?: number
  unavailable?: string[]
}

export const diagnoseAi = (kind: AiDiagnoseKind, id: number, language: string, clientId: string, signal: AbortSignal,
  cardOnly: boolean) =>
  post<AiDiagnosis>('/api/ai/diagnose', { kind, id, language, client_id: clientId, card_only: cardOnly }, signal)

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


// I17-09: saved reports in "Reporting" (folders personal or shared; the database checks every right).
export interface ReportFolder {
  folder_id: number
  folder_name: string
  visibility: 'PERSONAL' | 'SHARED'
  owner_username: string
  is_own: boolean
  can_edit: boolean
  report_count: number
}

export interface SavedReport {
  report_id: number
  folder_id: number
  title: string
  question: string
  owner_username: string
  is_own: boolean
  can_edit: boolean
  is_allowed: boolean
  sql_text: string | null
  created_at: string
  updated_at: string
}

const SAVED = '/api/reports/saved'

export const getReportFolders = () => request<ReportFolder[]>(`${SAVED}/folders`)

export const createReportFolder = (folderName: string, visibility: ReportFolder['visibility']) =>
  post<{ folder_id: number }>(`${SAVED}/folders`, { folder_name: folderName, visibility })

export const deleteReportFolder = (folderId: number) => post<{ deleted: boolean }>(`${SAVED}/folders/${folderId}/delete`, {})

export const getSavedReports = () => request<SavedReport[]>(`${SAVED}/reports`)

export const saveReport = (requestId: number, folderId: number, title: string) =>
  post<{ report_id: number }>(`${SAVED}/reports`, { request_id: requestId, folder_id: folderId, title })

export const deleteSavedReport = (reportId: number) => post<{ deleted: boolean }>(`${SAVED}/reports/${reportId}/delete`, {})

export const runSavedReport = (reportId: number) => post<AiReportAnswer>(`${SAVED}/reports/${reportId}/run`, {})
