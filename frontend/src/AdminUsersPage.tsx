import { Trans } from 'react-i18next'
import {
  useEffect,
  useState,
} from 'react'

import type {
  FormEvent,
} from 'react'

import { useAuth } from './authContext'

import {
  createUser,
  getUsers,
  resetUserPassword,
  setUserAccountLevel,
  setUserActive,
  updateUser,
} from './userCatalogApi'

import type {
  AssignableAccountLevel,
  AuthProvider,
  UserCatalogItem,
} from './userCatalogApi'

import { confirmAction } from './confirmAction'
import { formatDateTime } from './dateTime'

import './AdminUsersPage.css'


type EditorMode =
  | 'create'
  | 'edit'
  | null


function errorMessage(
  error: unknown,
): string {
  if (error instanceof Error) {
    return error.message
  }

  return 'Unknown error'
}


function formatUserDate(
  value: string,
): string {
  const date =
    new Date(
      value,
    )

  if (
    Number.isNaN(
      date.getTime(),
    )
  ) {
    return value
  }

  return formatDateTime(value)
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


function AdminUsersPage() {
  const {
    auth,
  } = useAuth()

  const isSuperadmin =
    auth.user?.account_level
    === 'SUPERADMIN'

  const [
    users,
    setUsers,
  ] = useState<UserCatalogItem[]>([])

  const [
    loading,
    setLoading,
  ] = useState(true)

  const [
    error,
    setError,
  ] = useState<string | null>(null)

  const [
    editorMode,
    setEditorMode,
  ] = useState<EditorMode>(null)

  const [
    editingUser,
    setEditingUser,
  ] = useState<UserCatalogItem | null>(null)

  const [
    username,
    setUsername,
  ] = useState('')

  const [
    displayName,
    setDisplayName,
  ] = useState('')

  const [
    email,
    setEmail,
  ] = useState('')

  const [
    accountLevel,
    setAccountLevel,
  ] = useState<AssignableAccountLevel>('USER')

  const [
    authProvider,
    setAuthProvider,
  ] = useState<AuthProvider>('LOCAL')

  const [
    password,
    setPassword,
  ] = useState('')

  const [
    externalSubject,
    setExternalSubject,
  ] = useState('')

  const [
    mustChangePassword,
    setMustChangePassword,
  ] = useState(true)

  const [
    saving,
    setSaving,
  ] = useState(false)

  const [
    changingUserId,
    setChangingUserId,
  ] = useState<number | null>(null)

  const [
    passwordResetUser,
    setPasswordResetUser,
  ] = useState<UserCatalogItem | null>(null)

  const [
    temporaryPassword,
    setTemporaryPassword,
  ] = useState('')

  const [
    confirmTemporaryPassword,
    setConfirmTemporaryPassword,
  ] = useState('')

  const [
    resettingPassword,
    setResettingPassword,
  ] = useState(false)


  async function loadUsers() {
    try {
      setLoading(true)
      setError(null)

      const result =
        await getUsers()

      setUsers(
        result,
      )
    } catch (loadError) {
      setError(
        errorMessage(
          loadError,
        ),
      )
    } finally {
      setLoading(false)
    }
  }


  useEffect(() => {
    let cancelled = false

    void getUsers()
      .then(result => {
        if (!cancelled) {
          setUsers(result)
        }
      })
      .catch(loadError => {
        if (!cancelled) {
          setError(errorMessage(loadError))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])


  function resetEditor() {
    setEditingUser(null)

    setUsername('')
    setDisplayName('')
    setEmail('')

    setAccountLevel('USER')
    setAuthProvider('LOCAL')

    setPassword('')
    setExternalSubject('')

    setMustChangePassword(true)
  }


  function openCreate() {
    resetEditor()

    setEditorMode(
      'create',
    )

    setError(null)
  }


  function openEdit(
    user: UserCatalogItem,
  ) {
    if (!user.can_manage) {
      return
    }

    setEditingUser(
      user,
    )

    setUsername(
      user.username,
    )

    setDisplayName(
      user.display_name
      ?? '',
    )

    setEmail(
      user.email
      ?? '',
    )

    setAccountLevel(
      user.account_level === 'ADMIN'
        ? 'ADMIN'
        : 'USER',
    )

    setAuthProvider(
      user.auth_provider,
    )

    setPassword('')

    setExternalSubject(
      user.external_subject
      ?? '',
    )

    setMustChangePassword(
      user.must_change_password,
    )

    setEditorMode(
      'edit',
    )

    setError(null)
  }


  function closeEditor() {
    if (saving) {
      return
    }

    setEditorMode(null)
    resetEditor()
  }


  async function submitUser(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    try {
      setSaving(true)
      setError(null)

      if (editorMode === 'create') {
        if (
          authProvider === 'LOCAL'
          && password.length === 0
        ) {
          throw new Error(
            'Password is required for LOCAL users.',
          )
        }

        if (authProvider === 'LOCAL') {
          const passwordPolicyErrors =
            validatePasswordPolicy(
              password,
              username,
            )

          if (passwordPolicyErrors.length > 0) {
            throw new Error(
              passwordPolicyErrors.join('; '),
            )
          }
        }

        await createUser({
          username:
            username.trim(),

          display_name:
            displayName.trim()
            || null,

          email:
            email.trim()
            || null,

          account_level:
            isSuperadmin
              ? accountLevel
              : 'USER',

          auth_provider:
            authProvider,

          password:
            authProvider === 'LOCAL'
              ? password
              : null,

          external_subject:
            authProvider === 'LOCAL'
              ? null
              : (
                  externalSubject.trim()
                  || null
                ),

          must_change_password:
            authProvider === 'LOCAL'
              ? mustChangePassword
              : false,
        })
      } else if (
        editorMode === 'edit'
        && editingUser !== null
      ) {
        await updateUser(
          editingUser.user_id,
          {
            display_name:
              displayName.trim()
              || null,

            email:
              email.trim()
              || null,
          },
        )
      } else {
        return
      }

      setEditorMode(null)
      resetEditor()

      await loadUsers()
    } catch (saveError) {
      setError(
        errorMessage(
          saveError,
        ),
      )
    } finally {
      setSaving(false)
    }
  }


  function openPasswordReset(
    user: UserCatalogItem,
  ) {
    if (!user.can_reset_password) {
      return
    }

    setEditorMode(null)
    setEditingUser(null)

    setPasswordResetUser(user)
    setTemporaryPassword('')
    setConfirmTemporaryPassword('')
    setError(null)
  }


  function closePasswordReset() {
    if (resettingPassword) {
      return
    }

    setPasswordResetUser(null)
    setTemporaryPassword('')
    setConfirmTemporaryPassword('')
  }


  async function submitPasswordReset(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    if (
      !passwordResetUser
      || resettingPassword
    ) {
      return
    }

    if (
      temporaryPassword
      !== confirmTemporaryPassword
    ) {
      setError(
        'Temporary passwords do not match.',
      )
      return
    }

    const passwordPolicyErrors =
      validatePasswordPolicy(
        temporaryPassword,
        passwordResetUser.username,
      )

    if (passwordPolicyErrors.length > 0) {
      setError(
        passwordPolicyErrors.join('; '),
      )
      return
    }

    try {
      setResettingPassword(true)
      setError(null)

      await resetUserPassword(
        passwordResetUser.user_id,
        temporaryPassword,
      )

      setPasswordResetUser(null)
      setTemporaryPassword('')
      setConfirmTemporaryPassword('')

      await loadUsers()
    } catch (resetError) {
      setError(
        errorMessage(
          resetError,
        ),
      )
    } finally {
      setResettingPassword(false)
    }
  }


  async function changeActiveState(
    user: UserCatalogItem,
  ) {
    if (!user.can_manage) {
      return
    }

    const nextState =
      !user.is_active

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Enable user?'
            : 'Disable user?',
        message:
          nextState
            ? `User ${user.username} will be enabled.`
            : `User ${user.username} will be disabled.`,
        detail:
          nextState
            ? 'The user will be able to sign in again. Existing account history is preserved.'
            : 'Active sessions will be revoked. The account and audit history remain stored; no data is physically deleted.',
        confirmLabel:
          nextState
            ? 'Enable'
            : 'Disable',
      })

    if (!confirmed) {
      return
    }

    try {
      setChangingUserId(
        user.user_id,
      )

      setError(null)

      await setUserActive(
        user.user_id,
        nextState,
      )

      await loadUsers()
    } catch (stateError) {
      setError(
        errorMessage(
          stateError,
        ),
      )
    } finally {
      setChangingUserId(null)
    }
  }


  async function changeAccountLevel(
    user: UserCatalogItem,
  ) {
    if (
      !isSuperadmin
      || !user.can_manage
      || user.account_level === 'SUPERADMIN'
    ) {
      return
    }

    const nextLevel:
      AssignableAccountLevel =
        user.account_level === 'ADMIN'
          ? 'USER'
          : 'ADMIN'

    const confirmed =
      await confirmAction({
        title: 'Change account level?',
        message:
          `Change ${user.username} from ${user.account_level} to ${nextLevel}?`,
        detail:
          nextLevel === 'ADMIN'
            ? 'ADMIN grants platform administration privileges. Active USER business-role assignments will be closed on promotion.'
            : 'USER removes platform administration privileges. Business-role access is managed separately in Roles and Access.',
        confirmLabel:
          nextLevel === 'ADMIN'
            ? 'Make ADMIN'
            : 'Make USER',
      })

    if (!confirmed) {
      return
    }

    try {
      setChangingUserId(
        user.user_id,
      )

      setError(null)

      await setUserAccountLevel(
        user.user_id,
        nextLevel,
      )

      await loadUsers()
    } catch (levelError) {
      setError(
        errorMessage(
          levelError,
        ),
      )
    } finally {
      setChangingUserId(null)
    }
  }


  return (
    <div className="admin-users-page">
      <header className="workspace-header">
        <div>
          <div className="breadcrumb">
            <Trans i18nKey="batch.tr0318" />
          </div>

          <h1>
            <Trans i18nKey="batch.tr0428" />
          </h1>

          <p>
            <Trans i18nKey="batch.tr0344" />
          </p>
        </div>

        <div className="admin-users-header-actions">
          <span className="badge badge-accent">
            {users.length} USERS
          </span>

          <button
            type="button"
            className="admin-users-primary-button"
            onClick={openCreate}
          >
            <Trans i18nKey="batch.tr0315" />
          </button>
        </div>
      </header>

      {
        error
          ? (
            <div className="error">
              <strong>
                <Trans i18nKey="batch.tr0343" />
              </strong>

              <span>
                {error}
              </span>
            </div>
          )
          : null
      }

      {
        editorMode
          ? (
            <section className="admin-user-editor">
              <div className="admin-user-editor-title">
                {
                  editorMode === 'create'
                    ?<Trans i18nKey="batch.tr0315" />
                    : `Edit ${username}`
                }
              </div>

              <form
                className="admin-user-form"
                onSubmit={submitUser}
              >
                <label>
                  <span>
                    <Trans i18nKey="batch.tr0121" />
                  </span>

                  <input
                    value={username}
                    maxLength={128}
                    required
                    readOnly={
                      editorMode === 'edit'
                    }
                    autoComplete="off"
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
                    <Trans i18nKey="batch.tr0317" />
                  </span>

                  <input
                    value={displayName}
                    maxLength={200}
                    onChange={
                      event =>
                        setDisplayName(
                          event.target.value,
                        )
                    }
                  />
                </label>

                <label>
                  <span>
                    <Trans i18nKey="batch.tr0319" />
                  </span>

                  <input
                    type="email"
                    value={email}
                    maxLength={320}
                    onChange={
                      event =>
                        setEmail(
                          event.target.value,
                        )
                    }
                  />
                </label>

                {
                  editorMode === 'create'
                    ? (
                      <>
                        <label>
                          <span>
                            <Trans i18nKey="batch.tr0309" />
                          </span>

                          <select
                            value={accountLevel}
                            disabled={!isSuperadmin}
                            onChange={
                              event =>
                                setAccountLevel(
                                  (event.target.value as AssignableAccountLevel),
                                )
                            }
                          >
                            <option value="USER">
                              USER
                            </option>

                            {
                              isSuperadmin
                                ? (
                                  <option value="ADMIN">
                                    ADMIN
                                  </option>
                                )
                                : null
                            }
                          </select>
                        </label>

                        <label>
                          <span>
                            <Trans i18nKey="batch.tr0312" />
                          </span>

                          <select
                            value={authProvider}
                            onChange={
                              event => {
                                const provider =
                                  (event.target.value as AuthProvider)

                                setAuthProvider(
                                  provider,
                                )

                                if (
                                  provider !== 'LOCAL'
                                ) {
                                  setPassword('')
                                  setMustChangePassword(false)
                                }

                                if (
                                  provider === 'LOCAL'
                                ) {
                                  setExternalSubject('')
                                  setMustChangePassword(true)
                                }
                              }
                            }
                          >
                            <option value="LOCAL">
                              LOCAL
                            </option>

                            <option value="LDAP">
                              LDAP
                            </option>

                            <option value="AD">
                              AD
                            </option>

                            <option value="OIDC">
                              OIDC
                            </option>
                          </select>
                        </label>

                        {
                          authProvider === 'LOCAL'
                            ? (
                              <>
                                <label>
                                  <span>
                                    <Trans i18nKey="batch.tr0324" />
                                  </span>

                                  <input
                                    type="password"
                                    value={password}
                                    maxLength={512}
                                    required
                                    autoComplete="new-password"
                                    onChange={
                                      event =>
                                        setPassword(
                                          event.target.value,
                                        )
                                    }
                                  />
                                </label>

                                <div className="admin-form-help">
                                  <strong>
                                    <Trans i18nKey="batch.tr0120" />
                                  </strong>{' '}
                                  <Trans i18nKey="batch.tr0308" />
                                </div>

                                <label className="admin-user-checkbox-label">
                                  <input
                                    type="checkbox"
                                    checked={mustChangePassword}
                                    onChange={
                                      event =>
                                        setMustChangePassword(
                                          event.target.checked,
                                        )
                                    }
                                  />

                                  <span>
                                    <Trans i18nKey="batch.tr0334" />
                                  </span>
                                </label>
                              </>
                            )
                            : (
                              <label>
                                <span>
                                  <Trans i18nKey="batch.tr0321" />
                                </span>

                                <input
                                  value={externalSubject}
                                  maxLength={512}
                                  onChange={
                                    event =>
                                      setExternalSubject(
                                        event.target.value,
                                      )
                                  }
                                  placeholder="Optional external identity"
                                />
                              </label>
                            )
                        }
                      </>
                    )
                    : (
                      <div className="admin-user-immutable">
                        <div>
                          <span>
                            <Trans i18nKey="batch.tr0309" />
                          </span>

                          <strong>
                            {editingUser?.account_level}
                          </strong>
                        </div>

                        <div>
                          <span>
                            <Trans i18nKey="batch.tr0312" />
                          </span>

                          <strong>
                            {editingUser?.auth_provider}
                          </strong>
                        </div>

                        <div>
                          <span>
                            <Trans i18nKey="batch.tr0322" />
                          </span>

                          <strong>
                            <Trans i18nKey="batch.tr0323" />
                          </strong>
                        </div>
                      </div>
                    )
                }

                <div className="admin-user-form-actions">
                  <button
                    type="button"
                    className="admin-users-secondary-button"
                    disabled={saving}
                    onClick={closeEditor}
                  >
                    <Trans i18nKey="batch.tr0222" />
                  </button>

                  <button
                    type="submit"
                    className="admin-users-primary-button"
                    disabled={saving}
                  >
                    {
                      saving
                        ?<Trans i18nKey="batch.tr0221" />
                        :<Trans i18nKey="batch.tr0220" />
                    }
                  </button>
                </div>
              </form>
            </section>
          )
          : null
      }

      {
        passwordResetUser
          ? (
            <section className="admin-user-editor">
              <div className="admin-user-editor-title">
                <Trans i18nKey="batch.tr0336" /> {passwordResetUser.username}
              </div>

              <form
                className="admin-user-form"
                onSubmit={submitPasswordReset}
              >
                <label>
                  <span>
                    <Trans i18nKey="batch.tr0339" />
                  </span>

                  <input
                    type="password"
                    value={temporaryPassword}
                    maxLength={128}
                    required
                    autoComplete="new-password"
                    onChange={
                      event =>
                        setTemporaryPassword(
                          event.target.value,
                        )
                    }
                  />
                </label>

                <label>
                  <span>
                    <Trans i18nKey="batch.tr0314" />
                  </span>

                  <input
                    type="password"
                    value={confirmTemporaryPassword}
                    maxLength={128}
                    required
                    autoComplete="new-password"
                    onChange={
                      event =>
                        setConfirmTemporaryPassword(
                          event.target.value,
                        )
                    }
                  />
                </label>

                <div className="admin-form-help">
                  <strong>
                    <Trans i18nKey="batch.tr0120" />
                  </strong>{' '}
                  <Trans i18nKey="batch.tr0308" />
                </div>

                <div className="admin-form-help">
                  <Trans i18nKey="batch.tr0342" />
                </div>

                <div className="admin-user-form-actions">
                  <button
                    type="button"
                    className="admin-users-secondary-button"
                    disabled={resettingPassword}
                    onClick={closePasswordReset}
                  >
                    <Trans i18nKey="batch.tr0222" />
                  </button>

                  <button
                    type="submit"
                    className="admin-users-primary-button"
                    disabled={
                      resettingPassword
                      || temporaryPassword.length === 0
                      || confirmTemporaryPassword.length === 0
                    }
                  >
                    {
                      resettingPassword
                        ?<Trans i18nKey="batch.tr0337" />
                        :<Trans i18nKey="batch.tr0338" />
                    }
                  </button>
                </div>
              </form>
            </section>
          )
          : null
      }

      {
        loading && users.length === 0
          ? (
            <div className="loading-panel">
              <Trans i18nKey="batch.tr0326" />
            </div>
          )
          : (
            <div className="data-table-wrapper">
              <table className="data-table admin-users-table">
                <thead>
                  <tr>
                    <th><Trans i18nKey="batch.tr0121" /></th>
                    <th><Trans i18nKey="batch.tr0092" /></th>
                    <th><Trans i18nKey="batch.tr0325" /></th>
                    <th><Trans i18nKey="batch.tr0333" /></th>
                    <th><Trans i18nKey="batch.tr0431" /></th>
                    <th><Trans i18nKey="batch.tr0036" /></th>
                    <th><Trans i18nKey="batch.tr0307" /></th>
                    <th><Trans i18nKey="batch.tr0072" /></th>
                    <th><Trans i18nKey="batch.tr0215" /></th>
                  </tr>
                </thead>

                <tbody>
                  {
                    users.map(
                      user => (
                        <tr key={user.user_id}>
                          <td>
                            <strong className="code-value">
                              {user.username}
                            </strong>

                            {
                              user.email
                                ? (
                                  <div className="admin-user-email">
                                    {user.email}
                                  </div>
                                )
                                : null
                            }
                          </td>

                          <td>
                            {user.display_name ?? ''}
                          </td>

                          <td>
                            <span
                              className={
                                user.account_level === 'SUPERADMIN'
                                  ? 'badge badge-accent'
                                  : 'badge'
                              }
                            >
                              {user.account_level}
                            </span>
                          </td>

                          <td>
                            {user.auth_provider}
                          </td>

                          <td>
                            {user.business_role_count}
                          </td>

                          <td>
                            <span
                              className={
                                user.is_active
                                  ? 'badge badge-success'
                                  : 'badge'
                              }
                            >
                              {
                                user.is_active
                                  ? 'ACTIVE'
                                  : 'DISABLED'
                              }
                            </span>
                          </td>

                          <td>
                            {
                              user.is_system
                                ? 'YES'
                                : 'NO'
                            }
                          </td>

                          <td>
                            {
                              formatUserDate(
                                user.updated_at,
                              )
                            }
                          </td>

                          <td>
                            {
                              user.can_manage
                                ? (
                                  <div className="admin-user-row-actions">
                                    <button
                                      type="button"
                                      className="admin-users-link-button"
                                      disabled={
                                        changingUserId
                                        === user.user_id
                                      }
                                      onClick={
                                        () =>
                                          openEdit(
                                            user,
                                          )
                                      }
                                    >
                                      <Trans i18nKey="batch.tr0218" />
                                    </button>

                                    <button
                                      type="button"
                                      className="admin-users-link-button"
                                      disabled={
                                        changingUserId
                                        === user.user_id
                                      }
                                      onClick={
                                        () => {
                                          void changeActiveState(
                                            user,
                                          )
                                        }
                                      }
                                    >
                                      {
                                        changingUserId === user.user_id
                                          ?<Trans i18nKey="batch.tr0115" />
                                          : user.is_active
                                            ?<Trans i18nKey="batch.tr0217" />
                                            :<Trans i18nKey="batch.tr0219" />
                                      }
                                    </button>

                                    {
                                      user.can_reset_password
                                        ? (
                                          <button
                                            type="button"
                                            className="admin-users-link-button"
                                            disabled={
                                              changingUserId
                                              === user.user_id
                                            }
                                            onClick={
                                              () =>
                                                openPasswordReset(
                                                  user,
                                                )
                                            }
                                          >
                                            <Trans i18nKey="batch.tr0335" />
                                          </button>
                                        )
                                        : null
                                    }

                                    {
                                      isSuperadmin
                                      && user.account_level !== 'SUPERADMIN'
                                        ? (
                                          <button
                                            type="button"
                                            className="admin-users-link-button"
                                            disabled={
                                              changingUserId
                                              === user.user_id
                                            }
                                            onClick={
                                              () => {
                                                void changeAccountLevel(
                                                  user,
                                                )
                                              }
                                            }
                                          >
                                            {
                                              user.account_level === 'ADMIN'
                                                ?<Trans i18nKey="batch.tr0329" />
                                                :<Trans i18nKey="batch.tr0327" />
                                            }
                                          </button>
                                        )
                                        : null
                                    }
                                  </div>
                                )
                                : (
                                  <span className="admin-user-protected">
                                    <Trans i18nKey="batch.tr0346" />
                                  </span>
                                )
                            }
                          </td>
                        </tr>
                      ),
                    )
                  }
                </tbody>
              </table>
            </div>
          )
      }
    </div>
  )
}


export default AdminUsersPage