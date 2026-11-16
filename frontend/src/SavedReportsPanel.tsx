import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  createReportFolder,
  deleteReportFolder,
  deleteSavedReport,
  getReportFolders,
  getSavedReports,
  runSavedReport,
  type AiReportAnswer,
  type ReportFolder,
  type SavedReport,
} from './aiApi'
import { confirmAction } from './confirmAction'
import { formatDateTime } from './dateTime'
import { cellText, downloadReportCsv } from './reportCsv'
import './SavedReportsPanel.css'


// I17-09: "Reporting" -> "My and shared reports". Folders are personal or shared; a report is the saved
// description and checked SQL of an assistant report and runs again with the rights of the viewer.
const SHOWN_ROWS = 200


function SavedReportsPanel() {
  const { t } = useTranslation()
  const [folders, setFolders] = useState<ReportFolder[]>([])
  const [reports, setReports] = useState<SavedReport[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [newName, setNewName] = useState('')
  const [newVisibility, setNewVisibility] = useState<ReportFolder['visibility']>('PERSONAL')
  const [opened, setOpened] = useState<{ report: SavedReport, answer: AiReportAnswer | null } | null>(null)
  const runSeq = useRef(0)
  const loadSeq = useRef(0)

  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const [folderList, reportList] = await Promise.all([getReportFolders(), getSavedReports()])
      if (seq !== loadSeq.current) {
        return
      }
      setError(null)
      setFolders(folderList)
      setReports(reportList)
      setSelected(current => (current !== null && folderList.some(f => f.folder_id === current))
        ? current
        : folderList[0]?.folder_id ?? null)
    } catch (exc) {
      if (seq === loadSeq.current) {
        setError(exc instanceof Error ? exc.message : String(exc))
      }
    }
  }, [])

  useEffect(() => {
    // The first load is started after the panel is shown; its state updates happen after the await.
    const timer = window.setTimeout(() => {
      void load()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [load])

  async function act(action: () => Promise<unknown>, done: string) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await action()
      setNotice(done)
      await load()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  function createFolder(event: FormEvent) {
    event.preventDefault()
    void act(async () => {
      const created = await createReportFolder(newName.trim(), newVisibility)
      setSelected(created.folder_id)
      setNewName('')
    }, t('savedReports.folderCreated'))
  }

  async function removeFolder(folder: ReportFolder) {
    if (await confirmAction({ title: t('savedReports.deleteFolder'), message: folder.folder_name,
                              confirmLabel: t('savedReports.delete'), cancelLabel: t('savedReports.cancel') })) {
      void act(() => deleteReportFolder(folder.folder_id), t('savedReports.deleted'))
    }
  }

  async function removeReport(report: SavedReport) {
    if (await confirmAction({ title: t('savedReports.deleteReport'), message: report.title,
                              confirmLabel: t('savedReports.delete'), cancelLabel: t('savedReports.cancel') })) {
      if (opened?.report.report_id === report.report_id) {
        setOpened(null)
      }
      void act(() => deleteSavedReport(report.report_id), t('savedReports.deleted'))
    }
  }

  async function open(report: SavedReport) {
    const seq = ++runSeq.current
    setOpened({ report, answer: null })
    setError(null)
    try {
      const answer = await runSavedReport(report.report_id)
      if (seq === runSeq.current) {
        setOpened({ report, answer })
      }
    } catch (exc) {
      if (seq === runSeq.current) {
        setOpened(null)
        setError(exc instanceof Error ? exc.message : String(exc))
      }
    }
  }

  function answerText(answer: AiReportAnswer): string {
    if (answer.status === 'DENIED') {
      return t('ai.denied')
    }
    if (answer.status === 'OK') {
      return t('ai.reportRows', { count: answer.rows?.length ?? 0 })
    }
    return t('ai.sqlError', { reason: answer.message ?? answer.code ?? '' })
  }

  const folder = folders.find(f => f.folder_id === selected) ?? null
  const shown = reports.filter(r => r.folder_id === selected)
  const answer = opened?.answer ?? null
  const columns = answer?.columns ?? []
  const rows = answer?.rows ?? []

  return (
    <section className="content-panel saved-reports">
      <div className="saved-reports-head">
        <h3>{t('savedReports.tab')}</h3>
        <p className="loads-muted">{t('savedReports.hint')}</p>
        {error ? <div className="error"><span>{error}</span></div> : null}
        {notice ? <div className="loads-notice">{notice}</div> : null}
      </div>

      <div className="saved-reports-grid">
        <aside className="saved-reports-folders">
          {folders.length === 0 ? <p className="loads-muted">{t('savedReports.noFolders')}</p> : null}
          {folders.map(f => (
            <button key={f.folder_id} type="button"
                    className={f.folder_id === selected ? 'saved-folder active' : 'saved-folder'}
                    onClick={() => setSelected(f.folder_id)}>
              <span>{f.folder_name}</span>
              <small>
                {f.visibility === 'SHARED' ? t('savedReports.shared') : t('savedReports.personal')} · {f.report_count}
              </small>
            </button>
          ))}
          <form className="saved-reports-new" onSubmit={createFolder}>
            <input required maxLength={200} value={newName} placeholder={t('savedReports.folderName')}
                   onChange={e => setNewName(e.target.value)} />
            <select value={newVisibility} onChange={e => setNewVisibility(e.target.value as ReportFolder['visibility'])}>
              <option value="PERSONAL">{t('savedReports.personal')}</option>
              <option value="SHARED">{t('savedReports.shared')}</option>
            </select>
            <button type="submit" disabled={busy}>{t('savedReports.createFolder')}</button>
          </form>
        </aside>

        <div className="saved-reports-list">
          {folder
            ? (
                <div className="saved-reports-folder-head">
                  <strong>{folder.folder_name}</strong>
                  <span className="loads-muted">
                    {folder.visibility === 'SHARED' ? t('savedReports.shared') : t('savedReports.personal')}
                    {' · '}{t('savedReports.owner')}: {folder.owner_username}
                  </span>
                  {folder.can_edit && folder.report_count === 0
                    ? <button type="button" disabled={busy} onClick={() => void removeFolder(folder)}>{t('savedReports.deleteFolder')}</button>
                    : null}
                </div>
              )
            : null}
          {folder && shown.length === 0 ? <p className="loads-muted">{t('savedReports.emptyFolder')}</p> : null}
          {shown.length > 0
            ? (
                <div className="data-table-wrapper">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{t('savedReports.title')}</th><th>{t('savedReports.owner')}</th>
                        <th>{t('savedReports.created')}</th><th />
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map(r => (
                        <tr key={r.report_id}>
                          <td title={r.question}>
                            {r.title}
                            {r.is_allowed ? null : <small className="saved-closed"> — {t('ai.denied')}</small>}
                          </td>
                          <td>{r.owner_username}</td>
                          <td>{formatDateTime(r.created_at)}</td>
                          <td className="loads-actions">
                            <button type="button" disabled={!r.is_allowed} onClick={() => void open(r)}>{t('savedReports.open')}</button>
                            {r.can_edit
                              ? <button type="button" disabled={busy} onClick={() => void removeReport(r)}>{t('savedReports.delete')}</button>
                              : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            : null}

          {opened
            ? (
                <div className="saved-reports-result">
                  <h4>{opened.report.title}</h4>
                  {answer === null ? <p className="loads-muted">{t('savedReports.running')}</p> : <p>{answerText(answer)}</p>}
                  {answer?.sql
                    ? (
                        <details>
                          <summary>{t('ai.showSql')}</summary>
                          <pre className="saved-sql">{answer.sql}</pre>
                        </details>
                      )
                    : null}
                  {answer?.status === 'OK' && columns.length > 0
                    ? (
                        <>
                          <div className="data-table-wrapper saved-result-table">
                            <table className="data-table">
                              <thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
                              <tbody>
                                {rows.slice(0, SHOWN_ROWS).map((row, index) => (
                                  <tr key={index}>{columns.map(c => <td key={c}>{cellText(row[c] ?? null)}</td>)}</tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                          <div className="loads-actions">
                            {rows.length > SHOWN_ROWS ? <span>{t('ai.shownRows', { shown: SHOWN_ROWS, count: rows.length })}</span> : null}
                            {answer.truncated ? <span>{t('ai.truncated')}</span> : null}
                            <button type="button"
                                    onClick={() => downloadReportCsv(columns, rows, `dwh_saved_report_${opened.report.report_id}.csv`)}>
                              {t('ai.downloadCsv')}
                            </button>
                          </div>
                        </>
                      )
                    : null}
                </div>
              )
            : null}
        </div>
      </div>
    </section>
  )
}


export default SavedReportsPanel
