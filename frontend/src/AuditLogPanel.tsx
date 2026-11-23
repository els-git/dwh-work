// I13-05: Administration -> "Journals" (SUPERADMIN / ADMIN). Two journals: the audit journal (all events) and
// the source password operations. Filters, 100 rows per page, "Show more"; details of an event open on click.
// Secret values never reach the browser: the database hides them (package 029). Codes are shown as they are.
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { formatDateTime } from './dateTime'
import {
  getAuditEvents,
  getAuditFilters,
  getSecretOperations,
  type AuditEvent,
  type AuditEventQuery,
  type AuditFilters,
  type SecretOperation,
  type SecretOperationQuery,
} from './auditLogApi'
import { getIntegrationSourceSystems, type IntegrationSourceSystem } from './integrationAdminApi'
import './AuditLogPanel.css'

type JournalTab = 'events' | 'secrets'

// Dates of the form are days: "from" is the start of the day, "to" includes the whole day.
function dayStart(value: string): string | undefined {
  return value ? new Date(`${value}T00:00:00`).toISOString() : undefined
}

function dayEnd(value: string): string | undefined {
  if (!value) {
    return undefined
  }
  const date = new Date(`${value}T00:00:00`)
  date.setDate(date.getDate() + 1)
  return date.toISOString()
}

function outcomeClass(outcome: string): string {
  const value = outcome.toUpperCase()
  if (value === 'SUCCESS' || value === 'SUCCEEDED' || value === 'PERMITTED_ATTEMPT') {
    return 'audit-outcome audit-outcome-ok'
  }
  if (value.startsWith('FAIL') || value.startsWith('DENIED')) {
    return 'audit-outcome audit-outcome-bad'
  }
  return 'audit-outcome'
}


function EventsJournal() {
  const { t } = useTranslation()
  const [filters, setFilters] = useState<AuditFilters | null>(null)
  const [form, setForm] = useState({ from: '', to: '', event_type: '', actor: '', entity: '', outcome: '' })
  const [query, setQuery] = useState<AuditEventQuery>({})
  const [rows, setRows] = useState<AuditEvent[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const requestNo = useRef(0)

  useEffect(() => {
    let alive = true
    getAuditFilters().then((data) => { if (alive) setFilters(data) }).catch(() => { /* lists stay empty */ })
    return () => { alive = false }
  }, [])

  // A new query replaces the list; a reply to an older query is dropped.
  useEffect(() => {
    const no = ++requestNo.current
    getAuditEvents(query)
      .then((page) => {
        if (no !== requestNo.current) return
        setRows(page.rows)
        setNext(page.has_more ? page.next_before_id : null)
        setError(null)
      })
      .catch((exc: unknown) => {
        if (no === requestNo.current) setError(exc instanceof Error ? exc.message : String(exc))
      })
      .finally(() => {
        if (no === requestNo.current) setLoading(false)
      })
  }, [query])

  const apply = (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setOpen(null)
    setQuery({ from: dayStart(form.from), to: dayEnd(form.to), event_type: form.event_type || undefined,
               actor: form.actor.trim() || undefined, entity: form.entity.trim() || undefined,
               outcome: form.outcome || undefined })
  }

  const more = () => {
    if (next === null) return
    const no = ++requestNo.current
    setLoading(true)
    getAuditEvents({ ...query, before_id: next })
      .then((page) => {
        if (no !== requestNo.current) return
        setRows((prev) => [...prev, ...page.rows])
        setNext(page.has_more ? page.next_before_id : null)
      })
      .catch((exc: unknown) => {
        if (no === requestNo.current) setError(exc instanceof Error ? exc.message : String(exc))
      })
      .finally(() => {
        if (no === requestNo.current) setLoading(false)
      })
  }

  return (
    <div className="audit-journal">
      <form className="audit-filters" onSubmit={apply}>
        <label>{t('audit.from')}<input type="date" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} /></label>
        <label>{t('audit.to')}<input type="date" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} /></label>
        <label>{t('audit.eventType')}
          <select value={form.event_type} onChange={(e) => setForm({ ...form, event_type: e.target.value })}>
            <option value="">{t('audit.all')}</option>
            {(filters?.event_types ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label>{t('audit.actor')}<input value={form.actor} maxLength={100} onChange={(e) => setForm({ ...form, actor: e.target.value })} /></label>
        <label>{t('audit.entity')}<input value={form.entity} maxLength={200} onChange={(e) => setForm({ ...form, entity: e.target.value })} /></label>
        <label>{t('audit.outcome')}
          <select value={form.outcome} onChange={(e) => setForm({ ...form, outcome: e.target.value })}>
            <option value="">{t('audit.all')}</option>
            {(filters?.outcomes ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <button type="submit" className="admin-primary-button" disabled={loading}>{t('audit.show')}</button>
      </form>

      {error ? <p className="audit-error">{t('audit.error', { message: error })}</p> : null}

      <table className="audit-table">
        <thead>
          <tr>
            <th>{t('audit.time')}</th><th>{t('audit.actor')}</th><th>{t('audit.eventType')}</th>
            <th>{t('audit.entity')}</th><th>{t('audit.action')}</th><th>{t('audit.outcome')}</th><th>{t('audit.source')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <EventRow key={row.id} row={row} open={open === row.id} onToggle={() => setOpen(open === row.id ? null : row.id)} />
          ))}
        </tbody>
      </table>
      {!loading && rows.length === 0 && !error ? <p className="audit-note">{t('audit.empty')}</p> : null}
      <div className="audit-footer">
        <span>{t('audit.shown', { count: rows.length })}</span>
        {next !== null ? <button type="button" onClick={more} disabled={loading}>{t('audit.more')}</button> : null}
        {loading ? <span>{t('audit.loading')}</span> : null}
      </div>
    </div>
  )
}


function EventRow({ row, open, onToggle }: { row: AuditEvent, open: boolean, onToggle: () => void }) {
  const { t } = useTranslation()
  const hasDetails = row.details !== null && typeof row.details === 'object' && Object.keys(row.details).length > 0
  return (
    <>
      <tr className={open ? 'audit-row open' : 'audit-row'} onClick={onToggle} title={t('audit.details')}>
        <td className="audit-nowrap">{formatDateTime(row.occurred_at)}</td>
        <td>{row.actor_username ?? '—'}{row.actor_account_level ? <small> {row.actor_account_level}</small> : null}</td>
        <td><code>{row.event_type}</code></td>
        <td>{row.entity_type}{row.entity_key ? <>: <code>{row.entity_key}</code></> : null}</td>
        <td>{row.action}</td>
        <td><span className={outcomeClass(row.outcome)}>{row.outcome}</span></td>
        <td>{row.source}</td>
      </tr>
      {open
        ? (
          <tr className="audit-details-row">
            <td colSpan={7}>
              <div className="audit-details">
                <span>#{row.id}{row.request_id ? ` · request ${row.request_id}` : ''}</span>
                {hasDetails ? <pre>{JSON.stringify(row.details, null, 2)}</pre> : <span>{t('audit.noDetails')}</span>}
              </div>
            </td>
          </tr>
        )
        : null}
    </>
  )
}


function SecretJournal() {
  const { t } = useTranslation()
  const [sources, setSources] = useState<IntegrationSourceSystem[]>([])
  const [form, setForm] = useState({ source: '', from: '', to: '', operation_type: '' })
  const [query, setQuery] = useState<SecretOperationQuery>({})
  const [rows, setRows] = useState<SecretOperation[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const requestNo = useRef(0)

  useEffect(() => {
    let alive = true
    getIntegrationSourceSystems().then((data) => { if (alive) setSources(data) }).catch(() => { /* no source list */ })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const no = ++requestNo.current
    getSecretOperations(query)
      .then((page) => {
        if (no !== requestNo.current) return
        setRows(page.rows)
        setNext(page.has_more ? page.next_before_id : null)
        setError(null)
      })
      .catch((exc: unknown) => {
        if (no === requestNo.current) setError(exc instanceof Error ? exc.message : String(exc))
      })
      .finally(() => {
        if (no === requestNo.current) setLoading(false)
      })
  }, [query])

  const apply = (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setQuery({ source_system_id: form.source ? Number(form.source) : undefined, from: dayStart(form.from),
               to: dayEnd(form.to), operation_type: form.operation_type || undefined })
  }

  const more = () => {
    if (next === null) return
    const no = ++requestNo.current
    setLoading(true)
    getSecretOperations({ ...query, before_id: next })
      .then((page) => {
        if (no !== requestNo.current) return
        setRows((prev) => [...prev, ...page.rows])
        setNext(page.has_more ? page.next_before_id : null)
      })
      .catch((exc: unknown) => {
        if (no === requestNo.current) setError(exc instanceof Error ? exc.message : String(exc))
      })
      .finally(() => {
        if (no === requestNo.current) setLoading(false)
      })
  }

  return (
    <div className="audit-journal">
      <form className="audit-filters" onSubmit={apply}>
        <label>{t('audit.sourceSystem')}
          <select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
            <option value="">{t('audit.all')}</option>
            {sources.map((s) => <option key={s.source_system_id} value={s.source_system_id}>{s.code}</option>)}
          </select>
        </label>
        <label>{t('audit.from')}<input type="date" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} /></label>
        <label>{t('audit.to')}<input type="date" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} /></label>
        <label>{t('audit.operation')}
          <select value={form.operation_type} onChange={(e) => setForm({ ...form, operation_type: e.target.value })}>
            <option value="">{t('audit.all')}</option>
            {['CREATE', 'REPLACE', 'REVOKE', 'READ'].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <button type="submit" className="admin-primary-button" disabled={loading}>{t('audit.show')}</button>
      </form>
      <p className="audit-note">{t('audit.secretsNote')}</p>

      {error ? <p className="audit-error">{t('audit.error', { message: error })}</p> : null}

      <table className="audit-table">
        <thead>
          <tr>
            <th>{t('audit.time')}</th><th>{t('audit.sourceSystem')}</th><th>{t('audit.operation')}</th>
            <th>{t('audit.phase')}</th><th>{t('audit.code')}</th><th>{t('audit.who')}</th><th>{t('audit.operationId')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.event_id} className="audit-row">
              <td className="audit-nowrap">{formatDateTime(row.occurred_at)}</td>
              <td title={row.source_name}>{row.source_code}</td>
              <td><code>{row.operation_type}</code></td>
              <td>{row.phase}</td>
              <td><span className={outcomeClass(row.code)}>{row.code}</span></td>
              <td>{row.actor_kind ?? '—'}{row.actor_ref ? <small> {row.actor_ref}</small> : null}</td>
              <td title={row.operation_id}><code>{row.operation_id.slice(0, 8)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!loading && rows.length === 0 && !error ? <p className="audit-note">{t('audit.empty')}</p> : null}
      <div className="audit-footer">
        <span>{t('audit.shown', { count: rows.length })}</span>
        {next !== null ? <button type="button" onClick={more} disabled={loading}>{t('audit.more')}</button> : null}
        {loading ? <span>{t('audit.loading')}</span> : null}
      </div>
    </div>
  )
}


export default function AuditLogPanel() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<JournalTab>('events')
  return (
    <section className="audit-panel">
      <div className="audit-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'events'} className={tab === 'events' ? 'active' : ''}
                onClick={() => setTab('events')}>{t('audit.eventsTab')}</button>
        <button type="button" role="tab" aria-selected={tab === 'secrets'} className={tab === 'secrets' ? 'active' : ''}
                onClick={() => setTab('secrets')}>{t('audit.secretsTab')}</button>
      </div>
      <p className="audit-note">{t('audit.intro')}</p>
      {tab === 'events' ? <EventsJournal /> : <SecretJournal />}
    </section>
  )
}
