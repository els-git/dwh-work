import type { i18n as I18n } from 'i18next'

// I17-12: what is on the screen when a screenshot is taken - the selected menu item and tabs, then the headings,
// table columns and buttons of the screen - each with its names in Russian, English and Azerbaijani from the
// interface texts. The assistant names them in the language of its answer (only ru / en / az) and searches the
// guide (Russian) by the selected ones.
export interface AiScreenLabel {
  label: string
  ru: string | null
  en: string | null
  az: string | null
  selected: boolean
}

const SELECTED = [
  '.nav-item.active',
  '.admin-shell-tab.active',
  '.admin-integrations-tab.active',
  '.tab.active',
  '[role="tab"][aria-selected="true"]',
  '[aria-current="page"]',
].join(', ')
// Elements of the screen, most telling first.
const ELEMENTS = ['h1, h2, h3, h4, legend', 'th', 'button']
const LANGUAGES = ['ru', 'en', 'az'] as const
const MAX_LABELS = 30
const MAX_LENGTH = 80

function strings(node: unknown, prefix: string, out: Map<string, string>) {
  if (typeof node === 'string') {
    const value = node.trim()
    if (value && !out.has(value)) {
      out.set(value, prefix)
    }
  } else if (node && typeof node === 'object') {
    for (const [key, child] of Object.entries(node as Record<string, unknown>)) {
      strings(child, prefix ? `${prefix}.${key}` : key, out)
    }
  }
}

function textOf(element: Element): string {
  return (element.textContent || '').replace(/\s+/g, ' ').trim()
}

function visible(element: HTMLElement): boolean {
  return !element.closest('.ai-panel') && element.getClientRects().length > 0
}

export function screenLabels(i18n: I18n, root: ParentNode = document): AiScreenLabel[] {
  const language = (i18n.language || 'ru').slice(0, 2)
  const texts = new Map<string, string>()
  strings(i18n.getResourceBundle(language, 'translation'), '', texts)
  const fixed = Object.fromEntries(LANGUAGES.map(code => [code, i18n.getFixedT(code)]))
  const seen = new Set<string>()
  const labels: AiScreenLabel[] = []

  function add(element: HTMLElement, selected: boolean) {
    const label = textOf(element)
    if (labels.length >= MAX_LABELS || !label || label.length > MAX_LENGTH || !/\p{L}/u.test(label)
        || seen.has(label)) {
      return
    }
    const key = texts.get(label)
    if (!key && !selected) {
      return // an element without an interface text (data, user names) says nothing about the screen
    }
    seen.add(label)
    const name = (code: (typeof LANGUAGES)[number]): string | null => {
      if (!key) {
        return code === language ? label : null
      }
      const value = fixed[code](key)
      return typeof value === 'string' && value !== key ? value.trim().slice(0, MAX_LENGTH) : null
    }
    labels.push({ label, ru: name('ru'), en: name('en'), az: name('az'), selected })
  }

  for (const element of Array.from(root.querySelectorAll<HTMLElement>(SELECTED))) {
    if (visible(element)) {
      add(element, true)
    }
  }
  for (const selector of ELEMENTS) {
    for (const element of Array.from(root.querySelectorAll<HTMLElement>(selector))) {
      if (visible(element) && !element.matches(SELECTED)) {
        add(element, false)
      }
    }
  }
  return labels
}
