export interface AuthUser {
  user_id: number
  username: string
  display_name: string | null
  email: string | null

  account_level:
    | 'SUPERADMIN'
    | 'ADMIN'
    | 'USER'

  auth_provider: string

  is_system: boolean
  must_change_password: boolean

  session_id: number
  session_created_at: string
  session_expires_at: string
}


export interface AuthRole {
  role_code: string
  role_name: string
}


export interface AuthPermission {
  permission_code: string
  permission_name: string
  permission_group: string
}


export interface AuthResource {
  resource_type: string
  resource_code: string
  resource_name: string
}


export interface AuthState {
  authenticated: boolean

  user: AuthUser | null

  roles: AuthRole[]
  permissions: AuthPermission[]
  resources: AuthResource[]
}


export interface ChangePasswordResponse {
  changed: boolean
  auth: AuthState
}


interface AuthApiError {
  error?: string
  message?: string
  detail?: string
  details?: {
    code?: string
    message?: string
    errors?: string[]
  } | unknown[]
}


async function readError(
  response: Response,
): Promise<string> {
  try {
    const body =
      await response.json() as AuthApiError

    if (
      body.details
      && !Array.isArray(body.details)
    ) {
      if (
        Array.isArray(body.details.errors)
        && body.details.errors.length > 0
      ) {
        return body.details.errors.join('; ')
      }

      if (body.details.message) {
        return body.details.message
      }
    }

    return (
      body.message
      ?? body.detail
      ?? `${response.status} ${response.statusText}`
    )
  } catch {
    return `${response.status} ${response.statusText}`
  }
}


export async function getAuthState(): Promise<AuthState> {
  const response = await fetch(
    '/api/auth/me',
    {
      method: 'GET',

      credentials: 'same-origin',

      headers: {
        Accept: 'application/json',
      },

      cache: 'no-store',
    },
  )

  if (!response.ok) {
    throw new Error(
      await readError(response),
    )
  }

  return response.json() as Promise<AuthState>
}


export async function login(
  username: string,
  password: string,
): Promise<AuthState> {
  const response = await fetch(
    '/api/auth/login',
    {
      method: 'POST',

      credentials: 'same-origin',

      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },

      body: JSON.stringify({
        username,
        password,
      }),
    },
  )

  if (!response.ok) {
    throw new Error(
      await readError(response),
    )
  }

  return response.json() as Promise<AuthState>
}


export async function changePassword(
  newPassword: string,
  currentPassword?: string,
): Promise<ChangePasswordResponse> {
  const payload: {
    new_password: string
    current_password?: string
  } = {
    new_password: newPassword,
  }

  if (currentPassword) {
    payload.current_password =
      currentPassword
  }

  const response = await fetch(
    '/api/auth/change-password',
    {
      method: 'POST',

      credentials: 'same-origin',

      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },

      body: JSON.stringify(payload),
    },
  )

  if (!response.ok) {
    throw new Error(
      await readError(response),
    )
  }

  return response.json() as Promise<ChangePasswordResponse>
}


export async function logout(): Promise<void> {
  const response = await fetch(
    '/api/auth/logout',
    {
      method: 'POST',

      credentials: 'same-origin',

      headers: {
        Accept: 'application/json',
      },
    },
  )

  if (!response.ok) {
    throw new Error(
      await readError(response),
    )
  }
}