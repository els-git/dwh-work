import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useTranslation } from 'react-i18next'

import {
  getAiConfig,
  getAiRequests,
  saveAiExample,
  saveAiGlossary,
  saveAiReportObject,
  saveAiValueColumn,
  type AiConfig,
  type AiExample,
  type AiGlossaryTerm,
  type AiReportObject,
  type AiRequestLogRow,
} from './aiApi'
import { formatDateTime } from './dateTime'
import './AiAssistantPanel.css'


// I17: Administration -> "AI assistant" (AI_ADMIN_MANAGE): whitelist of reporting objects with the
// permission each needs (I17-08), reference columns, terms, example questions and the request journal.
// The database checks the permission of every call (package 016).

const PERMISSIONS = [
  'REPORT_VIEW', 'ETL_VIEW', 'DQ_VIEW', 'INTEGRATION_VIEW', 'ADMIN_INTEGRATION_MANAGE', 'AI_REPORT_RUN',
]

const EMPTY_OBJECT = { object_name: '', title: '', description: '', required_permission_code: 'REPORT_VIEW', is_active: true }
const EMPTY_TERM = { term_id: null as number | null, term: '', meaning: '', object_id: '' as string, is_active: true }
const EMPTY_EXAMPLE = { example_id: null as number | null, object_id: '', question: '', sql_text: '', is_active: true }


function AiAdminPanel() {
  const { t } = useTranslation()
  const [config, setConfig] = useState<AiConfig | null>(null)
  const [requests, setRequests] = useState<AiRequestLogRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [objectForm, setObjectForm] = useState(EMPTY_OBJECT)
  const [valueForm, setValueForm] = useState({ object_id: '', column_name: '', max_values: 50 })
  const [termForm, setTermForm] = useState(EMPTY_TERM)
  const [exampleForm, setExampleForm] = useState(EMPTY_EXAMPLE)
  const loadSeq = useRef(0)

  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const [nextConfig, nextRequests] = await Promise.all([getAiConfig(), getAiRequests(100)])
      if (seq !== loadSeq.current) {
        return
      }
      setConfig(nextConfig)
      setRequests(nextRequests)
      setError(null)
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

  async function run(action: () => Promise<unknown>, done: () => void) {
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      await action()
      done()
      setNotice(t('aiAdmin.saved'))
      await load()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setSaving(false)
    }
  }

  function objectTitle(objectId: number | null): string {
    if (objectId === null) {
      return t('aiAdmin.allObjects')
    }
    const found = config?.report_objects.find(o => o.object_id === objectId)
    return found ? `${found.schema_name}.${found.object_name}` : `#${objectId}`
  }

  function submitObject(event: FormEvent) {
    event.preventDefault()
    void run(() => saveAiReportObject({ ...objectForm, description: objectForm.description || null }),
             () => setObjectForm(EMPTY_OBJECT))
  }

  function toggleObject(item: AiReportObject) {
    void run(() => saveAiReportObject({ object_name: item.object_name, title: item.title, description: item.description,
                                        required_permission_code: item.required_permission_code,
                                        is_active: !item.is_active }), () => undefined)
  }

  function submitValue(event: FormEvent) {
    event.preventDefault()
    void run(() => saveAiValueColumn({ object_id: Number(valueForm.object_id), column_name: valueForm.column_name,
                                       max_values: valueForm.max_values, is_active: true }),
             () => setValueForm({ object_id: '', column_name: '', max_values: 50 }))
  }

  function submitTerm(event: FormEvent) {
    event.preventDefault()
    void run(() => saveAiGlossary({ ...termForm, object_id: termForm.object_id ? Number(termForm.object_id) : null }),
             () => setTermForm(EMPTY_TERM))
  }

  function editTerm(item: AiGlossaryTerm) {
    setTermForm({ term_id: item.term_id, term: item.term, meaning: item.meaning,
                  object_id: item.object_id === null ? '' : String(item.object_id), is_active: item.is_active })
  }

  function submitExample(event: FormEvent) {
    event.preventDefault()
    void run(() => saveAiExample({ ...exampleForm, object_id: Number(exampleForm.object_id) }),
             () => setExampleForm(EMPTY_EXAMPLE))
  }

  function editExample(item: AiExample) {
    setExampleForm({ example_id: item.example_id, object_id: String(item.object_id), question: item.question,
                     sql_text: item.sql_text, is_active: item.is_active })
  }

  const objects = config?.report_objects ?? []
  const objectOptions = objects.map(o => (
    <option key={o.object_id} value={o.object_id}>{o.schema_name}.{o.object_name}</option>
  ))

  return (
    <section className="content-panel ai-admin-grid">
      <div>
        <h3>{t('aiAdmin.title')}</h3>
        <p className="loads-muted">{t('aiAdmin.hint')}</p>
        {error ? <div className="error"><span>{error}</span></div> : null}
        {notice ? <div className="loads-notice">{notice}</div> : null}
      </div>

      <div>
        <h4>{t('aiAdmin.objects')}</h4>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('aiAdmin.object')}</th><th>{t('aiAdmin.objectTitle')}</th>
                <th>{t('aiAdmin.permission')}</th><th>{t('aiAdmin.status')}</th><th />
              </tr>
            </thead>
            <tbody>
              {objects.map(item => (
                <tr key={item.object_id}>
                  <td className="code-value">{item.schema_name}.{item.object_name}</td>
                  <td>{item.title}</td>
                  <td><code>{item.required_permission_code}</code></td>
                  <td>{item.is_active ? t('aiAdmin.active') : t('aiAdmin.inactive')}</td>
                  <td className="loads-actions">
                    <button type="button" disabled={saving}
                            onClick={() => setObjectForm({ object_name: item.object_name, title: item.title,
                                                          description: item.description ?? '',
                                                          required_permission_code: item.required_permission_code,
                                                          is_active: item.is_active })}>
                      {t('aiAdmin.edit')}
                    </button>
                    <button type="button" disabled={saving} onClick={() => toggleObject(item)}>
                      {item.is_active ? t('aiAdmin.disable') : t('aiAdmin.enable')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form className="ai-admin-form" onSubmit={submitObject}>
          <label>{t('aiAdmin.objectName')}
            <input required value={objectForm.object_name} placeholder="v_..."
                   onChange={e => setObjectForm({ ...objectForm, object_name: e.target.value.trim() })} />
          </label>
          <label>{t('aiAdmin.objectTitle')}
            <input required value={objectForm.title} onChange={e => setObjectForm({ ...objectForm, title: e.target.value })} />
          </label>
          <label>{t('aiAdmin.permission')}
            <input required list="ai-permissions" value={objectForm.required_permission_code}
                   onChange={e => setObjectForm({ ...objectForm, required_permission_code: e.target.value.trim().toUpperCase() })} />
            <datalist id="ai-permissions">{PERMISSIONS.map(code => <option key={code} value={code} />)}</datalist>
          </label>
          <label>{t('aiAdmin.description')}
            <textarea rows={2} value={objectForm.description}
                      onChange={e => setObjectForm({ ...objectForm, description: e.target.value })} />
          </label>
          <button type="submit" disabled={saving}>{t('aiAdmin.save')}</button>
        </form>
      </div>

      <div>
        <h4>{t('aiAdmin.valueColumns')}</h4>
        <p className="loads-muted">{t('aiAdmin.valueColumnsHint')}</p>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead><tr><th>{t('aiAdmin.object')}</th><th>{t('aiAdmin.column')}</th><th>{t('aiAdmin.maxValues')}</th><th /></tr></thead>
            <tbody>
              {(config?.value_columns ?? []).map(item => (
                <tr key={item.value_column_id}>
                  <td className="code-value">{objectTitle(item.object_id)}</td>
                  <td><code>{item.column_name}</code></td>
                  <td>{item.max_values}</td>
                  <td className="loads-actions">
                    <button type="button" disabled={saving}
                            onClick={() => void run(() => saveAiValueColumn({ ...item, is_active: !item.is_active }), () => undefined)}>
                      {item.is_active ? t('aiAdmin.disable') : t('aiAdmin.enable')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form className="ai-admin-form" onSubmit={submitValue}>
          <label>{t('aiAdmin.object')}
            <select required value={valueForm.object_id} onChange={e => setValueForm({ ...valueForm, object_id: e.target.value })}>
              <option value="" />{objectOptions}
            </select>
          </label>
          <label>{t('aiAdmin.column')}
            <input required value={valueForm.column_name}
                   onChange={e => setValueForm({ ...valueForm, column_name: e.target.value.trim() })} />
          </label>
          <label>{t('aiAdmin.maxValues')}
            <input type="number" min={1} max={500} value={valueForm.max_values}
                   onChange={e => setValueForm({ ...valueForm, max_values: Number(e.target.value) })} />
          </label>
          <button type="submit" disabled={saving}>{t('aiAdmin.save')}</button>
        </form>
      </div>

      <div>
        <h4>{t('aiAdmin.glossary')}</h4>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead><tr><th>{t('aiAdmin.term')}</th><th>{t('aiAdmin.meaning')}</th><th>{t('aiAdmin.object')}</th><th /></tr></thead>
            <tbody>
              {(config?.glossary ?? []).map(item => (
                <tr key={item.term_id}>
                  <td>{item.term}{item.is_active ? '' : ` (${t('aiAdmin.inactive')})`}</td>
                  <td>{item.meaning}</td>
                  <td className="code-value">{objectTitle(item.object_id)}</td>
                  <td className="loads-actions">
                    <button type="button" disabled={saving} onClick={() => editTerm(item)}>{t('aiAdmin.edit')}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form className="ai-admin-form" onSubmit={submitTerm}>
          <label>{t('aiAdmin.term')}
            <input required value={termForm.term} onChange={e => setTermForm({ ...termForm, term: e.target.value })} />
          </label>
          <label>{t('aiAdmin.meaning')}
            <textarea required rows={2} value={termForm.meaning} onChange={e => setTermForm({ ...termForm, meaning: e.target.value })} />
          </label>
          <label>{t('aiAdmin.object')}
            <select value={termForm.object_id} onChange={e => setTermForm({ ...termForm, object_id: e.target.value })}>
              <option value="">{t('aiAdmin.allObjects')}</option>{objectOptions}
            </select>
          </label>
          <label>{t('aiAdmin.status')}
            <select value={termForm.is_active ? '1' : '0'} onChange={e => setTermForm({ ...termForm, is_active: e.target.value === '1' })}>
              <option value="1">{t('aiAdmin.active')}</option><option value="0">{t('aiAdmin.inactive')}</option>
            </select>
          </label>
          <button type="submit" disabled={saving}>{termForm.term_id ? t('aiAdmin.saveChange') : t('aiAdmin.add')}</button>
        </form>
      </div>

      <div>
        <h4>{t('aiAdmin.examples')}</h4>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead><tr><th>{t('aiAdmin.question')}</th><th>SQL</th><th>{t('aiAdmin.object')}</th><th /></tr></thead>
            <tbody>
              {(config?.examples ?? []).map(item => (
                <tr key={item.example_id}>
                  <td>{item.question}{item.is_active ? '' : ` (${t('aiAdmin.inactive')})`}</td>
                  <td><code>{item.sql_text}</code></td>
                  <td className="code-value">{objectTitle(item.object_id)}</td>
                  <td className="loads-actions">
                    <button type="button" disabled={saving} onClick={() => editExample(item)}>{t('aiAdmin.edit')}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form className="ai-admin-form" onSubmit={submitExample}>
          <label>{t('aiAdmin.object')}
            <select required value={exampleForm.object_id} onChange={e => setExampleForm({ ...exampleForm, object_id: e.target.value })}>
              <option value="" />{objectOptions}
            </select>
          </label>
          <label>{t('aiAdmin.question')}
            <input required value={exampleForm.question} onChange={e => setExampleForm({ ...exampleForm, question: e.target.value })} />
          </label>
          <label>SQL
            <textarea required rows={3} value={exampleForm.sql_text} onChange={e => setExampleForm({ ...exampleForm, sql_text: e.target.value })} />
          </label>
          <label>{t('aiAdmin.status')}
            <select value={exampleForm.is_active ? '1' : '0'} onChange={e => setExampleForm({ ...exampleForm, is_active: e.target.value === '1' })}>
              <option value="1">{t('aiAdmin.active')}</option><option value="0">{t('aiAdmin.inactive')}</option>
            </select>
          </label>
          <button type="submit" disabled={saving}>{exampleForm.example_id ? t('aiAdmin.saveChange') : t('aiAdmin.add')}</button>
        </form>
      </div>

      <div>
        <h4>{t('aiAdmin.journal')}</h4>
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>№</th><th>{t('aiAdmin.time')}</th><th>{t('aiAdmin.user')}</th><th>{t('aiAdmin.kind')}</th>
                <th>{t('aiAdmin.question')}</th><th>{t('aiAdmin.status')}</th><th>{t('aiAdmin.rows')}</th>
              </tr>
            </thead>
            <tbody>
              {requests.map(item => (
                <tr key={item.request_id}>
                  <td>{item.request_id}</td>
                  <td>{formatDateTime(item.started_at)}</td>
                  <td>{item.username}</td>
                  <td>{item.request_kind}{item.image_attached ? ` + ${t('ai.screenshot')}` : ''}</td>
                  <td title={item.sql_text ?? ''}>{item.question}</td>
                  <td><code>{item.status}</code>{item.result_code ? <> <small>{item.result_code}</small></> : null}</td>
                  <td>{item.row_count ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}


export default AiAdminPanel
