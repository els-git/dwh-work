import { Trans } from 'react-i18next'
import {
  useEffect,
  useState,
} from 'react'

import type {
  FormEvent,
} from 'react'

import {
  createIntegrationMapping,
  getIntegrationAdminMappings,
  getIntegrationSourceSystems,
  setIntegrationMappingActive,
  updateIntegrationMapping,
} from './integrationAdminApi'

import type {
  IntegrationMappingAdmin,
  IntegrationMappingSourcePayload,
  IntegrationSourceSystem,
} from './integrationAdminApi'

import {
  confirmAction,
} from './confirmAction'

import {
  useAuth,
} from './authContext'


function nullable(
  value: string,
): string | null {
  const normalized = value.trim()

  return normalized === ''
    ? null
    : normalized
}


function errorMessage(
  error: unknown,
): string {
  return error instanceof Error
    ? error.message
    : 'Unknown error'
}


function emptySource():
IntegrationMappingSourcePayload {
  return {
    source_layer: 'SOURCE',
    source_schema: '',
    source_object: '',
    source_column: '',
    source_role: 'INPUT',
  }
}


export default function AdminIntegrationMappings() {
  const {
    hasPermission,
  } = useAuth()

  const canManage =
    hasPermission(
      'ADMIN_MAPPING_MANAGE',
    )

  const [
    mappings,
    setMappings,
  ] = useState<IntegrationMappingAdmin[]>([])

  const [
    systems,
    setSystems,
  ] = useState<IntegrationSourceSystem[]>([])

  const [
    filterSystem,
    setFilterSystem,
  ] = useState('')

  const [
    loading,
    setLoading,
  ] = useState(true)

  const [
    error,
    setError,
  ] = useState<string | null>(null)

  const [
    creating,
    setCreating,
  ] = useState(false)

  const [
    editing,
    setEditing,
  ] = useState<IntegrationMappingAdmin | null>(null)

  const [
    saving,
    setSaving,
  ] = useState(false)

  const [
    changingId,
    setChangingId,
  ] = useState<number | null>(null)

  const [
    sourceSystemCode,
    setSourceSystemCode,
  ] = useState('')

  const [
    mappingStage,
    setMappingStage,
  ] = useState('SOURCE_TO_STG')

  const [
    transformName,
    setTransformName,
  ] = useState('')

  const [
    mappingPosition,
    setMappingPosition,
  ] = useState('1')

  const [
    stgSchema,
    setStgSchema,
  ] = useState('stg')

  const [
    stgTable,
    setStgTable,
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
    targetColumn,
    setTargetColumn,
  ] = useState('')

  const [
    mappingType,
    setMappingType,
  ] = useState('DIRECT')

  const [
    transformExpression,
    setTransformExpression,
  ] = useState('')

  const [
    expectedPgType,
    setExpectedPgType,
  ] = useState('')

  const [
    isActive,
    setIsActive,
  ] = useState(true)

  const [
    changeReason,
    setChangeReason,
  ] = useState('')

  const [
    sources,
    setSources,
  ] = useState<IntegrationMappingSourcePayload[]>([
    emptySource(),
  ])


  async function load() {
    try {
      setLoading(true)
      setError(null)

      const [
        mappingResult,
        systemResult,
      ] = await Promise.all([
        getIntegrationAdminMappings(
          filterSystem || undefined,
        ),
        getIntegrationSourceSystems(),
      ])

      setMappings(mappingResult)
      setSystems(systemResult)
    } catch (loadError) {
      setError(
        errorMessage(loadError),
      )
    } finally {
      setLoading(false)
    }
  }


  useEffect(() => {
    let cancelled = false

    void Promise.all([
      getIntegrationAdminMappings(
        filterSystem || undefined,
      ),
      getIntegrationSourceSystems(),
    ])
      .then(([
        mappingResult,
        systemResult,
      ]) => {
        if (cancelled) {
          return
        }

        setMappings(mappingResult)
        setSystems(systemResult)
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
  }, [filterSystem])


  function closeEditor() {
    setCreating(false)
    setEditing(null)
  }


  function openCreate() {
    if (!canManage) {
      return
    }

    setCreating(true)
    setEditing(null)
    setSourceSystemCode(
      filterSystem
      || systems[0]?.code
      || '',
    )
    setMappingStage('SOURCE_TO_STG')
    setTransformName('')
    setMappingPosition('1')
    setStgSchema('stg')
    setStgTable('')
    setTargetSchema('')
    setTargetObject('')
    setTargetColumn('')
    setMappingType('DIRECT')
    setTransformExpression('')
    setExpectedPgType('')
    setIsActive(true)
    setChangeReason('')
    setSources([
      emptySource(),
    ])
    setError(null)
  }


  function openEdit(
    item: IntegrationMappingAdmin,
  ) {
    if (!canManage) {
      return
    }

    setCreating(false)
    setEditing(item)
    setSourceSystemCode(
      item.source_system_code,
    )
    setMappingStage(
      item.mapping_stage,
    )
    setTransformName(
      item.transform_name,
    )
    setMappingPosition(
      String(item.mapping_position),
    )
    setStgSchema(item.stg_schema)
    setStgTable(item.stg_table)
    setTargetSchema(item.target_schema)
    setTargetObject(item.target_object)
    setTargetColumn(item.target_column)
    setMappingType(item.mapping_type)
    setTransformExpression(
      item.transform_expression ?? '',
    )
    setExpectedPgType(
      item.expected_pg_type ?? '',
    )
    setIsActive(item.is_active)
    setChangeReason('')
    setSources(
      item.sources.map(
        source => ({
          source_layer:
            source.source_layer,
          source_schema:
            source.source_schema,
          source_object:
            source.source_object,
          source_column:
            source.source_column,
          source_role:
            source.source_role,
        }),
      ),
    )
    setError(null)
  }


  function changeSource(
    index: number,
    field: keyof IntegrationMappingSourcePayload,
    value: string,
  ) {
    setSources(
      current =>
        current.map(
          (item, itemIndex) =>
            itemIndex === index
              ? {
                  ...item,
                  [field]: value,
                }
              : item,
        ),
    )
  }


  function removeSource(
    index: number,
  ) {
    setSources(
      current =>
        current.filter(
          (_, itemIndex) =>
            itemIndex !== index,
        ),
    )
  }


  async function submit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    const position =
      Number.parseInt(
        mappingPosition,
        10,
      )

    if (
      !Number.isInteger(position)
      || position < 1
    ) {
      setError(
        'Mapping position must be a positive integer.',
      )
      return
    }

    const normalizedSources =
      sources.map(
        source => ({
          source_layer:
            source.source_layer,
          source_schema:
            source.source_schema.trim(),
          source_object:
            source.source_object.trim(),
          source_column:
            source.source_column.trim(),
          source_role:
            source.source_role.trim()
            || 'INPUT',
        }),
      )

    try {
      setSaving(true)
      setError(null)

      if (creating) {
        await createIntegrationMapping({
          source_system_code:
            sourceSystemCode,
          mapping_stage:
            mappingStage,
          transform_name:
            transformName.trim(),
          mapping_position:
            position,
          stg_schema:
            stgSchema.trim(),
          stg_table:
            stgTable.trim(),
          target_schema:
            targetSchema.trim(),
          target_object:
            targetObject.trim(),
          target_column:
            targetColumn.trim(),
          mapping_type:
            mappingType,
          transform_expression:
            nullable(
              transformExpression,
            ),
          expected_pg_type:
            nullable(
              expectedPgType,
            ),
          is_active:
            isActive,
          sources:
            normalizedSources,
          change_reason:
            nullable(changeReason),
        })
      } else if (editing) {
        await updateIntegrationMapping(
          editing.mapping_id,
          {
            transform_name:
              transformName.trim(),
            mapping_position:
              position,
            stg_schema:
              stgSchema.trim(),
            stg_table:
              stgTable.trim(),
            mapping_type:
              mappingType,
            transform_expression:
              nullable(
                transformExpression,
              ),
            expected_pg_type:
              nullable(
                expectedPgType,
              ),
            is_active:
              isActive,
            sources:
              normalizedSources,
            change_reason:
              nullable(changeReason),
          },
        )
      }

      closeEditor()
      await load()
    } catch (saveError) {
      setError(
        errorMessage(saveError),
      )
    } finally {
      setSaving(false)
    }
  }


  async function changeState(
    item: IntegrationMappingAdmin,
  ) {
    if (!canManage) {
      return
    }

    const nextState =
      !item.is_active

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Enable mapping?'
            : 'Disable mapping?',
        message:
          `${item.target_schema}.${item.target_object}.${item.target_column}`,
        detail:
          'The change creates a new mapping version. Previous versions remain in history.',
        confirmLabel:
          nextState
            ? 'Enable'
            : 'Disable',
      })

    if (!confirmed) {
      return
    }

    try {
      setChangingId(
        item.mapping_id,
      )
      setError(null)

      await setIntegrationMappingActive(
        item.mapping_id,
        nextState,
      )

      await load()
    } catch (stateError) {
      setError(
        errorMessage(stateError),
      )
    } finally {
      setChangingId(null)
    }
  }


  return (
    <div className="integration-admin-panel">
      <div className="admin-integrations-toolbar">
        <label className="admin-integrations-filter">
          <span><Trans i18nKey="batch.tr0227" /></span>

          <select
            value={filterSystem}
            onChange={
              event => {
                setLoading(true)
                setError(null)
                setFilterSystem(
                  event.target.value,
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
                    value={item.code}
                  >
                    {item.code}  {item.name}
                  </option>
                ),
              )
            }
          </select>
        </label>

        {
          canManage
            ? (
                <button
                  type="button"
                  className="admin-primary-button"
                  onClick={openCreate}
                >
                  <Trans i18nKey="batch.tr0373" />
                </button>
              )
            : (
                <span className="integration-admin-readonly">
                  <Trans i18nKey="batch.tr0385" />
                </span>
              )
        }
      </div>

      {
        error
          ? (
              <div className="error">
                <strong><Trans i18nKey="batch.tr0378" /></strong>
                <span>{error}</span>
              </div>
            )
          : null
      }

      {
        creating || editing
          ? (
              <section className="admin-integrations-editor">
                <h3>
                  {
                    creating
                      ?<Trans i18nKey="batch.tr0373" />
                      : (
                          `Edit mapping #`
                          + editing?.mapping_id
                        )
                  }
                </h3>

                <form
                  className="admin-integrations-form"
                  onSubmit={submit}
                >
                  <label>
                    <span><Trans i18nKey="batch.tr0227" /></span>
                    <select
                      disabled={!creating}
                      value={sourceSystemCode}
                      onChange={
                        event =>
                          setSourceSystemCode(
                            event.target.value,
                          )
                      }
                    >
                      {
                        systems.map(
                          item => (
                            <option
                              key={item.source_system_id}
                              value={item.code}
                            >
                              {item.code}
                            </option>
                          ),
                        )
                      }
                    </select>
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0094" /></span>
                    <select
                      disabled={!creating}
                      value={mappingStage}
                      onChange={
                        event =>
                          setMappingStage(
                            event.target.value,
                          )
                      }
                    >
                      <option value="SOURCE_TO_STG">
                        SOURCE_TO_STG
                      </option>
                      <option value="STG_TO_ODS">
                        STG_TO_ODS
                      </option>
                    </select>
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0379" /></span>
                    <input
                      type="number"
                      min={1}
                      required
                      value={mappingPosition}
                      onChange={
                        event =>
                          setMappingPosition(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0394" /></span>
                    <input
                      required
                      value={transformName}
                      onChange={
                        event =>
                          setTransformName(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0390" /></span>
                    <input
                      required
                      value={stgSchema}
                      onChange={
                        event =>
                          setStgSchema(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0292" /></span>
                    <input
                      required
                      value={stgTable}
                      onChange={
                        event =>
                          setStgTable(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0228" /></span>
                    <input
                      required
                      readOnly={!creating}
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
                      required
                      readOnly={!creating}
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
                    <span><Trans i18nKey="batch.tr0052" /></span>
                    <input
                      required
                      readOnly={!creating}
                      value={targetColumn}
                      onChange={
                        event =>
                          setTargetColumn(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0381" /></span>
                    <select
                      value={mappingType}
                      onChange={
                        event =>
                          setMappingType(
                            event.target.value,
                          )
                      }
                    >
                      {
                        [
                          'DIRECT',
                          'CAST',
                          'DERIVED',
                          'CONSTANT',
                          'SYSTEM',
                        ].map(
                          value => (
                            <option
                              key={value}
                              value={value}
                            >
                              {value}
                            </option>
                          ),
                        )
                      }
                    </select>
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0291" /></span>
                    <input
                      value={expectedPgType}
                      onChange={
                        event =>
                          setExpectedPgType(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label className="integration-admin-check">
                    <input
                      type="checkbox"
                      checked={isActive}
                      onChange={
                        event =>
                          setIsActive(
                            event.target.checked,
                          )
                      }
                    />
                    <span><Trans i18nKey="batch.tr0289" /></span>
                  </label>

                  <label className="wide">
                    <span><Trans i18nKey="batch.tr0393" /></span>
                    <textarea
                      rows={3}
                      value={transformExpression}
                      onChange={
                        event =>
                          setTransformExpression(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <div className="wide integration-mapping-sources">
                    <div className="integration-mapping-sources-header">
                      <div>
                        <strong><Trans i18nKey="batch.tr0388" /></strong>
                        <span>
                          <Trans i18nKey="batch.tr0383" />
                        </span>
                      </div>

                      <button
                        type="button"
                        className="admin-secondary-button"
                        onClick={
                          () =>
                            setSources(
                              current => [
                                ...current,
                                emptySource(),
                              ],
                            )
                        }
                      >
                        <Trans i18nKey="batch.tr0371" />
                      </button>
                    </div>

                    {
                      sources.length === 0
                        ? (
                            <div className="integration-admin-empty">
                              <Trans i18nKey="batch.tr0382" />
                            </div>
                          )
                        : sources.map(
                            (source, index) => (
                              <div
                                key={index}
                                className="integration-mapping-source-row"
                              >
                                <span className="integration-mapping-source-order">
                                  {index + 1}
                                </span>

                                <select
                                  value={source.source_layer}
                                  onChange={
                                    event =>
                                      changeSource(
                                        index,
                                        'source_layer',
                                        event.target.value,
                                      )
                                  }
                                >
                                  <option value="SOURCE">
                                    SOURCE
                                  </option>
                                  <option value="STG">
                                    STG
                                  </option>
                                </select>

                                <input
                                  placeholder="schema"
                                  value={source.source_schema}
                                  onChange={
                                    event =>
                                      changeSource(
                                        index,
                                        'source_schema',
                                        event.target.value,
                                      )
                                  }
                                />

                                <input
                                  placeholder="object"
                                  value={source.source_object}
                                  onChange={
                                    event =>
                                      changeSource(
                                        index,
                                        'source_object',
                                        event.target.value,
                                      )
                                  }
                                />

                                <input
                                  placeholder="column"
                                  value={source.source_column}
                                  onChange={
                                    event =>
                                      changeSource(
                                        index,
                                        'source_column',
                                        event.target.value,
                                      )
                                  }
                                />

                                <input
                                  placeholder="role"
                                  value={source.source_role}
                                  onChange={
                                    event =>
                                      changeSource(
                                        index,
                                        'source_role',
                                        event.target.value,
                                      )
                                  }
                                />

                                <button
                                  type="button"
                                  className="admin-link-button"
                                  onClick={
                                    () =>
                                      removeSource(index)
                                  }
                                >
                                  <Trans i18nKey="batch.tr0386" />
                                </button>
                              </div>
                            ),
                          )
                    }
                  </div>

                  <label className="wide">
                    <span><Trans i18nKey="batch.tr0290" /></span>
                    <input
                      value={changeReason}
                      onChange={
                        event =>
                          setChangeReason(
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
        loading && mappings.length === 0
          ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0377" />
              </div>
            )
          : (
              <div className="data-table-wrapper">
                <table className="data-table integration-admin-wide-table">
                  <thead>
                    <tr>
                      <th><Trans i18nKey="batch.tr0389" /></th>
                      <th><Trans i18nKey="batch.tr0392" /></th>
                      <th><Trans i18nKey="batch.tr0185" /></th>
                      <th><Trans i18nKey="batch.tr0388" /></th>
                      <th><Trans i18nKey="batch.tr0053" /></th>
                      <th><Trans i18nKey="batch.tr0036" /></th>
                      <th><Trans i18nKey="batch.tr0215" /></th>
                    </tr>
                  </thead>

                  <tbody>
                    {
                      mappings.map(
                        item => (
                          <tr key={item.mapping_id}>
                            <td>
                              <strong>
                                {item.mapping_stage}
                              </strong>
                              <br />
                              <code>
                                {item.target_schema}.
                                {item.target_object}.
                                {item.target_column}
                              </code>
                            </td>

                            <td>
                              {item.transform_name}
                            </td>

                            <td>
                              {item.mapping_type}
                            </td>

                            <td>
                              <span className="integration-admin-source-count">
                                {item.sources.length}
                              </span>
                            </td>

                            <td>
                              v{item.version_no}
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
                                canManage
                                  ? (
                                      <div className="admin-integration-row-actions">
                                        <button
                                          type="button"
                                          className="admin-link-button"
                                          onClick={
                                            () =>
                                              openEdit(item)
                                          }
                                        >
                                          <Trans i18nKey="batch.tr0218" />
                                        </button>

                                        <button
                                          type="button"
                                          className="admin-link-button"
                                          disabled={
                                            changingId !== null
                                          }
                                          onClick={
                                            () => {
                                              void changeState(
                                                item,
                                              )
                                            }
                                          }
                                        >
                                          {
                                            changingId === item.mapping_id
                                              ?<Trans i18nKey="batch.tr0115" />
                                              : item.is_active
                                                ?<Trans i18nKey="batch.tr0217" />
                                                :<Trans i18nKey="batch.tr0219" />
                                          }
                                        </button>
                                      </div>
                                    )
                                  : (
                                      <span className="integration-admin-readonly">
                                        <Trans i18nKey="batch.tr0384" />
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
