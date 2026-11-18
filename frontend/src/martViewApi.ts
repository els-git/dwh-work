// I22: viewing DM marts in "Reporting" (/api/reports/marts). The server decides which marts the role may open,
// builds the queries itself and logs every opening and export; the client sends only filters, sort and format.

export type MartCell = string | number | boolean | null
export type MartColumnKind = 'number' | 'date' | 'boolean' | 'text'
export type MartFilterOp = 'eq' | 'contains' | 'gte' | 'lte' | 'empty' | 'not_empty'
export type MartExportFormat = 'CSV' | 'XLSX' | 'PDF'

export interface MartItem {
  object_id: number
  title: string
  description: string | null
  object_name: string
  last_run_status: string | null
  last_built_at: string | null
  published_at: string | null
}

export interface MartFilter {
  column: string
  op: MartFilterOp
  value: string | null
}

export interface MartSort {
  column: string
  desc: boolean
}

export interface MartColumn {
  name: string
  type: string
  kind: MartColumnKind
}

export interface MartPage {
  view_id: number
  page: number
  page_size: number
  total: number
  columns: MartColumn[]
  rows: Record<string, MartCell>[]
  filters: MartFilter[]
  sort: MartSort | null
  export_limits: Record<MartExportFormat, number>
}

// A refusal the user can act on: code and its values (rows, limit, other formats ...).
export interface MartRefusal {
  code?: string
  message?: string
  format?: MartExportFormat
  rows?: number
  limit?: number
  mart?: string
  other_formats?: MartExportFormat[]
  column?: string
}

export class MartViewHttpError extends Error {
  status: number
  refusal: MartRefusal | null

  constructor(status: number, message: string, refusal: MartRefusal | null) {
    super(message)
    this.status = status
    this.refusal = refusal
  }
}


async function failure(response: Response): Promise<MartViewHttpError> {
  let payload: { message?: string, detail?: unknown, details?: unknown } | null
  try {
    payload = await response.json() as { message?: string, detail?: unknown, details?: unknown }
  } catch {
    payload = null
  }
  const details = payload?.details
  const refusal = details !== null && typeof details === 'object' && !Array.isArray(details)
    ? details as MartRefusal
    : null
  const detail = typeof payload?.detail === 'string' ? payload.detail : null
  return new MartViewHttpError(response.status,
                               payload?.message ?? detail ?? `${response.status} ${response.statusText}`, refusal)
}


async function call<T>(path: string, body?: unknown): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' })
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, body === undefined
    ? { headers }
    : { method: 'POST', headers, body: JSON.stringify(body) })
  if (!response.ok) {
    throw await failure(response)
  }
  return response.json() as Promise<T>
}


const BASE = '/api/reports/marts'

export const getMarts = () => call<MartItem[]>(BASE)

export const openMartView = (objectId: number, filters: MartFilter[], sort: MartSort | null) =>
  call<MartPage>(`${BASE}/${objectId}/view`, { filters, sort })

export const getMartPage = (viewId: number, page: number) =>
  call<MartPage>(`${BASE}/views/${viewId}/page`, { page })


// Export: the server streams the file; it is saved under the name the server gives.
export async function exportMart(objectId: number, filters: MartFilter[], sort: MartSort | null,
                                 format: MartExportFormat): Promise<void> {
  const response = await fetch(`${BASE}/${objectId}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filters, sort, format }),
  })
  if (!response.ok) {
    throw await failure(response)
  }
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename="([^"]+)"/.exec(disposition)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = match ? match[1] : `mart.${format.toLowerCase()}`
  link.click()
  URL.revokeObjectURL(url)
}
