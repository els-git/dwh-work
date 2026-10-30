import {
  createContext,
  useContext,
} from 'react'

import type { AuthState } from './auth'


export interface AuthContextValue {
  auth: AuthState

  hasPermission: (
    permissionCode: string,
  ) => boolean

  hasResource: (
    resourceType: string,
    resourceCode: string,
  ) => boolean

  logout: () => Promise<void>
}


export const AuthContext =
  createContext<AuthContextValue | null>(
    null,
  )


export function useAuth(): AuthContextValue {
  const value = useContext(
    AuthContext,
  )

  if (!value) {
    throw new Error(
      'useAuth must be used inside AuthGate',
    )
  }

  return value
}
