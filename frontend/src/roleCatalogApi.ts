export interface RoleCatalogItem {
  role_id: number
  role_code: string
  role_name: string
  description: string | null
  is_active: boolean
  is_system: boolean
  created_at: string
  updated_at: string
  assigned_user_count: number
  permission_count: number
  resource_count: number
  can_manage: boolean
}


interface ApiErrorResponse {
  message?: string
}


export interface RoleCreatePayload {
  role_code: string
  role_name: string
  description: string | null
}


export interface RoleUpdatePayload {
  role_name: string
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


export function getRoles():
Promise<RoleCatalogItem[]> {
  return apiRequest<RoleCatalogItem[]>(
    '/api/admin/roles',
  )
}


export function createRole(
  payload: RoleCreatePayload,
): Promise<RoleCatalogItem> {
  return apiRequest<RoleCatalogItem>(
    '/api/admin/roles',
    {
      method: 'POST',
      body: JSON.stringify(
        payload,
      ),
    },
  )
}


export function updateRole(
  roleId: number,
  payload: RoleUpdatePayload,
): Promise<RoleCatalogItem> {
  return apiRequest<RoleCatalogItem>(
    '/api/admin/roles/'
    + encodeURIComponent(
      String(roleId),
    ),
    {
      method: 'PATCH',
      body: JSON.stringify(
        payload,
      ),
    },
  )
}


export function setRoleActive(
  roleId: number,
  isActive: boolean,
): Promise<RoleCatalogItem> {
  const action =
    isActive
      ? 'enable'
      : 'disable'

  return apiRequest<RoleCatalogItem>(
    '/api/admin/roles/'
    + encodeURIComponent(
      String(roleId),
    )
    + '/'
    + action,
    {
      method: 'POST',
    },
  )
}
