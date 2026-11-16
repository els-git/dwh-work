import {
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from 'react'
import { useTranslation } from 'react-i18next'

import {
  askAiHelp,
  askAiReport,
  getAiStatus,
  type AiCell,
  type AiReportAnswer,
  type AiStatus,
} from './aiApi'
import './AiAssistantPanel.css'


// I17: chat of the AI assistant, available on every screen. Help answers come from the user guide
// sections the user's role may read; a screenshot (I17-07) is sent once with the question and kept
// only in this page. Reports from a description show the SQL, the rows and a CSV download.

type Mode = 'help' | 'report'

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  image?: string
  status?: string
  sections?: string[]
  report?: AiReportAnswer
}

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const MAX_SHOT_WIDTH = 1600
const SHOWN_ROWS = 100


function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(file)
  })
}


async function captureScreen(): Promise<string> {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })
  try {
    const video = document.createElement('video')
    video.srcObject = stream
    video.muted = true
    await video.play()
    const scale = Math.min(1, MAX_SHOT_WIDTH / (video.videoWidth || MAX_SHOT_WIDTH))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(video.videoWidth * scale)
    canvas.height = Math.round(video.videoHeight * scale)
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
    video.pause()
    return canvas.toDataURL('image/jpeg', 0.85)
  } finally {
    stream.getTracks().forEach(track => track.stop())
  }
}


function cellText(value: AiCell): string {
  return value === null ? '' : String(value)
}


function downloadCsv(answer: AiReportAnswer) {
  const columns = answer.columns ?? []
  const quote = (text: string) => `"${text.replace(/"/g, '""')}"`
  const lines = [columns.map(quote).join(';')]
  for (const row of answer.rows ?? []) {
    lines.push(columns.map(column => quote(cellText(row[column] ?? null))).join(';'))
  }
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `dwh_report_${answer.request_id}.csv`
  link.click()
  URL.revokeObjectURL(url)
}


function AiAssistantPanel() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [mode, setMode] = useState<Mode>('help')
  const [question, setQuestion] = useState('')
  const [image, setImage] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const askSeq = useRef(0)
  const messageId = useRef(0)
  const listRef = useRef<HTMLDivElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (!open) {
      return
    }
    let active = true
    getAiStatus()
      .then(value => {
        if (active) {
          setStatus(value)
        }
      })
      .catch(() => {
        if (active) {
          setStatus(null)
        }
      })
    return () => {
      active = false
    }
  }, [open])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages, busy])

  function push(message: Omit<ChatMessage, 'id'>) {
    messageId.current += 1
    const id = messageId.current
    setMessages(current => [...current, { ...message, id }])
  }

  async function attachFile(file: File | null | undefined) {
    setError(null)
    if (!file) {
      return
    }
    if (!IMAGE_TYPES.includes(file.type)) {
      setError(t('ai.imageType'))
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError(t('ai.imageSize'))
      return
    }
    try {
      setImage(await readFile(file))
    } catch {
      setError(t('ai.imageRead'))
    }
  }

  async function takeScreenshot() {
    setError(null)
    try {
      setImage(await captureScreen())
    } catch {
      setError(t('ai.screenshotFailed'))
    }
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const item = Array.from(event.clipboardData.items).find(entry => IMAGE_TYPES.includes(entry.type))
    if (item) {
      event.preventDefault()
      void attachFile(item.getAsFile())
    }
  }

  async function send() {
    const text = question.trim()
    if (!text || busy) {
      return
    }
    const seq = ++askSeq.current
    const sentImage = mode === 'help' ? image : null
    push({ role: 'user', text, image: sentImage ?? undefined })
    setQuestion('')
    setImage(null)
    setError(null)
    setBusy(true)
    try {
      if (mode === 'help') {
        const answer = await askAiHelp(text, sentImage)
        if (seq !== askSeq.current) {
          return
        }
        push({ role: 'assistant', text: answer.answer, status: answer.status,
               sections: answer.sections.map(s => s.title) })
      } else {
        const answer = await askAiReport(text)
        if (seq !== askSeq.current) {
          return
        }
        const summary = answer.status === 'OK'
          ? t('ai.reportRows', { count: answer.rows?.length ?? 0 })
          : answer.message ?? answer.status
        push({ role: 'assistant', text: summary, status: answer.status, report: answer })
      }
    } catch (exc) {
      if (seq === askSeq.current) {
        push({ role: 'assistant', text: exc instanceof Error ? exc.message : String(exc), status: 'ERROR' })
      }
    } finally {
      if (seq === askSeq.current) {
        setBusy(false)
      }
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send()
    }
  }

  function renderReport(report: AiReportAnswer) {
    const columns = report.columns ?? []
    const rows = report.rows ?? []
    return (
      <div className="ai-report">
        {report.sql
          ? (
              <details>
                <summary>{t('ai.showSql')}</summary>
                <pre className="ai-sql">{report.sql}</pre>
              </details>
            )
          : null}
        {report.status === 'OK' && columns.length > 0
          ? (
              <>
                <div className="ai-table-wrapper">
                  <table className="ai-table">
                    <thead>
                      <tr>{columns.map(column => <th key={column}>{column}</th>)}</tr>
                    </thead>
                    <tbody>
                      {rows.slice(0, SHOWN_ROWS).map((row, index) => (
                        <tr key={index}>
                          {columns.map(column => <td key={column}>{cellText(row[column] ?? null)}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="ai-report-footer">
                  {rows.length > SHOWN_ROWS ? <span>{t('ai.shownRows', { shown: SHOWN_ROWS, count: rows.length })}</span> : null}
                  {report.truncated ? <span>{t('ai.truncated')}</span> : null}
                  <button type="button" onClick={() => downloadCsv(report)}>{t('ai.downloadCsv')}</button>
                </div>
              </>
            )
          : null}
      </div>
    )
  }

  const canReport = status?.can_report ?? false
  const screenCapture = typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getDisplayMedia)

  if (!open) {
    return (
      <button type="button" className="ai-launcher" onClick={() => setOpen(true)}>
        {t('ai.open')}
      </button>
    )
  }

  return (
    <aside className="ai-panel" aria-label={t('ai.title')}>
      <header className="ai-header">
        <strong>{t('ai.title')}</strong>
        <span className={status?.model_ready ? 'badge badge-success' : 'badge badge-warning'}>
          {status === null ? '…' : status.model_ready ? t('ai.modelReady') : t('ai.modelNotReady')}
        </span>
        <button type="button" className="ai-close" onClick={() => setOpen(false)} aria-label={t('ai.close')}>×</button>
      </header>

      <nav className="ai-modes">
        <button type="button" className={mode === 'help' ? 'active' : ''} onClick={() => setMode('help')}>
          {t('ai.modeHelp')}
        </button>
        {canReport
          ? (
              <button type="button" className={mode === 'report' ? 'active' : ''} onClick={() => setMode('report')}>
                {t('ai.modeReport')}
              </button>
            )
          : null}
      </nav>

      <div className="ai-messages" ref={listRef}>
        {messages.length === 0 ? <p className="ai-hint">{mode === 'help' ? t('ai.hintHelp') : t('ai.hintReport')}</p> : null}
        {messages.map(message => (
          <div key={message.id} className={`ai-message ai-${message.role} ai-status-${(message.status ?? '').toLowerCase()}`}>
            {message.image ? <img className="ai-thumb" src={message.image} alt={t('ai.screenshot')} /> : null}
            <div className="ai-text">{message.text}</div>
            {message.sections && message.sections.length > 0
              ? <div className="ai-sources">{t('ai.sources')}: {message.sections.join('; ')}</div>
              : null}
            {message.report ? renderReport(message.report) : null}
          </div>
        ))}
        {busy ? <div className="ai-message ai-assistant ai-busy">{t('ai.thinking')}</div> : null}
      </div>

      {error ? <div className="ai-error">{error}</div> : null}

      <footer className="ai-input">
        {image && mode === 'help'
          ? (
              <div className="ai-attachment">
                <img src={image} alt={t('ai.screenshot')} />
                <button type="button" onClick={() => setImage(null)}>{t('ai.removeImage')}</button>
              </div>
            )
          : null}
        <textarea
          value={question}
          maxLength={4000}
          rows={3}
          placeholder={mode === 'help' ? t('ai.placeholderHelp') : t('ai.placeholderReport')}
          onChange={event => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
        />
        <div className="ai-actions">
          {mode === 'help'
            ? (
                <>
                  {screenCapture
                    ? <button type="button" disabled={busy} onClick={() => void takeScreenshot()}>{t('ai.takeScreenshot')}</button>
                    : null}
                  <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}>{t('ai.attachImage')}</button>
                  <input
                    ref={fileRef}
                    type="file"
                    accept={IMAGE_TYPES.join(',')}
                    hidden
                    onChange={event => {
                      void attachFile(event.target.files?.[0])
                      event.target.value = ''
                    }}
                  />
                </>
              )
            : null}
          <button type="button" className="ai-send" disabled={busy || !question.trim()} onClick={() => void send()}>
            {t('ai.send')}
          </button>
        </div>
        <small className="ai-note">{t('ai.note')}</small>
      </footer>
    </aside>
  )
}


export default AiAssistantPanel
