import { Trans } from 'react-i18next'
import {
  AuthContext,
  type AuthContextValue,
} from './authContext'

import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from 'react'

import {
  changePassword,
  getAuthState,
  login,
  logout,
} from './auth'

import type {
  AuthState,
} from './auth'


interface AuthGateProps {
  children: ReactNode
}


function validatePasswordPolicy(
  password: string,
  username: string,
): string[] {
  const errors: string[] = []

  if (password.length < 12) {
    errors.push(
      'Password must be at least 12 characters long',
    )
  }

  if (password.length > 128) {
    errors.push(
      'Password must be no more than 128 characters long',
    )
  }

  let categories = 0

  if (/[A-Z]/.test(password)) {
    categories += 1
  }

  if (/[a-z]/.test(password)) {
    categories += 1
  }

  if (/[0-9]/.test(password)) {
    categories += 1
  }

  if (/[^A-Za-z0-9\s]/.test(password)) {
    categories += 1
  }

  if (categories < 3) {
    errors.push(
      'Password must include at least 3 of 4 categories: uppercase letters, lowercase letters, digits, special characters',
    )
  }

  const normalizedUsername =
    username.trim().toLowerCase()

  if (
    normalizedUsername !== ''
    && password
      .toLowerCase()
      .includes(normalizedUsername)
  ) {
    errors.push(
      'Password must not contain the username',
    )
  }

  return errors
}


function emptyAuthState(): AuthState {
  return {
    authenticated: false,
    user: null,
    roles: [],
    permissions: [],
    resources: [],
  }
}


export default function AuthGate({
  children,
}: AuthGateProps) {
  const [
    auth,
    setAuth,
  ] = useState<AuthState>(
    emptyAuthState(),
  )

  const [
    checkingSession,
    setCheckingSession,
  ] = useState(true)

  const [
    username,
    setUsername,
  ] = useState('')

  const [
    password,
    setPassword,
  ] = useState('')

  const [
    loginBusy,
    setLoginBusy,
  ] = useState(false)

  const [
    loginError,
    setLoginError,
  ] = useState<string | null>(null)


  const [
    newPassword,
    setNewPassword,
  ] = useState('')

  const [
    confirmPassword,
    setConfirmPassword,
  ] = useState('')

  const [
    passwordChangeBusy,
    setPasswordChangeBusy,
  ] = useState(false)

  const [
    passwordChangeError,
    setPasswordChangeError,
  ] = useState<string | null>(null)


  useEffect(() => {
    let cancelled = false

    async function restoreSession() {
      try {
        const state =
          await getAuthState()

        if (!cancelled) {
          setAuth(state)
        }
      } catch (error) {
        if (!cancelled) {
          setAuth(
            emptyAuthState(),
          )

          setLoginError(
            error instanceof Error
              ? error.message
              : 'Authentication service unavailable',
          )
        }
      } finally {
        if (!cancelled) {
          setCheckingSession(false)
        }
      }
    }

    void restoreSession()

    return () => {
      cancelled = true
    }
  }, [])


  async function submitLogin(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    if (loginBusy) {
      return
    }

    setLoginBusy(true)
    setLoginError(null)

    try {
      const state = await login(
        username.trim(),
        password,
      )

      setPassword('')
      setAuth(state)
    } catch (error) {
      setPassword('')

      setLoginError(
        error instanceof Error
          ? error.message
          : 'Login failed',
      )
    } finally {
      setLoginBusy(false)
    }
  }


  async function submitPasswordChange(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    if (passwordChangeBusy) {
      return
    }

    if (newPassword !== confirmPassword) {
      setPasswordChangeError(
        'New passwords do not match',
      )
      return
    }

    const passwordPolicyErrors =
      validatePasswordPolicy(
        newPassword,
        auth.user?.username ?? '',
      )

    if (passwordPolicyErrors.length > 0) {
      setPasswordChangeError(
        passwordPolicyErrors.join('; '),
      )
      return
    }

    setPasswordChangeBusy(true)
    setPasswordChangeError(null)

    try {
      const result = await changePassword(
        newPassword,
      )

      setNewPassword('')
      setConfirmPassword('')
      setAuth(result.auth)
    } catch (error) {
      setPasswordChangeError(
        error instanceof Error
          ? error.message
          : 'Password change failed',
      )
    } finally {
      setPasswordChangeBusy(false)
    }
  }


  async function performLogout() {
    try {
      await logout()
    } finally {
      setAuth(
        emptyAuthState(),
      )

      setPassword('')
      setLoginError(null)
    }
  }


  const contextValue =
    useMemo<AuthContextValue>(
      () => ({
        auth,

        hasPermission(
          permissionCode: string,
        ) {
          return auth.permissions.some(
            item =>
              item.permission_code
              === permissionCode,
          )
        },

        hasResource(
          resourceType: string,
          resourceCode: string,
        ) {
          return auth.resources.some(
            item =>
              item.resource_type
                === resourceType
              && item.resource_code
                === resourceCode,
          )
        },

        logout: performLogout,
      }),
      [auth],
    )


  if (checkingSession) {
    return (
      <div className="auth-startup-screen">
        <div className="auth-startup-mark">
          DWH
        </div>

        <p>
          <Trans i18nKey="batch.tr0100" />
        </p>
      </div>
    )
  }


  if (
    !auth.authenticated
    || !auth.user
  ) {
    return (
      <div className="auth-login-screen">
        <div className="auth-login-card">
          <div className="auth-login-brand">
            <div className="auth-login-mark">
              DWH
            </div>

            <div>
              <strong>
                <Trans i18nKey="batch.tr0123" />
              </strong>

              <span>
                <Trans i18nKey="batch.tr0122" />
              </span>
            </div>
          </div>

          <div className="auth-login-copy">
            <h1>
              <Trans i18nKey="batch.tr0110" />
            </h1>

            <p>
              <Trans i18nKey="batch.tr0103" />
            </p>
          </div>

          <form
            className="auth-login-form"
            onSubmit={submitLogin}
          >
            <label>
              <span>
                <Trans i18nKey="batch.tr0121" />
              </span>

              <input
                type="text"
                value={username}
                autoComplete="username"
                autoFocus
                disabled={loginBusy}
                onChange={
                  event =>
                    setUsername(
                      event.target.value,
                    )
                }
              />
            </label>

            <label>
              <span>
                <Trans i18nKey="batch.tr0108" />
              </span>

              <input
                type="password"
                value={password}
                autoComplete="current-password"
                disabled={loginBusy}
                onChange={
                  event =>
                    setPassword(
                      event.target.value,
                    )
                }
              />
            </label>

            {
              loginError
                ? (
                  <div
                    className="auth-login-error"
                    role="alert"
                  >
                    {loginError}
                  </div>
                )
                : null
            }

            <button
              type="submit"
              className="auth-login-submit"
              disabled={
                loginBusy
                || username.trim() === ''
                || password === ''
              }
            >
              {
                loginBusy
                  ?<Trans i18nKey="batch.tr0112" />
                  :<Trans i18nKey="batch.tr0110" />
              }
            </button>
          </form>

          <div className="auth-login-footer">
            <Trans i18nKey="batch.tr0104" />
          </div>
        </div>
      </div>
    )
  }


  if (auth.user.must_change_password) {
    return (
      <div className="auth-login-screen">
        <div className="auth-login-card">
          <div className="auth-login-brand">
            <div className="auth-login-mark">
              DWH
            </div>

            <div>
              <strong>
                <Trans i18nKey="batch.tr0123" />
              </strong>

              <span>
                <Trans i18nKey="batch.tr0122" />
              </span>
            </div>
          </div>

          <div className="auth-login-copy">
            <h1>
              <Trans i18nKey="batch.tr0098" />
            </h1>

            <p>
              <Trans i18nKey="batch.tr0113" />
            </p>
          </div>

          <div className="auth-login-copy">
            <p>
              <strong><Trans i18nKey="batch.tr0120" /></strong>
              {' '}
              <Trans i18nKey="batch.tr0096" />
            </p>
          </div>

          <form
            className="auth-login-form"
            onSubmit={submitPasswordChange}
          >
            <label>
              <span>
                <Trans i18nKey="batch.tr0106" />
              </span>

              <input
                type="password"
                value={newPassword}
                autoComplete="new-password"
                disabled={passwordChangeBusy}
                onChange={
                  event =>
                    setNewPassword(
                      event.target.value,
                    )
                }
              />
            </label>

            <label>
              <span>
                <Trans i18nKey="batch.tr0101" />
              </span>

              <input
                type="password"
                value={confirmPassword}
                autoComplete="new-password"
                disabled={passwordChangeBusy}
                onChange={
                  event =>
                    setConfirmPassword(
                      event.target.value,
                    )
                }
              />
            </label>

            {
              passwordChangeError
                ? (
                  <div
                    className="auth-login-error"
                    role="alert"
                  >
                    {passwordChangeError}
                  </div>
                )
                : null
            }

            <button
              type="submit"
              className="auth-login-submit"
              disabled={
                passwordChangeBusy
                || newPassword === ''
                || confirmPassword === ''
              }
            >
              {
                passwordChangeBusy
                  ?<Trans i18nKey="batch.tr0099" />
                  :<Trans i18nKey="batch.tr0098" />
              }
            </button>
          </form>

          <button
            type="button"
            className="auth-login-secondary"
            disabled={passwordChangeBusy}
            onClick={
              () => {
                void performLogout()
              }
            }
          >
            <Trans i18nKey="batch.tr0114" />
          </button>

          <div className="auth-login-footer">
            <Trans i18nKey="batch.tr0111" /> {auth.user.username}
          </div>
        </div>
      </div>
    )
  }


  return (
    <AuthContext.Provider
      value={contextValue}
    >
      {children}
    </AuthContext.Provider>
  )
}