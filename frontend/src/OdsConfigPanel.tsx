import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import {
  confirmAction,
} from './confirmAction'
import {
  formatDateTime,
} from './dateTime'
import {
  useSeverityLabel,
} from './severityLabels'
import {
  createOdsConfigProposal,
  getOdsConfig,
  publishOdsConfig,
  saveOdsConfigDraft,
  validateOdsConfig,
} from './integrationAdminApi'
import type {
  Compatibility,
  IntegrationSourceObject,
  ObjectConfigIssue,
  OdsConfigResponse,
  OdsDefinition,
  OdsField,
  OdsTransform,
} from './integrationAdminApi'


interface OdsConfigPanelProps {
  sourceObject: IntegrationSourceObject
  onClose: () => void
}


// One row of the editor: an ODS field (from an STG column, a constant or a technical field).
interface OdsRow extends OdsField {
  row_id: string
  included: boolean
  confirmed: boolean
}


const NAME_PATTERN = /^[a-z_][a-z0-9_]{0,62}$/

// Transformations with an STG column and without (decision O2, package 012).
const STG_TRANSFORMS: OdsTransform[] = ['COPY', 'CAST', 'TRIM', 'UPPER', 'LOWER', 'EMPTY_TO_NULL', 'DEFAULT', 'DATE_FROM_TEXT']

const FREE_TRANSFORMS: OdsTransform[] = ['CONSTANT', 'LOAD_RUN_ID', 'LOADED_AT']

const TECHNICAL: OdsTransform[] = ['LOAD_RUN_ID', 'LOADED_AT']

const WITH_PARAM: OdsTransform[] = ['DEFAULT', 'CONSTANT', 'DATE_FROM_TEXT']


function message(
  reason: unknown,
): string {
  return reason instanceof Error
    ? reason.message
    : String(reason)
}


// Same keys as the API document; fields sorted by target column, because the editor lists STG
// columns first and constants after them, while a saved version keeps the order it was saved in.
function canonical(
  definition: OdsDefinition,
): string {
  return JSON.stringify({
    target_table: definition.target_table,
    fields: [...definition.fields].sort((a, b) => a.target_column.localeCompare(b.target_column)).map(
      field => ({
        target_column: field.target_column,
        transform: field.transform,
        stg_column: field.stg_column,
        stg_type: field.stg_type,
        target_type: field.target_type,
        conversion: field.conversion,
        param: field.param,
        write_policy: field.write_policy,
        allow_explicit_null: field.allow_explicit_null,
      }),
    ),
    key: definition.key,
  })
}


function OdsConfigPanel({
  sourceObject,
  onClose,
}: OdsConfigPanelProps) {
  const { t } = useTranslation()

  const severityLabel = useSeverityLabel()

  const [data, setData] = useState<OdsConfigResponse | null>(null)

  const [loading, setLoading] = useState(true)

  const [busy, setBusy] = useState(false)

  const [error, setError] = useState<string | null>(null)

  const [notice, setNotice] = useState<string | null>(null)

  const [proposalDdl, setProposalDdl] = useState<string | null>(null)

  const [targetTable, setTargetTable] = useState('')

  const [rows, setRows] = useState<OdsRow[]>([])

  // Business key as ODS target columns, in key order.
  const [keyColumns, setKeyColumns] = useState<string[]>([])

  const [issues, setIssues] = useState<ObjectConfigIssue[] | null>(null)

  const freeRowCounter = useRef(0)

  // Responses for an older load are ignored.
  const requestSequence = useRef(0)

  const applyData = useCallback(
    (value: OdsConfigResponse) => {
      setData(value)

      const saved =
        value.config.draft?.definition
        ?? value.config.current?.definition
        ?? null

      const byStg = new Map<string, OdsField>()

      for (const field of saved?.fields ?? []) {
        if (field.stg_column !== null && !byStg.has(field.stg_column)) {
          byStg.set(field.stg_column, field)
        }
      }

      const nextRows: OdsRow[] = (value.stg_columns ?? []).map(
        column => {
          const field = byStg.get(column.name)

          return {
            row_id: `stg:${column.name}`,
            included: saved === null ? true : field !== undefined,
            confirmed: field?.conversion === 'CONFIRMED',
            target_column: field?.target_column ?? column.name,
            transform: field?.transform ?? 'COPY',
            stg_column: column.name,
            stg_type: column.type,
            target_type: field?.target_type ?? column.type,
            conversion: field?.conversion ?? 'NONE',
            param: field?.param ?? null,
            write_policy: field?.write_policy ?? 'AUTHORITATIVE',
            allow_explicit_null: field?.allow_explicit_null ?? false,
          }
        },
      )

      for (const field of saved?.fields ?? []) {
        if (field.stg_column === null) {
          nextRows.push({ ...field, row_id: `free:${field.target_column}`, included: true, confirmed: false })
        }
      }

      setRows(nextRows)
      setTargetTable(saved?.target_table ?? '')
      setKeyColumns(saved?.key ?? [])
      setIssues(value.config.draft?.last_validation?.issues ?? null)
    },
    [],
  )

  useEffect(
    () => {
      const sequence = ++requestSequence.current

      void getOdsConfig(sourceObject.source_object_id)
        .then(value => {
          if (sequence === requestSequence.current) {
            applyData(value)
          }
        })
        .catch(reason => {
          if (sequence === requestSequence.current) {
            setError(message(reason))
          }
        })
        .finally(() => {
          if (sequence === requestSequence.current) {
            setLoading(false)
          }
        })

      return () => {
        requestSequence.current += 1
      }
    },
    [
      applyData,
      sourceObject.source_object_id,
    ],
  )

  async function reload() {
    const sequence = ++requestSequence.current

    const value = await getOdsConfig(sourceObject.source_object_id)

    if (sequence === requestSequence.current) {
      applyData(value)
    }
  }

  // Runs an action, then reloads; the returned text is shown as the notice.
  async function run(
    action: () => Promise<string>,
  ) {
    setBusy(true)
    setError(null)
    setNotice(null)

    let text = ''

    try {
      text = await action()
    } catch (reason) {
      setError(message(reason))
    }

    try {
      await reload()
      setNotice(text === '' ? null : text)
    } catch (reason) {
      setError(message(reason))
    } finally {
      setBusy(false)
    }
  }

  function compatibility(
    source: string | null,
    targetType: string,
  ): Compatibility {
    if (source === null || source === targetType) {
      return 'SAME'
    }

    return data?.compatibility[`${source}|${targetType}`] ?? 'INCOMPATIBLE'
  }

  function toField(
    row: OdsRow,
  ): OdsField {
    const compat = compatibility(row.stg_column === null ? null : row.stg_type, row.target_type)

    return {
      target_column: row.target_column,
      transform: row.transform,
      stg_column: row.stg_column,
      stg_type: row.stg_column === null ? null : row.stg_type,
      target_type: row.target_type,
      conversion:
        compat === 'SAFE'
          ? 'SAFE'
          : compat === 'CONDITIONAL' && row.confirmed
            ? 'CONFIRMED'
            : 'NONE',
      param: WITH_PARAM.includes(row.transform) ? (row.param ?? '') : null,
      write_policy: TECHNICAL.includes(row.transform) ? 'INSERT_ONLY' : row.write_policy,
      allow_explicit_null: row.allow_explicit_null,
    }
  }

  function buildDefinition(): OdsDefinition {
    const included = rows.filter(row => row.included)

    return {
      target_table: targetTable === '' ? null : targetTable,
      fields: included.map(toField),
      key: keyColumns.filter(column => included.some(row => row.target_column === column)),
    }
  }

  const config = data?.config ?? null

  const draft = config?.draft ?? null

  const current = config?.current ?? null

  const legacy = config?.legacy ?? false

  const odsTables = data?.targets ?? []

  const target = odsTables.find(item => item.table === targetTable) ?? null

  const targetColumns = new Map((target?.columns ?? []).map(column => [column.name, column]))

  const edited = buildDefinition()

  const savedDefinition = draft?.definition ?? current?.definition ?? null

  const dirty =
    savedDefinition === null
      ? rows.some(row => row.included)
      : canonical(edited) !== canonical(savedDefinition)

  const valid =
    (targetTable === '' || NAME_PATTERN.test(targetTable))
    && new Set(edited.fields.map(field => field.target_column)).size === edited.fields.length
    && edited.fields.every(
      field => NAME_PATTERN.test(field.target_column)
        && (!WITH_PARAM.includes(field.transform) || (field.param ?? '') !== ''),
    )

  const lastValidation = draft?.last_validation ?? null

  const validationCurrent =
    draft !== null
    && lastValidation !== null
    && lastValidation.draft_revision === draft.draft_revision
    && lastValidation.definition_hash === draft.definition_hash

  const canPublish = draft !== null && !dirty && validationCurrent && lastValidation?.ok === true

  const canPropose =
    draft !== null
    && !dirty
    && draft.definition.target_table !== null
    && (target === null || draft.definition.fields.some(field => !targetColumns.has(field.target_column)))

  function updateRow(
    rowId: string,
    change: Partial<OdsRow>,
  ) {
    setRows(items => items.map(row => (row.row_id === rowId ? { ...row, ...change } : row)))
  }

  function changeTransform(
    row: OdsRow,
    transform: OdsTransform,
  ) {
    const change: Partial<OdsRow> = { transform }

    if (transform === 'DATE_FROM_TEXT') {
      change.target_type = 'DATE'
      change.param = data?.date_formats[0] ?? 'YYYY-MM-DD'
    } else if (transform === 'LOADED_AT') {
      change.target_type = 'TIMESTAMPTZ'
      change.param = null
      change.write_policy = 'INSERT_ONLY'
    } else if (transform === 'LOAD_RUN_ID') {
      change.target_type = 'BIGINT'
      change.param = null
      change.write_policy = 'INSERT_ONLY'
    } else if (!WITH_PARAM.includes(transform)) {
      change.param = null
    }

    updateRow(row.row_id, change)
  }

  function addFreeRow() {
    freeRowCounter.current += 1

    const name = `field_${freeRowCounter.current}`

    setRows(
      items => [
        ...items,
        {
          row_id: `new:${freeRowCounter.current}`,
          included: true,
          confirmed: false,
          target_column: name,
          transform: 'CONSTANT',
          stg_column: null,
          stg_type: null,
          target_type: 'TEXT',
          conversion: 'NONE',
          param: '',
          write_policy: 'AUTHORITATIVE',
          allow_explicit_null: false,
        },
      ],
    )
  }

  function toggleKey(
    column: string,
  ) {
    setKeyColumns(items => (items.includes(column) ? items.filter(item => item !== column) : [...items, column]))
  }

  function issueText(
    issue: ObjectConfigIssue,
  ): string {
    const subject = [issue.column, issue.source_column].filter(Boolean).join(' ← ')
    const text = t(`odsConfig.issue.${issue.code}`, {
      defaultValue: t(`objectConfig.issue.${issue.code}`, {
        defaultValue: issue.code,
        expected: issue.expected ?? '',
        actual: issue.actual ?? '',
        source_type: issue.source_type ?? '',
        target_type: issue.target_type ?? '',
      }),
      expected: issue.expected ?? '',
      actual: issue.actual ?? '',
      source_type: issue.source_type ?? '',
      target_type: issue.target_type ?? '',
    })

    return subject === '' ? text : `${subject}: ${text}`
  }

  async function saveDraft() {
    await run(
      async () => {
        await saveOdsConfigDraft(sourceObject.source_object_id, draft?.draft_revision ?? null, edited)

        return t('objectConfig.saved')
      },
    )
  }

  async function validate() {
    if (draft === null) {
      return
    }

    await run(
      async () => {
        const result = await validateOdsConfig(sourceObject.source_object_id, draft.draft_revision)

        setIssues(result.issues)

        return result.ok ? t('objectConfig.validOk') : t('objectConfig.validFailed')
      },
    )
  }

  async function publish() {
    if (draft === null) {
      return
    }

    const confirmed =
      await confirmAction({
        title: t('odsConfig.publishTitle'),
        message: t('odsConfig.publishMessage', {
          object: `${sourceObject.source_schema}.${sourceObject.source_object}`,
          version: draft.version_no,
        }),
        confirmLabel: t('objectConfig.publish'),
        cancelLabel: t('objectConfig.cancel'),
      })

    if (!confirmed) {
      return
    }

    await run(
      async () => {
        const result = await publishOdsConfig(sourceObject.source_object_id, draft.draft_revision)

        return t('objectConfig.published', { version: result.version_no, status: result.status })
      },
    )
  }

  async function propose() {
    if (draft === null) {
      return
    }

    await run(
      async () => {
        const result = await createOdsConfigProposal(sourceObject.source_object_id, draft.draft_revision)

        setProposalDdl(result.ddl)

        return t('odsConfig.proposalCreated', { id: result.proposal_id })
      },
    )
  }

  const typeChoices = data?.type_choices ?? []

  const transformLabel = (transform: OdsTransform) => t(`odsConfig.transform.${transform}`)

  return (
    <>
      {
        loading
          ? (
            <div className="loading-panel">
              {t('objectConfig.loading')}
            </div>
          )
          : config !== null && (
            <>
              <p>
                {t('objectConfig.currentLabel')}
                {' '}
                {
                  current === null
                    ? <span className="badge">{t('objectConfig.notPublished')}</span>
                    : (
                      <>
                        <span className="badge badge-success">{current.status}</span>
                        {` v${current.version_no} · ${formatDateTime(current.published_at)}`}
                      </>
                    )
                }
                {
                  draft !== null && (
                    <>
                      {' · '}
                      {t('objectConfig.draftLabel', { version: draft.version_no, revision: draft.draft_revision })}
                    </>
                  )
                }
              </p>

              <p>
                {
                  config.stg === null
                    ? t('odsConfig.noStg')
                    : t('odsConfig.stgSource', { table: `stg.${config.stg.table}`, version: config.stg.version_no })
                }
              </p>

              {
                legacy && (
                  <p>{t('odsConfig.legacyNote')}</p>
                )
              }

              {
                !legacy && config.stg !== null && (
                  <div className="admin-integrations-form">
                    <label>
                      <span>{t('odsConfig.targetTable')}</span>
                      <input
                        list="ods-config-target-tables"
                        value={targetTable}
                        disabled={busy}
                        onChange={event => setTargetTable(event.target.value.trim().toLowerCase())}
                      />
                      <datalist id="ods-config-target-tables">
                        {
                          odsTables.map(
                            item => (
                              <option key={item.table} value={item.table} />
                            ),
                          )
                        }
                      </datalist>
                    </label>

                    <p className="wide">
                      {
                        targetTable === ''
                          ? t('objectConfig.targetNone')
                          : !NAME_PATTERN.test(targetTable)
                            ? t('objectConfig.targetInvalid')
                            : target === null
                              ? t('objectConfig.targetNew')
                              : t('objectConfig.targetExisting', { columns: target.columns.length })
                      }
                    </p>
                    <p className="wide">{t('odsConfig.hint')}</p>
                  </div>
                )
              }

              {
                !legacy && config.stg !== null && rows.length > 0 && (
                  <div className="data-table-wrapper wide" style={{ maxHeight: '520px', overflowY: 'auto' }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('objectConfig.colInclude')}</th>
                          <th>{t('odsConfig.colStg')}</th>
                          <th>{t('odsConfig.colTransform')}</th>
                          <th>{t('odsConfig.colValue')}</th>
                          <th>{t('objectConfig.colTarget')}</th>
                          <th>{t('objectConfig.colTargetType')}</th>
                          <th>{t('objectConfig.colPolicy')}</th>
                          <th>{t('objectConfig.colNull')}</th>
                          <th>{t('objectConfig.colKey')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {
                          rows.map(
                            row => {
                              const disabled = busy || !row.included
                              const technical = TECHNICAL.includes(row.transform)
                              const compat = compatibility(row.stg_column === null ? null : row.stg_type, row.target_type)
                              const keyPosition = keyColumns.indexOf(row.target_column)
                              const existing = targetColumns.get(row.target_column)
                              const types = Array.from(new Set([row.target_type, ...(existing ? [existing.type] : []), ...typeChoices]))

                              return (
                                <tr key={row.row_id}>
                                  <td>
                                    <input
                                      type="checkbox"
                                      checked={row.included}
                                      disabled={busy}
                                      onChange={event => updateRow(row.row_id, { included: event.target.checked })}
                                    />
                                  </td>
                                  <td>
                                    {
                                      row.stg_column === null
                                        ? '—'
                                        : (
                                          <>
                                            <code>{row.stg_column}</code>
                                            <br />
                                            <small>{row.stg_type}</small>
                                          </>
                                        )
                                    }
                                  </td>
                                  <td>
                                    <select
                                      value={row.transform}
                                      disabled={disabled}
                                      onChange={event => changeTransform(row, event.target.value as OdsTransform)}
                                    >
                                      {
                                        (row.stg_column === null ? FREE_TRANSFORMS : STG_TRANSFORMS).map(
                                          transform => (
                                            <option key={transform} value={transform}>{transformLabel(transform)}</option>
                                          ),
                                        )
                                      }
                                    </select>
                                    {
                                      row.transform === 'CAST' && compat === 'CONDITIONAL' && (
                                        <label>
                                          <input
                                            type="checkbox"
                                            checked={row.confirmed}
                                            disabled={disabled}
                                            onChange={event => updateRow(row.row_id, { confirmed: event.target.checked })}
                                          />
                                          {' '}
                                          {t('objectConfig.confirm')}
                                        </label>
                                      )
                                    }
                                  </td>
                                  <td>
                                    {
                                      row.transform === 'DATE_FROM_TEXT' && (
                                        <select
                                          value={row.param ?? ''}
                                          disabled={disabled}
                                          onChange={event => updateRow(row.row_id, { param: event.target.value })}
                                        >
                                          {
                                            (data?.date_formats ?? []).map(
                                              format => (
                                                <option key={format} value={format}>{format}</option>
                                              ),
                                            )
                                          }
                                        </select>
                                      )
                                    }
                                    {
                                      (row.transform === 'DEFAULT' || row.transform === 'CONSTANT') && (
                                        <input
                                          value={row.param ?? ''}
                                          maxLength={200}
                                          disabled={disabled}
                                          onChange={event => updateRow(row.row_id, { param: event.target.value })}
                                        />
                                      )
                                    }
                                  </td>
                                  <td>
                                    <input
                                      value={row.target_column}
                                      disabled={disabled}
                                      aria-invalid={!NAME_PATTERN.test(row.target_column)}
                                      onChange={event => updateRow(row.row_id, { target_column: event.target.value.trim().toLowerCase() })}
                                    />
                                    {
                                      row.included && target !== null && existing === undefined && (
                                        <>
                                          <br />
                                          <small>{t('odsConfig.newColumn')}</small>
                                        </>
                                      )
                                    }
                                  </td>
                                  <td>
                                    <select
                                      value={row.target_type}
                                      disabled={disabled || row.transform === 'DATE_FROM_TEXT'}
                                      onChange={event => updateRow(row.row_id, { target_type: event.target.value })}
                                    >
                                      {
                                        types.map(
                                          type => (
                                            <option key={type} value={type}>{type}</option>
                                          ),
                                        )
                                      }
                                    </select>
                                  </td>
                                  <td>
                                    <select
                                      value={technical ? 'INSERT_ONLY' : row.write_policy}
                                      disabled={disabled || technical}
                                      onChange={event => updateRow(row.row_id, { write_policy: event.target.value as OdsField['write_policy'] })}
                                    >
                                      <option value="AUTHORITATIVE">{t('objectConfig.policy.AUTHORITATIVE')}</option>
                                      <option value="INSERT_ONLY">{t('objectConfig.policy.INSERT_ONLY')}</option>
                                    </select>
                                  </td>
                                  <td>
                                    <input
                                      type="checkbox"
                                      checked={row.allow_explicit_null}
                                      disabled={disabled}
                                      onChange={event => updateRow(row.row_id, { allow_explicit_null: event.target.checked })}
                                    />
                                  </td>
                                  <td>
                                    <input
                                      type="checkbox"
                                      checked={keyPosition >= 0}
                                      disabled={disabled || row.stg_column === null}
                                      onChange={() => toggleKey(row.target_column)}
                                    />
                                    {keyPosition >= 0 ? ` ${keyPosition + 1}` : ''}
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

              {
                issues !== null && issues.length > 0 && (
                  <div className="wide">
                    <h4>
                      {
                        validationCurrent && lastValidation?.ok
                          ? t('objectConfig.issuesWarnings')
                          : t('objectConfig.issuesTitle')
                      }
                      {validationCurrent ? '' : ` (${t('objectConfig.issuesOutdated')})`}
                    </h4>
                    <ul>
                      {
                        issues.map(
                          (issue, index) => (
                            <li key={`${issue.code}:${issue.column ?? ''}:${issue.source_column ?? ''}:${index}`}>
                              <span className={issue.severity === 'ERROR' ? 'badge badge-danger' : 'badge badge-warning'}>
                                {severityLabel(issue.severity)}
                              </span>
                              {' '}
                              {issueText(issue)}
                            </li>
                          ),
                        )
                      }
                    </ul>
                  </div>
                )
              }

              <div className="admin-integrations-form-actions wide">
                {
                  !legacy && config.stg !== null && (
                    <>
                      <button
                        type="button"
                        className="admin-secondary-button"
                        disabled={busy}
                        onClick={addFreeRow}
                      >
                        {t('odsConfig.addField')}
                      </button>
                      <button
                        type="button"
                        className="admin-primary-button"
                        disabled={busy || !dirty || !valid || !rows.some(row => row.included)}
                        title={valid ? undefined : t('odsConfig.invalid')}
                        onClick={() => { void saveDraft() }}
                      >
                        {t('objectConfig.saveDraft')}
                      </button>
                      <button
                        type="button"
                        className="admin-secondary-button"
                        disabled={busy || draft === null || dirty}
                        title={dirty ? t('objectConfig.saveFirst') : undefined}
                        onClick={() => { void validate() }}
                      >
                        {t('objectConfig.validate')}
                      </button>
                      <button
                        type="button"
                        className="admin-secondary-button"
                        disabled={busy || !canPropose}
                        onClick={() => { void propose() }}
                      >
                        {t('objectConfig.propose')}
                      </button>
                      <button
                        type="button"
                        className="admin-primary-button"
                        disabled={busy || !canPublish}
                        title={canPublish ? undefined : t('objectConfig.publishNeedsValidation')}
                        onClick={() => { void publish() }}
                      >
                        {t('objectConfig.publish')}
                      </button>
                    </>
                  )
                }
                <button
                  type="button"
                  className="admin-secondary-button"
                  onClick={onClose}
                >
                  {t('objectConfig.close')}
                </button>
              </div>

              {
                proposalDdl !== null && (
                  <div className="wide">
                    <pre style={{ whiteSpace: 'pre-wrap', overflowX: 'auto' }}>{proposalDdl}</pre>
                    <p>{t('objectConfig.proposalNote')}</p>
                  </div>
                )
              }
            </>
          )
      }

      {
        notice && (
          <p>{notice}</p>
        )
      }

      {
        error && (
          <div className="error">
            <strong>{t('objectConfig.error')}</strong>
            {' '}
            {error}
          </div>
        )
      }

      {
        loading || config === null
          ? (
            <div className="admin-integrations-form-actions">
              <button
                type="button"
                className="admin-secondary-button"
                onClick={onClose}
              >
                {t('objectConfig.close')}
              </button>
            </div>
          )
          : null
      }
    </>
  )
}


export default OdsConfigPanel
