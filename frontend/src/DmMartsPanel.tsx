import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import { confirmAction } from './confirmAction'
import { requestDiagnosis } from './aiBus'
import { formatDateTime } from './dateTime'
import {
  cancelDmRunRequest,
  createDmRunRequest,
  getDmMartStatus,
  type DmMartStatus,
} from './dmApi'
import './LoadsPage.css'


// I19: tab "Marts" of the section "Data loads": state of every DM mart (published version, last run with its
// error and key examples, last request) and manual run requests. The dispatcher reloads the marts; the list
// refreshes itself while a request is active.

const REFRESH_MS = 5000


function badgeClass(status: string | null): string {
  if (status === 'SUCCESS') {
    return 'badge badge-success'
  }
  if (status === 'FAILED') {
    return 'badge badge-danger'
  }
  if (status === 'QUEUED' || status === 'CLAIMED' || status === 'RUNNING') {
    return 'badge badge-warning'
  }
  return 'badge'
}


interface DmMartsPanelProps {
  canRun: boolean
  // I17-10: "Analyse with the assistant" for a failed run (AI_ASSISTANT_USE and ETL_VIEW).
  canDiagnose?: boolean
}


function DmMartsPanel({ canRun, canDiagnose = false }: DmMartsPanelProps) {
  const { t } = useTranslation()
  const [marts, setMarts] = useState<DmMartStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyMartId, setBusyMartId] = useState<number | null>(null)
  const loadSeq = useRef(0)

  // Responses of older requests are ignored.
  const refresh = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const result = await getDmMartStatus()
      if (seq === loadSeq.current) {
        setMarts(result)
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
    const timer = window.setTimeout(() => {
      void refresh()
    }, 0)
    return () => {
      window.clearTimeout(timer)
      loadSeq.current += 1
    }
  }, [refresh])

  const anyActive = marts.some(item => item.has_active_request)

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

  function reload() {
    setLoading(true)
    void refresh()
  }

  async function requestRun(item: DmMartStatus) {
    const confirmed = await confirmAction({
      title: t('dm.loads.run'),
      message: t('dm.loads.confirmRun', { mart: item.mart_code }),
      confirmLabel: t('loads.confirmRun'),
      cancelLabel: t('loads.cancel'),
    })
    if (!confirmed) {
      return
    }
    setBusyMartId(item.mart_id)
    setNotice(null)
    try {
      const created = await createDmRunRequest(item.mart_id)
      setNotice(t('dm.loads.requestCreated', { id: created.request_id, mart: item.mart_code }))
      setError(null)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError))
    } finally {
      setBusyMartId(null)
      void refresh()
    }
  }

  async function cancelRequest(item: DmMartStatus) {
    if (item.last_request_id === null) {
      return
    }
    setBusyMartId(item.mart_id)
    try {
      await cancelDmRunRequest(item.last_request_id)
      setNotice(t('loads.requestCancelled', { id: item.last_request_id }))
      setError(null)
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : String(cancelError))
    } finally {
      setBusyMartId(null)
      void refresh()
    }
  }

  return (
    <section className="content-panel">
      <div className="loads-toolbar">
        <p>{t('dm.loads.hint')}</p>
        <button type="button" className="loads-refresh" onClick={reload}>
          {t('loads.refresh')}
        </button>
      </div>

      {error ? <div className="error"><span>{error}</span></div> : null}
      {notice ? <div className="loads-notice">{notice}</div> : null}

      {loading
        ? <div className="loading-panel">{t('loads.loading')}</div>
        : marts.length === 0
          ? <div className="loads-muted">{t('dm.loads.empty')}</div>
          : (
            <div className="data-table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('dm.loads.col.mart')}</th>
                    <th>{t('dm.loads.col.version')}</th>
                    <th>{t('dm.loads.col.run')}</th>
                    <th>{t('loads.col.error')}</th>
                    <th>{t('loads.col.request')}</th>
                    {canRun ? <th>{t('loads.col.actions')}</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {marts.map(item => {
                    const busy = busyMartId === item.mart_id
                    const runnable = item.is_active && item.published_version_no !== null && item.table_exists
                    const examples = item.last_run_diagnostic?.examples ?? []
                    return (
                      <tr key={item.mart_id}>
                        <td>
                          <strong>{item.title}</strong>
                          <div className="code-value">dm.{item.mart_code}</div>
                          {!item.is_active ? <span className="badge">{t('dm.inactive')}</span> : null}
                          {item.ai_enabled ? <span className="badge">{t('dm.loads.ai')}</span> : null}
                          {item.auto_after_ods ? <div><small>{t('dm.loads.autoAfterOds')}</small></div> : null}
                        </td>
                        <td>
                          {item.published_version_no !== null
                            ? <div>v{item.published_version_no} <small>{formatDateTime(item.published_at)}</small></div>
                            : <span className="loads-muted">{t('dm.loads.notPublished')}</span>}
                          {item.draft_version_no !== null
                            ? (
                              <div>
                                <small>{t('dm.loads.draft', { no: item.draft_version_no })}</small>
                                {item.draft_build_status === 'BUILDING' || item.draft_build_status === 'FAILED'
                                  ? (
                                    <span className={item.draft_build_status === 'FAILED' ? 'badge badge-danger' : 'badge badge-warning'}
                                      title={item.draft_build_message ?? undefined}>
                                      {t(`dm.build.${item.draft_build_status}`)}
                                    </span>
                                  )
                                  : null}
                              </div>
                            )
                            : null}
                        </td>
                        <td>
                          {item.last_run_status === null
                            ? <span className="loads-muted">{t('loads.never')}</span>
                            : (
                              <>
                                <span className={badgeClass(item.last_run_status)}>{item.last_run_status}</span>
                                <div><small>{formatDateTime(item.last_run_finished_at ?? item.last_run_started_at)}</small></div>
                                {item.last_run_rows !== null
                                  ? <div><small>{t('dm.loads.rows', { rows: item.last_run_rows })}</small></div>
                                  : null}
                              </>
                            )}
                        </td>
                        <td className="integration-history-message">
                          {item.last_run_status === 'FAILED'
                            ? (
                              <>
                                <div><span className="code-value">{item.last_run_error_code}</span></div>
                                <div>{item.last_run_error_message}</div>
                                {examples.length > 0
                                  ? (
                                    <div>
                                      <small>
                                        {t('dm.loads.examples')}{' '}
                                        {examples.map(example => `${example.key} ×${example.rows}`).join(', ')}
                                      </small>
                                    </div>
                                  )
                                  : null}
                                {canDiagnose
                                  ? (
                                    <div>
                                      <button type="button" className="loads-link"
                                        onClick={() => requestDiagnosis({ kind: 'mart', id: item.mart_id,
                                          label: `dm.${item.mart_code}` })}>
                                        {t('ai.diagnose')}
                                      </button>
                                    </div>
                                  )
                                  : null}
                              </>
                            )
                            : null}
                        </td>
                        <td>
                          {item.last_request_id !== null
                            ? (
                              <>
                                <div>
                                  #{item.last_request_id} {t(`dm.loads.trigger.${item.last_request_trigger ?? 'MANUAL'}`)}{' '}
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
                            <td className="loads-actions">
                              <button type="button" disabled={!runnable || item.has_active_request || busy}
                                title={runnable ? undefined : t('dm.loads.notRunnable')}
                                onClick={() => { void requestRun(item) }}>
                                {t('dm.loads.run')}
                              </button>
                              {item.last_request_status === 'QUEUED'
                                ? (
                                  <button type="button" disabled={busy} onClick={() => { void cancelRequest(item) }}>
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

export default DmMartsPanel
