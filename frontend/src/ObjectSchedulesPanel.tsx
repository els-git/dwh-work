import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import { formatDateTime } from './dateTime'
import './LoadsPage.css'
import {
  getObjectSchedules,
  saveObjectSchedule,
  type ObjectSchedule,
  type ObjectScheduleInput,
  type ScheduledObject,
  type ScheduleFrequency,
  type ScheduleRunKind,
} from './integrationAdminApi'


// I09: tab "Schedules" of the section "Data loads". One schedule per object (P1); time zone Asia/Baku (P3).
// Saving never starts a run: the dispatcher creates the request when the time comes. Every state update happens
// after the await; responses of older requests are ignored.

const REFRESH_MS = 30000
const FREQUENCIES: ScheduleFrequency[] = ['EVERY_MINUTES', 'EVERY_HOURS', 'DAILY', 'WEEKLY']
const WEEK_DAYS = [1, 2, 3, 4, 5, 6, 7]


interface Draft {
  enabled: boolean
  frequency: ScheduleFrequency
  everyN: string
  atTime: string
  weekDays: number[]
  runKind: ScheduleRunKind
  retryCount: number
  retryDelay: string
}


function draftOf(schedule: ObjectSchedule | null): Draft {
  if (schedule === null) {
    return { enabled: true, frequency: 'DAILY', everyN: '60', atTime: '06:00', weekDays: [1, 2, 3, 4, 5],
      runKind: 'RUN', retryCount: 0, retryDelay: '15' }
  }
  return {
    enabled: schedule.enabled,
    frequency: schedule.frequency,
    everyN: schedule.every_n === null ? (schedule.frequency === 'EVERY_HOURS' ? '1' : '60') : String(schedule.every_n),
    atTime: schedule.at_time ?? '06:00',
    weekDays: schedule.week_days ?? [1, 2, 3, 4, 5],
    runKind: schedule.run_kind,
    retryCount: schedule.retry_count,
    retryDelay: String(schedule.retry_delay_minutes),
  }
}


function inputOf(draft: Draft): ObjectScheduleInput {
  const interval = draft.frequency === 'EVERY_MINUTES' || draft.frequency === 'EVERY_HOURS'
  return {
    enabled: draft.enabled,
    frequency: draft.frequency,
    every_n: interval ? Number(draft.everyN) : null,
    at_time: interval ? null : normalTime(draft.atTime),
    week_days: draft.frequency === 'WEEKLY' ? [...draft.weekDays].sort((a, b) => a - b) : null,
    run_kind: draft.runKind,
    retry_count: draft.retryCount,
    retry_delay_minutes: Number(draft.retryDelay),
  }
}


// Checked before sending: the database refuses the same values, but its answer is less precise.
function draftProblem(draft: Draft): { key: string, n?: number } | null {
  const whole = (value: string) => /^\d+$/.test(value.trim()) ? Number(value) : Number.NaN
  if (draft.frequency === 'EVERY_MINUTES') {
    const n = whole(draft.everyN)
    return n >= 15 && n <= 720 ? null : { key: 'schedule.invalid.minutes' }
  }
  if (draft.frequency === 'EVERY_HOURS') {
    const n = whole(draft.everyN)
    return n >= 1 && n <= 24 ? null : { key: 'schedule.invalid.hours' }
  }
  if (normalTime(draft.atTime) === null) {
    return { key: 'schedule.invalid.time' }
  }
  if (draft.frequency === 'WEEKLY' && draft.weekDays.length === 0) {
    return { key: 'schedule.invalid.weekDays' }
  }
  if (draft.retryCount > 0) {
    const n = whole(draft.retryDelay)
    if (!(n >= 5 && n <= 60)) {
      return { key: 'schedule.invalid.retryDelay' }
    }
  }
  return null
}


// "6:05" is accepted as "06:05"; anything else outside 00:00–23:59 is refused.
function normalTime(value: string): string | null {
  const match = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(value.trim())
  return match === null ? null : `${match[1].padStart(2, '0')}:${match[2]}`
}


function badgeClass(status: string): string {
  if (status === 'SUCCESS' || status === 'QUEUED_OK') {
    return 'badge badge-success'
  }
  if (status === 'FAILED' || status === 'SKIPPED_NOT_READY') {
    return 'badge badge-danger'
  }
  if (status === 'QUEUED' || status === 'CLAIMED' || status === 'RUNNING' || status === 'SKIPPED_BUSY') {
    return 'badge badge-warning'
  }
  return 'badge'
}


function ObjectSchedulesPanel() {
  const { t } = useTranslation()
  const [objects, setObjects] = useState<ScheduledObject[]>([])
  const [timeZone, setTimeZone] = useState('Asia/Baku')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // a refused save stays visible: the periodic reload only clears its own (load) error
  const [saveError, setSaveError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<Draft>(draftOf(null))
  const [history, setHistory] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const loadSeq = useRef(0)

  const refresh = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const result = await getObjectSchedules()
      if (seq === loadSeq.current) {
        setObjects(result.objects)
        setTimeZone(result.time_zone)
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
  }, [])

  useEffect(() => {
    const first = window.setTimeout(() => {
      void refresh()
    }, 0)
    const timer = window.setInterval(() => {
      void refresh()
    }, REFRESH_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
      loadSeq.current += 1
    }
  }, [refresh])

  function describe(schedule: ObjectSchedule): string {
    const kind = t(`schedule.runKind.${schedule.run_kind}`)
    let when: string
    if (schedule.frequency === 'EVERY_MINUTES') {
      when = t('schedule.everyMinutes', { n: schedule.every_n })
    } else if (schedule.frequency === 'EVERY_HOURS') {
      when = t('schedule.everyHours', { n: schedule.every_n })
    } else if (schedule.frequency === 'DAILY') {
      when = t('schedule.daily', { time: schedule.at_time })
    } else {
      const days = (schedule.week_days ?? []).map(day => t(`schedule.day.${day}`)).join(', ')
      when = t('schedule.weekly', { days, time: schedule.at_time })
    }
    return `${when} · ${kind}`
  }

  function startEdit(item: ScheduledObject) {
    setEditing(item.source_object_id)
    setDraft(draftOf(item.schedule))
    setNotice(null)
    setSaveError(null)
  }

  async function save(item: ScheduledObject) {
    const problem = draftProblem(draft)
    if (problem !== null) {
      setSaveError(t(problem.key))
      return
    }
    setSaving(true)
    setNotice(null)
    try {
      const saved = await saveObjectSchedule(item.source_object_id, inputOf(draft))
      const name = `${item.source_schema}.${item.source_object}`
      setNotice(saved.enabled
        ? t('schedule.savedEnabled', { object: name, next: formatDateTime(saved.next_run_at) })
        : t('schedule.savedDisabled', { object: name }))
      setSaveError(null)
      setEditing(null)
      void refresh()
    } catch (refused) {
      setSaveError(refused instanceof Error ? refused.message : String(refused))
    } finally {
      setSaving(false)
    }
  }

  const interval = draft.frequency === 'EVERY_MINUTES' || draft.frequency === 'EVERY_HOURS'

  function editor(item: ScheduledObject) {
    return (
      <tr key={`e${item.source_object_id}`} className="schedule-editor-row">
        <td colSpan={5}>
          <div className="schedule-editor">
            <label className="schedule-check">
              <input type="checkbox" checked={draft.enabled}
                onChange={event => setDraft({ ...draft, enabled: event.target.checked })} />
              {t('schedule.enabled')}
            </label>
            <label>
              {t('schedule.frequency')}
              <select value={draft.frequency}
                onChange={event => setDraft({ ...draft, frequency: event.target.value as ScheduleFrequency,
                  everyN: event.target.value === 'EVERY_HOURS' ? '1' : event.target.value === 'EVERY_MINUTES' ? '60' : draft.everyN })}>
                {FREQUENCIES.map(value => <option key={value} value={value}>{t(`schedule.freq.${value}`)}</option>)}
              </select>
            </label>
            {interval
              ? (
                <label>
                  {draft.frequency === 'EVERY_MINUTES' ? t('schedule.minutes') : t('schedule.hours')}
                  <input type="number" min={draft.frequency === 'EVERY_MINUTES' ? 15 : 1}
                    max={draft.frequency === 'EVERY_MINUTES' ? 720 : 24} value={draft.everyN}
                    onChange={event => setDraft({ ...draft, everyN: event.target.value })} />
                </label>
              )
              : (
                <label>
                  {t('schedule.time', { zone: timeZone })}
                  {/* free text HH:MM, 24-hour clock regardless of the browser locale; checked before sending */}
                  <input type="text" className="schedule-time" inputMode="numeric" maxLength={5} placeholder={t('schedule.timePlaceholder')}
                    value={draft.atTime}
                    onChange={event => setDraft({ ...draft, atTime: event.target.value })} />
                </label>
              )}
            {draft.frequency === 'WEEKLY'
              ? (
                <div className="schedule-days">
                  {WEEK_DAYS.map(day => (
                    <label key={day} className="schedule-check">
                      <input type="checkbox" checked={draft.weekDays.includes(day)}
                        onChange={event => setDraft({ ...draft, weekDays: event.target.checked
                          ? [...draft.weekDays, day] : draft.weekDays.filter(value => value !== day) })} />
                      {t(`schedule.day.${day}`)}
                    </label>
                  ))}
                </div>
              )
              : null}
            <label>
              {t('schedule.runKindLabel')}
              <select value={draft.runKind}
                onChange={event => setDraft({ ...draft, runKind: event.target.value as ScheduleRunKind })}>
                <option value="RUN">{t('schedule.runKind.RUN')}</option>
                <option value="FULL_RERUN">{t('schedule.runKind.FULL_RERUN')}</option>
              </select>
            </label>
            <label>
              {t('schedule.retries')}
              <select value={draft.retryCount}
                onChange={event => setDraft({ ...draft, retryCount: Number(event.target.value) })}>
                {[0, 1, 2, 3].map(value => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            {draft.retryCount > 0
              ? (
                <label>
                  {t('schedule.retryDelay')}
                  <input type="number" min={5} max={60} value={draft.retryDelay}
                    onChange={event => setDraft({ ...draft, retryDelay: event.target.value })} />
                </label>
              )
              : null}
            <div className="schedule-editor-actions">
              <button type="button" className="loads-refresh" disabled={saving} onClick={() => { void save(item) }}>
                {t('schedule.save')}
              </button>
              <button type="button" className="loads-link" disabled={saving} onClick={() => { setEditing(null); setSaveError(null) }}>
                {t('schedule.cancel')}
              </button>
            </div>
            {saveError ? <div className="error schedule-error"><span>{saveError}</span></div> : null}
            <p className="loads-muted schedule-note">
              {draft.enabled && !item.ready ? t('schedule.notReady') : t('schedule.noRunOnSave')}
            </p>
          </div>
        </td>
      </tr>
    )
  }

  function historyRow(item: ScheduledObject) {
    return (
      <tr key={`h${item.source_object_id}`} className="schedule-history-row">
        <td colSpan={5}>
          <div className="schedule-history">
            <div>
              <strong>{t('schedule.lastRequests')}</strong>
              {item.requests.length === 0
                ? <p className="loads-muted">{t('schedule.noRequests')}</p>
                : (
                  <ul>
                    {item.requests.map(request => (
                      <li key={request.request_id}>
                        <span className="badge">{t(`schedule.trigger.${request.trigger_kind}`)}
                          {request.attempt > 1 ? ` ${t('schedule.attempt', { n: request.attempt - 1 })}` : ''}</span>
                        {' '}#{request.request_id} · {formatDateTime(request.requested_at)} · {request.requested_by}{' '}
                        <span className={badgeClass(request.status)}>{request.status}</span>
                        {request.result_code ? <small> {request.result_code}</small> : null}
                      </li>
                    ))}
                  </ul>
                )}
            </div>
            <div>
              <strong>{t('schedule.lastEvents')}</strong>
              {item.events.length === 0
                ? <p className="loads-muted">{t('schedule.noEvents')}</p>
                : (
                  <ul>
                    {item.events.map(event => (
                      <li key={event.event_id}>
                        <span className={badgeClass(event.event === 'QUEUED' || event.event === 'RETRY_QUEUED' ? 'QUEUED_OK' : event.event)}>
                          {t(`schedule.event.${event.event}`)}
                        </span>
                        {' '}{formatDateTime(event.occurred_at)}
                        {event.request_id !== null ? ` · #${event.request_id}` : ''}
                      </li>
                    ))}
                  </ul>
                )}
            </div>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <section className="content-panel">
      <div className="loads-toolbar">
        <p>{t('schedule.hint', { zone: timeZone })}</p>
        <button type="button" className="loads-refresh" onClick={() => { setLoading(true); void refresh() }}>
          {t('loads.refresh')}
        </button>
      </div>

      {error ? <div className="error"><span>{error}</span></div> : null}
      {notice ? <div className="loads-notice">{notice}</div> : null}

      {loading
        ? <div className="loading-panel">{t('schedule.loading')}</div>
        : (
          <div className="data-table-wrapper">
            <table className="data-table schedule-table">
              <thead>
                <tr>
                  <th>{t('schedule.col.object')}</th>
                  <th>{t('schedule.col.schedule')}</th>
                  <th>{t('schedule.col.next')}</th>
                  <th>{t('schedule.col.last')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {objects.map(item => {
                  const schedule = item.schedule
                  const last = item.requests[0]
                  return [
                    <tr key={item.source_object_id}>
                      <td>
                        <strong>{item.source_schema}.{item.source_object}</strong>
                        <div><small className="loads-muted">{item.source_code}</small></div>
                        {!item.ready ? <div><small className="loads-muted">{t('loads.noConfig')}</small></div> : null}
                      </td>
                      <td>
                        {schedule === null
                          ? <span className="loads-muted">{t('schedule.none')}</span>
                          : (
                            <>
                              <span className={schedule.enabled ? 'badge badge-success' : 'badge'}>
                                {schedule.enabled ? t('schedule.on') : t('schedule.off')}
                              </span>
                              <div>{describe(schedule)}</div>
                              {schedule.retry_count > 0
                                ? <div><small>{t('schedule.retryInfo', { n: schedule.retry_count, minutes: schedule.retry_delay_minutes })}</small></div>
                                : null}
                            </>
                          )}
                      </td>
                      <td>{schedule?.enabled && schedule.next_run_at ? formatDateTime(schedule.next_run_at) : '—'}</td>
                      <td>
                        {last
                          ? (
                            <>
                              <span className="badge">{t(`schedule.trigger.${last.trigger_kind}`)}</span>{' '}
                              <span className={badgeClass(last.status)}>{last.status}</span>
                              <div><small>{formatDateTime(last.finished_at ?? last.requested_at)}</small></div>
                            </>
                          )
                          : <span className="loads-muted">{t('loads.never')}</span>}
                      </td>
                      <td className="loads-actions">
                        <button type="button" className="loads-link" onClick={() => startEdit(item)}>
                          {schedule === null ? t('schedule.create') : t('schedule.edit')}
                        </button>
                        <button type="button" className="loads-link"
                          onClick={() => setHistory(history === item.source_object_id ? null : item.source_object_id)}>
                          {t('schedule.history')}
                        </button>
                      </td>
                    </tr>,
                    editing === item.source_object_id ? editor(item) : null,
                    history === item.source_object_id ? historyRow(item) : null,
                  ]
                })}
              </tbody>
            </table>
          </div>
        )}
    </section>
  )
}


export default ObjectSchedulesPanel
