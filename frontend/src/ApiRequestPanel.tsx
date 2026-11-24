import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import {
  getApiRequest,
  importApiCurl,
  probeApiRequest,
  revokeApiCredential,
  saveApiRequest,
  saveApiStructure,
  setApiCredential,
  type ApiAuthKind,
  type ApiPageKind,
  type ApiProbeColumn,
  type ApiProbeResult,
  type ApiRequestDocument,
  type ApiRequestState,
  type IntegrationSourceObject,
} from './integrationAdminApi'
import './LoadsPage.css'


// I11: tab "HTTP request" of an API object in the mapping editor. The request is saved into the draft of the
// object; the credential (token, password, key) is sent once and never shown again. "Check the request" reads
// the first page and suggests columns; "Save structure" makes them the source columns of the mapping.
// Every state update happens after the await; answers to older requests are ignored.

interface Form {
  url: string
  format: 'JSON' | 'XML'
  recordsPath: string
  authKind: ApiAuthKind
  authUser: string
  authName: string
  pageKind: ApiPageKind
  size: string
  pageParam: string
  sizeParam: string
  offsetParam: string
  limitParam: string
  cursorParam: string
  cursorPath: string
  nextPath: string
  maxPages: string
  incrementParam: string
  params: string
  headers: string
}

const EMPTY: Form = {
  url: '', format: 'JSON', recordsPath: '', authKind: 'NONE', authUser: '', authName: '', pageKind: 'NONE',
  size: '100', pageParam: 'page', sizeParam: 'size', offsetParam: 'offset', limitParam: 'limit',
  cursorParam: 'cursor', cursorPath: '', nextPath: 'LINK_HEADER', maxPages: '1000', incrementParam: '',
  params: '', headers: '',
}

function formOf(doc: ApiRequestDocument | null): Form {
  if (!doc) {
    return EMPTY
  }
  const p = doc.pagination ?? { kind: 'NONE' as ApiPageKind }
  return {
    url: doc.url,
    format: doc.format ?? 'JSON',
    recordsPath: doc.records_path ?? '',
    authKind: doc.auth?.kind ?? 'NONE',
    authUser: doc.auth?.user ?? '',
    authName: doc.auth?.name ?? '',
    pageKind: p.kind,
    size: String(p.size ?? 100),
    pageParam: p.page_param ?? 'page',
    sizeParam: p.size_param ?? (p.kind === 'PAGE' ? 'size' : ''),
    offsetParam: p.offset_param ?? 'offset',
    limitParam: p.limit_param ?? 'limit',
    cursorParam: p.cursor_param ?? 'cursor',
    cursorPath: p.cursor_path ?? '',
    nextPath: p.next_path ?? 'LINK_HEADER',
    maxPages: String(p.max_pages ?? 1000),
    incrementParam: doc.increment?.param ?? '',
    params: (doc.params ?? []).map(x => `${x.name}=${x.value}`).join('\n'),
    headers: (doc.headers ?? []).map(x => `${x.name}: ${x.value}`).join('\n'),
  }
}

function lines(text: string, separator: string) {
  return text.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const at = line.indexOf(separator)
    return at < 0
      ? { name: line, value: '' }
      : { name: line.slice(0, at).trim(), value: line.slice(at + separator.length).trim() }
  })
}

function docOf(form: Form): ApiRequestDocument {
  const size = Number(form.size) || 100
  const pagination: NonNullable<ApiRequestDocument['pagination']> = {
    kind: form.pageKind, max_pages: Number(form.maxPages) || 1000,
  }
  if (form.pageKind === 'PAGE') {
    Object.assign(pagination, { page_param: form.pageParam, size_param: form.sizeParam || 'size', size })
  } else if (form.pageKind === 'OFFSET') {
    Object.assign(pagination, { offset_param: form.offsetParam, limit_param: form.limitParam, size })
  } else if (form.pageKind === 'CURSOR') {
    Object.assign(pagination, { cursor_param: form.cursorParam, cursor_path: form.cursorPath })
    if (form.sizeParam) {
      Object.assign(pagination, { size_param: form.sizeParam, size })
    }
  } else if (form.pageKind === 'NEXT_LINK') {
    Object.assign(pagination, { next_path: form.nextPath || 'LINK_HEADER' })
    if (form.sizeParam) {
      Object.assign(pagination, { size_param: form.sizeParam, size })
    }
  }
  return {
    url: form.url.trim(),
    format: form.format,
    records_path: form.recordsPath.trim(),
    auth: form.authKind === 'BASIC'
      ? { kind: 'BASIC', user: form.authUser.trim() }
      : form.authKind === 'HEADER' || form.authKind === 'QUERY'
        ? { kind: form.authKind, name: form.authName.trim() }
        : { kind: form.authKind },
    pagination,
    increment: form.incrementParam.trim() ? { param: form.incrementParam.trim() } : null,
    params: lines(form.params, '='),
    headers: lines(form.headers, ':'),
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

interface ApiRequestPanelProps {
  sourceObject: IntegrationSourceObject
  onStructureSaved: () => void
}

function ApiRequestPanel({ sourceObject, onStructureSaved }: ApiRequestPanelProps) {
  const { t } = useTranslation()
  const objectId = sourceObject.source_object_id
  const [state, setState] = useState<ApiRequestState | null>(null)
  const [form, setForm] = useState<Form>(EMPTY)
  const [curl, setCurl] = useState('')
  const [secret, setSecret] = useState('')
  const [probe, setProbe] = useState<ApiProbeResult | null>(null)
  const [columns, setColumns] = useState<ApiProbeColumn[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)

  const load = useCallback(async (resetForm: boolean) => {
    const mine = ++seq.current
    try {
      const value = await getApiRequest(objectId)
      if (mine === seq.current) {
        setState(value)
        if (resetForm) {
          setForm(formOf(value.draft_request ?? value.published_request))
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

  async function act(action: () => Promise<string>, resetForm = false) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const text = await action()
      setNotice(text)
      await load(resetForm)
    } catch (actionError) {
      setError(message(actionError))
    } finally {
      setBusy(false)
    }
  }

  const set = (patch: Partial<Form>) => setForm({ ...form, ...patch })

  const saveRequest = () => act(async () => {
    await saveApiRequest(objectId, docOf(form))
    return t('apiRequest.saved')
  })

  const doImport = () => act(async () => {
    const result = await importApiCurl(objectId, curl)
    setCurl('')
    return t('apiRequest.imported') + (result.credential_stored ? ' ' + t('apiRequest.importedCredential') : '')
      + (result.notes.length ? ` (${result.notes.join('; ')})` : '')
  }, true)

  const saveSecret = () => act(async () => {
    await setApiCredential(objectId, secret)
    setSecret('')
    return t('apiRequest.credentialSaved')
  })

  const revokeSecret = () => act(async () => {
    await revokeApiCredential(objectId)
    return t('apiRequest.credentialRevoked')
  })

  const doProbe = () => act(async () => {
    const result = await probeApiRequest(objectId, docOf(form))
    setProbe(result)
    setColumns(result.columns)
    return t('apiRequest.probed', { records: result.records })
  })

  const saveStructure = () => act(async () => {
    const result = await saveApiStructure(objectId, columns)
    onStructureSaved()
    return t('apiRequest.structureSaved', { columns: result.columns_captured })
  })

  if (state === null) {
    return error
      ? <div className="error"><span>{error}</span></div>
      : <div className="loading-panel">{t('apiRequest.loading')}</div>
  }

  return (
    <div className="api-request-panel">
      <p className="loads-muted">
        {state.draft_version_id !== null ? t('apiRequest.inDraft') : t('apiRequest.noDraft')}
        {' '}
        {t('apiRequest.allowedHosts', { hosts: state.allowed_hosts.join(', ') || '—' })}
      </p>

      {error ? <div className="error"><span>{error}</span></div> : null}
      {notice ? <div className="loads-notice">{notice}</div> : null}

      <fieldset className="api-request-block">
        <legend>{t('apiRequest.importTitle')}</legend>
        <textarea rows={3} value={curl} placeholder="curl 'https://…' -H 'Authorization: Bearer …'"
          onChange={event => setCurl(event.target.value)} />
        <p className="loads-muted">{t('apiRequest.importHint')}</p>
        <button type="button" className="admin-secondary-button" disabled={busy || curl.trim().length < 4}
          onClick={() => { void doImport() }}>
          {t('apiRequest.import')}
        </button>
      </fieldset>

      <fieldset className="api-request-block api-request-form">
        <legend>{t('apiRequest.requestTitle')}</legend>
        <label className="wide">
          <span>{t('apiRequest.url')}</span>
          <input value={form.url} placeholder="https://host/path" onChange={event => set({ url: event.target.value })} />
        </label>
        <label>
          <span>{t('apiRequest.format')}</span>
          <select value={form.format} onChange={event => set({ format: event.target.value as Form['format'] })}>
            <option value="JSON">JSON</option>
            <option value="XML">XML</option>
          </select>
        </label>
        <label>
          <span>{t('apiRequest.recordsPath')}</span>
          <input value={form.recordsPath} placeholder={form.format === 'XML' ? 'response.items.service' : 'data.items'}
            onChange={event => set({ recordsPath: event.target.value })} />
        </label>
        <label>
          <span>{t('apiRequest.authKind')}</span>
          <select value={form.authKind} onChange={event => set({ authKind: event.target.value as ApiAuthKind })}>
            {(['NONE', 'BEARER', 'BASIC', 'HEADER', 'QUERY'] as ApiAuthKind[]).map(kind => (
              <option key={kind} value={kind}>{t(`apiRequest.auth.${kind}`)}</option>
            ))}
          </select>
        </label>
        {form.authKind === 'BASIC'
          ? (
            <label>
              <span>{t('apiRequest.authUser')}</span>
              <input value={form.authUser} onChange={event => set({ authUser: event.target.value })} />
            </label>
          )
          : null}
        {form.authKind === 'HEADER' || form.authKind === 'QUERY'
          ? (
            <label>
              <span>{t('apiRequest.authName')}</span>
              <input value={form.authName} placeholder={form.authKind === 'HEADER' ? 'X-Api-Key' : 'api_key'}
                onChange={event => set({ authName: event.target.value })} />
            </label>
          )
          : null}
        <label>
          <span>{t('apiRequest.pageKind')}</span>
          <select value={form.pageKind} onChange={event => set({ pageKind: event.target.value as ApiPageKind })}>
            {(['NONE', 'PAGE', 'OFFSET', 'CURSOR', 'NEXT_LINK'] as ApiPageKind[]).map(kind => (
              <option key={kind} value={kind}>{t(`apiRequest.page.${kind}`)}</option>
            ))}
          </select>
        </label>
        {form.pageKind === 'PAGE'
          ? (
            <>
              <label><span>{t('apiRequest.pageParam')}</span>
                <input value={form.pageParam} onChange={event => set({ pageParam: event.target.value })} /></label>
              <label><span>{t('apiRequest.sizeParam')}</span>
                <input value={form.sizeParam} onChange={event => set({ sizeParam: event.target.value })} /></label>
            </>
          )
          : null}
        {form.pageKind === 'OFFSET'
          ? (
            <>
              <label><span>{t('apiRequest.offsetParam')}</span>
                <input value={form.offsetParam} onChange={event => set({ offsetParam: event.target.value })} /></label>
              <label><span>{t('apiRequest.limitParam')}</span>
                <input value={form.limitParam} onChange={event => set({ limitParam: event.target.value })} /></label>
            </>
          )
          : null}
        {form.pageKind === 'CURSOR'
          ? (
            <>
              <label><span>{t('apiRequest.cursorParam')}</span>
                <input value={form.cursorParam} onChange={event => set({ cursorParam: event.target.value })} /></label>
              <label><span>{t('apiRequest.cursorPath')}</span>
                <input value={form.cursorPath} placeholder="next_cursor"
                  onChange={event => set({ cursorPath: event.target.value })} /></label>
            </>
          )
          : null}
        {form.pageKind === 'NEXT_LINK'
          ? (
            <label><span>{t('apiRequest.nextPath')}</span>
              <input value={form.nextPath} onChange={event => set({ nextPath: event.target.value })} /></label>
          )
          : null}
        {form.pageKind !== 'NONE'
          ? (
            <>
              {form.pageKind === 'CURSOR' || form.pageKind === 'NEXT_LINK'
                ? (
                  <label><span>{t('apiRequest.sizeParamOptional')}</span>
                    <input value={form.sizeParam} onChange={event => set({ sizeParam: event.target.value })} /></label>
                )
                : null}
              <label><span>{t('apiRequest.size')}</span>
                <input value={form.size} inputMode="numeric" onChange={event => set({ size: event.target.value })} /></label>
              <label><span>{t('apiRequest.maxPages')}</span>
                <input value={form.maxPages} inputMode="numeric" onChange={event => set({ maxPages: event.target.value })} /></label>
            </>
          )
          : null}
        <label>
          <span>{t('apiRequest.incrementParam')}</span>
          <input value={form.incrementParam} placeholder="updated_since"
            onChange={event => set({ incrementParam: event.target.value })} />
        </label>
        <label className="wide">
          <span>{t('apiRequest.params')}</span>
          <textarea rows={3} value={form.params} placeholder="limit=50"
            onChange={event => set({ params: event.target.value })} />
        </label>
        <label className="wide">
          <span>{t('apiRequest.headers')}</span>
          <textarea rows={2} value={form.headers} placeholder="Accept: application/json"
            onChange={event => set({ headers: event.target.value })} />
        </label>
        <div className="admin-integrations-form-actions wide">
          <button type="button" className="admin-secondary-button" disabled={busy || !form.url.trim()}
            onClick={() => { void doProbe() }}>
            {t('apiRequest.probe')}
          </button>
          <button type="button" className="admin-primary-button" disabled={busy || !form.url.trim()}
            onClick={() => { void saveRequest() }}>
            {t('apiRequest.save')}
          </button>
        </div>
      </fieldset>

      <fieldset className="api-request-block">
        <legend>{t('apiRequest.credentialTitle')}</legend>
        <p>
          {state.has_credential
            ? t('apiRequest.credentialSet', { key: state.credential_key_id ?? '—' })
            : t('apiRequest.credentialNone')}
        </p>
        <div className="api-request-inline">
          <input type="password" autoComplete="new-password" value={secret} placeholder={t('apiRequest.credentialValue')}
            onChange={event => setSecret(event.target.value)} />
          <button type="button" className="admin-secondary-button" disabled={busy || !secret}
            onClick={() => { void saveSecret() }}>
            {state.has_credential ? t('apiRequest.credentialReplace') : t('apiRequest.credentialSave')}
          </button>
          {state.has_credential
            ? (
              <button type="button" className="admin-secondary-button" disabled={busy}
                onClick={() => { void revokeSecret() }}>
                {t('apiRequest.credentialRevoke')}
              </button>
            )
            : null}
        </div>
      </fieldset>

      {probe
        ? (
          <fieldset className="api-request-block">
            <legend>{t('apiRequest.structureTitle')}</legend>
            <p>{t('apiRequest.probeResult', { records: probe.records })}
              {probe.more_pages ? ' ' + t('apiRequest.morePages') : ''}</p>
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
                        {probe.type_choices.map(type => <option key={type} value={type}>{type}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="loads-muted">{t('apiRequest.structureHint')}</p>
            <button type="button" className="admin-primary-button" disabled={busy || columns.length === 0}
              onClick={() => { void saveStructure() }}>
              {t('apiRequest.structureSave')}
            </button>
          </fieldset>
        )
        : null}

      {state.curl
        ? (
          <fieldset className="api-request-block">
            <legend>{t('apiRequest.curlTitle')}</legend>
            <code className="api-request-curl">{state.curl}</code>
          </fieldset>
        )
        : null}
    </div>
  )
}

export default ApiRequestPanel
