import type {
  EtlJobRun,
  EtlRun,
  HealthResponse,
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
