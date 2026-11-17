import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useTranslation } from 'react-i18next'

import { confirmAction } from './confirmAction'
import { formatDateTime } from './dateTime'
import {
  createDmVersion,
  discardDmVersion,
  fieldTypeFor,
  getDmCatalog,
  getDmMarts,
  getDmVersion,
  proposeDmDdl,
  publishDmVersion,
  rejectDmDdl,
  saveDmEntity,
  saveDmEntityKey,
  saveDmMart,
  saveDmVersion,
  type DmAggregate,
  type DmCatalog,
  type DmDefinition,
  type DmFieldDef,
  type DmMart,
  type DmMartSave,
  type DmOperator,
  type DmSourceDef,
  type DmSourceKind,
  type DmVersionDetail,
} from './dmApi'
import './AiAssistantPanel.css'
import './LoadsPage.css'
import './DmBuilderPanel.css'


// I19: Administration -> "DM marts" (ADMIN_INTEGRATION_MANAGE): catalog of entities and their key columns in
// ODS tables (I19-01) and the mart builder (I19-02): grain, sources (same key, child with aggregates, lookups
// and roles of one table), fields with priority, filters, preview of the fill SQL, DDL proposal (applied by a
// SQL package), publication. The database checks every call and validates the version (package 018).

const KINDS: DmSourceKind[] = ['GRAIN', 'SAME_KEY', 'CHILD', 'LOOKUP']
const AGGREGATES: DmAggregate[] = ['SUM', 'COUNT', 'COUNT_DISTINCT', 'MIN', 'MAX', 'AVG']
const OPERATORS: DmOperator[] = ['=', '<>', '>', '>=', '<', '<=', 'IN', 'IS NULL', 'IS NOT NULL', 'LIKE']
const TYPE_HINTS = ['text', 'varchar(100)', 'bigint', 'integer', 'numeric', 'numeric(18,2)', 'date', 'timestamp',
  'timestamptz', 'boolean']
const PERMISSIONS = ['REPORT_VIEW', 'ETL_VIEW', 'DQ_VIEW', 'INTEGRATION_VIEW', 'ADMIN_INTEGRATION_MANAGE']

const EMPTY_ENTITY = { entity_id: null as number | null, entity_code: '', title: '', is_active: true }
const EMPTY_MART: DmMartSave = {
  mart_id: null, mart_code: '', title: '', description: '', required_permission_code: 'REPORT_VIEW',
  ai_enabled: false, auto_after_ods: true, is_active: true,
}

// The filter value is edited as text: IN takes a comma-separated list.
interface FilterRow {
  alias: string
  column: string
  operator: DmOperator
  valueText: string
}

interface Draft {
  grain_entity_id: number | null
  sources: DmSourceDef[]
  fields: DmFieldDef[]
  filters: FilterRow[]
}


function toDraft(definition: DmDefinition): Draft {
  return {
    grain_entity_id: definition.grain_entity_id,
    sources: definition.sources.map(source => ({ ...source })),
    fields: definition.fields.map(field => ({ ...field, sources: field.sources.map(item => ({ ...item })) })),
    filters: definition.filters.map(filter => ({
      alias: filter.alias,
      column: filter.column,
      operator: filter.operator,
      valueText: Array.isArray(filter.value) ? filter.value.join(', ') : filter.value === null ? '' : String(filter.value),
    })),
  }
}

function toDefinition(draft: Draft): DmDefinition {
  return {
    grain_entity_id: draft.grain_entity_id,
    sources: draft.sources.map(source => source.kind === 'LOOKUP'
      ? { alias: source.alias, table: source.table, kind: source.kind, ref_alias: source.ref_alias ?? null,
          ref_column: source.ref_column ?? null, lookup_column: source.lookup_column ?? null }
      : { alias: source.alias, table: source.table, kind: source.kind }),
    fields: draft.fields.map(field => ({ ...field, title: field.title || null })),
    filters: draft.filters.map(filter => ({
      alias: filter.alias,
      column: filter.column,
      operator: filter.operator,
      value: filter.operator === 'IS NULL' || filter.operator === 'IS NOT NULL'
        ? null
        : filter.operator === 'IN'
          ? filter.valueText.split(',').map(part => part.trim()).filter(part => part !== '')
          : filter.valueText,
    })),
  }
}


function DmBuilderPanel() {
  const { t } = useTranslation()
  const [catalog, setCatalog] = useState<DmCatalog | null>(null)
  const [marts, setMarts] = useState<DmMart[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [entityForm, setEntityForm] = useState(EMPTY_ENTITY)
  const [keyForm, setKeyForm] = useState({ entity_id: '', table_name: '', column_name: '' })
  const [martForm, setMartForm] = useState<DmMartSave>(EMPTY_MART)
  const [selectedMartId, setSelectedMartId] = useState<number | null>(null)
  const [detail, setDetail] = useState<DmVersionDetail | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [problems, setProblems] = useState<string[] | null>(null)
  const loadSeq = useRef(0)
  const versionSeq = useRef(0)

  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const [nextCatalog, nextMarts] = await Promise.all([getDmCatalog(), getDmMarts()])
      if (seq !== loadSeq.current) {
        return
      }
      setCatalog(nextCatalog)
      setMarts(nextMarts)
      setError(null)
    } catch (exc) {
      if (seq === loadSeq.current) {
        setError(exc instanceof Error ? exc.message : String(exc))
      }
    }
  }, [])

  useEffect(() => {
    // The first load starts after the panel is shown; state updates happen after the await.
    const timer = window.setTimeout(() => {
      void load()
    }, 0)
    return () => {
      window.clearTimeout(timer)
      loadSeq.current += 1
      versionSeq.current += 1
    }
  }, [load])

  async function openVersion(versionId: number) {
    const seq = ++versionSeq.current
    try {
      const next = await getDmVersion(versionId)
      if (seq !== versionSeq.current) {
        return
      }
      setDetail(next)
      setDraft(toDraft(next.definition))
      setProblems(null)
      setError(null)
    } catch (exc) {
      if (seq === versionSeq.current) {
        setError(exc instanceof Error ? exc.message : String(exc))
      }
    }
  }

  async function run(action: () => Promise<unknown>, done?: (result: unknown) => void | Promise<void>,
    message?: string) {
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const result = await action()
      if (done) {
        await done(result)
      }
      setNotice(message ?? t('dm.saved'))
      await load()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setSaving(false)
    }
  }

  // ------------------------------------------------------------------ catalog of keys

  const tables = catalog?.ods_tables ?? []
  const entities = catalog?.entities ?? []

  function columnsOf(table: string | undefined | null): { name: string, type: string }[] {
    return tables.find(item => item.table_name === table)?.columns ?? []
  }

  function submitEntity(event: FormEvent) {
    event.preventDefault()
    void run(() => saveDmEntity({ entity_id: entityForm.entity_id,
      entity_code: entityForm.entity_id === null ? entityForm.entity_code : null,
      title: entityForm.title, is_active: entityForm.is_active }), () => setEntityForm(EMPTY_ENTITY))
  }

  function submitKey(event: FormEvent) {
    event.preventDefault()
    void run(() => saveDmEntityKey(Number(keyForm.entity_id), { table_name: keyForm.table_name,
      column_name: keyForm.column_name, is_active: true }),
    () => setKeyForm({ ...keyForm, table_name: '', column_name: '' }))
  }

  // ------------------------------------------------------------------ marts

  const selectedMart = marts.find(item => item.mart_id === selectedMartId) ?? null

  function selectMart(mart: DmMart) {
    setSelectedMartId(mart.mart_id)
    setMartForm({ mart_id: mart.mart_id, mart_code: mart.mart_code, title: mart.title, description: mart.description ?? '',
      required_permission_code: mart.required_permission_code, ai_enabled: mart.ai_enabled,
      auto_after_ods: mart.auto_after_ods, is_active: mart.is_active })
    versionSeq.current += 1
    setDetail(null)
    setDraft(null)
    setProblems(null)
    const current = mart.versions.find(item => item.status === 'DRAFT') ?? mart.versions.find(item => item.status === 'PUBLISHED')
    if (current) {
      void openVersion(current.version_id)
    }
  }

  function submitMart(event: FormEvent) {
    event.preventDefault()
    void run(() => saveDmMart({ ...martForm, description: martForm.description || null,
      mart_code: martForm.mart_id === null ? martForm.mart_code : null }),
    result => {
      const created = result as { mart_id: number }
      setSelectedMartId(created.mart_id)
      setMartForm({ ...martForm, mart_id: created.mart_id })
    })
  }

  function newMart() {
    setSelectedMartId(null)
    setMartForm(EMPTY_MART)
    versionSeq.current += 1
    setDetail(null)
    setDraft(null)
    setProblems(null)
  }

  function newVersion() {
    if (selectedMartId === null) {
      return
    }
    void run(() => createDmVersion(selectedMartId), async result => {
      await openVersion((result as { version_id: number }).version_id)
    })
  }

  // ------------------------------------------------------------------ version editor

  const readOnly = detail === null || detail.version.status !== 'DRAFT'

  function patch(next: Partial<Draft>) {
    if (draft) {
      setDraft({ ...draft, ...next })
    }
  }

  function setSource(index: number, next: Partial<DmSourceDef>) {
    if (!draft) {
      return
    }
    const sources = draft.sources.map((item, i) => i === index ? { ...item, ...next } : item)
    patch({ sources })
  }

  function addSource() {
    if (!draft) {
      return
    }
    const kind: DmSourceKind = draft.sources.some(item => item.kind === 'GRAIN') ? 'SAME_KEY' : 'GRAIN'
    patch({ sources: [...draft.sources, { alias: `s${draft.sources.length + 1}`, table: '', kind }] })
  }

  function removeSource(index: number) {
    if (draft) {
      patch({ sources: draft.sources.filter((_, i) => i !== index) })
    }
  }

  function grainKey(table: string): string | null {
    const entity = entities.find(item => item.entity_id === draft?.grain_entity_id)
    return entity?.keys.find(key => key.table_name === table && key.is_active)?.column_name ?? null
  }

  function kindOf(alias: string): DmSourceKind | null {
    return draft?.sources.find(item => item.alias === alias)?.kind ?? null
  }

  function tableOf(alias: string): string | null {
    return draft?.sources.find(item => item.alias === alias)?.table ?? null
  }

  function setField(index: number, next: Partial<DmFieldDef>) {
    if (draft) {
      patch({ fields: draft.fields.map((item, i) => i === index ? { ...item, ...next } : item) })
    }
  }

  function addField() {
    if (draft) {
      const alias = draft.sources[0]?.alias ?? ''
      patch({ fields: [...draft.fields, { name: '', title: null, type: 'text', is_key: false,
        sources: [{ alias, column: '', aggregate: 'NONE' }] }] })
    }
  }

  // All columns of a non-child source as fields (the grain key becomes the key field).
  function addAllColumns(source: DmSourceDef) {
    if (!draft) {
      return
    }
    const names = new Set(draft.fields.map(item => item.name))
    const key = source.kind === 'GRAIN' ? grainKey(source.table) : null
    const added: DmFieldDef[] = []
    for (const column of columnsOf(source.table)) {
      let name = column.name
      if (names.has(name)) {
        name = `${source.alias}_${column.name}`
      }
      if (names.has(name) || !/^[a-z][a-z0-9_]{0,62}$/.test(name) || name === 'dm_loaded_at') {
        continue
      }
      names.add(name)
      added.push({ name, title: null, type: fieldTypeFor(column.type),
        is_key: key === column.name && !draft.fields.some(item => item.is_key),
        sources: [{ alias: source.alias, column: column.name, aggregate: 'NONE' }] })
    }
    patch({ fields: [...draft.fields, ...added] })
  }

  function setFieldSource(fieldIndex: number, sourceIndex: number, next: Partial<DmFieldDef['sources'][number]>) {
    if (!draft) {
      return
    }
    const field = draft.fields[fieldIndex]
    const sources = field.sources.map((item, i) => {
      if (i !== sourceIndex) {
        return item
      }
      const merged = { ...item, ...next }
      // Aggregates only for CHILD sources (the database refuses others).
      if (kindOf(merged.alias) !== 'CHILD') {
        merged.aggregate = 'NONE'
      } else if (merged.aggregate === 'NONE') {
        merged.aggregate = 'SUM'
      }
      return merged
    })
    const first = sources[0]
    const named = field.name === '' && first && first.column !== ''
      ? { name: first.column, type: fieldTypeFor(columnsOf(tableOf(first.alias)).find(c => c.name === first.column)?.type ?? 'text') }
      : {}
    setField(fieldIndex, { sources, ...named })
  }

  function setFilter(index: number, next: Partial<FilterRow>) {
    if (draft) {
      patch({ filters: draft.filters.map((item, i) => i === index ? { ...item, ...next } : item) })
    }
  }

  function saveVersion() {
    if (!detail || !draft) {
      return
    }
    const versionId = detail.version.version_id
    void run(() => saveDmVersion(versionId, toDefinition(draft)), async result => {
      const saved = result as { problems: string[] }
      await openVersion(versionId)
      setProblems(saved.problems)
    }, t('dm.version.saved'))
  }

  function versionAction(action: (id: number) => Promise<unknown>, message: string, reopen = true) {
    if (!detail) {
      return
    }
    const versionId = detail.version.version_id
    void run(() => action(versionId), async () => {
      if (reopen) {
        await openVersion(versionId)
      } else {
        setDetail(null)
        setDraft(null)
      }
    }, message)
  }

  async function publish() {
    if (!detail) {
      return
    }
    const confirmed = await confirmAction({
      title: t('dm.version.publish'),
      message: t('dm.version.confirmPublish', { mart: detail.version.mart_code, no: detail.version.version_no }),
      confirmLabel: t('dm.version.publish'),
      cancelLabel: t('loads.cancel'),
    })
    if (confirmed) {
      versionAction(publishDmVersion, t('dm.version.published'))
    }
  }

  // ------------------------------------------------------------------ render

  const tableOptions = tables.map(item => (
    <option key={item.table_name} value={item.table_name}>ods.{item.table_name}</option>
  ))

  function columnOptions(table: string | null | undefined) {
    return columnsOf(table).map(item => <option key={item.name} value={item.name}>{item.name} ({item.type})</option>)
  }

  function renderSources(current: Draft) {
    return (
      <div className="dm-block">
        <h5>{t('dm.version.sources')}</h5>
        <p className="loads-muted">{t('dm.version.sourcesHint')}</p>
        <table className="data-table dm-edit-table">
          <thead>
            <tr>
              <th>{t('dm.version.alias')}</th><th>{t('dm.version.table')}</th><th>{t('dm.version.kind')}</th>
              <th>{t('dm.version.join')}</th><th />
            </tr>
          </thead>
          <tbody>
            {current.sources.map((source, index) => {
              const earlier = current.sources.slice(0, index).filter(item => item.kind !== 'CHILD')
              const key = source.kind === 'LOOKUP' ? null : grainKey(source.table)
              return (
                <tr key={index}>
                  <td>
                    <input className="dm-alias" disabled={readOnly} value={source.alias}
                      onChange={e => setSource(index, { alias: e.target.value.trim().toLowerCase() })} />
                  </td>
                  <td>
                    <select disabled={readOnly} value={source.table}
                      onChange={e => setSource(index, { table: e.target.value, lookup_column: null })}>
                      <option value="" />{tableOptions}
                    </select>
                  </td>
                  <td>
                    <select disabled={readOnly} value={source.kind}
                      onChange={e => setSource(index, { kind: e.target.value as DmSourceKind })}>
                      {KINDS.map(kind => <option key={kind} value={kind}>{t(`dm.kind.${kind}`)}</option>)}
                    </select>
                  </td>
                  <td>
                    {source.kind === 'LOOKUP'
                      ? (
                        <div className="dm-inline">
                          <select disabled={readOnly} value={source.lookup_column ?? ''}
                            onChange={e => setSource(index, { lookup_column: e.target.value })}>
                            <option value="" />{columnOptions(source.table)}
                          </select>
                          <span>=</span>
                          <select disabled={readOnly} value={source.ref_alias ?? ''}
                            onChange={e => setSource(index, { ref_alias: e.target.value, ref_column: null })}>
                            <option value="" />
                            {earlier.map(item => <option key={item.alias} value={item.alias}>{item.alias}</option>)}
                          </select>
                          <select disabled={readOnly} value={source.ref_column ?? ''}
                            onChange={e => setSource(index, { ref_column: e.target.value })}>
                            <option value="" />{columnOptions(tableOf(source.ref_alias ?? ''))}
                          </select>
                        </div>
                      )
                      : source.table === ''
                        ? null
                        : key
                          ? <span className="code-value">{source.table}.{key}</span>
                          : <span className="dm-warning">{t('dm.version.noKey')}</span>}
                  </td>
                  <td className="loads-actions">
                    {!readOnly && source.kind !== 'CHILD' && source.table !== ''
                      ? <button type="button" onClick={() => addAllColumns(source)}>{t('dm.version.allColumns')}</button>
                      : null}
                    {!readOnly ? <button type="button" onClick={() => removeSource(index)}>{t('dm.remove')}</button> : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!readOnly ? <button type="button" className="loads-refresh" onClick={addSource}>{t('dm.version.addSource')}</button> : null}
      </div>
    )
  }

  function renderFields(current: Draft) {
    return (
      <div className="dm-block">
        <h5>{t('dm.version.fields')}</h5>
        <p className="loads-muted">{t('dm.version.fieldsHint')}</p>
        <datalist id="dm-types">{TYPE_HINTS.map(type => <option key={type} value={type} />)}</datalist>
        <table className="data-table dm-edit-table">
          <thead>
            <tr>
              <th>{t('dm.version.fieldName')}</th><th>{t('dm.version.fieldTitle')}</th><th>{t('dm.version.type')}</th>
              <th>{t('dm.version.key')}</th><th>{t('dm.version.valueFrom')}</th><th />
            </tr>
          </thead>
          <tbody>
            {current.fields.map((field, index) => (
              <tr key={index}>
                <td>
                  <input disabled={readOnly} value={field.name}
                    onChange={e => setField(index, { name: e.target.value.trim().toLowerCase() })} />
                </td>
                <td>
                  <input disabled={readOnly} value={field.title ?? ''} onChange={e => setField(index, { title: e.target.value })} />
                </td>
                <td>
                  <input className="dm-type" disabled={readOnly} list="dm-types" value={field.type}
                    onChange={e => setField(index, { type: e.target.value.trim().toLowerCase() })} />
                </td>
                <td>
                  <input type="checkbox" disabled={readOnly} checked={field.is_key}
                    onChange={e => patch({ fields: current.fields.map((item, i) => ({
                      ...item, is_key: i === index ? e.target.checked : (e.target.checked ? false : item.is_key) })) })} />
                </td>
                <td>
                  {field.sources.map((item, sourceIndex) => (
                    <div className="dm-inline" key={sourceIndex}>
                      <small>{sourceIndex + 1}.</small>
                      <select disabled={readOnly} value={item.alias}
                        onChange={e => setFieldSource(index, sourceIndex, { alias: e.target.value, column: '' })}>
                        <option value="" />
                        {current.sources.map(source => <option key={source.alias} value={source.alias}>{source.alias}</option>)}
                      </select>
                      <select disabled={readOnly} value={item.column}
                        onChange={e => setFieldSource(index, sourceIndex, { column: e.target.value })}>
                        <option value="" />{columnOptions(tableOf(item.alias))}
                      </select>
                      {kindOf(item.alias) === 'CHILD'
                        ? (
                          <select disabled={readOnly} value={item.aggregate}
                            onChange={e => setFieldSource(index, sourceIndex, { aggregate: e.target.value as DmAggregate })}>
                            {AGGREGATES.map(agg => <option key={agg} value={agg}>{t(`dm.agg.${agg}`)}</option>)}
                          </select>
                        )
                        : null}
                      {!readOnly && field.sources.length > 1
                        ? (
                          <button type="button" className="loads-link"
                            onClick={() => setField(index, { sources: field.sources.filter((_, i) => i !== sourceIndex) })}>
                            ×
                          </button>
                        )
                        : null}
                    </div>
                  ))}
                  {!readOnly && !field.is_key && field.sources.length < 9
                    ? (
                      <button type="button" className="loads-link"
                        onClick={() => setField(index, { sources: [...field.sources, { alias: '', column: '', aggregate: 'NONE' }] })}>
                        {t('dm.version.addPriority')}
                      </button>
                    )
                    : null}
                </td>
                <td className="loads-actions">
                  {!readOnly
                    ? (
                      <button type="button" onClick={() => patch({ fields: current.fields.filter((_, i) => i !== index) })}>
                        {t('dm.remove')}
                      </button>
                    )
                    : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!readOnly ? <button type="button" className="loads-refresh" onClick={addField}>{t('dm.version.addField')}</button> : null}
      </div>
    )
  }

  function renderFilters(current: Draft) {
    return (
      <div className="dm-block">
        <h5>{t('dm.version.filters')}</h5>
        <table className="data-table dm-edit-table">
          <tbody>
            {current.filters.map((filter, index) => (
              <tr key={index}>
                <td>
                  <select disabled={readOnly} value={filter.alias}
                    onChange={e => setFilter(index, { alias: e.target.value, column: '' })}>
                    <option value="" />
                    {current.sources.map(source => <option key={source.alias} value={source.alias}>{source.alias}</option>)}
                  </select>
                </td>
                <td>
                  <select disabled={readOnly} value={filter.column} onChange={e => setFilter(index, { column: e.target.value })}>
                    <option value="" />{columnOptions(tableOf(filter.alias))}
                  </select>
                </td>
                <td>
                  <select disabled={readOnly} value={filter.operator}
                    onChange={e => setFilter(index, { operator: e.target.value as DmOperator })}>
                    {OPERATORS.map(op => <option key={op} value={op}>{op}</option>)}
                  </select>
                </td>
                <td>
                  <input disabled={readOnly || filter.operator === 'IS NULL' || filter.operator === 'IS NOT NULL'}
                    value={filter.valueText} placeholder={filter.operator === 'IN' ? t('dm.version.inHint') : ''}
                    onChange={e => setFilter(index, { valueText: e.target.value })} />
                </td>
                <td className="loads-actions">
                  {!readOnly
                    ? (
                      <button type="button" onClick={() => patch({ filters: current.filters.filter((_, i) => i !== index) })}>
                        {t('dm.remove')}
                      </button>
                    )
                    : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!readOnly
          ? (
            <button type="button" className="loads-refresh"
              onClick={() => patch({ filters: [...current.filters, { alias: current.sources[0]?.alias ?? '', column: '',
                operator: '=', valueText: '' }] })}>
              {t('dm.version.addFilter')}
            </button>
          )
          : null}
      </div>
    )
  }

  function renderVersion() {
    if (!detail || !draft) {
      return null
    }
    const version = detail.version
    return (
      <div className="dm-version">
        <h4>
          {t('dm.version.title', { mart: version.mart_code, no: version.version_no })}{' '}
          <span className="badge">{t(`dm.status.${version.status}`)}</span>{' '}
          {version.ddl_status !== 'NONE' ? <span className="badge">{t(`dm.ddl.${version.ddl_status}`)}</span> : null}
        </h4>
        {readOnly ? <p className="loads-muted">{t('dm.version.readOnly')}</p> : null}

        <label className="dm-grain">{t('dm.version.grain')}
          <select disabled={readOnly} value={draft.grain_entity_id ?? ''}
            onChange={e => patch({ grain_entity_id: e.target.value === '' ? null : Number(e.target.value) })}>
            <option value="" />
            {entities.filter(item => item.is_active).map(item => (
              <option key={item.entity_id} value={item.entity_id}>{item.title} ({item.entity_code})</option>
            ))}
          </select>
        </label>

        {renderSources(draft)}
        {renderFields(draft)}
        {renderFilters(draft)}

        {problems !== null
          ? problems.length === 0
            ? <div className="loads-notice">{t('dm.version.noProblems')}</div>
            : (
              <div className="error">
                <div>
                  <strong>{t('dm.version.problems')}</strong>
                  <ul>{problems.map(item => <li key={item}>{item}</li>)}</ul>
                </div>
              </div>
            )
          : null}
        {detail.missing.length > 0
          ? <div className="error"><span>{t('dm.version.missing', { list: detail.missing.join(', ') })}</span></div>
          : null}

        <div className="loads-actions dm-actions">
          {!readOnly
            ? (
              <>
                <button type="button" disabled={saving} onClick={saveVersion}>{t('dm.version.save')}</button>
                <button type="button" disabled={saving}
                  onClick={() => versionAction(proposeDmDdl, t('dm.version.ddlProposed'))}>
                  {t('dm.version.proposeDdl')}
                </button>
                {version.ddl_status === 'PROPOSED'
                  ? (
                    <button type="button" disabled={saving} onClick={() => versionAction(rejectDmDdl, t('dm.saved'))}>
                      {t('dm.version.rejectDdl')}
                    </button>
                  )
                  : null}
                <button type="button" disabled={saving} onClick={() => { void publish() }}>{t('dm.version.publish')}</button>
                <button type="button" disabled={saving || version.ddl_status === 'APPLIED'}
                  onClick={() => versionAction(discardDmVersion, t('dm.version.discarded'), false)}>
                  {t('dm.version.discard')}
                </button>
              </>
            )
            : null}
          {selectedMart && !selectedMart.versions.some(item => item.status === 'DRAFT')
            ? <button type="button" disabled={saving} onClick={newVersion}>{t('dm.version.newDraft')}</button>
            : null}
        </div>

        {version.ddl_text
          ? (
            <div className="dm-block">
              <h5>{t('dm.version.ddl')}</h5>
              <p className="loads-muted">{t('dm.version.ddlHint')}</p>
              <pre className="dm-sql">{version.ddl_text}</pre>
              <small className="code-value">SHA-256 {version.ddl_sha256}</small>
            </div>
          )
          : null}
        <div className="dm-block">
          <h5>{t('dm.version.fillSql')}</h5>
          <p className="loads-muted">{t('dm.version.fillSqlHint')}</p>
          {detail.fill_sql
            ? <pre className="dm-sql">{detail.fill_sql}</pre>
            : <span className="loads-muted">{detail.fill_sql_error ?? t('dm.version.fillSqlEmpty')}</span>}
        </div>
      </div>
    )
  }

  return (
    <section className="content-panel ai-admin-grid">
      <p className="loads-muted">{t('dm.hint')}</p>
      {error ? <div className="error"><span>{error}</span></div> : null}
      {notice ? <div className="loads-notice">{notice}</div> : null}

      <div>
        <h4>{t('dm.entities')}</h4>
        <p className="loads-muted">{t('dm.entitiesHint')}</p>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr><th>{t('dm.entity')}</th><th>{t('dm.keys')}</th><th>{t('aiAdmin.status')}</th><th /></tr>
            </thead>
            <tbody>
              {entities.map(item => (
                <tr key={item.entity_id}>
                  <td><strong>{item.title}</strong><div className="code-value">{item.entity_code}</div></td>
                  <td>
                    {item.keys.map(key => (
                      <div key={key.entity_key_id} className={key.is_active ? '' : 'loads-muted'}>
                        <span className="code-value">{key.table_name}.{key.column_name}</span>{' '}
                        <small>{key.type_family}</small>{' '}
                        <button type="button" className="loads-link" disabled={saving}
                          onClick={() => void run(() => saveDmEntityKey(item.entity_id, { table_name: key.table_name,
                            column_name: key.column_name, is_active: !key.is_active }))}>
                          {key.is_active ? t('aiAdmin.disable') : t('aiAdmin.enable')}
                        </button>
                      </div>
                    ))}
                  </td>
                  <td>{item.is_active ? t('aiAdmin.active') : t('aiAdmin.inactive')}</td>
                  <td className="loads-actions">
                    <button type="button" disabled={saving}
                      onClick={() => setEntityForm({ entity_id: item.entity_id, entity_code: item.entity_code,
                        title: item.title, is_active: item.is_active })}>
                      {t('aiAdmin.edit')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form className="ai-admin-form" onSubmit={submitEntity}>
          <label>{t('dm.entityCode')}
            <input required disabled={entityForm.entity_id !== null} value={entityForm.entity_code} placeholder="CLIENT"
              onChange={e => setEntityForm({ ...entityForm, entity_code: e.target.value.trim().toUpperCase() })} />
          </label>
          <label>{t('dm.entityTitle')}
            <input required value={entityForm.title} onChange={e => setEntityForm({ ...entityForm, title: e.target.value })} />
          </label>
          <label className="dm-check">
            <input type="checkbox" checked={entityForm.is_active}
              onChange={e => setEntityForm({ ...entityForm, is_active: e.target.checked })} />
            {t('aiAdmin.active')}
          </label>
          <button type="submit" disabled={saving}>{t('aiAdmin.save')}</button>
          {entityForm.entity_id !== null
            ? <button type="button" onClick={() => setEntityForm(EMPTY_ENTITY)}>{t('dm.newEntity')}</button>
            : null}
        </form>
        <form className="ai-admin-form" onSubmit={submitKey}>
          <label>{t('dm.entity')}
            <select required value={keyForm.entity_id} onChange={e => setKeyForm({ ...keyForm, entity_id: e.target.value })}>
              <option value="" />
              {entities.map(item => <option key={item.entity_id} value={item.entity_id}>{item.title}</option>)}
            </select>
          </label>
          <label>{t('dm.version.table')}
            <select required value={keyForm.table_name}
              onChange={e => setKeyForm({ ...keyForm, table_name: e.target.value, column_name: '' })}>
              <option value="" />{tableOptions}
            </select>
          </label>
          <label>{t('dm.keyColumn')}
            <select required value={keyForm.column_name} onChange={e => setKeyForm({ ...keyForm, column_name: e.target.value })}>
              <option value="" />{columnOptions(keyForm.table_name)}
            </select>
          </label>
          <button type="submit" disabled={saving}>{t('dm.addKey')}</button>
        </form>
      </div>

      <div>
        <h4>{t('dm.marts')}</h4>
        <p className="loads-muted">{t('dm.martsHint')}</p>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr><th>{t('dm.mart')}</th><th>{t('aiAdmin.permission')}</th><th>{t('dm.versions')}</th><th /></tr>
            </thead>
            <tbody>
              {marts.map(item => (
                <tr key={item.mart_id} className={item.mart_id === selectedMartId ? 'dm-selected' : undefined}>
                  <td>
                    <strong>{item.title}</strong><div className="code-value">dm.{item.mart_code}</div>
                    {!item.is_active ? <span className="badge">{t('dm.inactive')}</span> : null}
                    {item.ai_enabled ? <span className="badge">{t('dm.loads.ai')}</span> : null}
                  </td>
                  <td><code>{item.required_permission_code}</code></td>
                  <td>
                    {item.versions.map(version => (
                      <div key={version.version_id}>
                        <button type="button" className="loads-link" onClick={() => {
                          setSelectedMartId(item.mart_id)
                          void openVersion(version.version_id)
                        }}>
                          v{version.version_no}
                        </button>{' '}
                        <small>{t(`dm.status.${version.status}`)} · {formatDateTime(version.published_at ?? version.updated_at)}</small>
                      </div>
                    ))}
                  </td>
                  <td className="loads-actions">
                    <button type="button" onClick={() => selectMart(item)}>{t('dm.open')}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form className="ai-admin-form" onSubmit={submitMart}>
          <label>{t('dm.martCode')}
            <input required disabled={martForm.mart_id !== null} value={martForm.mart_code ?? ''} placeholder="pension_requests"
              onChange={e => setMartForm({ ...martForm, mart_code: e.target.value.trim().toLowerCase() })} />
          </label>
          <label>{t('dm.martTitle')}
            <input required value={martForm.title} onChange={e => setMartForm({ ...martForm, title: e.target.value })} />
          </label>
          <label>{t('aiAdmin.permission')}
            <input required list="dm-permissions" value={martForm.required_permission_code}
              onChange={e => setMartForm({ ...martForm, required_permission_code: e.target.value.trim().toUpperCase() })} />
            <datalist id="dm-permissions">{PERMISSIONS.map(code => <option key={code} value={code} />)}</datalist>
          </label>
          <label>{t('aiAdmin.description')}
            <textarea rows={2} value={martForm.description ?? ''}
              onChange={e => setMartForm({ ...martForm, description: e.target.value })} />
          </label>
          <label className="dm-check">
            <input type="checkbox" checked={martForm.ai_enabled}
              onChange={e => setMartForm({ ...martForm, ai_enabled: e.target.checked })} />
            {t('dm.aiEnabled')}
          </label>
          <label className="dm-check">
            <input type="checkbox" checked={martForm.auto_after_ods}
              onChange={e => setMartForm({ ...martForm, auto_after_ods: e.target.checked })} />
            {t('dm.autoAfterOds')}
          </label>
          <label className="dm-check">
            <input type="checkbox" checked={martForm.is_active}
              onChange={e => setMartForm({ ...martForm, is_active: e.target.checked })} />
            {t('aiAdmin.active')}
          </label>
          <button type="submit" disabled={saving}>{t('aiAdmin.save')}</button>
          {martForm.mart_id !== null ? <button type="button" onClick={newMart}>{t('dm.newMart')}</button> : null}
          {selectedMart && selectedMart.versions.length === 0
            ? <button type="button" disabled={saving} onClick={newVersion}>{t('dm.version.newDraft')}</button>
            : null}
        </form>
      </div>

      {renderVersion()}
    </section>
  )
}

export default DmBuilderPanel
