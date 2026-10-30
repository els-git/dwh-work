export interface AccessRoleItem {
  role_id: number
  role_code: string
  role_name: string
  description: string | null
  is_active: boolean
  is_system: boolean
  permission_count: number
  resource_count: number
  can_manage: boolean
}


export interface AccessPermissionItem {
  permission_id: number
  permission_code: string
  permission_name: string
  permission_group: string
  is_active: boolean
  is_assignable: boolean
  is_assigned: boolean
  assigned_at: string | null
}


export interface AccessResourceItem {
  resource_id: number
  resource_type: string
  resource_code: string
  resource_name: string
  description: string | null
  is_active: boolean
  is_system: boolean
  is_assigned: boolean
  assigned_at: string | null
}


interface ApiErrorResponse {
  message?: string
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


export function getAccessRoles():
Promise<AccessRoleItem[]> {
  return apiRequest<AccessRoleItem[]>(
    '/api/admin/access/roles',
  )
}


export function getAccessRolePermissions(
  roleId: number,
): Promise<AccessPermissionItem[]> {
  return apiRequest<AccessPermissionItem[]>(
    '/api/admin/access/roles/'
    + roleId
    + '/permissions',
  )
}


export function getAccessRoleResources(
  roleId: number,
): Promise<AccessResourceItem[]> {
  return apiRequest<AccessResourceItem[]>(
    '/api/admin/access/roles/'
    + roleId
    + '/resources',
  )
}


export function setAccessRolePermission(
  roleId: number,
  permissionId: number,
  isAssigned: boolean,
): Promise<AccessPermissionItem> {
  const action =
    isAssigned
      ? 'grant'
      : 'revoke'

  return apiRequest<AccessPermissionItem>(
    '/api/admin/access/roles/'
    + roleId
    + '/permissions/'
    + permissionId
    + '/'
    + action,
    {
      method: 'POST',
    },
  )
}


export function setAccessRoleResource(
  roleId: number,
  resourceId: number,
  isAssigned: boolean,
): Promise<AccessResourceItem> {
  const action =
    isAssigned
      ? 'grant'
      : 'revoke'

  return apiRequest<AccessResourceItem>(
    '/api/admin/access/roles/'
    + roleId
    + '/resources/'
    + resourceId
    + '/'
    + action,
    {
      method: 'POST',
    },
  )
}


export interface AccessUserItem {
  user_id: number
  username: string
  display_name: string | null
  email: string | null
  account_level: string
  auth_provider: string
  is_active: boolean
  is_system: boolean
  business_role_count: number
  can_manage_roles: boolean
}


export interface AccessUserRoleItem {
  role_id: number
  role_code: string
  role_name: string
  description: string | null
  role_is_active: boolean
  role_is_system: boolean
  is_assigned: boolean
  assigned_at: string | null
}


export function getAccessUsers():
Promise<AccessUserItem[]> {
  return apiRequest<AccessUserItem[]>(
    '/api/admin/access/users',
  )
}


export function getAccessUserRoles(
  userId: number,
): Promise<AccessUserRoleItem[]> {
  return apiRequest<AccessUserRoleItem[]>(
    '/api/admin/access/users/'
    + userId
    + '/roles',
  )
}


export function setAccessUserRole(
  userId: number,
  roleId: number,
  isAssigned: boolean,
): Promise<AccessUserRoleItem> {
  const action =
    isAssigned
      ? 'grant'
      : 'revoke'

  return apiRequest<AccessUserRoleItem>(
    '/api/admin/access/users/'
    + userId
    + '/roles/'
    + roleId
    + '/'
    + action,
    {
      method: 'POST',
    },
  )
}