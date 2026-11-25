import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import {
  checkFiles,
  getFileConfig,
  getFileRegister,
  saveFileConfig,
  saveFileStructure,
  type ApiProbeColumn,
  type FileCheckResult,
  type FileConfigDocument,
  type FileConfigState,
  type FileFormat,
  type FileMode,
  type IntegrationSourceObject,
  type RegisteredFile,
} from './integrationAdminApi'
import './LoadsPage.css'


// I12: tab "Files" of a file object in the mapping editor. The folder is fixed by the source code on the DWH
// server; the settings (mask, format, paths to the records and to the actuality date, mode) are saved into the
// draft of the object. "Check files" shows what the next load would do with each file and suggests columns;
// "Save structure" makes them the source columns of the mapping. The register lists every file seen by loads.
// Every state update happens after the await; answers to older requests are ignored.

interface Form {
  mask: string
  format: FileFormat
  recordsPath: string
  datePath: string
  mode: FileMode
  timeZone: string
  waitSeconds: string
}

const EMPTY: Form = {
  mask: '', format: 'JSON', recordsPath: '', datePath: '', mode: 'LATEST', timeZone: 'Asia/Baku', waitSeconds: '60',
}

function formOf(doc: FileConfigDocument | null): Form {
  if (!doc) {
    return EMPTY
  }
  return {
    mask: doc.mask, format: doc.format, recordsPath: doc.records_path, datePath: doc.date_path, mode: doc.mode,
    timeZone: doc.time_zone, waitSeconds: String(doc.wait_seconds),
  }
}

function docOf(form: Form): FileConfigDocument {
  return {
    mask: form.mask.trim(), format: form.format, records_path: form.recordsPath.trim(), date_path: form.datePath.trim(),
    mode: form.mode, time_zone: form.timeZone.trim(), wait_seconds: Number(form.waitSeconds) || 0,
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 2 ** 20 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 2 ** 20).toFixed(1)} MB`
}

interface FileSourcePanelProps {
  sourceObject: IntegrationSourceObject
  onStructureSaved: () => void
}

function FileSourcePanel({ sourceObject, onStructureSaved }: FileSourcePanelProps) {
  const { t } = useTranslation()
  const objectId = sourceObject.source_object_id
  const [state, setState] = useState<FileConfigState | null>(null)
  const [register, setRegister] = useState<RegisteredFile[]>([])
  const [form, setForm] = useState<Form>(EMPTY)
  const [check, setCheck] = useState<FileCheckResult | null>(null)
  const [columns, setColumns] = useState<ApiProbeColumn[]>([])
  const [newName, setNewName] = useState('')
  const [newType, setNewType] = useState('text')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)

  const load = useCallback(async (resetForm: boolean) => {
    const mine = ++seq.current
    try {
      const [value, files] = await Promise.all([getFileConfig(objectId), getFileRegister(objectId)])
      if (mine === seq.current) {
        setState(value)
        setRegister(files)
        if (resetForm) {
          setForm(formOf(value.draft_config ?? value.published_config))
        }
      }
    } catch (loadError) {
      if (mine === seq.current) {
        setError(message(loadError))
      }
    }
  }, [objectId])

  useEffect(() => {
    const first = window.setTimeout(() => {
      void load(true)
    }, 0)
    return () => {
      window.clearTimeout(first)
      seq.current += 1
    }
  }, [load])

  async function act(action: () => Promise<string>) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const text = await action()
      setNotice(text)
      await load(false)
    } catch (actionError) {
      setError(message(actionError))
    } finally {
      setBusy(false)
    }
  }

  const set = (patch: Partial<Form>) => setForm({ ...form, ...patch })

  const save = () => act(async () => {
    await saveFileConfig(objectId, docOf(form))
    return t('fileSource.saved')
  })

  const doCheck = () => act(async () => {
    const result = await checkFiles(objectId, docOf(form))
    setCheck(result)
    setColumns(result.columns)
    return t('fileSource.checked', { files: result.files_total })
  })

  const saveStructure = () => act(async () => {
    const result = await saveFileStructure(objectId, columns)
    onStructureSaved()
    return t('fileSource.structureSaved', { columns: result.columns_captured })
  })

  // Ф9: a field missing in the sample file is added by its path (a.b.c) with a type.
  const newNameValid = /^[A-Za-z_][A-Za-z0-9_.:-]{0,199}$/.test(newName.trim())
    && !columns.some(column => column.name === newName.trim())

  const addField = () => {
    setColumns([...columns, { name: newName.trim(), type: newType, nullable: true }])
    setNewName('')
  }

  if (state === null) {
    return error
      ? <div className="error"><span>{error}</span></div>
      : <div className="loading-panel">{t('fileSource.loading')}</div>
  }

  const ready = form.mask.trim() !== '' && form.datePath.trim() !== ''
    && (form.format !== 'XML' || form.recordsPath.trim() !== '')

  return (
    <div className="api-request-panel file-source-panel">
      <p className="loads-muted">
        {state.draft_version_id !== null ? t('fileSource.inDraft') : t('fileSource.noDraft')}
      </p>
      <p>
        {t('fileSource.folder')}
        {' '}
        <code>{state.folder}</code>
        {' — '}
        {state.folder_exists ? t('fileSource.folderReady') : t('fileSource.folderMissing')}
        {' '}
        <span className="loads-muted">{t('fileSource.maxSize', { mb: state.max_file_mb })}</span>
      </p>

      {error ? <div className="error"><span>{error}</span></div> : null}
      {notice ? <div className="loads-notice">{notice}</div> : null}

      <fieldset className="api-request-block api-request-form">
        <legend>{t('fileSource.settingsTitle')}</legend>
        <label>
          <span>{t('fileSource.mask')}</span>
          <input value={form.mask} placeholder="services_*.json" onChange={event => set({ mask: event.target.value })} />
        </label>
        <label>
          <span>{t('fileSource.format')}</span>
          <select value={form.format} onChange={event => set({ format: event.target.value as FileFormat })}>
            <option value="JSON">JSON</option>
            <option value="JSONL">{t('fileSource.jsonl')}</option>
            <option value="XML">XML</option>
          </select>
        </label>
        {form.format === 'JSONL'
          ? null
          : (
            <label>
              <span>{t('fileSource.recordsPath')}</span>
              <input value={form.recordsPath} placeholder={form.format === 'XML' ? 'export.items.service' : 'data.items'}
                onChange={event => set({ recordsPath: event.target.value })} />
            </label>
          )}
        <label>
          <span>{t('fileSource.datePath')}</span>
          <input value={form.datePath}
            placeholder={form.format === 'XML' ? 'export.header.actual_at' : form.format === 'JSONL' ? 'actual_at' : 'meta.actual_at'}
            onChange={event => set({ datePath: event.target.value })} />
        </label>
        <label>
          <span>{t('fileSource.mode')}</span>
          <select value={form.mode} onChange={event => set({ mode: event.target.value as FileMode })}>
            <option value="LATEST">{t('fileSource.modeLatest')}</option>
            <option value="ALL_NEW">{t('fileSource.modeAllNew')}</option>
          </select>
        </label>
        <label>
          <span>{t('fileSource.timeZone')}</span>
          <input value={form.timeZone} onChange={event => set({ timeZone: event.target.value })} />
        </label>
        <label>
          <span>{t('fileSource.wait')}</span>
          <input value={form.waitSeconds} inputMode="numeric" onChange={event => set({ waitSeconds: event.target.value })} />
        </label>
        <p className="loads-muted wide">{t(form.format === 'JSONL' ? 'fileSource.hintJsonl' : 'fileSource.hint')}</p>
        <div className="admin-integrations-form-actions wide">
          <button type="button" className="admin-secondary-button" disabled={busy || !ready || !state.folder_exists}
            onClick={() => { void doCheck() }}>
            {t('fileSource.check')}
          </button>
          <button type="button" className="admin-primary-button" disabled={busy || form.mask.trim() === ''}
            onClick={() => { void save() }}>
            {t('fileSource.save')}
          </button>
        </div>
      </fieldset>

      {check
        ? (
          <fieldset className="api-request-block">
            <legend>{t('fileSource.checkTitle')}</legend>
            <p>
              {t('fileSource.checkSummary', { files: check.files_total, shown: check.files.length })}
              {' '}
              {check.last_loaded_at ? t('fileSource.lastLoaded', { at: when(check.last_loaded_at) }) : t('fileSource.nothingLoaded')}
            </p>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('fileSource.file')}</th>
                    <th>{t('fileSource.size')}</th>
                    <th>{t('fileSource.actualAt')}</th>
                    <th>{t('fileSource.records')}</th>
                    <th>{t('fileSource.verdict')}</th>
                  </tr>
                </thead>
                <tbody>
                  {check.files.map(file => (
                    <tr key={file.name}>
                      <td>{file.name}</td>
                      <td>{size(file.size)}</td>
                      <td>{when(file.actual_at)}</td>
                      <td>{file.records ?? '—'}</td>
                      <td title={file.message ?? undefined}>
                        {t(`fileSource.verdicts.${file.verdict}`)}
                        {file.message && ['BROKEN', 'WAIT', 'TOO_LARGE'].includes(file.verdict)
                          ? <div className="loads-muted">{file.message}</div>
                          : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {columns.length > 0
              ? (
                <>
                  <p>{check.sample_file ? t('fileSource.structureFrom', { file: check.sample_file }) : t('fileSource.structureManual')}</p>
                  <table className="data-table">
                    <thead>
                      <tr><th>{t('apiRequest.column')}</th><th>{t('apiRequest.type')}</th></tr>
                    </thead>
                    <tbody>
                      {columns.map((column, index) => (
                        <tr key={column.name}>
                          <td>{column.name}</td>
                          <td>
                            <select value={column.type} aria-label={column.name}
                              onChange={event => setColumns(columns.map((c, i) => i === index ? { ...c, type: event.target.value } : c))}>
                              {check.type_choices.map(type => <option key={type} value={type}>{type}</option>)}
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="loads-muted">{t('apiRequest.structureHint')}</p>
                  <button type="button" className="admin-primary-button" disabled={busy}
                    onClick={() => { void saveStructure() }}>
                    {t('apiRequest.structureSave')}
                  </button>
                </>
              )
              : <p className="loads-muted">{t('fileSource.noColumns')}</p>}
            <div className="api-request-inline">
              <input value={newName} placeholder="a.b.c" aria-label={t('fileSource.addFieldPath')}
                onChange={event => setNewName(event.target.value)} />
              <select value={newType} aria-label={t('fileSource.addFieldType')}
                onChange={event => setNewType(event.target.value)}>
                {check.type_choices.map(type => <option key={type} value={type}>{type}</option>)}
              </select>
              <button type="button" className="admin-secondary-button" disabled={busy || !newNameValid}
                onClick={addField}>
                {t('fileSource.addField')}
              </button>
            </div>
          </fieldset>
        )
        : null}

      <fieldset className="api-request-block">
        <legend>{t('fileSource.registerTitle')}</legend>
        {register.length === 0
          ? <p className="loads-muted">{t('fileSource.registerEmpty')}</p>
          : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('fileSource.file')}</th>
                    <th>{t('fileSource.status')}</th>
                    <th>{t('fileSource.actualAt')}</th>
                    <th>{t('fileSource.rows')}</th>
                    <th>{t('fileSource.attempts')}</th>
                    <th>{t('fileSource.updatedAt')}</th>
                  </tr>
                </thead>
                <tbody>
                  {register.map(file => (
                    <tr key={file.file_id}>
                      <td title={file.sha256}>{file.file_name}</td>
                      <td>
                        {t(`fileSource.statuses.${file.status}`)}
                        {file.message ? <div className="loads-muted">{file.message}</div> : null}
                      </td>
                      <td>{when(file.actual_at)}</td>
                      <td>{file.rows_read ?? '—'}</td>
                      <td>{file.attempts}</td>
                      <td>{when(file.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </fieldset>
    </div>
  )
}

export default FileSourcePanel
