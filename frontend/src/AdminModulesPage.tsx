import { Trans } from 'react-i18next'
import {
  useEffect,
  useState,
} from 'react'

import type {
  FormEvent,
} from 'react'

import {
  createModule,
  getModules,
  setModuleActive,
  updateModule,
} from './moduleCatalogApi'

import type {
  ModuleCatalogItem,
} from './moduleCatalogApi'

import { confirmAction } from './confirmAction'
import { formatDateTime } from './dateTime'

import './AdminModulesPage.css'


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


function formatModuleDate(
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


function AdminModulesPage() {
  const [
    modules,
    setModules,
  ] = useState<ModuleCatalogItem[]>([])

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
    moduleCode,
    setModuleCode,
  ] = useState('')

  const [
    moduleName,
    setModuleName,
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
    changingCode,
    setChangingCode,
  ] = useState<string | null>(null)


  async function loadModules() {
    try {
      setLoading(true)
      setError(null)

      const result =
        await getModules()

      setModules(
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

    void getModules()
      .then(result => {
        if (!cancelled) {
          setModules(result)
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
    setModuleCode('')
    setModuleName('')
    setDescription('')
    setEditorMode('create')
    setError(null)
  }


  function openEdit(
    module: ModuleCatalogItem,
  ) {
    setModuleCode(
      module.module_code,
    )

    setModuleName(
      module.module_name,
    )

    setDescription(
      module.description ?? '',
    )

    setEditorMode('edit')
    setError(null)
  }


  function closeEditor() {
    if (saving) {
      return
    }

    setEditorMode(null)
  }


  async function submitModule(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    try {
      setSaving(true)
      setError(null)

      if (editorMode === 'create') {
        await createModule({
          module_code:
            moduleCode.trim(),
          module_name:
            moduleName.trim(),
          description:
            description.trim()
            || null,
        })
      } else if (editorMode === 'edit') {
        await updateModule(
          moduleCode,
          {
            module_name:
              moduleName.trim(),
            description:
              description.trim()
              || null,
          },
        )
      } else {
        return
      }

      setEditorMode(null)

      await loadModules()
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
    module: ModuleCatalogItem,
  ) {
    const nextState =
      !module.is_active

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Enable module?'
            : 'Disable module?',
        message:
          nextState
            ? `Module ${module.module_code} will be enabled.`
            : `Module ${module.module_code} will be disabled.`,
        detail:
          nextState
            ? 'The module will become available for administration and access assignment again.'
            : 'Disabled modules remain in the catalog and history. No data is physically deleted.',
        confirmLabel:
          nextState
            ? 'Enable'
            : 'Disable',
      })

    if (!confirmed) {
      return
    }

    try {
      setChangingCode(
        module.module_code,
      )

      setError(null)

      await setModuleActive(
        module.module_code,
        nextState,
      )

      await loadModules()
    } catch (stateError) {
      setError(
        errorMessage(
          stateError,
        ),
      )
    } finally {
      setChangingCode(null)
    }
  }


  return (
    <div className="admin-modules-page">
      <header className="workspace-header">
        <div>
          <div className="breadcrumb">
            <Trans i18nKey="batch.tr0300" />
          </div>

          <h1>
            <Trans i18nKey="batch.tr0430" />
          </h1>

          <p>
            <Trans i18nKey="batch.tr0295" />
          </p>
        </div>

        <div className="admin-modules-header-actions">
          <span className="badge badge-accent">
            {modules.length} MODULES
          </span>

          <button
            type="button"
            className="admin-primary-button"
            onClick={openCreate}
          >
            <Trans i18nKey="batch.tr0297" />
          </button>
        </div>
      </header>

      {
        error
          ? (
            <div className="error">
              <strong>
                <Trans i18nKey="batch.tr0303" />
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
            <section className="admin-module-editor">
              <div className="admin-module-editor-title">
                {
                  editorMode === 'create'
                    ?<Trans i18nKey="batch.tr0297" />
                    : `Edit ${moduleCode}`
                }
              </div>

              <form
                className="admin-module-form"
                onSubmit={submitModule}
              >
                <label>
                  <span>
                    <Trans i18nKey="batch.tr0304" />
                  </span>

                  <input
                    value={moduleCode}
                    maxLength={64}
                    required
                    readOnly={
                      editorMode === 'edit'
                    }
                    onChange={
                      event =>
                        setModuleCode(
                          event.target.value
                            .toUpperCase(),
                        )
                    }
                    placeholder="EXAMPLE_MODULE"
                  />
                </label>

                <label>
                  <span>
                    <Trans i18nKey="batch.tr0305" />
                  </span>

                  <input
                    value={moduleName}
                    maxLength={200}
                    required
                    onChange={
                      event =>
                        setModuleName(
                          event.target.value,
                        )
                    }
                    placeholder="Business module name"
                  />
                </label>

                <label className="admin-module-description">
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

                <div className="admin-module-form-actions">
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
        loading && modules.length === 0
          ? (
            <div className="loading-panel">
              <Trans i18nKey="batch.tr0302" />
            </div>
          )
          : (
            <div className="data-table-wrapper">
              <table className="data-table admin-modules-table">
                <thead>
                  <tr>
                    <th><Trans i18nKey="batch.tr0093" /></th>
                    <th><Trans i18nKey="batch.tr0092" /></th>
                    <th><Trans i18nKey="batch.tr0223" /></th>
                    <th><Trans i18nKey="batch.tr0036" /></th>
                    <th><Trans i18nKey="batch.tr0307" /></th>
                    <th><Trans i18nKey="batch.tr0072" /></th>
                    <th><Trans i18nKey="batch.tr0215" /></th>
                  </tr>
                </thead>

                <tbody>
                  {
                    modules.map(
                      module => (
                        <tr key={module.resource_id}>
                          <td>
                            <strong className="code-value">
                              {module.module_code}
                            </strong>
                          </td>

                          <td>
                            <strong>
                              {module.module_name}
                            </strong>
                          </td>

                          <td className="admin-module-description-cell">
                            {module.description ?? ''}
                          </td>

                          <td>
                            <span
                              className={
                                module.is_active
                                  ? 'badge badge-success'
                                  : 'badge'
                              }
                            >
                              {
                                module.is_active
                                  ? 'ACTIVE'
                                  : 'DISABLED'
                              }
                            </span>
                          </td>

                          <td>
                            {
                              module.is_system
                                ? 'YES'
                                : 'NO'
                            }
                          </td>

                          <td>
                            {
                              formatModuleDate(
                                module.updated_at,
                              )
                            }
                          </td>

                          <td>
                            <div className="admin-module-row-actions">
                              <button
                                type="button"
                                className="admin-link-button"
                                onClick={
                                  () =>
                                    openEdit(
                                      module,
                                    )
                                }
                              >
                                <Trans i18nKey="batch.tr0218" />
                              </button>

                              <button
                                type="button"
                                className="admin-link-button"
                                disabled={
                                  changingCode
                                  === module.module_code
                                }
                                onClick={
                                  () => {
                                    void changeActiveState(
                                      module,
                                    )
                                  }
                                }
                              >
                                {
                                  changingCode
                                  === module.module_code
                                    ?<Trans i18nKey="batch.tr0115" />
                                    : module.is_active
                                      ?<Trans i18nKey="batch.tr0217" />
                                      :<Trans i18nKey="batch.tr0219" />
                                }
                              </button>
                            </div>
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


export default AdminModulesPage