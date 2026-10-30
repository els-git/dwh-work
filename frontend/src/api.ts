import type {
  DqIssue,
  DqResult,
  DqRun,
  EtlError,
  EtlJobRun,
  EtlRun,
  HealthResponse,
  IntegrationDqRule,
  IntegrationMapping,
  IntegrationObject,
  IntegrationOverview,
  SourceColumn,
  ValidationContract,
} from './types'

import type {
  RetailReportingOverview,
} from './types'


interface ApiErrorResponse {
  error?: string
  message?: string
  request_id?: string
  path?: string
}


async function apiGet<T>(
  path: string,
): Promise<T> {
  const response = await fetch(path, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
  })

  if (!response.ok) {
    let apiError: ApiErrorResponse | null

    try {
      apiError = await response.json() as ApiErrorResponse
    } catch {
      apiError = null
    }

    const message =
      apiError?.message
      ?? `${response.status} ${response.statusText}`

    throw new Error(message)
  }

  return response.json() as Promise<T>
}


export function getHealth(): Promise<HealthResponse> {
  return apiGet<HealthResponse>(
    '/api/health',
  )
}


export function getIntegrations(): Promise<IntegrationObject[]> {
  return apiGet<IntegrationObject[]>(
    '/api/integrations',
  )
}


export function getIntegrationOverview(
  sourceObjectId: number,
): Promise<IntegrationOverview> {
  return apiGet<IntegrationOverview>(
    `/api/integrations/${sourceObjectId}/overview`,
  )
}


export function getIntegrationStructure(
  sourceObjectId: number,
): Promise<SourceColumn[]> {
  return apiGet<SourceColumn[]>(
    `/api/integrations/${sourceObjectId}/structure`,
  )
}


export function getIntegrationMappings(
  sourceObjectId: number,
): Promise<IntegrationMapping[]> {
  return apiGet<IntegrationMapping[]>(
    `/api/integrations/${sourceObjectId}/mappings`,
  )
}


export function getIntegrationValidations(
  sourceObjectId: number,
): Promise<ValidationContract[]> {
  return apiGet<ValidationContract[]>(
    `/api/integrations/${sourceObjectId}/validations`,
  )
}


export function getIntegrationDqRules(
  sourceObjectId: number,
): Promise<IntegrationDqRule[]> {
  return apiGet<IntegrationDqRule[]>(
    `/api/integrations/${sourceObjectId}/dq-rules`,
  )
}


export function getIntegrationRuns(
  sourceObjectId: number,
): Promise<EtlJobRun[]> {
  return apiGet<EtlJobRun[]>(
    `/api/integrations/${sourceObjectId}/runs`,
  )
}


export function getIntegrationErrors(
  sourceObjectId: number,
): Promise<EtlError[]> {
  return apiGet<EtlError[]>(
    `/api/integrations/${sourceObjectId}/errors`,
  )
}


export function getIntegrationIssues(
  sourceObjectId: number,
): Promise<DqIssue[]> {
  return apiGet<DqIssue[]>(
    `/api/integrations/${sourceObjectId}/issues`,
  )
}

export function getEtlRuns(): Promise<EtlRun[]> {
  return apiGet<EtlRun[]>(
    '/api/etl/runs',
  )
}


export function getEtlRunJobs(
  batchRunId: number,
): Promise<EtlJobRun[]> {
  return apiGet<EtlJobRun[]>(
    `/api/etl/runs/${batchRunId}/jobs`,
  )
}

export function getDqRuns(): Promise<DqRun[]> {
  return apiGet<DqRun[]>(
    '/api/dq/runs',
  )
}


export function getDqRunResults(
  dqRunId: number,
): Promise<DqResult[]> {
  return apiGet<DqResult[]>(
    `/api/dq/runs/${dqRunId}/results`,
  )
}


export function getDqRunIssues(
  dqRunId: number,
): Promise<DqIssue[]> {
  return apiGet<DqIssue[]>(
    `/api/dq/issues?dq_run_id=${dqRunId}`,
  )
}

export function getRetailReportingOverview(): Promise<RetailReportingOverview> {
  return apiGet<RetailReportingOverview>(
    '/api/reporting/retail/overview',
  )
}

export type RetailReportCode =
  | 'status'
  | 'stage'
  | 'channel'
  | 'product'


export type RetailReportExportFormat =
  | 'xlsx'
  | 'pdf'
  | 'txt'


function exportFilename(
  disposition: string | null,
  report: RetailReportCode,
  fileFormat: RetailReportExportFormat,
): string {
  if (disposition) {
    const match =
      disposition.match(
        /filename="?([^";]+)"?/i,
      )

    if (match?.[1]) {
      return match[1]
    }
  }

  const timestamp =
    new Date()
      .toISOString()
      .replace(/[-:]/g, '')
      .replace(/\.\d{3}Z$/, '')
      .replace('T', '_')

  return (
    `retail_${report}_`
    + `${timestamp}.${fileFormat}`
  )
}


export async function downloadRetailReport(
  report: RetailReportCode,
  fileFormat: RetailReportExportFormat,
): Promise<void> {
  const response = await fetch(
    '/api/reporting/retail/export'
    + `?report=${encodeURIComponent(report)}`
    + `&format=${encodeURIComponent(fileFormat)}`,
    {
      method: 'GET',
      headers: {
        Accept: '*/*',
      },
    },
  )

  if (!response.ok) {
    let apiError: ApiErrorResponse | null

    try {
      apiError =
        await response.json() as ApiErrorResponse
    } catch {
      apiError = null
    }

    throw new Error(
      apiError?.message
      ?? `${response.status} ${response.statusText}`,
    )
  }

  const blob =
    await response.blob()

  const filename =
    exportFilename(
      response.headers.get(
        'Content-Disposition',
      ),
      report,
      fileFormat,
    )

  const objectUrl =
    URL.createObjectURL(blob)

  try {
    const link =
      document.createElement('a')

    link.href = objectUrl
    link.download = filename

    document.body.appendChild(link)

    link.click()

    link.remove()
  } finally {
    window.setTimeout(
      () => {
        URL.revokeObjectURL(
          objectUrl,
        )
      },
      1000,
    )
  }
}