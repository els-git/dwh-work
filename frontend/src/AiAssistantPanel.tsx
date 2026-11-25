import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'

import {
  AiHttpError,
  askAiHelp,
  askAiReport,
  cancelAiQuestion,
  diagnoseAi,
  getAiStatus,
  type AiDiagnosisCard,
  type AiHelpAnswer,
  type AiHistoryItem,
  type AiReportAnswer,
  type AiStatus,
} from './aiApi'
import { AI_DIAGNOSE_EVENT, type AiDiagnoseRequest } from './aiBus'
import './AiAssistantPanel.css'
import { cellText, downloadReportCsv } from './reportCsv'
import SaveReportForm from './SaveReportForm'


// I17: chat of the AI assistant, available on every screen. Help answers come from the user guide
// sections the user's role may read; a screenshot (I17-07) is sent once with the question and kept
// only in this page. Reports from a description show the SQL, the rows and a CSV download.
// I17-10: the last 10 messages go with every question (memory of the dialogue, kept only in this page; a
// screenshot is not sent again, the answer about it stays in the memory); "New dialogue" clears them. The window
// is minimized, not closed: an answer prepared meanwhile is announced by a notification and a dot on the button.
// Screens ask for the diagnosis of a load error by a window event (aiBus): first the card without the model,
// then the diagnosis of the model from the facts collected by the server.

type Mode = 'help' | 'report'

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  question?: string
  image?: string
  status?: string
  sections?: string[]
  closedNearby?: boolean
  report?: AiReportAnswer
  seconds?: number
  unavailable?: string[]
  // I17-12: a script answer offers the model; foreign - the question is not in Russian (the guide is).
  followUp?: { kind: 'help', question: string } | { kind: 'diagnose', request: AiDiagnoseRequest }
  foreign?: boolean
}

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const MAX_SHOT_WIDTH = 1600
const SHOWN_ROWS = 100
const HISTORY_MESSAGES = 10


// Memory of the dialogue sent with a question: the last messages, the SQL of a report with its answer, a mark
// instead of a screenshot.
function historyOf(list: ChatMessage[], shotMark: string): AiHistoryItem[] {
  return list.slice(-HISTORY_MESSAGES).map(message => ({
    role: message.role,
    text: message.role === 'assistant' && message.report?.sql
      ? `${message.text}\nSQL: ${message.report.sql}`
      : message.image ? `${message.text}\n${shotMark}` : message.text,
  })).filter(item => item.text.trim() !== '')
}


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


// The model writes simple Markdown; it is shown as text with bold, code and list bullets (no HTML from the model).
function cleanLatex(text: string): string {
  return text
    .replace(/\$\s*\\(?:rightarrow|to|longrightarrow)\s*\$/g, '→')
    .replace(/\$\s*\\leftarrow\s*\$/g, '←')
    .replace(/\$\s*\\Rightarrow\s*\$/g, '⇒')
    .replace(/\\(?:rightarrow|to)\b/g, '→')
}


function renderInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(part => part !== '').map((part, index) => {
    if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>
    }
    if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) {
      return <code key={index}>{part.slice(1, -1)}</code>
    }
    return <Fragment key={index}>{part}</Fragment>
  })
}


function renderMarkdown(text: string): ReactNode {
  return cleanLatex(text).split('\n').map((line, index) => {
    const heading = /^#{1,4}\s+(.*)$/.exec(line)
    if (heading) {
      return <div key={index} className="ai-line"><strong>{renderInline(heading[1])}</strong></div>
    }
    const bullet = /^(\s*)[*-]\s+(.*)$/.exec(line)
    if (bullet) {
      return (
        <div key={index} className="ai-line" style={{ paddingLeft: `${Math.min(bullet[1].length, 8) * 4 + 4}px` }}>
          • {renderInline(bullet[2])}
        </div>
      )
    }
    return line.trim() === ''
      ? <div key={index} className="ai-gap" />
      : <div key={index} className="ai-line">{renderInline(line)}</div>
  })
}


// Wall clock for the answer timer (outside the component: only event handlers and timers read it).
function clockMs(): number {
  return Date.now()
}


function newClientId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
  }
}


function downloadCsv(answer: AiReportAnswer) {
  downloadReportCsv(answer.columns ?? [], answer.rows ?? [], `dwh_report_${answer.request_id}.csv`)
}


function AiAssistantPanel() {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  // I17-10: an answer arrived while the window was minimized.
  const [unread, setUnread] = useState(false)
  const [toast, setToast] = useState(false)
  const openRef = useRef(false)
  const diagnoseRef = useRef<(request: AiDiagnoseRequest) => void>(() => undefined)
  const [wide, setWide] = useState(() => {
    try {
      return window.localStorage.getItem('dwh.ai.wide') === '1'
    } catch {
      return false
    }
  })
  const toggleWide = () => {
    const next = !wide
    setWide(next)
    try {
      window.localStorage.setItem('dwh.ai.wide', next ? '1' : '0')
    } catch {
      // The choice is only a convenience: without storage it lasts until the page is reloaded.
    }
  }
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [mode, setMode] = useState<Mode>('help')
  const [question, setQuestion] = useState('')
  const [image, setImage] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [busySince, setBusySince] = useState(0)
  const [now, setNow] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const askSeq = useRef(0)
  const running = useRef<{ clientId: string, abort: AbortController } | null>(null)
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
  }, [messages, busy, open])

  useEffect(() => {
    openRef.current = open
  }, [open])

  useEffect(() => {
    const handler = (event: Event) => diagnoseRef.current((event as CustomEvent<AiDiagnoseRequest>).detail)
    window.addEventListener(AI_DIAGNOSE_EVENT, handler)
    return () => window.removeEventListener(AI_DIAGNOSE_EVENT, handler)
  }, [])

  function openPanel() {
    setOpen(true)
    setUnread(false)
    setToast(false)
  }

  // An answer that arrives while the window is minimized is announced.
  function announce() {
    if (!openRef.current) {
      setUnread(true)
      setToast(true)
    }
  }

  // Seconds counter while the model is answering.
  useEffect(() => {
    if (!busy) {
      return
    }
    const timer = window.setInterval(() => setNow(clockMs()), 1000)
    return () => window.clearInterval(timer)
  }, [busy])

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

  // Runs one question: busy state and timer, Stop, protection against answers of an older question.
  async function ask(work: (seq: number, clientId: string, signal: AbortSignal, spent: () => number) => Promise<void>) {
    const seq = ++askSeq.current
    setError(null)
    const startedAt = clockMs()
    setBusySince(startedAt)
    setNow(startedAt)
    setBusy(true)
    const spent = () => Math.max(1, Math.round((clockMs() - startedAt) / 1000))
    const clientId = newClientId()
    const abort = new AbortController()
    running.current = { clientId, abort }
    try {
      await work(seq, clientId, abort.signal, spent)
    } catch (exc) {
      if (seq === askSeq.current) {
        push({ role: 'assistant', text: errorText(exc), status: 'ERROR', seconds: spent() })
        announce()
      }
    } finally {
      if (running.current?.clientId === clientId) {
        running.current = null
      }
      if (seq === askSeq.current) {
        setBusy(false)
        // The average answer time changes with every answer: read it again.
        getAiStatus()
          .then(value => {
            if (seq === askSeq.current) {
              setStatus(value)
            }
          })
          .catch(() => undefined)
      }
    }
  }

  async function send() {
    const text = question.trim()
    if (!text || busy) {
      return
    }
    const sentImage = mode === 'help' ? image : null
    const earlier = historyOf(messages, t('ai.historyShot'))
    push({ role: 'user', text, image: sentImage ?? undefined })
    setQuestion('')
    setImage(null)
    await ask(async (seq, clientId, signal, spent) => {
      if (mode === 'help') {
        const answer = await askAiHelp(text, sentImage, clientId, signal, earlier)
        if (seq !== askSeq.current) {
          return
        }
        const script = answer.mode === 'SCRIPT' && answer.status !== 'DENIED'
        push({ role: 'assistant', text: helpText(answer), status: answer.status,
               sections: answer.sections.map(s => s.title), closedNearby: answer.closed_nearby === true,
               seconds: script ? undefined : spent(),
               followUp: script ? { kind: 'help', question: text } : undefined, foreign: answer.foreign === true })
      } else {
        const answer = await askAiReport(text, clientId, signal, earlier)
        if (seq !== askSeq.current) {
          return
        }
        push({ role: 'assistant', text: reportText(answer), status: answer.status, report: answer, seconds: spent(),
               question: text })
      }
      announce()
    })
  }

  function cardText(card: AiDiagnosisCard, waiting: boolean): string {
    const lines = [`**${card.title}**${card.code ? ` (\`${card.code}\`)` : ''}`]
    if (card.where.length > 0) {
      lines.push(`${t('ai.diagWhere')}: ${card.where.join(' → ')}`)
    }
    lines.push(`${t('ai.diagAction')}: ${card.action}`, t(`ai.diagWho.${card.who}`))
    if (waiting) {
      lines.push('', t('ai.diagWait'))
    }
    return lines.join('\n')
  }

  // I17-10: card at once (no model), then the diagnosis written by the model from the facts of the server.
  async function runDiagnosis(request: AiDiagnoseRequest) {
    openPanel()
    if (busy) {
      setError(t('ai.diagBusy'))
      return
    }
    const language = ['ru', 'en', 'az'].includes(i18n.language?.slice(0, 2)) ? i18n.language.slice(0, 2) : 'ru'
    push({ role: 'user', text: t('ai.diagRequest', { subject: request.label }) })
    await ask(async (seq, clientId, signal, spent) => {
      const first = await diagnoseAi(request.kind, request.id, language, clientId, signal, true)
      if (seq !== askSeq.current) {
        return
      }
      if (first.card.known) {
        // I17-12 (R4): a known code is answered by the card; the model only by the button.
        push({ role: 'assistant', text: cardText(first.card, false), status: 'CARD',
               followUp: { kind: 'diagnose', request } })
        announce()
        return
      }
      push({ role: 'assistant', text: cardText(first.card, true), status: 'CARD' })
      await diagnoseByModel(request, language, seq, clientId, signal, spent)
    })
  }

  async function diagnoseByModel(request: AiDiagnoseRequest, language: string, seq: number, clientId: string,
    signal: AbortSignal, spent: () => number) {
    const full = await diagnoseAi(request.kind, request.id, language, clientId, signal, false)
    if (seq !== askSeq.current) {
      return
    }
    push({ role: 'assistant', text: full.answer ?? '', status: 'OK', seconds: spent(),
           unavailable: full.unavailable ?? [] })
    announce()
  }

  // I17-12: the model explains a script answer or a known error on request (the same question).
  async function askModel(followUp: NonNullable<ChatMessage['followUp']>) {
    if (busy) {
      return
    }
    if (followUp.kind === 'diagnose') {
      const language = ['ru', 'en', 'az'].includes(i18n.language?.slice(0, 2)) ? i18n.language.slice(0, 2) : 'ru'
      await ask((seq, clientId, signal, spent) => diagnoseByModel(followUp.request, language, seq, clientId, signal, spent))
      return
    }
    const earlier = historyOf(messages, t('ai.historyShot'))
    await ask(async (seq, clientId, signal, spent) => {
      const answer = await askAiHelp(followUp.question, null, clientId, signal, earlier, 'ai')
      if (seq !== askSeq.current) {
        return
      }
      push({ role: 'assistant', text: helpText(answer), status: answer.status,
             sections: answer.sections.map(s => s.title), closedNearby: answer.closed_nearby === true,
             seconds: spent() })
      announce()
    })
  }

  function newDialogue() {
    if (!busy) {
      askSeq.current += 1
      setMessages([])
      setError(null)
    }
  }

  // Fixed answers are shown in the language of the interface by their code; the model's own text as it is.
  function helpText(answer: AiHelpAnswer): string {
    if (answer.status === 'DENIED') {
      return t('ai.denied')
    }
    return answer.status === 'NO_DATA' ? t('ai.noHelp') : answer.answer
  }

  function reportText(answer: AiReportAnswer): string {
    switch (answer.status) {
      case 'OK':
        return t('ai.reportRows', { count: answer.rows?.length ?? 0 })
      case 'DENIED':
        return t('ai.denied')
      case 'NO_DATA':
        return t('ai.noData')
      case 'REFUSED':
        return t('ai.refused', { reason: answer.message ?? answer.code ?? '' })
      default:
        return t('ai.sqlError', { reason: answer.message ?? answer.code ?? '' })
    }
  }

  function errorText(exc: unknown): string {
    if (exc instanceof DOMException && exc.name === 'AbortError') {
      return t('ai.stopped')
    }
    if (exc instanceof AiHttpError) {
      if (exc.status === 409) {
        return t('ai.stopped')
      }
      if (exc.status === 429) {
        return t('ai.busy')
      }
      if (exc.status === 503) {
        return t('ai.unavailable')
      }
    }
    return exc instanceof Error ? exc.message : String(exc)
  }

  function stop() {
    const current = running.current
    if (!current) {
      return
    }
    // First the server stops the model, then the page stops waiting: the next question is not refused as busy.
    void cancelAiQuestion(current.clientId)
      .catch(() => undefined)
      .finally(() => current.abort.abort())
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send()
    }
  }

  // Screens ask for a diagnosis by a window event; the handler always uses the current state of the chat.
  useEffect(() => {
    diagnoseRef.current = request => {
      void runDiagnosis(request)
    }
  })

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
      <>
        <div className="ai-launcher-row">
          <button type="button" className="ai-launcher" onClick={openPanel}>
            {t('ai.open')}{busy ? ` · ${t('ai.answering')}` : ''}
            {unread ? <span className="ai-dot" aria-label={t('ai.answerReady')} /> : null}
          </button>
        </div>
        {toast
          ? (
              <div className="ai-toast" role="status">
                <span>{t('ai.answerReady')}</span>
                <button type="button" onClick={openPanel}>{t('ai.openAnswer')}</button>
                <button type="button" className="ai-toast-close" aria-label={t('ai.close')} onClick={() => setToast(false)}>×</button>
              </div>
            )
          : null}
      </>
    )
  }

  return (
    <aside className={wide ? 'ai-panel ai-panel-wide' : 'ai-panel'} aria-label={t('ai.title')}>
      <header className="ai-header">
        <strong>{t('ai.title')}</strong>
        <span className={status?.model_ready ? 'badge badge-success' : 'badge badge-warning'}>
          {status === null ? '…' : status.model_ready ? t('ai.modelReady') : t('ai.modelNotReady')}
        </span>
        <button type="button" className="ai-expand" onClick={toggleWide}
                title={wide ? t('ai.collapse') : t('ai.expand')} aria-label={wide ? t('ai.collapse') : t('ai.expand')}>
          {wide ? '⤡' : '⤢'}
        </button>
        <button type="button" className="ai-new" disabled={busy || messages.length === 0} onClick={newDialogue}
                title={t('ai.newDialogueHint')}>
          {t('ai.newDialogue')}
        </button>
        <button type="button" className="ai-close" onClick={() => setOpen(false)}
                title={t('ai.minimize')} aria-label={t('ai.minimize')}>—</button>
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
            <div className="ai-text">{message.role === 'assistant' ? renderMarkdown(message.text) : message.text}</div>
            {message.sections && message.sections.length > 0
              ? <div className="ai-sources">{t('ai.sources')}: {message.sections.join('; ')}</div>
              : null}
            {message.closedNearby ? <div className="ai-closed-note">{t('ai.closedNearby')}</div> : null}
            {message.unavailable && message.unavailable.length > 0
              ? <div className="ai-closed-note">{t('ai.diagUnavailable', { list: message.unavailable.join(', ') })}</div>
              : null}
            {message.report ? renderReport(message.report) : null}
            {message.report?.status === 'OK' && status?.can_save_reports
              ? <SaveReportForm requestId={message.report.request_id} defaultTitle={message.question ?? ''} />
              : null}
            {message.report?.repaired ? <div className="ai-sources">{t('ai.repaired')}</div> : null}
            {message.foreign ? <div className="ai-closed-note">{t('ai.scriptRussianOnly')}</div> : null}
            {message.followUp
              ? (
                  <button type="button" className="ai-follow-up" disabled={busy}
                    onClick={() => { void askModel(message.followUp!) }}>
                    {message.followUp.kind === 'diagnose' ? t('ai.diagnoseByModel')
                      : message.status === 'NO_DATA' ? t('ai.askModel') : t('ai.explainByModel')}
                  </button>
                )
              : null}
            {message.seconds ? <div className="ai-time">{t('ai.answerTime', { seconds: message.seconds })}</div> : null}
          </div>
        ))}
        {busy
          ? (
              <div className="ai-message ai-assistant ai-busy">
                {status?.average_answer_seconds
                  ? t('ai.thinkingAverage', { minutes: Math.max(1, Math.round(status.average_answer_seconds / 60)) })
                  : t('ai.thinking')}
                {' · '}{t('ai.elapsed', { seconds: Math.max(0, Math.round((now - busySince) / 1000)) })}
              </div>
            )
          : null}
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
          {busy
            ? (
                <button type="button" className="ai-stop" onClick={stop}>
                  {t('ai.stop')}
                </button>
              )
            : (
                <button type="button" className="ai-send" disabled={!question.trim()} onClick={() => void send()}>
                  {t('ai.send')}
                </button>
              )}
        </div>
        <small className="ai-note">{t('ai.note')}</small>
      </footer>
    </aside>
  )
}


export default AiAssistantPanel
