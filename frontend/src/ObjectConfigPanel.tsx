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
import OdsConfigPanel from './OdsConfigPanel'
import {
  checkObjectConfigDrift,
  createObjectConfigProposal,
  getObjectConfig,
  publishObjectConfig,
  rejectTargetProposal,
  saveObjectConfigDraft,
  validateObjectConfig,
} from './integrationAdminApi'
import type {
  Compatibility,
  IntegrationSourceObject,
  LoadStrategy,
  ObjectConfigDefinition,
  ObjectConfigField,
  ObjectConfigIssue,
  ObjectConfigResponse,
  ObjectLoadStrategy,
  TargetTable,
  WritePolicy,
} from './integrationAdminApi'


interface ObjectConfigPanelProps {
  sourceObject: IntegrationSourceObject
  onClose: () => void
}


// One row of the editor: a source column and what it is mapped to.
interface MappingRow {
  source_column: string
  source_type: string
  in_snapshot: boolean
  nullable: boolean
  included: boolean
  target_column: string
  target_type: string
  confirmed: boolean
  write_policy: WritePolicy
  allow_explicit_null: boolean
}


const NAME_PATTERN = /^[a-z_][a-z0-9_]{0,62}$/

const WRITE_POLICIES: WritePolicy[] = ['AUTHORITATIVE', 'INSERT_ONLY', 'NO_WRITE']

const NEW_COLUMN = '__new__'

// Types allowed for the change field of an INCREMENTAL load (same as the database check).
const CHANGE_COLUMN_TYPES = ['DATE', 'TIMESTAMP', 'TIMESTAMPTZ', 'INTEGER', 'BIGINT']

const DEFAULT_OVERLAP_MINUTES = 10

const MAX_OVERLAP_MINUTES = 10080


function baseType(
  type: string,
): string {
  return type.split('(')[0]
}


// Strategy of a definition: nested "strategy" of a saved version or the flat keys of a draft.
function strategyOf(
  definition: ObjectConfigDefinition,
): ObjectLoadStrategy {
  const raw = definition.strategy ?? definition

  return {
    load_strategy: raw.load_strategy ?? 'FULL',
    change_column: raw.change_column ?? null,
    overlap_minutes: raw.overlap_minutes ?? DEFAULT_OVERLAP_MINUTES,
    delete_flag_column: raw.delete_flag_column ?? null,
  }
}


function columnName(
  source: string,
): string {
  const name = source.toLowerCase().replace(/[^a-z0-9_]/g, '_')

  return (/^[a-z_]/.test(name) ? name : `_${name}`).slice(0, 63)
}


// Same field order as the API document, so that edited and saved versions compare equal.
function canonical(
  definition: ObjectConfigDefinition,
): string {
  return JSON.stringify({
    target_schema: definition.target_schema,
    target_table: definition.target_table,
    fields: definition.fields.map(
      field => ({
        target_column: field.target_column,
        source_column: field.source_column,
        source_type: field.source_type,
        target_type: field.target_type,
        conversion: field.conversion,
        write_policy: field.write_policy,
        allow_explicit_null: field.allow_explicit_null,
      }),
    ),
    key: definition.key,
    strategy: strategyOf(definition),
  })
}


function message(
  reason: unknown,
): string {
  return reason instanceof Error
    ? reason.message
    : String(reason)
}


function ObjectConfigPanel({
  sourceObject,
  onClose,
}: ObjectConfigPanelProps) {
  const { t } = useTranslation()

  const severityLabel = useSeverityLabel()

  const [data, setData] = useState<ObjectConfigResponse | null>(null)

  const [loading, setLoading] = useState(true)

  const [busy, setBusy] = useState(false)

  const [error, setError] = useState<string | null>(null)

  const [notice, setNotice] = useState<string | null>(null)

  const [targetSchema, setTargetSchema] = useState<'stg' | 'ods'>('stg')

  const [targetTable, setTargetTable] = useState('')

  const [rows, setRows] = useState<MappingRow[]>([])

  // Business key as source columns, in key order.
  const [keyColumns, setKeyColumns] = useState<string[]>([])

  const [issues, setIssues] = useState<ObjectConfigIssue[] | null>(null)

  // I06-05: the panel edits the source -> STG mapping or the STG -> ODS configuration.
  const [section, setSection] = useState<'stg' | 'ods'>('stg')

  const [loadStrategy, setLoadStrategy] = useState<LoadStrategy>('FULL')

  // Change field and delete flag as source columns, like the key.
  const [changeSource, setChangeSource] = useState('')

  const [overlapText, setOverlapText] = useState(String(DEFAULT_OVERLAP_MINUTES))

  const [deleteSource, setDeleteSource] = useState('')

  // Responses for an older load are ignored.
  const requestSequence = useRef(0)

  const applyData = useCallback(
    (value: ObjectConfigResponse) => {
      setData(value)

      const saved =
        value.config.draft?.definition
        ?? value.config.current?.definition
        ?? null

      const snapshotColumns = value.snapshot?.columns ?? []

      const byTarget = new Map<string, ObjectConfigField>()

      const bySource = new Map<string, ObjectConfigField>()

      for (const field of saved?.fields ?? []) {
        byTarget.set(field.target_column, field)

        if (field.source_column !== null && !bySource.has(field.source_column)) {
          bySource.set(field.source_column, field)
        }
      }

      const nextRows: MappingRow[] = snapshotColumns.map(
        column => {
          const field = bySource.get(column.name)

          return {
            source_column: column.name,
            source_type: column.type,
            in_snapshot: true,
            nullable: column.nullable,
            included: saved === null ? true : field !== undefined,
            target_column: field?.target_column ?? columnName(column.name),
            target_type: field?.target_type ?? column.type,
            confirmed: field?.conversion === 'CONFIRMED',
            write_policy: field?.write_policy ?? 'AUTHORITATIVE',
            allow_explicit_null: field?.allow_explicit_null ?? false,
          }
        },
      )

      // Saved fields whose source column is no longer in the snapshot (schema drift).
      for (const [source, field] of bySource) {
        if (!snapshotColumns.some(column => column.name === source)) {
          nextRows.push({
            source_column: source,
            source_type: field.source_type,
            in_snapshot: false,
            nullable: true,
            included: true,
            target_column: field.target_column,
            target_type: field.target_type,
            confirmed: field.conversion === 'CONFIRMED',
            write_policy: field.write_policy,
            allow_explicit_null: field.allow_explicit_null,
          })
        }
      }

      setRows(nextRows)
      setTargetSchema(saved?.target_schema ?? 'stg')
      setTargetTable(saved?.target_table ?? '')
      setKeyColumns(
        saved === null
          ? snapshotColumns.filter(column => column.primary_key).map(column => column.name)
          : saved.key
            .map(target => byTarget.get(target)?.source_column ?? null)
            .filter((source): source is string => source !== null),
      )
      const strategy = saved === null ? null : strategyOf(saved)

      const sourceOf = (column: string | null | undefined) => (
        column ? byTarget.get(column)?.source_column ?? '' : ''
      )

      setLoadStrategy(strategy?.load_strategy ?? 'FULL')
      setChangeSource(sourceOf(strategy?.change_column))
      setOverlapText(String(strategy?.overlap_minutes ?? DEFAULT_OVERLAP_MINUTES))
      setDeleteSource(sourceOf(strategy?.delete_flag_column))
      setIssues(value.config.draft?.last_validation?.issues ?? null)
    },
    [],
  )

  useEffect(
    () => {
      const sequence = ++requestSequence.current

      void getObjectConfig(sourceObject.source_object_id)
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
    const value = await getObjectConfig(sourceObject.source_object_id)

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

  const config = data?.config ?? null

  const draft = config?.draft ?? null

  const current = config?.current ?? null

  const legacy = config?.object.legacy ?? false

  const targets: TargetTable[] = data?.targets ?? []

  const target =
    targets.find(
      item => item.schema === targetSchema && item.table === targetTable,
    ) ?? null

  const targetColumns = new Map(
    (target?.columns ?? []).map(column => [column.name, column]),
  )

  function compatibility(
    source: string,
    targetType: string,
  ): Compatibility {
    if (source === targetType) {
      return 'SAME'
    }

    return data?.compatibility[`${source}|${targetType}`] ?? 'INCOMPATIBLE'
  }

  function effectiveTargetType(
    row: MappingRow,
  ): string {
    return targetColumns.get(row.target_column)?.type ?? row.target_type
  }

  const overlapMinutes = /^\d{1,5}$/.test(overlapText) ? Number(overlapText) : Number.NaN

  const overlapValid = overlapMinutes >= 0 && overlapMinutes <= MAX_OVERLAP_MINUTES

  function buildDefinition(): ObjectConfigDefinition {
    const included = rows.filter(row => row.included)

    const targetOf = (source: string) => (
      source === '' ? null : included.find(row => row.source_column === source)?.target_column ?? null
    )

    return {
      target_schema: targetTable === '' ? null : targetSchema,
      target_table: targetTable === '' ? null : targetTable,
      fields: included.map(
        row => {
          const targetType = effectiveTargetType(row)
          const compat = compatibility(row.source_type, targetType)

          return {
            target_column: row.target_column,
            source_column: row.source_column,
            source_type: row.source_type,
            target_type: targetType,
            conversion:
              compat === 'SAFE'
                ? 'SAFE'
                : compat === 'CONDITIONAL' && row.confirmed
                  ? 'CONFIRMED'
                  : 'NONE',
            write_policy: row.write_policy,
            allow_explicit_null: row.allow_explicit_null,
          }
        },
      ),
      key: keyColumns
        .map(source => included.find(row => row.source_column === source)?.target_column ?? null)
        .filter((column): column is string => column !== null),
      load_strategy: loadStrategy,
      change_column: targetOf(changeSource),
      overlap_minutes: overlapValid ? overlapMinutes : DEFAULT_OVERLAP_MINUTES,
      delete_flag_column: targetOf(deleteSource),
    }
  }

  const edited = buildDefinition()

  const savedDefinition = draft?.definition ?? current?.definition ?? null

  const dirty =
    savedDefinition === null
      ? rows.length > 0
      : canonical(edited) !== canonical(savedDefinition)

  const namesValid =
    (targetTable === '' || NAME_PATTERN.test(targetTable))
    && edited.fields.every(field => NAME_PATTERN.test(field.target_column))
    && overlapValid

  const lastValidation = draft?.last_validation ?? null

  const validationCurrent =
    draft !== null
    && lastValidation !== null
    && lastValidation.draft_revision === draft.draft_revision
    && lastValidation.definition_hash === draft.definition_hash

  const canPublish =
    draft !== null && !dirty && validationCurrent && lastValidation?.ok === true

  const missingColumns =
    draft === null
      ? []
      : draft.definition.fields.filter(field => target !== null && !targetColumns.has(field.target_column))

  const canPropose =
    draft !== null
    && !dirty
    && draft.definition.target_table !== null
    && (target === null || missingColumns.length > 0)

  const changeCandidates = rows.filter(
    row => row.included
      && row.write_policy !== 'NO_WRITE'
      && CHANGE_COLUMN_TYPES.includes(baseType(effectiveTargetType(row)))
      && CHANGE_COLUMN_TYPES.includes(baseType(row.source_type)),
  )

  const deleteCandidates = rows.filter(
    row => row.included && row.write_policy !== 'NO_WRITE' && effectiveTargetType(row) === 'BOOLEAN',
  )

  function updateRow(
    source: string,
    change: Partial<MappingRow>,
  ) {
    setRows(
      items => items.map(
        row => (row.source_column === source ? { ...row, ...change } : row),
      ),
    )
  }

  function toggleKey(
    source: string,
  ) {
    setKeyColumns(
      items => (items.includes(source) ? items.filter(item => item !== source) : [...items, source]),
    )
  }

  function issueText(
    issue: ObjectConfigIssue,
  ): string {
    const subject = [issue.column, issue.source_column].filter(Boolean).join(' ← ')
    const text = t(`objectConfig.issue.${issue.code}`, {
      defaultValue: issue.code,
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
        await saveObjectConfigDraft(
          sourceObject.source_object_id,
          draft?.draft_revision ?? null,
          edited,
        )

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
        const result = await validateObjectConfig(sourceObject.source_object_id, draft.draft_revision)

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
        title: t('objectConfig.publishTitle'),
        message: t('objectConfig.publishMessage', {
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
        const result = await publishObjectConfig(sourceObject.source_object_id, draft.draft_revision)

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
        await createObjectConfigProposal(sourceObject.source_object_id, draft.draft_revision)

        return t('objectConfig.proposalCreated')
      },
    )
  }

  async function checkDrift() {
    await run(
      async () => {
        const result = await checkObjectConfigDrift(sourceObject.source_object_id)

        return result.status === 'DRIFT' ? t('objectConfig.driftFound') : t('objectConfig.driftNone')
      },
    )
  }

  async function reject(
    proposalId: number,
  ) {
    const confirmed =
      await confirmAction({
        title: t('objectConfig.rejectTitle'),
        message: t('objectConfig.rejectMessage', { id: proposalId }),
        confirmLabel: t('objectConfig.reject'),
        cancelLabel: t('objectConfig.cancel'),
      })

    if (!confirmed) {
      return
    }

    await run(
      async () => {
        await rejectTargetProposal(proposalId)

        return t('objectConfig.rejected')
      },
    )
  }

  const tablesOfSchema = targets.filter(item => item.schema === targetSchema)

  const typeChoices = data?.type_choices ?? []

  const driftChanges = current?.status_details.changes ?? []

  return (
    <section className="admin-integrations-editor">
      <h3>
        {t('objectConfig.title', { object: `${sourceObject.source_system_code} · ${sourceObject.source_schema}.${sourceObject.source_object}` })}
      </h3>

      <nav className="admin-integrations-tabs">
        <button
          type="button"
          className={section === 'stg' ? 'admin-integrations-tab active' : 'admin-integrations-tab'}
          onClick={() => setSection('stg')}
        >
          {t('odsConfig.tabStg')}
        </button>
        <button
          type="button"
          className={section === 'ods' ? 'admin-integrations-tab active' : 'admin-integrations-tab'}
          onClick={() => setSection('ods')}
        >
          {t('odsConfig.tabOds')}
        </button>
      </nav>

      {
        section === 'ods' && (
          <OdsConfigPanel
            sourceObject={sourceObject}
            onClose={onClose}
          />
        )
      }

      {
        section === 'stg' && (
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
                        <span className={current.status === 'READY' ? 'badge badge-success' : 'badge badge-warning'}>
                          {current.status}
                        </span>
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

              {
                legacy && (
                  <p>{t('objectConfig.legacyNote')}</p>
                )
              }

              {
                data?.snapshot === null && (
                  <p>{t('objectConfig.noSnapshot')}</p>
                )
              }

              {
                data?.snapshot && (
                  <p>
                    {t('objectConfig.snapshotLabel', {
                      id: data.snapshot.metadata_capture_id,
                      columns: data.snapshot.columns.length,
                    })}
                    {data.snapshot.captured_at ? ` · ${formatDateTime(data.snapshot.captured_at)}` : ''}
                  </p>
                )
              }

              {
                current !== null && current.status === 'DRIFT' && (
                  <div className="error">
                    <strong>{t('objectConfig.driftTitle')}</strong>
                    <ul>
                      {
                        driftChanges.map(
                          change => (
                            <li key={`${change.change}:${change.source_column ?? ''}`}>
                              {
                                t(`objectConfig.drift.${change.change}`, {
                                  column: change.source_column ?? '',
                                  before: change.before ?? '',
                                  after: change.after ?? '',
                                })
                              }
                              {change.mapped ? ` (${t('objectConfig.driftMapped')})` : ''}
                            </li>
                          ),
                        )
                      }
                    </ul>
                  </div>
                )
              }

              {
                !legacy && (
                  <div className="admin-integrations-form">
                    <label>
                      <span>{t('objectConfig.targetSchema')}</span>
                      <select
                        value={targetSchema}
                        disabled={busy}
                        onChange={event => setTargetSchema(event.target.value as 'stg' | 'ods')}
                      >
                        <option value="stg">stg</option>
                        <option value="ods">ods</option>
                      </select>
                    </label>

                    <label>
                      <span>{t('objectConfig.targetTable')}</span>
                      <input
                        list="object-config-target-tables"
                        value={targetTable}
                        disabled={busy}
                        onChange={event => setTargetTable(event.target.value.trim().toLowerCase())}
                      />
                      <datalist id="object-config-target-tables">
                        {
                          tablesOfSchema.map(
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
                  </div>
                )
              }

              {
                !legacy && rows.length > 0 && (
                  <div className="admin-integrations-form">
                    <h4 className="wide">{t('objectConfig.strategyTitle')}</h4>

                    <label>
                      <span>{t('objectConfig.strategy')}</span>
                      <select
                        value={loadStrategy}
                        disabled={busy}
                        onChange={event => setLoadStrategy(event.target.value as LoadStrategy)}
                      >
                        <option value="FULL">{t('objectConfig.strategyFull')}</option>
                        <option value="INCREMENTAL">{t('objectConfig.strategyIncremental')}</option>
                      </select>
                    </label>

                    <label>
                      <span>{t('objectConfig.changeColumn')}</span>
                      <select
                        value={changeSource}
                        disabled={busy}
                        onChange={event => setChangeSource(event.target.value)}
                      >
                        <option value="">{t('objectConfig.changeColumnNone')}</option>
                        {
                          changeSource !== '' && !changeCandidates.some(row => row.source_column === changeSource) && (
                            <option value={changeSource}>{changeSource}</option>
                          )
                        }
                        {
                          changeCandidates.map(
                            row => (
                              <option key={row.source_column} value={row.source_column}>
                                {`${row.source_column} → ${row.target_column} (${effectiveTargetType(row)})`}
                              </option>
                            ),
                          )
                        }
                      </select>
                    </label>

                    <label>
                      <span>{t('objectConfig.overlapMinutes')}</span>
                      <input
                        type="number"
                        min={0}
                        max={MAX_OVERLAP_MINUTES}
                        step={1}
                        value={overlapText}
                        disabled={busy}
                        aria-invalid={!overlapValid}
                        onChange={event => setOverlapText(event.target.value.trim())}
                      />
                    </label>

                    <label>
                      <span>{t('objectConfig.deleteFlag')}</span>
                      <select
                        value={deleteSource}
                        disabled={busy}
                        onChange={event => setDeleteSource(event.target.value)}
                      >
                        <option value="">{t('objectConfig.deleteFlagNone')}</option>
                        {
                          deleteSource !== '' && !deleteCandidates.some(row => row.source_column === deleteSource) && (
                            <option value={deleteSource}>{deleteSource}</option>
                          )
                        }
                        {
                          deleteCandidates.map(
                            row => (
                              <option key={row.source_column} value={row.source_column}>
                                {`${row.source_column} → ${row.target_column}`}
                              </option>
                            ),
                          )
                        }
                      </select>
                    </label>

                    <p className="wide">{t('objectConfig.strategyHint')}</p>
                  </div>
                )
              }

              {
                rows.length > 0 && (
                  <div className="data-table-wrapper wide" style={{ maxHeight: '520px', overflowY: 'auto' }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('objectConfig.colInclude')}</th>
                          <th>{t('objectConfig.colSource')}</th>
                          <th>{t('objectConfig.colTarget')}</th>
                          <th>{t('objectConfig.colTargetType')}</th>
                          <th>{t('objectConfig.colConversion')}</th>
                          <th>{t('objectConfig.colPolicy')}</th>
                          <th>{t('objectConfig.colNull')}</th>
                          <th>{t('objectConfig.colKey')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {
                          rows.map(
                            row => {
                              const targetType = effectiveTargetType(row)
                              const compat = compatibility(row.source_type, targetType)
                              const existing = targetColumns.has(row.target_column)
                              const keyPosition = keyColumns.indexOf(row.source_column)
                              const disabled = busy || legacy || !row.included

                              return (
                                <tr key={row.source_column}>
                                  <td>
                                    <input
                                      type="checkbox"
                                      checked={row.included}
                                      disabled={busy || legacy}
                                      onChange={event => updateRow(row.source_column, { included: event.target.checked })}
                                    />
                                  </td>
                                  <td>
                                    <code>{row.source_column}</code>
                                    <br />
                                    <small>
                                      {row.source_type}
                                      {row.nullable ? '' : ' NOT NULL'}
                                      {row.in_snapshot ? '' : ` · ${t('objectConfig.notInSnapshot')}`}
                                    </small>
                                  </td>
                                  <td>
                                    {
                                      target !== null && (
                                        <select
                                          value={existing ? row.target_column : NEW_COLUMN}
                                          disabled={disabled}
                                          onChange={
                                            event =>
                                              updateRow(
                                                row.source_column,
                                                event.target.value === NEW_COLUMN
                                                  ? {
                                                      target_column: targetColumns.has(columnName(row.source_column))
                                                        ? `${columnName(row.source_column)}_new`.slice(0, 63)
                                                        : columnName(row.source_column),
                                                    }
                                                  : { target_column: event.target.value },
                                              )
                                          }
                                        >
                                          {
                                            target.columns
                                              .filter(
                                                column =>
                                                  column.name === row.target_column
                                                  || compatibility(row.source_type, column.type) !== 'INCOMPATIBLE',
                                              )
                                              .map(
                                                column => (
                                                  <option key={column.name} value={column.name}>
                                                    {`${column.name} (${column.type})`}
                                                  </option>
                                                ),
                                              )
                                          }
                                          <option value={NEW_COLUMN}>{t('objectConfig.newColumn')}</option>
                                        </select>
                                      )
                                    }
                                    {
                                      !existing && (
                                        <input
                                          value={row.target_column}
                                          disabled={disabled}
                                          onChange={
                                            event =>
                                              updateRow(row.source_column, {
                                                target_column: event.target.value.trim().toLowerCase(),
                                              })
                                          }
                                        />
                                      )
                                    }
                                  </td>
                                  <td>
                                    {
                                      existing
                                        ? targetType
                                        : (
                                          <select
                                            value={row.target_type}
                                            disabled={disabled}
                                            onChange={event => updateRow(row.source_column, { target_type: event.target.value })}
                                          >
                                            {
                                              Array.from(new Set([row.source_type, row.target_type, ...typeChoices]))
                                                .filter(type => type === row.source_type || compatibility(row.source_type, type) !== 'INCOMPATIBLE')
                                                .map(
                                                  type => (
                                                    <option key={type} value={type}>{type}</option>
                                                  ),
                                                )
                                            }
                                          </select>
                                        )
                                    }
                                  </td>
                                  <td>
                                    <span
                                      className={
                                        compat === 'INCOMPATIBLE'
                                          ? 'badge badge-danger'
                                          : compat === 'CONDITIONAL'
                                            ? 'badge badge-warning'
                                            : 'badge badge-success'
                                      }
                                    >
                                      {t(`objectConfig.compat.${compat}`)}
                                    </span>
                                    {
                                      compat === 'CONDITIONAL' && (
                                        <label>
                                          <input
                                            type="checkbox"
                                            checked={row.confirmed}
                                            disabled={disabled}
                                            onChange={event => updateRow(row.source_column, { confirmed: event.target.checked })}
                                          />
                                          {' '}
                                          {t('objectConfig.confirm')}
                                        </label>
                                      )
                                    }
                                  </td>
                                  <td>
                                    <select
                                      value={row.write_policy}
                                      disabled={disabled}
                                      onChange={event => updateRow(row.source_column, { write_policy: event.target.value as WritePolicy })}
                                    >
                                      {
                                        WRITE_POLICIES.map(
                                          policy => (
                                            <option key={policy} value={policy}>{t(`objectConfig.policy.${policy}`)}</option>
                                          ),
                                        )
                                      }
                                    </select>
                                  </td>
                                  <td>
                                    <input
                                      type="checkbox"
                                      checked={row.allow_explicit_null}
                                      disabled={disabled}
                                      onChange={event => updateRow(row.source_column, { allow_explicit_null: event.target.checked })}
                                    />
                                  </td>
                                  <td>
                                    <input
                                      type="checkbox"
                                      checked={keyPosition >= 0}
                                      disabled={disabled}
                                      onChange={() => toggleKey(row.source_column)}
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
                  !legacy && (
                    <>
                      <button
                        type="button"
                        className="admin-primary-button"
                        disabled={busy || !dirty || !namesValid || rows.length === 0}
                        title={namesValid ? undefined : t('objectConfig.namesInvalid')}
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
                {
                  current !== null && (
                    <button
                      type="button"
                      className="admin-secondary-button"
                      disabled={busy}
                      onClick={() => { void checkDrift() }}
                    >
                      {t('objectConfig.checkDrift')}
                    </button>
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
                config.proposals.length > 0 && (
                  <div className="wide">
                    <h4>{t('objectConfig.proposalsTitle')}</h4>
                    {
                      config.proposals.map(
                        proposal => (
                          <div key={proposal.proposal_id}>
                            <p>
                              {`#${proposal.proposal_id} · ${proposal.change_kind} · ${proposal.target_schema}.${proposal.target_table} · `}
                              <span className={proposal.status === 'PROPOSED' ? 'badge badge-warning' : 'badge'}>
                                {t(`objectConfig.proposalStatus.${proposal.status}`)}
                              </span>
                              {` · ${formatDateTime(proposal.created_at)}`}
                              {
                                proposal.status === 'PROPOSED' && (
                                  <>
                                    {' '}
                                    <button
                                      type="button"
                                      className="admin-link-button"
                                      disabled={busy}
                                      onClick={() => { void reject(proposal.proposal_id) }}
                                    >
                                      {t('objectConfig.reject')}
                                    </button>
                                  </>
                                )
                              }
                            </p>
                            <pre style={{ whiteSpace: 'pre-wrap', overflowX: 'auto' }}>{proposal.ddl}</pre>
                          </div>
                        ),
                      )
                    }
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
    </section>
  )
}


export default ObjectConfigPanel
