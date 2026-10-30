import { Trans } from 'react-i18next'
import {
  useEffect,
  useState,
} from 'react'

import type {
  FormEvent,
} from 'react'

import {
  createIntegrationSourceObject,
  createIntegrationSourceSystem,
  getIntegrationSourceObjects,
  getIntegrationSourceSystems,
  setIntegrationSourceObjectActive,
  setIntegrationSourceSystemActive,
  updateIntegrationSourceObject,
  updateIntegrationSourceSystem,
} from './integrationAdminApi'

import type {
  IntegrationSourceObject,
  IntegrationSourceSystem,
} from './integrationAdminApi'

import {
  confirmAction,
} from './confirmAction'

import {
  formatDateTime,
} from './dateTime'

import AdminIntegrationContracts
  from './AdminIntegrationContracts'
import AdminIntegrationMappings
  from './AdminIntegrationMappings'
import AdminIntegrationDqRules
  from './AdminIntegrationDqRules'
import AdminIntegrationRunControl
  from './AdminIntegrationRunControl'
import AdminIntegrationHistory
  from './AdminIntegrationHistory'

import './AdminIntegrationsPage.css'


type Mode =
  | 'systems'
  | 'objects'
  | 'contracts'
  | 'mappings'
  | 'dq'
  | 'run-control'
  | 'history'


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


function nullable(
  value: string,
): string | null {
  const normalized =
    value.trim()

  return normalized === ''
    ? null
    : normalized
}


function AdminIntegrationsPage() {
  const [
    mode,
    setMode,
  ] = useState<Mode>('systems')

  const [
    systems,
    setSystems,
  ] = useState<IntegrationSourceSystem[]>([])

  const [
    objects,
    setObjects,
  ] = useState<IntegrationSourceObject[]>([])

  const [
    selectedSystemId,
    setSelectedSystemId,
  ] = useState<number | null>(null)

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
    saving,
    setSaving,
  ] = useState(false)

  const [
    changingKey,
    setChangingKey,
  ] = useState<string | null>(null)

  const [
    editingSystem,
    setEditingSystem,
  ] = useState<IntegrationSourceSystem | null>(null)

  const [
    systemCode,
    setSystemCode,
  ] = useState('')

  const [
    systemName,
    setSystemName,
  ] = useState('')

  const [
    sourceType,
    setSourceType,
  ] = useState('POSTGRESQL')

  const [
    systemDescription,
    setSystemDescription,
  ] = useState('')

  const [
    editingObject,
    setEditingObject,
  ] = useState<IntegrationSourceObject | null>(null)

  const [
    objectSystemId,
    setObjectSystemId,
  ] = useState<number | null>(null)

  const [
    sourceSchema,
    setSourceSchema,
  ] = useState('')

  const [
    sourceObject,
    setSourceObject,
  ] = useState('')

  const [
    objectType,
    setObjectType,
  ] = useState('TABLE')

  const [
    loadStrategy,
    setLoadStrategy,
  ] = useState('FULL')

  const [
    watermarkColumn,
    setWatermarkColumn,
  ] = useState('')

  const [
    targetSchema,
    setTargetSchema,
  ] = useState('')

  const [
    targetObject,
    setTargetObject,
  ] = useState('')

  const [
    loadOrder,
    setLoadOrder,
  ] = useState('100')

  const [
    objectDescription,
    setObjectDescription,
  ] = useState('')


  async function loadAll() {
    try {
      setLoading(true)
      setError(null)

      const [
        systemResult,
        objectResult,
      ] = await Promise.all([
        getIntegrationSourceSystems(),
        getIntegrationSourceObjects(),
      ])

      setSystems(systemResult)
      setObjects(objectResult)

      setSelectedSystemId(
        current => {
          if (
            current !== null
            && systemResult.some(
              item =>
                item.source_system_id === current,
            )
          ) {
            return current
          }

          return (
            systemResult[0]?.source_system_id
            ?? null
          )
        },
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

    void Promise.all([
      getIntegrationSourceSystems(),
      getIntegrationSourceObjects(),
    ])
      .then(([
        systemResult,
        objectResult,
      ]) => {
        if (cancelled) {
          return
        }

        setSystems(systemResult)
        setObjects(objectResult)

        setSelectedSystemId(
          current => {
            if (
              current !== null
              && systemResult.some(
                item =>
                  item.source_system_id === current,
              )
            ) {
              return current
            }

            return (
              systemResult[0]?.source_system_id
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
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])


  function closeEditor() {
    if (saving) {
      return
    }

    setEditorMode(null)
    setEditingSystem(null)
    setEditingObject(null)
  }


  function openCreateSystem() {
    setEditingSystem(null)
    setSystemCode('')
    setSystemName('')
    setSourceType('POSTGRESQL')
    setSystemDescription('')
    setEditorMode('create')
    setError(null)
  }


  function openEditSystem(
    item: IntegrationSourceSystem,
  ) {
    setEditingSystem(item)
    setSystemCode(item.code)
    setSystemName(item.name)
    setSourceType(item.source_type)
    setSystemDescription(
      item.description ?? '',
    )
    setEditorMode('edit')
    setError(null)
  }


  function openCreateObject() {
    const defaultSystemId =
      selectedSystemId
      ?? systems[0]?.source_system_id
      ?? null

    setEditingObject(null)
    setObjectSystemId(defaultSystemId)
    setSourceSchema('')
    setSourceObject('')
    setObjectType('TABLE')
    setLoadStrategy('FULL')
    setWatermarkColumn('')
    setTargetSchema('')
    setTargetObject('')
    setLoadOrder('100')
    setObjectDescription('')
    setEditorMode('create')
    setError(null)
  }


  function openEditObject(
    item: IntegrationSourceObject,
  ) {
    setEditingObject(item)
    setObjectSystemId(
      item.source_system_id,
    )
    setSourceSchema(item.source_schema)
    setSourceObject(item.source_object)
    setObjectType(item.object_type)
    setLoadStrategy(item.load_strategy)
    setWatermarkColumn(
      item.watermark_column ?? '',
    )
    setTargetSchema(
      item.target_schema ?? '',
    )
    setTargetObject(
      item.target_object ?? '',
    )
    setLoadOrder(
      String(item.load_order),
    )
    setObjectDescription(
      item.description ?? '',
    )
    setEditorMode('edit')
    setError(null)
  }


  async function submitSystem(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    try {
      setSaving(true)
      setError(null)

      if (editorMode === 'create') {
        await createIntegrationSourceSystem({
          code:
            systemCode.trim().toUpperCase(),
          name:
            systemName.trim(),
          source_type:
            sourceType.trim(),
          description:
            nullable(systemDescription),
        })
      } else if (
        editorMode === 'edit'
        && editingSystem !== null
      ) {
        await updateIntegrationSourceSystem(
          editingSystem.source_system_id,
          {
            name:
              systemName.trim(),
            source_type:
              sourceType.trim(),
            description:
              nullable(systemDescription),
          },
        )
      } else {
        return
      }

      closeEditor()
      await loadAll()
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


  async function submitObject(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    if (objectSystemId === null) {
      setError(
        'Source system is required.',
      )
      return
    }

    const parsedLoadOrder =
      Number.parseInt(
        loadOrder,
        10,
      )

    if (
      !Number.isInteger(parsedLoadOrder)
      || parsedLoadOrder <= 0
    ) {
      setError(
        'Load order must be a positive integer.',
      )
      return
    }

    try {
      setSaving(true)
      setError(null)

      if (editorMode === 'create') {
        await createIntegrationSourceObject({
          source_system_id:
            objectSystemId,
          source_schema:
            sourceSchema.trim(),
          source_object:
            sourceObject.trim(),
          object_type:
            objectType,
          load_strategy:
            loadStrategy,
          watermark_column:
            nullable(watermarkColumn),
          target_schema:
            nullable(targetSchema),
          target_object:
            nullable(targetObject),
          load_order:
            parsedLoadOrder,
          description:
            nullable(objectDescription),
        })
      } else if (
        editorMode === 'edit'
        && editingObject !== null
      ) {
        await updateIntegrationSourceObject(
          editingObject.source_object_id,
          {
            object_type:
              objectType,
            load_strategy:
              loadStrategy,
            watermark_column:
              nullable(watermarkColumn),
            target_schema:
              nullable(targetSchema),
            target_object:
              nullable(targetObject),
            load_order:
              parsedLoadOrder,
            description:
              nullable(objectDescription),
          },
        )
      } else {
        return
      }

      closeEditor()
      await loadAll()
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


  async function changeSystemState(
    item: IntegrationSourceSystem,
  ) {
    const nextState =
      !item.is_active

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Enable source system?'
            : 'Disable source system?',
        message:
          `${item.code} will be ${
            nextState
              ? 'enabled'
              : 'disabled'
          }.`,
        detail:
          'The source system remains in configuration history. No integration metadata is physically deleted.',
        confirmLabel:
          nextState
            ? 'Enable'
            : 'Disable',
      })

    if (!confirmed) {
      return
    }

    const key =
      `system:${item.source_system_id}`

    try {
      setChangingKey(key)
      setError(null)

      await setIntegrationSourceSystemActive(
        item.source_system_id,
        nextState,
      )

      await loadAll()
    } catch (stateError) {
      setError(
        errorMessage(
          stateError,
        ),
      )
    } finally {
      setChangingKey(null)
    }
  }


  async function changeObjectState(
    item: IntegrationSourceObject,
  ) {
    const nextState =
      !item.is_active

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Enable source object?'
            : 'Disable source object?',
        message:
          `${item.source_schema}.${item.source_object} will be ${
            nextState
              ? 'enabled'
              : 'disabled'
          }.`,
        detail:
          'The source object remains in configuration history. No source or DWH data is physically deleted.',
        confirmLabel:
          nextState
            ? 'Enable'
            : 'Disable',
      })

    if (!confirmed) {
      return
    }

    const key =
      `object:${item.source_object_id}`

    try {
      setChangingKey(key)
      setError(null)

      await setIntegrationSourceObjectActive(
        item.source_object_id,
        nextState,
      )

      await loadAll()
    } catch (stateError) {
      setError(
        errorMessage(
          stateError,
        ),
      )
    } finally {
      setChangingKey(null)
    }
  }


  const filteredObjects =
    selectedSystemId === null
      ? objects
      : objects.filter(
          item =>
            item.source_system_id
            === selectedSystemId,
        )


  return (
    <section className="admin-integrations-page">
      <header className="workspace-header">
        <div>
          <div className="breadcrumb">
            <Trans i18nKey="batch.tr0194" />
          </div>

          <h1>
            <Trans i18nKey="batch.tr0429" />
          </h1>

          <p>
            <Trans i18nKey="batch.tr0209" />
          </p>
        </div>

        <div className="admin-integrations-summary">
          <span className="badge badge-accent">
            {systems.length} SYSTEMS
          </span>

          <span className="badge">
            {objects.length} OBJECTS
          </span>
        </div>
      </header>

      <nav className="admin-integrations-tabs">
        <button
          type="button"
          className={
            mode === 'systems'
              ? 'admin-integrations-tab active'
              : 'admin-integrations-tab'
          }
          onClick={
            () => {
              setMode('systems')
              closeEditor()
            }
          }
        >
          <Trans i18nKey="batch.tr0211" />
        </button>

        <button
          type="button"
          className={
            mode === 'objects'
              ? 'admin-integrations-tab active'
              : 'admin-integrations-tab'
          }
          onClick={
            () => {
              setMode('objects')
              closeEditor()
            }
          }
        >
          <Trans i18nKey="batch.tr0208" />
        </button>

        <button
          type="button"
          className={
            mode === 'contracts'
              ? 'admin-integrations-tab active'
              : 'admin-integrations-tab'
          }
          onClick={
            () => {
              setMode('contracts')
              closeEditor()
            }
          }
        >
          <Trans i18nKey="batch.tr0073" />
        </button>

        <button
          type="button"
          className={
            mode === 'mappings'
              ? 'admin-integrations-tab active'
              : 'admin-integrations-tab'
          }
          onClick={
            () => {
              setMode('mappings')
              closeEditor()
            }
          }
        >
          <Trans i18nKey="batch.tr0074" />
        </button>

        <button
          type="button"
          className={
            mode === 'dq'
              ? 'admin-integrations-tab active'
              : 'admin-integrations-tab'
          }
          onClick={
            () => {
              setMode('dq')
              closeEditor()
            }
          }
        >
          <Trans i18nKey="batch.tr0229" />
        </button>

        <button
          type="button"
          className={
            mode === 'run-control'
              ? 'admin-integrations-tab active'
              : 'admin-integrations-tab'
          }
          onClick={
            () => {
              setMode('run-control')
              closeEditor()
            }
          }
        >
          <Trans i18nKey="batch.tr0207" />
        </button>

        <button
          type="button"
          className={
            mode === 'history'
              ? 'admin-integrations-tab active'
              : 'admin-integrations-tab'
          }
          onClick={
            () => {
              setMode('history')
              closeEditor()
            }
          }
        >
          <Trans i18nKey="batch.tr0197" />
        </button>
      </nav>

      {
        error
          ? (
              <div className="error">
                <strong>
                  <Trans i18nKey="batch.tr0198" />
                </strong>

                <span>
                  {error}
                </span>
              </div>
            )
          : null
      }

      {
        mode === 'systems'
          ? (
              <>
                <div className="admin-integrations-toolbar">
                  <div>
                    <Trans i18nKey="batch.tr0204" />
                  </div>

                  <button
                    type="button"
                    className="admin-primary-button"
                    onClick={openCreateSystem}
                  >
                    <Trans i18nKey="batch.tr0191" />
                  </button>
                </div>

                {
                  editorMode
                    ? (
                        <section className="admin-integrations-editor">
                          <h3>
                            {
                              editorMode === 'create'
                                ?<Trans i18nKey="batch.tr0191" />
                                : `Edit ${systemCode}`
                            }
                          </h3>

                          <form
                            className="admin-integrations-form"
                            onSubmit={submitSystem}
                          >
                            <label>
                              <span><Trans i18nKey="batch.tr0093" /></span>
                              <input
                                required
                                maxLength={64}
                                readOnly={
                                  editorMode === 'edit'
                                }
                                value={systemCode}
                                onChange={
                                  event =>
                                    setSystemCode(
                                      event.target.value
                                        .toUpperCase(),
                                    )
                                }
                              />
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0092" /></span>
                              <input
                                required
                                value={systemName}
                                onChange={
                                  event =>
                                    setSystemName(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0230" /></span>
                              <input
                                required
                                value={sourceType}
                                onChange={
                                  event =>
                                    setSourceType(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label className="wide">
                              <span><Trans i18nKey="batch.tr0223" /></span>
                              <textarea
                                rows={3}
                                value={systemDescription}
                                onChange={
                                  event =>
                                    setSystemDescription(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <div className="admin-integrations-form-actions wide">
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
                  loading && systems.length === 0
                    ? (
                        <div className="loading-panel">
                          <Trans i18nKey="batch.tr0203" />
                        </div>
                      )
                    : (
                        <div className="data-table-wrapper">
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th><Trans i18nKey="batch.tr0093" /></th>
                                <th><Trans i18nKey="batch.tr0092" /></th>
                                <th><Trans i18nKey="batch.tr0185" /></th>
                                <th><Trans i18nKey="batch.tr0206" /></th>
                                <th><Trans i18nKey="batch.tr0036" /></th>
                                <th><Trans i18nKey="batch.tr0072" /></th>
                                <th><Trans i18nKey="batch.tr0215" /></th>
                              </tr>
                            </thead>

                            <tbody>
                              {
                                systems.map(
                                  item => {
                                    const key =
                                      `system:${item.source_system_id}`

                                    return (
                                      <tr key={item.source_system_id}>
                                        <td>
                                          <strong className="code-value">
                                            {item.code}
                                          </strong>
                                        </td>

                                        <td>
                                          {item.name}
                                        </td>

                                        <td>
                                          {item.source_type}
                                        </td>

                                        <td>
                                          {item.object_count}
                                        </td>

                                        <td>
                                          <span
                                            className={
                                              item.is_active
                                                ? 'badge badge-success'
                                                : 'badge'
                                            }
                                          >
                                            {
                                              item.is_active
                                                ? 'ACTIVE'
                                                : 'DISABLED'
                                            }
                                          </span>
                                        </td>

                                        <td>
                                          {
                                            formatDateTime(
                                              item.updated_at,
                                            )
                                          }
                                        </td>

                                        <td>
                                          <div className="admin-integration-row-actions">
                                            <button
                                              type="button"
                                              className="admin-link-button"
                                              onClick={
                                                () =>
                                                  openEditSystem(
                                                    item,
                                                  )
                                              }
                                            >
                                              <Trans i18nKey="batch.tr0218" />
                                            </button>

                                            <button
                                              type="button"
                                              className="admin-link-button"
                                              disabled={
                                                changingKey !== null
                                              }
                                              onClick={
                                                () => {
                                                  void changeSystemState(
                                                    item,
                                                  )
                                                }
                                              }
                                            >
                                              {
                                                changingKey === key
                                                  ?<Trans i18nKey="batch.tr0115" />
                                                  : item.is_active
                                                    ?<Trans i18nKey="batch.tr0217" />
                                                    :<Trans i18nKey="batch.tr0219" />
                                              }
                                            </button>
                                          </div>
                                        </td>
                                      </tr>
                                    )
                                  },
                                )
                              }
                            </tbody>
                          </table>
                        </div>
                      )
                }
              </>
            )
          : mode === 'objects'
            ? (
              <>
                <div className="admin-integrations-toolbar">
                  <label className="admin-integrations-filter">
                    <span>
                      <Trans i18nKey="batch.tr0227" />
                    </span>

                    <select
                      value={
                        selectedSystemId
                        ?? ''
                      }
                      onChange={
                        event => {
                          const value =
                            event.target.value

                          setSelectedSystemId(
                            value === ''
                              ? null
                              : Number(value),
                          )
                        }
                      }
                    >
                      <option value="">
                        <Trans i18nKey="batch.tr0226" />
                      </option>

                      {
                        systems.map(
                          item => (
                            <option
                              key={item.source_system_id}
                              value={item.source_system_id}
                            >
                              {item.code}  {item.name}
                            </option>
                          ),
                        )
                      }
                    </select>
                  </label>

                  <button
                    type="button"
                    className="admin-primary-button"
                    disabled={systems.length === 0}
                    onClick={openCreateObject}
                  >
                    <Trans i18nKey="batch.tr0190" />
                  </button>
                </div>

                {
                  editorMode
                    ? (
                        <section className="admin-integrations-editor">
                          <h3>
                            {
                              editorMode === 'create'
                                ?<Trans i18nKey="batch.tr0190" />
                                : (
                                    `Edit ${sourceSchema}.`
                                    + sourceObject
                                  )
                            }
                          </h3>

                          <form
                            className="admin-integrations-form"
                            onSubmit={submitObject}
                          >
                            <label>
                              <span><Trans i18nKey="batch.tr0227" /></span>
                              <select
                                required
                                disabled={
                                  editorMode === 'edit'
                                }
                                value={
                                  objectSystemId
                                  ?? ''
                                }
                                onChange={
                                  event =>
                                    setObjectSystemId(
                                      Number(
                                        event.target.value,
                                      ),
                                    )
                                }
                              >
                                {
                                  systems.map(
                                    item => (
                                      <option
                                        key={item.source_system_id}
                                        value={item.source_system_id}
                                      >
                                        {item.code}
                                      </option>
                                    ),
                                  )
                                }
                              </select>
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0216" /></span>
                              <input
                                required
                                readOnly={
                                  editorMode === 'edit'
                                }
                                value={sourceSchema}
                                onChange={
                                  event =>
                                    setSourceSchema(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0224" /></span>
                              <input
                                required
                                readOnly={
                                  editorMode === 'edit'
                                }
                                value={sourceObject}
                                onChange={
                                  event =>
                                    setSourceObject(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0205" /></span>
                              <select
                                value={objectType}
                                onChange={
                                  event =>
                                    setObjectType(
                                      event.target.value,
                                    )
                                }
                              >
                                <option value="TABLE">
                                  TABLE
                                </option>

                                <option value="VIEW">
                                  VIEW
                                </option>
                              </select>
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0201" /></span>
                              <select
                                value={loadStrategy}
                                onChange={
                                  event =>
                                    setLoadStrategy(
                                      event.target.value,
                                    )
                                }
                              >
                                <option value="FULL">
                                  FULL
                                </option>
                                <option value="INCREMENTAL">
                                  INCREMENTAL
                                </option>
                                <option value="INCREMENTAL_OVERLAP">
                                  INCREMENTAL_OVERLAP
                                </option>
                                <option value="SNAPSHOT">
                                  SNAPSHOT
                                </option>
                              </select>
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0214" /></span>
                              <input
                                value={watermarkColumn}
                                onChange={
                                  event =>
                                    setWatermarkColumn(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0228" /></span>
                              <input
                                value={targetSchema}
                                onChange={
                                  event =>
                                    setTargetSchema(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0225" /></span>
                              <input
                                value={targetObject}
                                onChange={
                                  event =>
                                    setTargetObject(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label>
                              <span><Trans i18nKey="batch.tr0199" /></span>
                              <input
                                type="number"
                                min={1}
                                required
                                value={loadOrder}
                                onChange={
                                  event =>
                                    setLoadOrder(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <label className="wide">
                              <span><Trans i18nKey="batch.tr0223" /></span>
                              <textarea
                                rows={3}
                                value={objectDescription}
                                onChange={
                                  event =>
                                    setObjectDescription(
                                      event.target.value,
                                    )
                                }
                              />
                            </label>

                            <div className="admin-integrations-form-actions wide">
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
                  loading && objects.length === 0
                    ? (
                        <div className="loading-panel">
                          <Trans i18nKey="batch.tr0202" />
                        </div>
                      )
                    : (
                        <div className="data-table-wrapper">
                          <table className="data-table admin-integration-object-table">
                            <thead>
                              <tr>
                                <th><Trans i18nKey="batch.tr0042" /></th>
                                <th><Trans i18nKey="batch.tr0185" /></th>
                                <th><Trans i18nKey="batch.tr0231" /></th>
                                <th><Trans i18nKey="batch.tr0232" /></th>
                                <th><Trans i18nKey="batch.tr0038" /></th>
                                <th><Trans i18nKey="batch.tr0071" /></th>
                                <th><Trans i18nKey="batch.tr0036" /></th>
                                <th><Trans i18nKey="batch.tr0215" /></th>
                              </tr>
                            </thead>

                            <tbody>
                              {
                                filteredObjects.map(
                                  item => {
                                    const key =
                                      `object:${item.source_object_id}`

                                    return (
                                      <tr key={item.source_object_id}>
                                        <td>
                                          <strong>
                                            {item.source_system_code}
                                          </strong>
                                          <br />
                                          <code>
                                            {item.source_schema}.{item.source_object}
                                          </code>
                                        </td>

                                        <td>
                                          {item.object_type}
                                        </td>

                                        <td>
                                          {item.load_strategy}
                                        </td>

                                        <td>
                                          {item.watermark_column ?? ''}
                                        </td>

                                        <td>
                                          {
                                            item.target_schema
                                            && item.target_object
                                              ? (
                                                  `${item.target_schema}.`
                                                  + item.target_object
                                                )
                                              : ''
                                          }
                                        </td>

                                        <td>
                                          {item.load_order}
                                        </td>

                                        <td>
                                          <span
                                            className={
                                              item.is_active
                                                ? 'badge badge-success'
                                                : 'badge'
                                            }
                                          >
                                            {
                                              item.is_active
                                                ? 'ACTIVE'
                                                : 'DISABLED'
                                            }
                                          </span>
                                        </td>

                                        <td>
                                          <div className="admin-integration-row-actions">
                                            <button
                                              type="button"
                                              className="admin-link-button"
                                              onClick={
                                                () =>
                                                  openEditObject(
                                                    item,
                                                  )
                                              }
                                            >
                                              <Trans i18nKey="batch.tr0218" />
                                            </button>

                                            <button
                                              type="button"
                                              className="admin-link-button"
                                              disabled={
                                                changingKey !== null
                                              }
                                              onClick={
                                                () => {
                                                  void changeObjectState(
                                                    item,
                                                  )
                                                }
                                              }
                                            >
                                              {
                                                changingKey === key
                                                  ?<Trans i18nKey="batch.tr0115" />
                                                  : item.is_active
                                                    ?<Trans i18nKey="batch.tr0217" />
                                                    :<Trans i18nKey="batch.tr0219" />
                                              }
                                            </button>
                                          </div>
                                        </td>
                                      </tr>
                                    )
                                  },
                                )
                              }
                            </tbody>
                          </table>
                        </div>
                      )
                }
              </>
            )
            : null
      }

      {
        mode === 'contracts'
          ? (
              <AdminIntegrationContracts />
            )
          : null
      }

      {
        mode === 'mappings'
          ? (
              <AdminIntegrationMappings />
            )
          : null
      }

      {
        mode === 'dq'
          ? (
              <AdminIntegrationDqRules />
            )
          : null
      }

      {
        mode === 'run-control'
          ? (
              <AdminIntegrationRunControl />
            )
          : null
      }

      {
        mode === 'history'
          ? (
              <AdminIntegrationHistory />
            )
          : null
      }
    </section>
  )
}


export default AdminIntegrationsPage
