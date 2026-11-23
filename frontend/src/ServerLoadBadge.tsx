// I13-13: server load for administrators (N1-N5 = A). The badge under the user in the sidebar shows CPU and
// memory (every 5 s); a click opens the panel over the page (every 2 s): CPU total and per core, memory and
// swap, disks, network, file systems, DWH services and PostgreSQL connections. The panel does not block the
// page: the assistant and the loads stay usable while the load is watched.
import { useEffect, useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { formatDateTime } from './dateTime'
import { getServerLoad, ServerLoadHttpError, type ServerLoad, type ServerLoadSample } from './serverLoadApi'
import './ServerLoad.css'

const BADGE_MS = 5000
const PANEL_MS = 2000
const HOUR_SECONDS = 3600
const WINDOWS = [1, 5, 15, 60] as const
const SERIES_A = '#3a8ad8'
const SERIES_B = '#d9741a'

const SERVICE_KEYS: Record<string, string> = {
  'dwh-api.service': 'serverLoad.serviceApi',
  'dwh-dispatcher.service': 'serverLoad.serviceDispatcher',
  'dwh-llm.service': 'serverLoad.serviceLlm',
  'postgresql@17-main.service': 'serverLoad.servicePostgres',
}

function percentLevel(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return 'unknown'
  }
  return value >= 90 ? 'critical' : value >= 70 ? 'warning' : 'normal'
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '—'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size >= 100 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`
}

const formatRate = (value: number | null | undefined) => `${formatBytes(value)}/s`
const formatPercent = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : `${value.toFixed(value >= 10 ? 0 : 1)} %`

function memoryPercent(load: ServerLoad | null): number | null {
  const used = load?.current?.memory.used
  return used === undefined || !load?.memory_total ? null : 100 * used / load.memory_total
}

function timeLabel(at: number): string {
  return formatDateTime(new Date(at * 1000).toISOString()).split(' ').slice(-1)[0] ?? ''
}


interface Series {
  label: string
  color: string
  values: number[]
}

interface ChartProps {
  title: string
  value: string
  times: number[]
  series: Series[]
  max?: number
  format: (value: number) => string
  small?: boolean
}

// One-axis line chart over the selected window; hover shows the time and every series at that moment.
function LineChart({ title, value, times, series, max, format, small }: ChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const width = 300
  const height = small ? 40 : 90
  const count = times.length
  const top = max ?? Math.max(1, ...series.flatMap((s) => s.values))
  const x = (i: number) => count <= 1 ? width : i * width / (count - 1)
  const y = (v: number) => height - Math.min(v, top) / top * (height - 2) - 1
  const index = hover !== null && hover < count ? hover : null

  const onMove = (event: MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (count === 0 || box.width === 0) {
      return
    }
    setHover(Math.round((event.clientX - box.left) / box.width * (count - 1)))
  }

  return (
    <div className={small ? 'load-chart load-chart-small' : 'load-chart'}>
      <div className="load-chart-head">
        <span>{title}</span>
        <strong>{value}</strong>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`${title}: ${value}`}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <line className="load-grid" x1="0" x2={width} y1={height / 2} y2={height / 2} />
        {series.map((s) => (
          <polyline key={s.label} fill="none" stroke={s.color} strokeWidth="2" vectorEffect="non-scaling-stroke"
                    strokeLinejoin="round" points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
        ))}
        {index !== null
          ? <line className="load-crosshair" x1={x(index)} x2={x(index)} y1="0" y2={height} />
          : null}
      </svg>
      {!small && series.length > 1
        ? (
          <div className="load-legend">
            {series.map((s) => (
              <span key={s.label}><i style={{ background: s.color }} />{s.label}</span>
            ))}
            <span className="load-scale">{format(top)}</span>
          </div>
        )
        : null}
      {index !== null
        ? (
          <div className="load-tooltip">
            <span>{timeLabel(times[index])}</span>
            {series.map((s) => <span key={s.label}>{series.length > 1 ? `${s.label}: ` : ''}{format(s.values[index])}</span>)}
          </div>
        )
        : null}
    </div>
  )
}


interface PanelProps {
  load: ServerLoad | null
  samples: ServerLoadSample[]
  error: string | null
  onClose: () => void
}

function ServerLoadPanel({ load, samples, error, onClose }: PanelProps) {
  const { t } = useTranslation()
  const [minutes, setMinutes] = useState<number>(5)
  const current = load?.current ?? null
  const lastAt = samples.length > 0 ? samples[samples.length - 1].at : 0
  const shown = samples.filter((s) => s.at > lastAt - minutes * 60)
  const times = shown.map((s) => s.at)
  const memTotal = load?.memory_total ?? 0
  const disks = current ? Object.keys(current.disks).sort() : []
  const postgres = current?.postgres ?? null
  const postgresTotal = postgres ? Object.values(postgres).reduce((a, b) => a + b, 0) : null

  return (
    <section className="load-panel" aria-label={t('serverLoad.title')}>
      <header className="load-panel-head">
        <div>
          <h2>{t('serverLoad.title')}</h2>
          <small>
            {current
              ? t('serverLoad.updated', { time: timeLabel(current.at), seconds: load?.interval_seconds ?? 2 })
              : t('serverLoad.waiting')}
          </small>
        </div>
        <div className="load-windows" role="group" aria-label={t('serverLoad.window')}>
          {WINDOWS.map((m) => (
            <button key={m} type="button" className={m === minutes ? 'active' : ''} onClick={() => setMinutes(m)}>
              {t('serverLoad.minutes', { count: m })}
            </button>
          ))}
        </div>
        <button type="button" className="load-close" onClick={onClose}>{t('serverLoad.close')}</button>
      </header>

      {error ? <p className="load-error">{t('serverLoad.error', { message: error })}</p> : null}
      {load && !load.sampling ? <p className="load-error">{t('serverLoad.notSampling')}</p> : null}

      {current
        ? (
          <div className="load-body">
            <div className="load-grid-2">
              <LineChart title={t('serverLoad.cpu')} value={formatPercent(current.cpu)} times={times} max={100}
                         format={formatPercent} series={[{ label: t('serverLoad.cpu'), color: SERIES_A,
                                                            values: shown.map((s) => s.cpu) }]} />
              <LineChart title={t('serverLoad.memory')}
                         value={`${formatBytes(current.memory.used)} / ${formatBytes(memTotal)}`}
                         times={times} max={100} format={formatPercent}
                         series={[{ label: t('serverLoad.memory'), color: SERIES_A,
                                    values: shown.map((s) => memTotal ? 100 * s.memory.used / memTotal : 0) }]} />
            </div>

            <dl className="load-facts">
              <div><dt>{t('serverLoad.load')}</dt><dd>{current.load.map((v) => v.toFixed(2)).join(' · ')}</dd></div>
              <div><dt>{t('serverLoad.iowait')}</dt><dd>{formatPercent(current.iowait)}</dd></div>
              <div><dt>{t('serverLoad.steal')}</dt><dd>{formatPercent(current.steal)}</dd></div>
              <div><dt>{t('serverLoad.cache')}</dt><dd>{formatBytes(current.memory.cache)}</dd></div>
              <div>
                <dt>{t('serverLoad.swap')}</dt>
                <dd>{formatBytes(current.memory.swap_used)} / {formatBytes(load?.swap_total)}</dd>
              </div>
            </dl>

            <h3>{t('serverLoad.cores', { count: current.cores.length })}</h3>
            <div className="load-cores">
              {current.cores.map((value, core) => (
                <LineChart key={core} small title={t('serverLoad.core', { n: core })} value={formatPercent(value)}
                           times={times} max={100} format={formatPercent}
                           series={[{ label: String(core), color: SERIES_A, values: shown.map((s) => s.cores[core] ?? 0) }]} />
              ))}
            </div>

            <h3>{t('serverLoad.disks')}</h3>
            <div className="load-grid-2">
              {disks.map((disk) => (
                <LineChart key={disk} title={disk}
                           value={`${t('serverLoad.active')} ${formatPercent(current.disks[disk].busy)}`}
                           times={times} format={formatRate}
                           series={[
                             { label: t('serverLoad.read'), color: SERIES_A, values: shown.map((s) => s.disks[disk]?.read ?? 0) },
                             { label: t('serverLoad.write'), color: SERIES_B, values: shown.map((s) => s.disks[disk]?.write ?? 0) },
                           ]} />
              ))}
              <LineChart title={t('serverLoad.network')}
                         value={`↓ ${formatRate(current.net.rx)} · ↑ ${formatRate(current.net.tx)}`}
                         times={times} format={formatRate}
                         series={[
                           { label: t('serverLoad.received'), color: SERIES_A, values: shown.map((s) => s.net.rx) },
                           { label: t('serverLoad.sent'), color: SERIES_B, values: shown.map((s) => s.net.tx) },
                         ]} />
            </div>

            <table className="load-table">
              <thead>
                <tr><th>{t('serverLoad.fileSystem')}</th><th>{t('serverLoad.free')}</th><th>{t('serverLoad.size')}</th><th>{t('serverLoad.used')}</th></tr>
              </thead>
              <tbody>
                {(load?.file_systems ?? []).map((fs) => (
                  <tr key={fs.path}>
                    <td>{fs.path}</td>
                    <td>{formatBytes(fs.free)}</td>
                    <td>{formatBytes(fs.total)}</td>
                    <td><span className={`load-level load-${percentLevel(fs.used_percent)}`}>{formatPercent(fs.used_percent)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>{t('serverLoad.services')}</h3>
            <table className="load-table">
              <thead>
                <tr><th>{t('serverLoad.service')}</th><th>{t('serverLoad.cpuShare')}</th><th>{t('serverLoad.memory')}</th></tr>
              </thead>
              <tbody>
                {Object.entries(current.services).map(([unit, service]) => (
                  <tr key={unit}>
                    <td>{SERVICE_KEYS[unit] ? t(SERVICE_KEYS[unit]) : unit} <small>{unit}</small></td>
                    {service.running
                      ? <><td>{formatPercent(service.cpu)}</td><td>{formatBytes(service.memory)}</td></>
                      : <td colSpan={2}>{t('serverLoad.notRunning')}</td>}
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>{t('serverLoad.postgres', { count: postgresTotal ?? 0 })}</h3>
            {postgres
              ? (
                <table className="load-table">
                  <tbody>
                    {Object.entries(postgres).map(([role, count]) => (
                      <tr key={role}><td>{role}</td><td>{count}</td></tr>
                    ))}
                  </tbody>
                </table>
              )
              : <p className="load-note">{t('serverLoad.postgresUnknown')}</p>}
            <p className="load-note">{t('serverLoad.note')}</p>
          </div>
        )
        : null}
    </section>
  )
}


export default function ServerLoadBadge() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [load, setLoad] = useState<ServerLoad | null>(null)
  const [samples, setSamples] = useState<ServerLoadSample[]>([])
  const [error, setError] = useState<string | null>(null)
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    if (denied) {
      return undefined
    }
    // One polling loop per mode; a reply that arrives after the mode changed is dropped (cancelled).
    let cancelled = false
    let timer: number | undefined
    let lastAt: number | null = null

    const tick = async () => {
      if (!document.hidden) {
        try {
          const data = await getServerLoad(open ? (lastAt ?? 0) : undefined)
          if (cancelled) {
            return
          }
          if (open && data.samples) {
            const fresh = data.samples
            const first = lastAt === null
            if (fresh.length > 0) {
              lastAt = fresh[fresh.length - 1].at
            } else if (first) {
              lastAt = 0
            }
            setSamples((prev) => {
              const merged = first ? fresh : [...prev, ...fresh]
              const edge = (merged.length > 0 ? merged[merged.length - 1].at : 0) - HOUR_SECONDS
              return merged.filter((s) => s.at > edge)
            })
          }
          setLoad(data)
          setError(null)
        } catch (exc) {
          if (cancelled) {
            return
          }
          if (exc instanceof ServerLoadHttpError && (exc.status === 401 || exc.status === 403)) {
            setDenied(true)
            return
          }
          setError(exc instanceof Error ? exc.message : String(exc))
        }
      }
      if (!cancelled) {
        timer = window.setTimeout(() => { void tick() }, open ? PANEL_MS : BADGE_MS)
      }
    }

    void tick()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [open, denied])

  if (denied) {
    return null
  }

  const cpu = load?.current?.cpu ?? null
  const mem = memoryPercent(load)

  return (
    <>
      <button type="button" className="load-badge" onClick={() => setOpen((v) => !v)}
              aria-expanded={open} title={t('serverLoad.badgeTitle')}>
        <span className="load-badge-title">{t('serverLoad.badge')}</span>
        <span className={`load-badge-item load-${percentLevel(cpu)}`}>
          <span>{t('serverLoad.cpuShort')}</span>
          <strong>{formatPercent(cpu)}</strong>
          <i style={{ width: `${Math.min(100, cpu ?? 0)}%` }} />
        </span>
        <span className={`load-badge-item load-${percentLevel(mem)}`}>
          <span>{t('serverLoad.memShort')}</span>
          <strong>{formatPercent(mem)}</strong>
          <i style={{ width: `${Math.min(100, mem ?? 0)}%` }} />
        </span>
        {error ? <small className="load-badge-error">{t('serverLoad.noData')}</small> : null}
      </button>
      {open
        // The panel is rendered at the page level: inside the sidebar it would stay below the page's own layers.
        ? createPortal(<ServerLoadPanel load={load} samples={samples} error={error} onClose={() => setOpen(false)} />,
                       document.body)
        : null}
    </>
  )
}
