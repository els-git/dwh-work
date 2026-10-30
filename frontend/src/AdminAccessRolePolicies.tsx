import { Trans } from 'react-i18next'
import {
  useEffect,
  useState,
} from 'react'

import {
  getAccessRolePermissions,
  getAccessRoleResources,
  getAccessRoles,
  setAccessRolePermission,
  setAccessRoleResource,
} from './accessControlApi'

import type {
  AccessPermissionItem,
  AccessResourceItem,
  AccessRoleItem,
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


function AdminAccessPage() {
  const [
    roles,
    setRoles,
  ] = useState<AccessRoleItem[]>([])

  const [
    selectedRoleId,
    setSelectedRoleId,
  ] = useState<number | null>(null)

  const [
    permissions,
    setPermissions,
  ] = useState<AccessPermissionItem[]>([])

  const [
    resources,
    setResources,
  ] = useState<AccessResourceItem[]>([])

  const [
    loadingRoles,
    setLoadingRoles,
  ] = useState(true)

  const [
    loadingDetails,
    setLoadingDetails,
  ] = useState(false)

  const [
    error,
    setError,
  ] = useState<string | null>(null)

  const [
    changingKey,
    setChangingKey,
  ] = useState<string | null>(null)


  const selectedRole =
    roles.find(
      role =>
        role.role_id
        === selectedRoleId,
    )
    ?? null


  async function refreshSelected(
    roleId: number,
  ) {
    const [
      roleResult,
      permissionResult,
      resourceResult,
    ] = await Promise.all([
      getAccessRoles(),
      getAccessRolePermissions(
        roleId,
      ),
      getAccessRoleResources(
        roleId,
      ),
    ])

    setRoles(roleResult)
    setPermissions(permissionResult)
    setResources(resourceResult)
  }


  useEffect(() => {
    let cancelled = false

    void getAccessRoles()
      .then(result => {
        if (cancelled) {
          return
        }

        setRoles(result)
        setLoadingDetails(result.length > 0)

        setSelectedRoleId(
          current => {
            if (
              current !== null
              && result.some(
                role =>
                  role.role_id === current,
              )
            ) {
              return current
            }

            return (
              result[0]?.role_id
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
          setLoadingRoles(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])


  useEffect(() => {
    if (selectedRoleId === null) {
      return
    }

    let cancelled = false

    void Promise.all([
      getAccessRolePermissions(
        selectedRoleId,
      ),
      getAccessRoleResources(
        selectedRoleId,
      ),
    ])
      .then(([
        permissionResult,
        resourceResult,
      ]) => {
        if (cancelled) {
          return
        }

        setPermissions(permissionResult)
        setResources(resourceResult)
      })
      .catch(loadError => {
        if (cancelled) {
          return
        }

        setPermissions([])
        setResources([])
        setError(errorMessage(loadError))
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingDetails(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    selectedRoleId,
  ])


  async function changePermission(
    permission: AccessPermissionItem,
  ) {
    if (
      selectedRole === null
      || !selectedRole.can_manage
    ) {
      return
    }

    const nextState =
      !permission.is_assigned

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Grant permission?'
            : 'Revoke permission?',
        message:
          nextState
            ? (
                `${permission.permission_code} `
                + `will be granted to `
                + `${selectedRole.role_code}.`
              )
            : (
                `${permission.permission_code} `
                + `will be revoked from `
                + `${selectedRole.role_code}.`
              ),
        detail:
          nextState
            ? (
                'For scoped USER authorization, '
                + 'the required permission and resource '
                + 'must belong to the same business role.'
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
      `permission:${permission.permission_id}`

    try {
      setChangingKey(key)
      setError(null)

      await setAccessRolePermission(
        selectedRole.role_id,
        permission.permission_id,
        nextState,
      )

      await refreshSelected(
        selectedRole.role_id,
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


  async function changeResource(
    resource: AccessResourceItem,
  ) {
    if (
      selectedRole === null
      || !selectedRole.can_manage
    ) {
      return
    }

    const nextState =
      !resource.is_assigned

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Grant resource access?'
            : 'Revoke resource access?',
        message:
          nextState
            ? (
                `${resource.resource_type}/`
                + `${resource.resource_code} `
                + `will be granted to `
                + `${selectedRole.role_code}.`
              )
            : (
                `${resource.resource_type}/`
                + `${resource.resource_code} `
                + `will be revoked from `
                + `${selectedRole.role_code}.`
              ),
        detail:
          nextState
            ? (
                'This resource can participate in '
                + 'same-role scoped authorization.'
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
      `resource:${resource.resource_id}`

    try {
      setChangingKey(key)
      setError(null)

      await setAccessRoleResource(
        selectedRole.role_id,
        resource.resource_id,
        nextState,
      )

      await refreshSelected(
        selectedRole.role_id,
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
            <Trans i18nKey="batch.tr0141" />
          </div>

          <h1>
            <Trans i18nKey="batch.tr0427" />
          </h1>

          <p>
            <Trans i18nKey="batch.tr0136" />
          </p>
        </div>

        <span className="badge badge-accent">
          {roles.length} ROLES
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
            <Trans i18nKey="batch.tr0139" />
          </div>

          {
            loadingRoles
              ? (
                  <div className="admin-access-empty">
                    <Trans i18nKey="batch.tr0152" />
                  </div>
                )
              : null
          }

          {
            !loadingRoles
            && roles.length === 0
              ? (
                  <div className="admin-access-empty">
                    <Trans i18nKey="batch.tr0186" />
                  </div>
                )
              : null
          }

          {
            roles.map(
              role => (
                <button
                  type="button"
                  key={role.role_id}
                  className={
                    role.role_id
                    === selectedRoleId
                      ? 'admin-access-role active'
                      : 'admin-access-role'
                  }
                  onClick={() => {
                    if (selectedRoleId === role.role_id) {
                      return
                    }

                    setLoadingDetails(true)
                    setError(null)
                    setPermissions([])
                    setResources([])
                    setSelectedRoleId(role.role_id)
                  }}
                >
                  <span className="admin-access-role-code">
                    {role.role_code}
                  </span>

                  <span className="admin-access-role-name">
                    {role.role_name}
                  </span>

                  <span className="admin-access-role-counts">
                    {
                      role.permission_count
                    } <Trans i18nKey="batch.tr0160" />
                    {'  '}
                    {
                      role.resource_count
                    } <Trans i18nKey="batch.tr0161" />
                  </span>
                </button>
              ),
            )
          }
        </aside>

        <div className="admin-access-content">
          {
            selectedRole === null
              ? (
                  <div className="admin-access-empty-panel">
                    <Trans i18nKey="batch.tr0171" />
                  </div>
                )
              : (
                  <>
                    <div className="admin-access-role-header">
                      <div>
                        <div className="admin-access-role-eyebrow">
                          <Trans i18nKey="batch.tr0173" />
                        </div>

                        <h2>
                          {selectedRole.role_name}
                        </h2>

                        <div className="admin-access-selected-code">
                          {selectedRole.role_code}
                        </div>
                      </div>

                      <div className="admin-access-role-meta">
                        <span
                          className={
                            selectedRole.is_active
                              ? 'admin-access-state active'
                              : 'admin-access-state inactive'
                          }
                        >
                          {
                            selectedRole.is_active
                              ? 'ACTIVE'
                              : 'INACTIVE'
                          }
                        </span>

                        <span>
                          {
                            selectedRole.permission_count
                          } <Trans i18nKey="batch.tr0160" />
                        </span>

                        <span>
                          {
                            selectedRole.resource_count
                          } <Trans i18nKey="batch.tr0161" />
                        </span>
                      </div>
                    </div>

                    {
                      !selectedRole.is_active
                        ? (
                            <div className="admin-access-warning">
                              <Trans i18nKey="batch.tr0179" />
                            </div>
                          )
                        : null
                    }

                    {
                      loadingDetails
                        ? (
                            <div className="admin-access-empty-panel">
                              <Trans i18nKey="batch.tr0150" />
                            </div>
                          )
                        : null
                    }


                    {
                      !loadingDetails
                        ? (
                            <>
                              <section className="admin-access-policy-card">
                                <div className="admin-access-policy-header">
                                  <div>
                                    <h3>
                                      <Trans i18nKey="batch.tr0187" />
                                    </h3>

                                    <p>
                                      <Trans i18nKey="batch.tr0158" />
                                    </p>
                                  </div>

                                  <span className="admin-access-count">
                                    {
                                      permissions.filter(
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
                                        <th><Trans i18nKey="batch.tr0159" /></th>
                                        <th><Trans i18nKey="batch.tr0093" /></th>
                                        <th><Trans i18nKey="batch.tr0148" /></th>
                                        <th><Trans i18nKey="batch.tr0036" /></th>
                                        <th><Trans i18nKey="batch.tr0137" /></th>
                                        <th><Trans i18nKey="batch.tr0133" /></th>
                                      </tr>
                                    </thead>

                                    <tbody>
                                      {
                                        permissions.map(
                                          permission => {
                                            const key =
                                              `permission:${permission.permission_id}`

                                            const busy =
                                              changingKey === key

                                            const disabled =
                                              changingKey !== null
                                              || !selectedRole.can_manage
                                              || (
                                                !permission.is_active
                                                && !permission.is_assigned
                                              )

                                            return (
                                              <tr
                                                key={permission.permission_id}
                                              >
                                                <td>
                                                  {
                                                    permission.permission_name
                                                  }
                                                </td>

                                                <td>
                                                  <code>
                                                    {
                                                      permission.permission_code
                                                    }
                                                  </code>
                                                </td>

                                                <td>
                                                  {
                                                    permission.permission_group
                                                  }
                                                </td>

                                                <td>
                                                  <span
                                                    className={
                                                      permission.is_assigned
                                                        ? 'admin-access-assignment assigned'
                                                        : 'admin-access-assignment'
                                                    }
                                                  >
                                                    {
                                                      permission.is_assigned
                                                        ? 'ASSIGNED'
                                                        : 'NOT ASSIGNED'
                                                    }
                                                  </span>
                                                </td>

                                                <td>
                                                  {
                                                    permission.assigned_at
                                                      ? formatDateTime(
                                                          permission.assigned_at,
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
                                                        void changePermission(
                                                          permission,
                                                        )
                                                    }
                                                  >
                                                    {
                                                      busy
                                                        ?<Trans i18nKey="batch.tr0115" />
                                                        : permission.is_assigned
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

                              <section className="admin-access-policy-card">
                                <div className="admin-access-policy-header">
                                  <div>
                                    <h3>
                                      <Trans i18nKey="batch.tr0188" />
                                    </h3>

                                    <p>
                                      <Trans i18nKey="batch.tr0154" />
                                    </p>
                                  </div>

                                  <span className="admin-access-count">
                                    {
                                      resources.filter(
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
                                        <th><Trans i18nKey="batch.tr0185" /></th>
                                        <th><Trans i18nKey="batch.tr0093" /></th>
                                        <th><Trans i18nKey="batch.tr0092" /></th>
                                        <th><Trans i18nKey="batch.tr0036" /></th>
                                        <th><Trans i18nKey="batch.tr0137" /></th>
                                        <th><Trans i18nKey="batch.tr0133" /></th>
                                      </tr>
                                    </thead>

                                    <tbody>
                                      {
                                        resources.length === 0
                                          ? (
                                              <tr>
                                                <td
                                                  colSpan={6}
                                                  className="admin-access-table-empty"
                                                >
                                                  <Trans i18nKey="batch.tr0156" />
                                                </td>
                                              </tr>
                                            )
                                          : resources.map(
                                              resource => {
                                                const key =
                                                  `resource:${resource.resource_id}`

                                                const busy =
                                                  changingKey === key

                                                const disabled =
                                                  changingKey !== null
                                                  || !selectedRole.can_manage
                                                  || (
                                                    !resource.is_active
                                                    && !resource.is_assigned
                                                  )

                                                return (
                                                  <tr
                                                    key={resource.resource_id}
                                                  >
                                                    <td>
                                                      {
                                                        resource.resource_type
                                                      }
                                                    </td>

                                                    <td>
                                                      <code>
                                                        {
                                                          resource.resource_code
                                                        }
                                                      </code>
                                                    </td>

                                                    <td>
                                                      {
                                                        resource.resource_name
                                                      }
                                                    </td>

                                                    <td>
                                                      <span
                                                        className={
                                                          resource.is_assigned
                                                            ? 'admin-access-assignment assigned'
                                                            : 'admin-access-assignment'
                                                        }
                                                      >
                                                        {
                                                          resource.is_assigned
                                                            ? 'ASSIGNED'
                                                            : 'NOT ASSIGNED'
                                                        }
                                                      </span>
                                                    </td>

                                                    <td>
                                                      {
                                                        resource.assigned_at
                                                          ? formatDateTime(
                                                              resource.assigned_at,
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
                                                            void changeResource(
                                                              resource,
                                                            )
                                                        }
                                                      >
                                                        {
                                                          busy
                                                            ?<Trans i18nKey="batch.tr0115" />
                                                            : resource.is_assigned
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
                            </>
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


export default AdminAccessPage