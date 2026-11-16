import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { createReportFolder, getReportFolders, saveReport, type ReportFolder } from './aiApi'


// I17-09: "Save to Reporting" under a report of the assistant. The database copies the SQL of this request
// (the user's own successful report); the client sends only the request id, the folder and the title.
interface Props {
  requestId: number
  defaultTitle: string
}

const NEW_FOLDER = 'new'


function SaveReportForm({ requestId, defaultTitle }: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [folders, setFolders] = useState<ReportFolder[] | null>(null)
  const [folderId, setFolderId] = useState('')
  const [folderName, setFolderName] = useState('')
  const [visibility, setVisibility] = useState<ReportFolder['visibility']>('PERSONAL')
  const [title, setTitle] = useState(defaultTitle.slice(0, 200))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  async function start() {
    setOpen(true)
    setError(null)
    try {
      const list = await getReportFolders()
      // Only own personal folders and shared folders accept a report (the database checks it again).
      const usable = list.filter(f => f.visibility === 'SHARED' || f.is_own)
      setFolders(usable)
      setFolderId(usable.length ? String(usable[0].folder_id) : NEW_FOLDER)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setFolders([])
      setFolderId(NEW_FOLDER)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      let target = Number(folderId)
      let targetName = folders?.find(f => f.folder_id === target)?.folder_name ?? ''
      if (folderId === NEW_FOLDER) {
        target = (await createReportFolder(folderName.trim(), visibility)).folder_id
        targetName = folderName.trim()
      }
      await saveReport(requestId, target, title.trim())
      setSaved(targetName)
      setOpen(false)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  if (saved !== null) {
    return <div className="ai-sources">{t('savedReports.savedTo', { folder: saved })}</div>
  }
  if (!open) {
    return (
      <div className="ai-save">
        <button type="button" onClick={() => void start()}>{t('savedReports.saveToReporting')}</button>
      </div>
    )
  }
  return (
    <form className="ai-save ai-save-form" onSubmit={event => void submit(event)}>
      <label>{t('savedReports.title')}
        <input required maxLength={200} value={title} onChange={e => setTitle(e.target.value)} />
      </label>
      <label>{t('savedReports.folder')}
        <select value={folderId} disabled={folders === null} onChange={e => setFolderId(e.target.value)}>
          {(folders ?? []).map(f => (
            <option key={f.folder_id} value={f.folder_id}>
              {f.folder_name} — {f.visibility === 'SHARED' ? t('savedReports.shared') : t('savedReports.personal')}
            </option>
          ))}
          <option value={NEW_FOLDER}>{t('savedReports.newFolder')}</option>
        </select>
      </label>
      {folderId === NEW_FOLDER
        ? (
            <>
              <label>{t('savedReports.folderName')}
                <input required maxLength={200} value={folderName} onChange={e => setFolderName(e.target.value)} />
              </label>
              <label>{t('savedReports.visibility')}
                <select value={visibility} onChange={e => setVisibility(e.target.value as ReportFolder['visibility'])}>
                  <option value="PERSONAL">{t('savedReports.personal')}</option>
                  <option value="SHARED">{t('savedReports.shared')}</option>
                </select>
              </label>
            </>
          )
        : null}
      {error ? <div className="ai-error">{error}</div> : null}
      <div className="ai-save-actions">
        <button type="submit" disabled={busy || folders === null}>{t('savedReports.save')}</button>
        <button type="button" disabled={busy} onClick={() => setOpen(false)}>{t('savedReports.cancel')}</button>
      </div>
    </form>
  )
}


export default SaveReportForm
