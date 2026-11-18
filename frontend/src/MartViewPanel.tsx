import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { formatDateTime } from './dateTime'
import {
  exportMart,
  getMartPage,
  getMarts,
  MartViewHttpError,
  openMartView,
  type MartCell,
  type MartColumn,
  type MartColumnKind,
  type MartExportFormat,
  type MartFilter,
  type MartFilterOp,
  type MartItem,
  type MartPage,
  type MartRefusal,
  type MartSort,
} from './martViewApi'
import './SavedReportsPanel.css'


// I22: "Reporting" -> "Marts". The marts the role may open; a page of 100 rows with filters and sort; export
// CSV / XLSX / PDF. Every opening and export is logged by the server.
const FORMATS: MartExportFormat[] = ['CSV', 'XLSX', 'PDF']

const OPS: Record<MartColumnKind, MartFilterOp[]> = {
  number: ['eq', 'gte', 'lte', 'empty', 'not_empty'],
  date: ['eq', 'gte', 'lte', 'empty', 'not_empty'],
  text: ['eq', 'contains', 'empty', 'not_empty'],
  boolean: ['eq', 'empty', 'not_empty'],
}

const NO_VALUE: MartFilterOp[] = ['empty', 'not_empty']


function cellText(value: MartCell): string {
  if (value === null) {
    return ''
  }
  return typeof value === 'boolean' ? (value ? '✓' : '—') : String(value)
}


function MartViewPanel() {
  const { t, i18n } = useTranslation()
  const [marts, setMarts] = useState<MartItem[]>([])
  const [selected, setSelected] = useState<MartItem | null>(null)
  const [page, setPage] = useState<MartPage | null>(null)
  const [draft, setDraft] = useState<MartFilter[]>([])
  const [format, setFormat] = useState<MartExportFormat>('XLSX')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<MartRefusal | null>(null)
  const seq = useRef(0)

  const number = useCallback((value: number) => value.toLocaleString(i18n.language), [i18n.language])

  useEffect(() => {
    let alive = true
    getMarts()
      .then(list => {
        if (alive) {
          setMarts(list)
        }
      })
      .catch((exc: unknown) => {
        if (alive) {
          setError(exc instanceof Error ? exc.message : String(exc))
        }
      })
    return () => {
      alive = false
    }
  }, [])

  function showFailure(exc: unknown) {
    if (exc instanceof MartViewHttpError && exc.refusal?.code) {
      setRefusal(exc.refusal)
      setError(null)
      return
    }
    setRefusal(null)
    setError(exc instanceof Error ? exc.message : String(exc))
  }

  async function load(work: () => Promise<MartPage>) {
    const current = ++seq.current
    setBusy(true)
    setError(null)
    setRefusal(null)
    try {
      const result = await work()
      if (current === seq.current) {
        setPage(result)
      }
    } catch (exc) {
      if (current === seq.current) {
        showFailure(exc)
      }
    } finally {
      if (current === seq.current) {
        setBusy(false)
      }
    }
  }

  function choose(mart: MartItem) {
    setSelected(mart)
    setPage(null)
    setDraft([])
    void load(() => openMartView(mart.object_id, [], null))
  }

  function apply(event: FormEvent) {
    event.preventDefault()
    if (selected) {
      void load(() => openMartView(selected.object_id, draft, page?.sort ?? null))
    }
  }

  function sortBy(column: MartColumn) {
    if (!selected || !page) {
      return
    }
    const sort: MartSort = { column: column.name, desc: page.sort?.column === column.name ? !page.sort.desc : false }
    void load(() => openMartView(selected.object_id, page.filters, sort))
  }

  function goTo(target: number) {
    if (page) {
      void load(() => getMartPage(page.view_id, target))
    }
  }

  async function download() {
    if (!selected || !page) {
      return
    }
    setBusy(true)
    setError(null)
    setRefusal(null)
    try {
      await exportMart(selected.object_id, page.filters, page.sort, format)
    } catch (exc) {
      showFailure(exc)
    } finally {
      setBusy(false)
    }
  }

  function setFilter(index: number, change: Partial<MartFilter>) {
    setDraft(draft.map((f, i) => (i === index ? { ...f, ...change } : f)))
  }

  function addFilter() {
    const column = page?.columns[0]
    if (column) {
      setDraft([...draft, { column: column.name, op: OPS[column.kind][0], value: '' }])
    }
  }

  const columnOf = (name: string) => page?.columns.find(c => c.name === name) ?? null
  const pages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1

  function refusalBlock(r: MartRefusal) {
    if (r.code === 'MART_EXPORT_TOO_MANY_ROWS' && r.format && r.rows !== undefined && r.limit !== undefined) {
      const others = r.other_formats ?? []
      return (
        <div className="error mart-refusal">
          <strong>{t('martView.tooMany.title', { format: r.format })}</strong>
          <p>{t('martView.tooMany.text', { mart: r.mart ?? selected?.title ?? '', code: selected?.object_name ?? '',
                                            rows: number(r.rows), format: r.format, limit: number(r.limit) })}</p>
          <p>{t('martView.tooMany.what')}</p>
          <ul>
            <li>{t('martView.tooMany.narrow', { limit: number(r.limit) })}</li>
            {others.length > 0
              ? <li>{t('martView.tooMany.other', { formats: others.join(' / '),
                                                   limit: number(page?.export_limits[others[0]] ?? 0) })}</li>
              : <li>{t('martView.tooMany.parts')}</li>}
          </ul>
        </div>
      )
    }
    const known = ['MART_FILTER_VALUE', 'MART_FILTER_COLUMN', 'MART_FILTER_OPERATOR', 'MART_SORT_COLUMN',
                   'MART_QUERY_TIMEOUT', 'AI_OBJECT_NOT_ALLOWED']
    return (
      <div className="error">
        <span>{known.includes(r.code ?? '') ? t(`martView.code.${r.code}`, { column: r.column ?? '' }) : r.message}</span>
        {r.code === 'MART_FILTER_VALUE' && r.message ? <small> ({r.message})</small> : null}
      </div>
    )
  }

  return (
    <section className="content-panel saved-reports mart-view">
      <div className="saved-reports-head">
        <h3>{t('martView.tab')}</h3>
        <p className="loads-muted">{t('martView.hint')}</p>
        {error ? <div className="error"><span>{error}</span></div> : null}
        {refusal ? refusalBlock(refusal) : null}
      </div>

      <div className="saved-reports-grid">
        <aside className="saved-reports-folders">
          {marts.length === 0 ? <p className="loads-muted">{t('martView.noMarts')}</p> : null}
          {marts.map(m => (
            <button key={m.object_id} type="button" title={m.description ?? ''}
                    className={m.object_id === selected?.object_id ? 'saved-folder active' : 'saved-folder'}
                    onClick={() => choose(m)}>
              <span>{m.title}</span>
              <small>
                dm.{m.object_name}
                {m.last_built_at ? ` · ${t('martView.built')} ${formatDateTime(m.last_built_at)}` : ''}
              </small>
            </button>
          ))}
        </aside>

        <div className="saved-reports-list">
          {selected === null ? <p className="loads-muted">{t('martView.choose')}</p> : null}
          {selected && page
            ? (
                <>
                  <div className="saved-reports-folder-head">
                    <strong>{selected.title}</strong>
                    <span className="loads-muted">dm.{selected.object_name} · {t('martView.rows', { count: page.total, rows: number(page.total) })}</span>
                  </div>

                  <form className="mart-filters" onSubmit={apply}>
                    {draft.map((f, index) => {
                      const kind = columnOf(f.column)?.kind ?? 'text'
                      return (
                        <div key={index} className="mart-filter">
                          <select value={f.column} onChange={e => {
                            const next = columnOf(e.target.value)
                            const ops = OPS[next?.kind ?? 'text']
                            setFilter(index, { column: e.target.value, op: ops.includes(f.op) ? f.op : ops[0] })
                          }}>
                            {page.columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                          </select>
                          <select value={f.op} onChange={e => setFilter(index, { op: e.target.value as MartFilterOp })}>
                            {OPS[kind].map(op => <option key={op} value={op}>{t(`martView.op.${op}`)}</option>)}
                          </select>
                          {NO_VALUE.includes(f.op)
                            ? null
                            : kind === 'boolean'
                              ? (
                                  <select value={f.value ?? ''} onChange={e => setFilter(index, { value: e.target.value })}>
                                    <option value="" />
                                    <option value="true">{t('martView.yes')}</option>
                                    <option value="false">{t('martView.no')}</option>
                                  </select>
                                )
                              : <input required maxLength={200} value={f.value ?? ''}
                                       type={kind === 'date' && !columnOf(f.column)?.type.startsWith('timestamp') ? 'date' : 'text'}
                                       placeholder={kind === 'date' ? 'YYYY-MM-DD' : ''}
                                       onChange={e => setFilter(index, { value: e.target.value })} />}
                          <button type="button" onClick={() => setDraft(draft.filter((_, i) => i !== index))}>×</button>
                        </div>
                      )
                    })}
                    <div className="loads-actions">
                      <button type="button" disabled={busy || draft.length >= 20} onClick={addFilter}>{t('martView.addFilter')}</button>
                      <button type="submit" disabled={busy}>{t('martView.apply')}</button>
                    </div>
                  </form>

                  <div className="loads-actions mart-export">
                    <select value={format} onChange={e => setFormat(e.target.value as MartExportFormat)}>
                      {FORMATS.map(fmt => (
                        <option key={fmt} value={fmt}>
                          {fmt}{page.total > page.export_limits[fmt] ? ` — ${t('martView.overLimit', { limit: number(page.export_limits[fmt]) })}` : ''}
                        </option>
                      ))}
                    </select>
                    <button type="button" disabled={busy || page.total === 0} onClick={() => void download()}>
                      {t('martView.download', { rows: number(page.total) })}
                    </button>
                  </div>

                  <div className="data-table-wrapper saved-result-table">
                    <table className="data-table">
                      <thead>
                        <tr>
                          {page.columns.map(c => (
                            <th key={c.name} className="mart-sortable" title={c.type} onClick={() => sortBy(c)}>
                              {c.name}{page.sort?.column === c.name ? (page.sort.desc ? ' ▼' : ' ▲') : ''}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {page.rows.map((row, index) => (
                          <tr key={index}>{page.columns.map(c => <td key={c.name}>{cellText(row[c.name] ?? null)}</td>)}</tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="loads-actions">
                    <button type="button" disabled={busy || page.page <= 1} onClick={() => goTo(page.page - 1)}>{t('martView.prev')}</button>
                    <span>{t('martView.page', { page: number(page.page), pages: number(pages) })}</span>
                    <button type="button" disabled={busy || page.page >= pages} onClick={() => goTo(page.page + 1)}>{t('martView.next')}</button>
                  </div>
                </>
              )
            : selected && busy ? <p className="loads-muted">{t('martView.loading')}</p> : null}
        </div>
      </div>
    </section>
  )
}


export default MartViewPanel
