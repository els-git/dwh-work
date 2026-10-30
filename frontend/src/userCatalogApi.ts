export type UserAccountLevel =
  | 'SUPERADMIN'
  | 'ADMIN'
  | 'USER'


export type AssignableAccountLevel =
  | 'ADMIN'
  | 'USER'


export type AuthProvider =
  | 'LOCAL'
  | 'LDAP'
  | 'AD'
  | 'OIDC'


export interface UserCatalogItem {
  user_id: number
  username: string

  display_name: string | null
  email: string | null

  account_level: UserAccountLevel
  auth_provider: AuthProvider

  external_subject: string | null

  is_active: boolean
  is_system: boolean
  must_change_password: boolean

  created_at: string
  updated_at: string

  business_role_count: number
  can_manage: boolean
  can_reset_password: boolean
}


interface ApiErrorResponse {
  message?: string
  detail?: string
  details?: {
    code?: string
    message?: string
    errors?: string[]
  } | unknown[]
}


export interface UserCreatePayload {
  username: string
  display_name: string | null
  email: string | null

  account_level: AssignableAccountLevel
  auth_provider: AuthProvider

  password?: string | null
  external_subject?: string | null

  must_change_password: boolean
}


export interface UserUpdatePayload {
  display_name?: string | null
  email?: string | null
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
    let payload:
      ApiErrorResponse | null

    try {
      payload =
        (await response.json()) as ApiErrorResponse
    } catch {
      payload = null
    }

    if (
      payload?.details
      && !Array.isArray(payload.details)
      && Array.isArray(payload.details.errors)
      && payload.details.errors.length > 0
    ) {
      throw new Error(
        payload.details.errors.join('; '),
      )
    }

    throw new Error(
      payload?.message
      ?? payload?.detail
      ?? `${response.status} ${response.statusText}`,
    )
  }

  return response.json() as Promise<T>
}


export function getUsers():
Promise<UserCatalogItem[]> {
  return apiRequest<UserCatalogItem[]>(
    '/api/admin/users',
  )
}


export function createUser(
  payload: UserCreatePayload,
): Promise<UserCatalogItem> {
  return apiRequest<UserCatalogItem>(
    '/api/admin/users',
    {
      method: 'POST',
      body: JSON.stringify(
        payload,
      ),
    },
  )
}


export function updateUser(
  userId: number,
  payload: UserUpdatePayload,
): Promise<UserCatalogItem> {
  return apiRequest<UserCatalogItem>(
    '/api/admin/users/'
    + encodeURIComponent(
      String(userId),
    ),
    {
      method: 'PATCH',
      body: JSON.stringify(
        payload,
      ),
    },
  )
}


export function resetUserPassword(
  userId: number,
  password: string,
): Promise<UserCatalogItem> {
  return apiRequest<UserCatalogItem>(
    '/api/admin/users/'
    + encodeURIComponent(
      String(userId),
    )
    + '/reset-password',
    {
      method: 'POST',
      body: JSON.stringify({
        password,
      }),
    },
  )
}


export function setUserActive(
  userId: number,
  isActive: boolean,
): Promise<UserCatalogItem> {
  const action =
    isActive
      ? 'enable'
      : 'disable'

  return apiRequest<UserCatalogItem>(
    '/api/admin/users/'
    + encodeURIComponent(
      String(userId),
    )
    + '/'
    + action,
    {
      method: 'POST',
    },
  )
}


export function setUserAccountLevel(
  userId: number,
  accountLevel: AssignableAccountLevel,
): Promise<UserCatalogItem> {
  return apiRequest<UserCatalogItem>(
    '/api/admin/users/'
    + encodeURIComponent(
      String(userId),
    )
    + '/account-level',
    {
      method: 'PATCH',
      body: JSON.stringify({
        account_level:
          accountLevel,
      }),
    },
  )
}