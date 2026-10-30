import { Trans } from 'react-i18next'
import {
  useEffect,
  useState,
} from 'react'

import type {
  FormEvent,
} from 'react'

import {
  createIntegrationContract,
  getIntegrationContracts,
  getIntegrationSourceSystems,
  setIntegrationContractActive,
  updateIntegrationContract,
} from './integrationAdminApi'

import type {
  IntegrationContract,
  IntegrationSourceSystem,
} from './integrationAdminApi'

import {
  confirmAction,
} from './confirmAction'


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


export default function AdminIntegrationContracts() {
  const [
    contracts,
    setContracts,
  ] = useState<IntegrationContract[]>([])

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
    editing,
    setEditing,
  ] = useState<IntegrationContract | null>(null)

  const [
    creating,
    setCreating,
  ] = useState(false)

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
    sourceSchema,
    setSourceSchema,
  ] = useState('')

  const [
    sourceObject,
    setSourceObject,
  ] = useState('')

  const [
    stgTable,
    setStgTable,
  ] = useState('')

  const [
    sourceColumn,
    setSourceColumn,
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
    expectedPgType,
    setExpectedPgType,
  ] = useState('')

  const [
    isRequired,
    setIsRequired,
  ] = useState(false)

  const [
    allowBlank,
    setAllowBlank,
  ] = useState(true)

  const [
    isActive,
    setIsActive,
  ] = useState(true)

  const [
    description,
    setDescription,
  ] = useState('')

  const [
    changeReason,
    setChangeReason,
  ] = useState('')


  async function load() {
    try {
      setLoading(true)
      setError(null)

      const [
        contractResult,
        systemResult,
      ] = await Promise.all([
        getIntegrationContracts(
          filterSystem || undefined,
        ),
        getIntegrationSourceSystems(),
      ])

      setContracts(contractResult)
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
      getIntegrationContracts(
        filterSystem || undefined,
      ),
      getIntegrationSourceSystems(),
    ])
      .then(([
        contractResult,
        systemResult,
      ]) => {
        if (cancelled) {
          return
        }

        setContracts(contractResult)
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


  function resetEditor() {
    setCreating(false)
    setEditing(null)
  }


  function openCreate() {
    setEditing(null)
    setCreating(true)

    setSourceSystemCode(
      filterSystem
      || systems[0]?.code
      || '',
    )

    setSourceSchema('')
    setSourceObject('')
    setStgTable('')
    setSourceColumn('')
    setTargetSchema('ods')
    setTargetObject('')
    setTargetColumn('')
    setExpectedPgType('')
    setIsRequired(false)
    setAllowBlank(true)
    setIsActive(true)
    setDescription('')
    setChangeReason('')
    setError(null)
  }


  function openEdit(
    item: IntegrationContract,
  ) {
    setCreating(false)
    setEditing(item)

    setSourceSystemCode(
      item.source_system_code,
    )
    setSourceSchema(item.source_schema)
    setSourceObject(item.source_object)
    setStgTable(item.stg_table)
    setSourceColumn(item.source_column)
    setTargetSchema(item.target_schema)
    setTargetObject(item.target_object)
    setTargetColumn(item.target_column)
    setExpectedPgType(
      item.expected_pg_type,
    )
    setIsRequired(item.is_required)
    setAllowBlank(item.allow_blank)
    setIsActive(item.is_active)
    setDescription(
      item.description ?? '',
    )
    setChangeReason('')
    setError(null)
  }


  async function submit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    try {
      setSaving(true)
      setError(null)

      if (creating) {
        await createIntegrationContract({
          source_system_code:
            sourceSystemCode,
          source_schema:
            sourceSchema.trim(),
          source_object:
            sourceObject.trim(),
          stg_table:
            stgTable.trim(),
          source_column:
            sourceColumn.trim(),
          target_schema:
            targetSchema.trim(),
          target_object:
            targetObject.trim(),
          target_column:
            targetColumn.trim(),
          expected_pg_type:
            expectedPgType.trim(),
          is_required:
            isRequired,
          allow_blank:
            allowBlank,
          is_active:
            isActive,
          description:
            nullable(description),
          change_reason:
            nullable(changeReason),
        })
      } else if (editing) {
        await updateIntegrationContract(
          editing.contract_id,
          {
            target_schema:
              targetSchema.trim(),
            target_object:
              targetObject.trim(),
            target_column:
              targetColumn.trim(),
            expected_pg_type:
              expectedPgType.trim(),
            is_required:
              isRequired,
            allow_blank:
              allowBlank,
            is_active:
              isActive,
            description:
              nullable(description),
            change_reason:
              nullable(changeReason),
          },
        )
      }

      resetEditor()
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
    item: IntegrationContract,
  ) {
    const nextState =
      !item.is_active

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Enable contract?'
            : 'Disable contract?',
        message:
          `${item.source_schema}.${item.source_object}.${item.source_column}`,
        detail:
          'The change creates a new current contract version. History is preserved.',
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
        item.contract_id,
      )
      setError(null)

      await setIntegrationContractActive(
        item.contract_id,
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

        <button
          type="button"
          className="admin-primary-button"
          disabled={systems.length === 0}
          onClick={openCreate}
        >
          <Trans i18nKey="batch.tr0282" />
        </button>
      </div>

      {
        error
          ? (
              <div className="error">
                <strong><Trans i18nKey="batch.tr0281" /></strong>
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
                      ?<Trans i18nKey="batch.tr0282" />
                      : (
                          `Edit contract #`
                          + editing?.contract_id
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
                      required
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
                    <span><Trans i18nKey="batch.tr0216" /></span>
                    <input
                      required
                      readOnly={!creating}
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
                      readOnly={!creating}
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
                    <span><Trans i18nKey="batch.tr0292" /></span>
                    <input
                      required
                      readOnly={!creating}
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
                    <span><Trans i18nKey="batch.tr0287" /></span>
                    <input
                      required
                      readOnly={!creating}
                      value={sourceColumn}
                      onChange={
                        event =>
                          setSourceColumn(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0291" /></span>
                    <input
                      required
                      value={expectedPgType}
                      onChange={
                        event =>
                          setExpectedPgType(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0228" /></span>
                    <input
                      required
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
                      value={targetColumn}
                      onChange={
                        event =>
                          setTargetColumn(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label className="integration-admin-check">
                    <input
                      type="checkbox"
                      checked={isRequired}
                      onChange={
                        event =>
                          setIsRequired(
                            event.target.checked,
                          )
                      }
                    />
                    <span><Trans i18nKey="batch.tr0294" /></span>
                  </label>

                  <label className="integration-admin-check">
                    <input
                      type="checkbox"
                      checked={allowBlank}
                      onChange={
                        event =>
                          setAllowBlank(
                            event.target.checked,
                          )
                      }
                    />
                    <span><Trans i18nKey="batch.tr0293" /></span>
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
                    <span><Trans i18nKey="batch.tr0223" /></span>
                    <textarea
                      rows={2}
                      value={description}
                      onChange={
                        event =>
                          setDescription(
                            event.target.value,
                          )
                      }
                    />
                  </label>

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
                      onClick={resetEditor}
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
        loading && contracts.length === 0
          ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0286" />
              </div>
            )
          : (
              <div className="data-table-wrapper">
                <table className="data-table integration-admin-wide-table">
                  <thead>
                    <tr>
                      <th><Trans i18nKey="batch.tr0042" /></th>
                      <th><Trans i18nKey="batch.tr0038" /></th>
                      <th><Trans i18nKey="batch.tr0185" /></th>
                      <th><Trans i18nKey="batch.tr0054" /></th>
                      <th><Trans i18nKey="batch.tr0053" /></th>
                      <th><Trans i18nKey="batch.tr0036" /></th>
                      <th><Trans i18nKey="batch.tr0215" /></th>
                    </tr>
                  </thead>

                  <tbody>
                    {
                      contracts.map(
                        item => (
                          <tr key={item.contract_id}>
                            <td>
                              <strong>
                                {item.source_system_code}
                              </strong>
                              <br />
                              <code>
                                {item.source_schema}.
                                {item.source_object}.
                                {item.source_column}
                              </code>
                            </td>

                            <td>
                              <code>
                                {item.target_schema}.
                                {item.target_object}.
                                {item.target_column}
                              </code>
                            </td>

                            <td>
                              {item.expected_pg_type}
                            </td>

                            <td>
                              {
                                item.is_required
                                  ? 'REQUIRED'
                                  : 'OPTIONAL'
                              }
                              {' / '}
                              {
                                item.allow_blank
                                  ? 'BLANK OK'
                                  : 'NO BLANK'
                              }
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
                                    changingId === item.contract_id
                                      ?<Trans i18nKey="batch.tr0115" />
                                      : item.is_active
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
