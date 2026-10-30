export interface ModuleCatalogItem {
  resource_id: number
  module_code: string
  module_name: string
  description: string | null
  is_active: boolean
  is_system: boolean
  created_at: string
  updated_at: string
}


interface ApiErrorResponse {
  message?: string
}


export interface ModuleCreatePayload {
  module_code: string
  module_name: string
  description: string | null
}


export interface ModuleUpdatePayload {
  module_name: string
  description: string | null
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
      ?? `${response.status} ${response.statusText}`,
    )
  }

  return response.json() as Promise<T>
}


export function getModules():
Promise<ModuleCatalogItem[]> {
  return apiRequest<ModuleCatalogItem[]>(
    '/api/admin/modules',
  )
}


export function createModule(
  payload: ModuleCreatePayload,
): Promise<ModuleCatalogItem> {
  return apiRequest<ModuleCatalogItem>(
    '/api/admin/modules',
    {
      method: 'POST',
      body: JSON.stringify(
        payload,
      ),
    },
  )
}


export function updateModule(
  moduleCode: string,
  payload: ModuleUpdatePayload,
): Promise<ModuleCatalogItem> {
  return apiRequest<ModuleCatalogItem>(
    '/api/admin/modules/'
    + encodeURIComponent(
      moduleCode,
    ),
    {
      method: 'PATCH',
      body: JSON.stringify(
        payload,
      ),
    },
  )
}


export function setModuleActive(
  moduleCode: string,
  isActive: boolean,
): Promise<ModuleCatalogItem> {
  const action =
    isActive
      ? 'enable'
      : 'disable'

  return apiRequest<ModuleCatalogItem>(
    '/api/admin/modules/'
    + encodeURIComponent(
      moduleCode,
    )
    + '/'
    + action,
    {
      method: 'POST',
    },
  )
}