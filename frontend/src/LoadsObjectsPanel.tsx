import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import { confirmAction } from './confirmAction'
import { formatDateTime } from './dateTime'
import './LoadsPage.css'
import {
  cancelObjectRunRequest,
  createObjectRunRequest,
  getObjectLoadStatus,
  type ObjectLoadStatus,
  type ObjectRunKind,
} from './integrationAdminApi'


// I08: tab "Objects" of the section "Data loads": state of every object and run requests.
// Requests are executed by the dispatcher (run_local_dwh.ps1 -Mode Run); the list refreshes
// itself while a request of any object is active.

const REFRESH_MS = 5000


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
}


function LoadsObjectsPanel({ canRun }: LoadsObjectsPanelProps) {
  const { t } = useTranslation()
  const [objects, setObjects] = useState<ObjectLoadStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyObjectId, setBusyObjectId] = useState<number | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const loadSeq = useRef(0)

  // Every state update happens after the await; responses of older requests are ignored.
  const refresh = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const result = await getObjectLoadStatus()
      if (seq === loadSeq.current) {
        setObjects(result)
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

  function reload() {
    setLoading(true)
    void refresh()
  }

  const anyActive = objects.some(item => item.has_active_request)

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
                  {canRun ? <th>{t('loads.col.actions')}</th> : null}
                </tr>
              </thead>
              <tbody>
                {objects.map(item => {
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
