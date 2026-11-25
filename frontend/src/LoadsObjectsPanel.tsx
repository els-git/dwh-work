import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import { confirmAction } from './confirmAction'
import { requestDiagnosis } from './aiBus'
import { formatDateTime } from './dateTime'
import './LoadsPage.css'
import {
  cancelObjectRunRequest,
  createObjectRunRequest,
  getObjectLoadStatus,
  getObjectSchedules,
  type ObjectLoadStatus,
  type ObjectSchedule,
  type ObjectRunKind,
} from './integrationAdminApi'


// I08: tab "Objects" of the section "Data loads": state of every object and run requests.
// Requests are executed by the dispatcher (run_local_dwh.ps1 -Mode Run); the list refreshes
// itself while a request of any object is active.

const REFRESH_MS = 5000

// I08-06: search and filters, applied in the browser to the list already loaded (decision F1 = A).
// The choice survives runs and "Refresh" and is remembered in this browser (F3 = A).
type RunStateFilter = 'ALL' | 'SUCCESS' | 'FAILED' | 'ACTIVE' | 'NEVER'
type ScheduleFilter = 'ALL' | 'ON' | 'OFF'

interface ObjectFilters {
  text: string
  source: string
  state: RunStateFilter
  schedule: ScheduleFilter
}

const EMPTY_FILTERS: ObjectFilters = { text: '', source: '', state: 'ALL', schedule: 'ALL' }
const FILTERS_KEY = 'dwh.loads.objectFilters'
const RUN_STATES: RunStateFilter[] = ['ALL', 'SUCCESS', 'FAILED', 'ACTIVE', 'NEVER']
const SCHEDULE_STATES: ScheduleFilter[] = ['ALL', 'ON', 'OFF']

function loadFilters(): ObjectFilters {
  try {
    const raw = window.localStorage.getItem(FILTERS_KEY)
    if (raw === null) {
      return EMPTY_FILTERS
    }
    const saved = JSON.parse(raw) as Partial<ObjectFilters>
    return {
      text: typeof saved.text === 'string' ? saved.text : '',
      source: typeof saved.source === 'string' ? saved.source : '',
      state: RUN_STATES.includes(saved.state as RunStateFilter) ? saved.state as RunStateFilter : 'ALL',
      schedule: SCHEDULE_STATES.includes(saved.schedule as ScheduleFilter) ? saved.schedule as ScheduleFilter : 'ALL',
    }
  } catch {
    return EMPTY_FILTERS
  }
}

function saveFilters(filters: ObjectFilters) {
  try {
    window.localStorage.setItem(FILTERS_KEY, JSON.stringify(filters))
  } catch {
    // storage unavailable (private window): the choice lives until the page is closed
  }
}

function runState(item: ObjectLoadStatus): RunStateFilter {
  if (item.has_active_request
    || ['QUEUED', 'CLAIMED', 'RUNNING'].includes(item.last_request_status ?? '')) {
    return 'ACTIVE'
  }
  if (item.stg_run_status === 'FAILED' || item.ods_run_status === 'FAILED') {
    return 'FAILED'
  }
  if (item.stg_run_status === null) {
    return 'NEVER'
  }
  return 'SUCCESS'
}

function matchesText(item: ObjectLoadStatus, text: string): boolean {
  const needle = text.trim().toLowerCase()
  if (needle === '') {
    return true
  }
  return [
    item.source_system_code,
    item.source_schema,
    item.source_object,
    `${item.source_schema}.${item.source_object}`,
    item.stg_table,
    item.ods_table,
  ].some(value => (value ?? '').toLowerCase().includes(needle))
}


function badgeClass(status: string | null): string {
  if (status === 'SUCCESS' || status === 'READY') {
    return 'badge badge-success'
  }
  if (status === 'FAILED' || status === 'DRIFT') {
    return 'badge badge-danger'
  }
  if (status === 'QUEUED' || status === 'CLAIMED' || status === 'RUNNING') {
    return 'badge badge-warning'
  }
  return 'badge'
}


interface LoadsObjectsPanelProps {
  canRun: boolean
  // I17-10: "Analyse with the assistant" for a failed object (AI_ASSISTANT_USE and ETL_VIEW).
  canDiagnose?: boolean
}


function LoadsObjectsPanel({ canRun, canDiagnose = false }: LoadsObjectsPanelProps) {
  const { t } = useTranslation()
  const [objects, setObjects] = useState<ObjectLoadStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyObjectId, setBusyObjectId] = useState<number | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  // I09: schedules of the objects (only for those who may run objects; a failure leaves the column empty)
  const [schedules, setSchedules] = useState<Record<number, ObjectSchedule | null>>({})
  const loadSeq = useRef(0)
  const [filters, setFilters] = useState<ObjectFilters>(loadFilters)

  function updateFilters(change: Partial<ObjectFilters>) {
    setFilters(current => {
      const next = { ...current, ...change }
      saveFilters(next)
      return next
    })
  }

  // Every state update happens after the await; responses of older requests are ignored.
  const refresh = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const [result, scheduleList] = await Promise.all([
        getObjectLoadStatus(),
        canRun ? getObjectSchedules().catch(() => null) : Promise.resolve(null),
      ])
      if (seq === loadSeq.current) {
        setObjects(result)
        setSchedules(scheduleList === null ? {}
          : Object.fromEntries(scheduleList.objects.map(item => [item.source_object_id, item.schedule])))
        setError(null)
      }
    } catch (loadError) {
      if (seq === loadSeq.current) {
        setError(loadError instanceof Error ? loadError.message : String(loadError))
      }
    } finally {
      if (seq === loadSeq.current) {
        setLoading(false)
      }
    }
  }, [canRun])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh()
    }, 0)
    return () => {
      window.clearTimeout(timer)
      loadSeq.current += 1
    }
  }, [refresh])

  function reload() {
    setLoading(true)
    void refresh()
  }

  const anyActive = objects.some(item => item.has_active_request)

  const sourceCodes = useMemo(
    () => Array.from(new Set(objects.map(item => item.source_system_code))).sort(),
    [objects],
  )

  const shown = useMemo(() => objects.filter(item => {
    if (!matchesText(item, filters.text)) {
      return false
    }
    if (filters.source !== '' && item.source_system_code !== filters.source) {
      return false
    }
    if (filters.state !== 'ALL' && runState(item) !== filters.state) {
      return false
    }
    if (canRun && filters.schedule !== 'ALL') {
      const enabled = schedules[item.source_object_id]?.enabled === true
      if ((filters.schedule === 'ON') !== enabled) {
        return false
      }
    }
    return true
  }), [objects, filters, schedules, canRun])

  const filtered = filters.text.trim() !== '' || filters.source !== '' || filters.state !== 'ALL'
    || (canRun && filters.schedule !== 'ALL')

  useEffect(() => {
    if (!anyActive) {
      return
    }
    const timer = window.setInterval(() => {
      void refresh()
    }, REFRESH_MS)
    return () => {
      window.clearInterval(timer)
    }
  }, [anyActive, refresh])

  async function requestRun(item: ObjectLoadStatus, kind: ObjectRunKind) {
    const name = `${item.source_schema}.${item.source_object}`
    const confirmed = await confirmAction({
      title: t(`loads.kind.${kind}`),
      message: t(`loads.confirm.${kind}`, { object: name }),
      confirmLabel: t('loads.confirmRun'),
      cancelLabel: t('loads.cancel'),
    })
    if (!confirmed) {
      return
    }
    setBusyObjectId(item.source_object_id)
    setNotice(null)
    try {
      const created = await createObjectRunRequest(item.source_object_id, kind)
      setNotice(t('loads.requestCreated', { id: created.request_id, object: name }))
      setError(null)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError))
    } finally {
      setBusyObjectId(null)
      void refresh()
    }
  }

  async function cancelRequest(item: ObjectLoadStatus) {
    if (item.last_request_id === null) {
      return
    }
    setBusyObjectId(item.source_object_id)
    try {
      await cancelObjectRunRequest(item.last_request_id)
      setNotice(t('loads.requestCancelled', { id: item.last_request_id }))
      setError(null)
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : String(cancelError))
    } finally {
      setBusyObjectId(null)
      void refresh()
    }
  }

  function runCell(status: string | null, finishedAt: string | null, read: number | null,
    inserted: number | null, updated: number | null) {
    if (status === null) {
      return <span className="loads-muted">{t('loads.never')}</span>
    }
    return (
      <>
        <span className={badgeClass(status)}>{status}</span>
        <div><small>{formatDateTime(finishedAt)}</small></div>
        {read !== null
          ? <div><small>{t('loads.rows', { read, inserted: inserted ?? 0, updated: updated ?? 0 })}</small></div>
          : null}
      </>
    )
  }

  function lastError(item: ObjectLoadStatus): string | null {
    if (item.ods_run_status === 'FAILED' && item.ods_error_message) {
      return `ODS: ${item.ods_error_message}`
    }
    if (item.stg_run_status === 'FAILED' && item.stg_error_message) {
      return `STG: ${item.stg_error_message}`
    }
    return null
  }

  return (
    <section className="content-panel">
      <div className="loads-toolbar">
        <p>{t('loads.objectsHint')}</p>
        <button type="button" className="loads-refresh" onClick={reload}>
          {t('loads.refresh')}
        </button>
      </div>

      {error ? <div className="error"><span>{error}</span></div> : null}
      {notice ? <div className="loads-notice">{notice}</div> : null}

      <div className="loads-filters">
        <input
          type="search"
          className="loads-filter-text"
          value={filters.text}
          placeholder={t('loads.filter.search')}
          aria-label={t('loads.filter.search')}
          onChange={event => updateFilters({ text: event.target.value })}
        />
        <label>
          <span>{t('loads.filter.source')}</span>
          <select value={filters.source} onChange={event => updateFilters({ source: event.target.value })}>
            <option value="">{t('loads.filter.all')}</option>
            {sourceCodes.map(code => <option key={code} value={code}>{code}</option>)}
            {filters.source !== '' && !sourceCodes.includes(filters.source)
              ? <option value={filters.source}>{filters.source}</option>
              : null}
          </select>
        </label>
        <label>
          <span>{t('loads.filter.state')}</span>
          <select value={filters.state}
            onChange={event => updateFilters({ state: event.target.value as RunStateFilter })}>
            {RUN_STATES.map(state => <option key={state} value={state}>{t(`loads.filter.stateValue.${state}`)}</option>)}
          </select>
        </label>
        {canRun
          ? (
            <label>
              <span>{t('loads.filter.schedule')}</span>
              <select value={filters.schedule}
                onChange={event => updateFilters({ schedule: event.target.value as ScheduleFilter })}>
                {SCHEDULE_STATES.map(state => (
                  <option key={state} value={state}>{t(`loads.filter.scheduleValue.${state}`)}</option>
                ))}
              </select>
            </label>
          )
          : null}
        <button type="button" className="loads-refresh" disabled={!filtered}
          onClick={() => updateFilters(EMPTY_FILTERS)}>
          {t('loads.filter.reset')}
        </button>
        {!loading
          ? <span className="loads-filter-count">{t('loads.filter.count', { shown: shown.length, total: objects.length })}</span>
          : null}
      </div>

      {loading
        ? <div className="loading-panel">{t('loads.loading')}</div>
        : (
          <div className="data-table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('loads.col.object')}</th>
                  <th>{t('loads.col.config')}</th>
                  <th>{t('loads.col.stg')}</th>
                  <th>{t('loads.col.ods')}</th>
                  <th>{t('loads.col.error')}</th>
                  <th>{t('loads.col.request')}</th>
                  {canRun ? <th>{t('loads.col.schedule')}</th> : null}
                  {canRun ? <th>{t('loads.col.actions')}</th> : null}
                </tr>
              </thead>
              <tbody>
                {shown.length === 0
                  ? (
                    <tr>
                      <td colSpan={canRun ? 8 : 6} className="loads-muted">
                        {objects.length === 0 ? t('loads.filter.noObjects') : t('loads.filter.nothing')}
                      </td>
                    </tr>
                  )
                  : null}
                {shown.map(item => {
                  const ready = !item.is_legacy && item.is_active && item.load_status === 'READY'
                  const busy = busyObjectId === item.source_object_id
                  const blocked = !ready || item.has_active_request || busy
                  const errorText = lastError(item)
                  const isExpanded = expanded === item.source_object_id
                  return (
                    <tr key={item.source_object_id}>
                      <td>
                        <strong>{item.source_system_code}</strong>
                        <div className="code-value">{item.source_schema}.{item.source_object}</div>
                        {item.is_legacy ? <span className="badge">{t('loads.legacy')}</span> : null}
                      </td>
                      <td>
                        {item.load_version_no !== null
                          ? (
                            <div>
                              STG v{item.load_version_no}{' '}
                              <span className={badgeClass(item.load_status)}>{item.load_status}</span>
                              <div><small>{item.load_strategy} · {item.stg_schema}.{item.stg_table}</small></div>
                            </div>
                          )
                          : <span className="loads-muted">{item.is_legacy ? t('loads.legacyConfig') : t('loads.noConfig')}</span>}
                        {item.ods_version_no !== null
                          ? (
                            <div>
                              ODS v{item.ods_version_no}{' '}
                              <span className={badgeClass(item.ods_status)}>{item.ods_status}</span>
                              <div><small>ods.{item.ods_table}</small></div>
                            </div>
                          )
                          : null}
                      </td>
                      <td>{runCell(item.stg_run_status, item.stg_finished_at, item.stg_rows_read,
                        item.stg_rows_inserted, item.stg_rows_updated)}</td>
                      <td>{runCell(item.ods_run_status, item.ods_finished_at, item.ods_rows_read,
                        item.ods_rows_inserted, item.ods_rows_updated)}</td>
                      <td className="integration-history-message">
                        {errorText
                          ? (
                            <button
                              type="button"
                              className="loads-link"
                              title={t('loads.showFull')}
                              onClick={() => setExpanded(isExpanded ? null : item.source_object_id)}
                            >
                              {isExpanded || errorText.length <= 160 ? errorText : `${errorText.slice(0, 160)}…`}
                            </button>
                          )
                          : null}
                        {(item.file_errors ?? 0) > 0
                          ? <div className="loads-file-errors">⚠ {t('loads.fileErrors', { count: item.file_errors })}</div>
                          : null}
                        {(errorText || (item.file_errors ?? 0) > 0) && canDiagnose
                          ? (
                            <div>
                              <button type="button" className="loads-link"
                                onClick={() => requestDiagnosis({ kind: 'object', id: item.source_object_id,
                                  label: `${item.source_schema}.${item.source_object}` })}>
                                {t('ai.diagnose')}
                              </button>
                            </div>
                          )
                          : null}
                      </td>
                      <td>
                        {item.last_request_id !== null
                          ? (
                            <>
                              <div>
                                #{item.last_request_id} {t(`loads.kind.${item.last_request_kind ?? 'RUN'}`)}{' '}
                                <span className={badgeClass(item.last_request_status)}>{item.last_request_status}</span>
                              </div>
                              <small>{item.last_request_by} · {formatDateTime(item.last_request_at)}</small>
                              {item.last_request_status === 'QUEUED'
                                ? <div><small>{t('loads.waitingDispatcher')}</small></div>
                                : null}
                            </>
                          )
                          : null}
                      </td>
                      {canRun
                        ? (
                          <td>
                            {schedules[item.source_object_id]?.enabled
                              ? (
                                <>
                                  <span className="badge badge-success">{t('schedule.on')}</span>
                                  <div><small>{t('loads.nextRun', { at: formatDateTime(schedules[item.source_object_id]?.next_run_at ?? null) })}</small></div>
                                </>
                              )
                              : <span className="loads-muted">{schedules[item.source_object_id] ? t('schedule.off') : t('schedule.none')}</span>}
                          </td>
                        )
                        : null}
                      {canRun
                        ? (
                          <td className="loads-actions">
                            <button type="button" disabled={blocked}
                              onClick={() => { void requestRun(item, 'RUN') }}>
                              {t('loads.kind.RUN')}
                            </button>
                            <button type="button" disabled={blocked || !item.can_continue_ods}
                              title={item.can_continue_ods ? undefined : t('loads.continueHint')}
                              onClick={() => { void requestRun(item, 'CONTINUE_ODS') }}>
                              {t('loads.kind.CONTINUE_ODS')}
                            </button>
                            <button type="button" disabled={blocked}
                              onClick={() => { void requestRun(item, 'FULL_RERUN') }}>
                              {t('loads.kind.FULL_RERUN')}
                            </button>
                            {item.last_request_status === 'QUEUED'
                              ? (
                                <button type="button" disabled={busy}
                                  onClick={() => { void cancelRequest(item) }}>
                                  {t('loads.cancelRequest')}
                                </button>
                              )
                              : null}
                          </td>
                        )
                        : null}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
    </section>
  )
}

export default LoadsObjectsPanel
