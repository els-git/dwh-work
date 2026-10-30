import { Trans } from 'react-i18next'
import {
  useEffect,
  useState,
} from 'react'

import {
  getAccessUserRoles,
  getAccessUsers,
  setAccessUserRole,
} from './accessControlApi'

import type {
  AccessUserItem,
  AccessUserRoleItem,
} from './accessControlApi'

import {
  confirmAction,
} from './confirmAction'

import {
  formatDateTime,
} from './dateTime'

import './AdminAccessPage.css'


function errorMessage(
  error: unknown,
): string {
  if (error instanceof Error) {
    return error.message
  }

  return 'Unknown error'
}


function AdminAccessUserAssignments() {
  const [
    users,
    setUsers,
  ] = useState<AccessUserItem[]>([])

  const [
    selectedUserId,
    setSelectedUserId,
  ] = useState<number | null>(null)

  const [
    roles,
    setRoles,
  ] = useState<AccessUserRoleItem[]>([])

  const [
    loadingUsers,
    setLoadingUsers,
  ] = useState(true)

  const [
    loadingRoles,
    setLoadingRoles,
  ] = useState(false)

  const [
    changingKey,
    setChangingKey,
  ] = useState<string | null>(null)

  const [
    error,
    setError,
  ] = useState<string | null>(null)


  const selectedUser =
    users.find(
      user =>
        user.user_id === selectedUserId,
    )
    ?? null


  async function refreshSelected(
    userId: number,
  ) {
    const [
      userResult,
      roleResult,
    ] = await Promise.all([
      getAccessUsers(),
      getAccessUserRoles(
        userId,
      ),
    ])

    setUsers(userResult)
    setRoles(roleResult)
  }


  useEffect(() => {
    let cancelled = false

    void getAccessUsers()
      .then(result => {
        if (cancelled) {
          return
        }

        setUsers(result)
        setLoadingRoles(result.length > 0)

        setSelectedUserId(
          current => {
            if (
              current !== null
              && result.some(
                user =>
                  user.user_id === current,
              )
            ) {
              return current
            }

            return (
              result[0]?.user_id
              ?? null
            )
          },
        )
      })
      .catch(loadError => {
        if (!cancelled) {
          setError(errorMessage(loadError))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingUsers(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])


  useEffect(() => {
    if (selectedUserId === null) {
      return
    }

    let cancelled = false

    void getAccessUserRoles(
      selectedUserId,
    )
      .then(result => {
        if (!cancelled) {
          setRoles(result)
        }
      })
      .catch(loadError => {
        if (cancelled) {
          return
        }

        setRoles([])
        setError(errorMessage(loadError))
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingRoles(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    selectedUserId,
  ])


  async function changeRole(
    role: AccessUserRoleItem,
  ) {
    if (
      selectedUser === null
      || !selectedUser.can_manage_roles
    ) {
      return
    }

    const nextState =
      !role.is_assigned

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Grant business role?'
            : 'Revoke business role?',
        message:
          nextState
            ? (
                role.role_code
                + ' will be granted to '
                + selectedUser.username
                + '.'
              )
            : (
                role.role_code
                + ' will be revoked from '
                + selectedUser.username
                + '.'
              ),
        detail:
          nextState
            ? (
                'The user will receive the permissions '
                + 'and resources currently assigned '
                + 'to this business role.'
              )
            : (
                'The current assignment will be closed '
                + 'in history. It will not be physically deleted.'
              ),
        confirmLabel:
          nextState
            ? 'Grant'
            : 'Revoke',
      })

    if (!confirmed) {
      return
    }

    const key =
      'role:' + role.role_id

    try {
      setChangingKey(key)
      setError(null)

      await setAccessUserRole(
        selectedUser.user_id,
        role.role_id,
        nextState,
      )

      await refreshSelected(
        selectedUser.user_id,
      )
    } catch (changeError) {
      setError(
        errorMessage(
          changeError,
        ),
      )
    } finally {
      setChangingKey(null)
    }
  }


  return (
    <section className="admin-access-page">
      <div className="admin-access-header">
        <div>
          <div className="breadcrumb">
            <Trans i18nKey="batch.tr0142" />
          </div>

          <h1>
            <Trans i18nKey="batch.tr0182" />
          </h1>

          <p>
            <Trans i18nKey="batch.tr0135" />
          </p>
        </div>

        <span className="badge badge-accent">
          {users.length} USERS
        </span>
      </div>

      {
        error
          ? (
              <div className="admin-access-error">
                {error}
              </div>
            )
          : null
      }

      <div className="admin-access-layout">
        <aside className="admin-access-sidebar">
          <div className="admin-access-sidebar-title">
            <Trans i18nKey="batch.tr0428" />
          </div>

          {
            loadingUsers
              ? (
                  <div className="admin-access-empty">
                    <Trans i18nKey="batch.tr0153" />
                  </div>
                )
              : null
          }

          {
            !loadingUsers
            && users.length === 0
              ? (
                  <div className="admin-access-empty">
                    <Trans i18nKey="batch.tr0157" />
                  </div>
                )
              : null
          }

          {
            users.map(
              user => (
                <button
                  type="button"
                  key={user.user_id}
                  className={
                    user.user_id === selectedUserId
                      ? 'admin-access-role active'
                      : 'admin-access-role'
                  }
                  onClick={() => {
                    if (selectedUserId === user.user_id) {
                      return
                    }

                    setLoadingRoles(true)
                    setError(null)
                    setRoles([])
                    setSelectedUserId(user.user_id)
                  }}
                >
                  <span className="admin-access-role-code">
                    {user.username}
                  </span>

                  <span className="admin-access-role-name">
                    {
                      user.display_name
                      ?? user.username
                    }
                  </span>

                  <span className="admin-access-role-counts">
                    {user.account_level}
                    {'  '}
                    {user.business_role_count} <Trans i18nKey="batch.tr0169" />
                  </span>
                </button>
              ),
            )
          }
        </aside>

        <div className="admin-access-content">
          {
            selectedUser === null
              ? (
                  <div className="admin-access-empty-panel">
                    <Trans i18nKey="batch.tr0172" />
                  </div>
                )
              : (
                  <>
                    <div className="admin-access-role-header">
                      <div>
                        <div className="admin-access-role-eyebrow">
                          <Trans i18nKey="batch.tr0174" />
                        </div>

                        <h2>
                          {
                            selectedUser.display_name
                            ?? selectedUser.username
                          }
                        </h2>

                        <div className="admin-access-selected-code">
                          {selectedUser.username}
                        </div>
                      </div>

                      <div className="admin-access-role-meta">
                        <span
                          className={
                            selectedUser.is_active
                              ? 'admin-access-state active'
                              : 'admin-access-state inactive'
                          }
                        >
                          {
                            selectedUser.is_active
                              ? 'ACTIVE'
                              : 'INACTIVE'
                          }
                        </span>

                        <span>
                          {selectedUser.account_level}
                        </span>

                        <span>
                          {selectedUser.auth_provider}
                        </span>

                        <span>
                          {selectedUser.business_role_count} <Trans i18nKey="batch.tr0169" />
                        </span>
                      </div>
                    </div>

                    {
                      !selectedUser.can_manage_roles
                        ? (
                            <div className="admin-access-warning">
                              <Trans i18nKey="batch.tr0140" />
                            </div>
                          )
                        : null
                    }

                    {
                      selectedUser.can_manage_roles
                      && !selectedUser.is_active
                        ? (
                            <div className="admin-access-warning">
                              <Trans i18nKey="batch.tr0180" />
                            </div>
                          )
                        : null
                    }

                    {
                      loadingRoles
                        ? (
                            <div className="admin-access-empty-panel">
                              <Trans i18nKey="batch.tr0151" />
                            </div>
                          )
                        : null
                    }

                    {
                      !loadingRoles
                        ? (
                            <section className="admin-access-policy-card">
                              <div className="admin-access-policy-header">
                                <div>
                                  <h3>
                                    <Trans i18nKey="batch.tr0139" />
                                  </h3>

                                  <p>
                                    <Trans i18nKey="batch.tr0132" />
                                  </p>
                                </div>

                                <span className="admin-access-count">
                                  {
                                    roles.filter(
                                      item =>
                                        item.is_assigned,
                                    ).length
                                  } ASSIGNED
                                </span>
                              </div>

                              <div className="table-wrap">
                                <table className="admin-access-table">
                                  <thead>
                                    <tr>
                                      <th><Trans i18nKey="batch.tr0166" /></th>
                                      <th><Trans i18nKey="batch.tr0093" /></th>
                                      <th><Trans i18nKey="batch.tr0168" /></th>
                                      <th><Trans i18nKey="batch.tr0138" /></th>
                                      <th><Trans i18nKey="batch.tr0137" /></th>
                                      <th><Trans i18nKey="batch.tr0133" /></th>
                                    </tr>
                                  </thead>

                                  <tbody>
                                    {
                                      roles.length === 0
                                        ? (
                                            <tr>
                                              <td
                                                colSpan={6}
                                                className="admin-access-table-empty"
                                              >
                                                <Trans i18nKey="batch.tr0186" />
                                              </td>
                                            </tr>
                                          )
                                        : roles.map(
                                            role => {
                                              const key =
                                                'role:' + role.role_id

                                              const busy =
                                                changingKey === key

                                              const disabled =
                                                changingKey !== null
                                                || !selectedUser.can_manage_roles
                                                || (
                                                  !selectedUser.is_active
                                                  && !role.is_assigned
                                                )
                                                || (
                                                  !role.role_is_active
                                                  && !role.is_assigned
                                                )

                                              return (
                                                <tr
                                                  key={role.role_id}
                                                >
                                                  <td>
                                                    {role.role_name}
                                                  </td>

                                                  <td>
                                                    <code>
                                                      {role.role_code}
                                                    </code>
                                                  </td>

                                                  <td>
                                                    <span
                                                      className={
                                                        role.role_is_active
                                                          ? 'admin-access-state active'
                                                          : 'admin-access-state inactive'
                                                      }
                                                    >
                                                      {
                                                        role.role_is_active
                                                          ? 'ACTIVE'
                                                          : 'INACTIVE'
                                                      }
                                                    </span>
                                                  </td>

                                                  <td>
                                                    <span
                                                      className={
                                                        role.is_assigned
                                                          ? 'admin-access-assignment assigned'
                                                          : 'admin-access-assignment'
                                                      }
                                                    >
                                                      {
                                                        role.is_assigned
                                                          ? 'ASSIGNED'
                                                          : 'NOT ASSIGNED'
                                                      }
                                                    </span>
                                                  </td>

                                                  <td>
                                                    {
                                                      role.assigned_at
                                                        ? formatDateTime(
                                                            role.assigned_at,
                                                          )
                                                        : ''
                                                    }
                                                  </td>

                                                  <td>
                                                    <button
                                                      type="button"
                                                      className="admin-access-action"
                                                      disabled={disabled}
                                                      onClick={
                                                        () =>
                                                          void changeRole(
                                                            role,
                                                          )
                                                      }
                                                    >
                                                      {
                                                        busy
                                                          ?<Trans i18nKey="batch.tr0115" />
                                                          : role.is_assigned
                                                            ?<Trans i18nKey="batch.tr0162" />
                                                            :<Trans i18nKey="batch.tr0144" />
                                                      }
                                                    </button>
                                                  </td>
                                                </tr>
                                              )
                                            },
                                          )
                                    }
                                  </tbody>
                                </table>
                              </div>
                            </section>
                          )
                        : null
                    }
                  </>
                )
          }
        </div>
      </div>
    </section>
  )
}


export default AdminAccessUserAssignments