import { Trans } from 'react-i18next'
import {
    useEffect,
    useState,
  } from 'react'
  
  import type {
    FormEvent,
  } from 'react'
  
  import {
    createRole,
    getRoles,
    setRoleActive,
    updateRole,
  } from './roleCatalogApi'
  
  import type {
    RoleCatalogItem,
  } from './roleCatalogApi'
  
  import { confirmAction } from './confirmAction'
  import { formatDateTime } from './dateTime'
  
  import './AdminRolesPage.css'
  
  
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
  
  
  function formatRoleDate(
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
  
  
  function AdminRolesPage() {
    const [
      roles,
      setRoles,
    ] = useState<RoleCatalogItem[]>([])
  
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
      roleId,
      setRoleId,
    ] = useState<number | null>(null)
  
    const [
      roleCode,
      setRoleCode,
    ] = useState('')
  
    const [
      roleName,
      setRoleName,
    ] = useState('')
  
    const [
      description,
      setDescription,
    ] = useState('')
  
    const [
      saving,
      setSaving,
    ] = useState(false)
  
    const [
      changingRoleId,
      setChangingRoleId,
    ] = useState<number | null>(null)
  
  
    async function loadRoles() {
      try {
        setLoading(true)
        setError(null)
  
        const result =
          await getRoles()
  
        setRoles(
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

      void getRoles()
        .then(result => {
          if (!cancelled) {
            setRoles(result)
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
  
  
    function openCreate() {
      setRoleId(null)
      setRoleCode('')
      setRoleName('')
      setDescription('')
      setEditorMode('create')
      setError(null)
    }
  
  
    function openEdit(
      role: RoleCatalogItem,
    ) {
      if (!role.can_manage) {
        return
      }
  
      setRoleId(
        role.role_id,
      )
  
      setRoleCode(
        role.role_code,
      )
  
      setRoleName(
        role.role_name,
      )
  
      setDescription(
        role.description ?? '',
      )
  
      setEditorMode('edit')
      setError(null)
    }
  
  
    function closeEditor() {
      if (saving) {
        return
      }
  
      setEditorMode(null)
      setRoleId(null)
    }
  
  
    async function submitRole(
      event: FormEvent<HTMLFormElement>,
    ) {
      event.preventDefault()
  
      try {
        setSaving(true)
        setError(null)
  
        if (editorMode === 'create') {
          await createRole({
            role_code:
              roleCode.trim(),
            role_name:
              roleName.trim(),
            description:
              description.trim()
              || null,
          })
        } else if (
          editorMode === 'edit'
          && roleId !== null
        ) {
          await updateRole(
            roleId,
            {
              role_name:
                roleName.trim(),
              description:
                description.trim()
                || null,
            },
          )
        } else {
          return
        }
  
        setEditorMode(null)
        setRoleId(null)
  
        await loadRoles()
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
  
  
    async function changeActiveState(
      role: RoleCatalogItem,
    ) {
      if (!role.can_manage) {
        return
      }
  
      const nextState =
        !role.is_active
  
      const confirmed =
        await confirmAction({
          title:
            nextState
              ? 'Enable role?'
              : 'Disable role?',
          message:
            nextState
              ? `Role ${role.role_code} will be enabled.`
              : `Role ${role.role_code} will be disabled.`,
          detail:
            nextState
              ? 'Existing role assignments will become effective again where applicable.'
              : 'The role remains in the catalog and history. Existing assignments are not physically deleted.',
          confirmLabel:
            nextState
              ? 'Enable'
              : 'Disable',
        })
  
      if (!confirmed) {
        return
      }
  
      try {
        setChangingRoleId(
          role.role_id,
        )
  
        setError(null)
  
        await setRoleActive(
          role.role_id,
          nextState,
        )
  
        await loadRoles()
      } catch (stateError) {
        setError(
          errorMessage(
            stateError,
          ),
        )
      } finally {
        setChangingRoleId(null)
      }
    }
  
  
    return (
      <div className="admin-roles-page">
        <header className="workspace-header">
          <div>
            <div className="breadcrumb">
              <Trans i18nKey="batch.tr0363" />
            </div>
  
            <h1>
              <Trans i18nKey="batch.tr0431" />
            </h1>
  
            <p>
              <Trans i18nKey="batch.tr0359" />
            </p>
          </div>
  
          <div className="admin-roles-header-actions">
            <span className="badge badge-accent">
              {roles.length} ROLES
            </span>
  
            <button
              type="button"
              className="admin-primary-button"
              onClick={openCreate}
            >
              <Trans i18nKey="batch.tr0361" />
            </button>
          </div>
        </header>
  
        {
          error
            ? (
              <div className="error">
                <strong>
                  <Trans i18nKey="batch.tr0367" />
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
              <section className="admin-role-editor">
                <div className="admin-role-editor-title">
                  {
                    editorMode === 'create'
                      ?<Trans i18nKey="batch.tr0361" />
                      : `Edit ${roleCode}`
                  }
                </div>
  
                <form
                  className="admin-role-form"
                  onSubmit={submitRole}
                >
                  <label>
                    <span>
                      <Trans i18nKey="batch.tr0368" />
                    </span>
  
                    <input
                      value={roleCode}
                      maxLength={64}
                      required
                      readOnly={
                        editorMode === 'edit'
                      }
                      onChange={
                        event =>
                          setRoleCode(
                            event.target.value
                              .toUpperCase(),
                          )
                      }
                      placeholder="EXAMPLE_ROLE"
                    />
                  </label>
  
                  <label>
                    <span>
                      <Trans i18nKey="batch.tr0369" />
                    </span>
  
                    <input
                      value={roleName}
                      maxLength={200}
                      required
                      onChange={
                        event =>
                          setRoleName(
                            event.target.value,
                          )
                      }
                      placeholder="Business role name"
                    />
                  </label>
  
                  <label className="admin-role-description">
                    <span>
                      <Trans i18nKey="batch.tr0223" />
                    </span>
  
                    <textarea
                      value={description}
                      maxLength={2000}
                      rows={3}
                      onChange={
                        event =>
                          setDescription(
                            event.target.value,
                          )
                      }
                    />
                  </label>
  
                  <div className="admin-role-form-actions">
                    <button
                      type="button"
                      className="admin-secondary-button"
                      disabled={saving}
                      onClick={closeEditor}
                    >
                      <Trans i18nKey="batch.tr0222" />
                    </button>
  
                    <button
                      type="submit"
                      className="admin-primary-button"
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
          loading && roles.length === 0
            ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0366" />
              </div>
            )
            : roles.length === 0
              ? (
                <div className="loading-panel">
                  <Trans i18nKey="batch.tr0186" />
                </div>
              )
              : (
                <div className="data-table-wrapper">
                  <table className="data-table admin-roles-table">
                    <thead>
                      <tr>
                        <th><Trans i18nKey="batch.tr0093" /></th>
                        <th><Trans i18nKey="batch.tr0092" /></th>
                        <th><Trans i18nKey="batch.tr0223" /></th>
                        <th><Trans i18nKey="batch.tr0036" /></th>
                        <th><Trans i18nKey="batch.tr0428" /></th>
                        <th><Trans i18nKey="batch.tr0187" /></th>
                        <th><Trans i18nKey="batch.tr0188" /></th>
                        <th><Trans i18nKey="batch.tr0072" /></th>
                        <th><Trans i18nKey="batch.tr0215" /></th>
                      </tr>
                    </thead>
  
                    <tbody>
                      {
                        roles.map(
                          role => (
                            <tr key={role.role_id}>
                              <td>
                                <strong className="code-value">
                                  {role.role_code}
                                </strong>
  
                                {
                                  role.is_system
                                    ? (
                                      <span className="admin-role-system-label">
                                        SYSTEM
                                      </span>
                                    )
                                    : null
                                }
                              </td>
  
                              <td>
                                <strong>
                                  {role.role_name}
                                </strong>
                              </td>
  
                              <td className="admin-role-description-cell">
                                {role.description ?? ''}
                              </td>
  
                              <td>
                                <span
                                  className={
                                    role.is_active
                                      ? 'badge badge-success'
                                      : 'badge'
                                  }
                                >
                                  {
                                    role.is_active
                                      ? 'ACTIVE'
                                      : 'DISABLED'
                                  }
                                </span>
                              </td>
  
                              <td>
                                {role.assigned_user_count}
                              </td>
  
                              <td>
                                {role.permission_count}
                              </td>
  
                              <td>
                                {role.resource_count}
                              </td>
  
                              <td>
                                {
                                  formatRoleDate(
                                    role.updated_at,
                                  )
                                }
                              </td>
  
                              <td>
                                {
                                  role.can_manage
                                    ? (
                                      <div className="admin-role-row-actions">
                                        <button
                                          type="button"
                                          className="admin-link-button"
                                          onClick={
                                            () =>
                                              openEdit(
                                                role,
                                              )
                                          }
                                        >
                                          <Trans i18nKey="batch.tr0218" />
                                        </button>
  
                                        <button
                                          type="button"
                                          className="admin-link-button"
                                          disabled={
                                            changingRoleId
                                            === role.role_id
                                          }
                                          onClick={
                                            () => {
                                              void changeActiveState(
                                                role,
                                              )
                                            }
                                          }
                                        >
                                          {
                                            changingRoleId
                                            === role.role_id
                                              ?<Trans i18nKey="batch.tr0115" />
                                              : role.is_active
                                                ?<Trans i18nKey="batch.tr0217" />
                                                :<Trans i18nKey="batch.tr0219" />
                                          }
                                        </button>
                                      </div>
                                    )
                                    : (
                                      <span className="admin-role-protected">
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
  
  
  export default AdminRolesPage