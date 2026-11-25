import type { i18n as I18n } from 'i18next'

// I17-12: the selected menu item and tabs of the current screen, sent with a screenshot so that the assistant
// names them as the interface does (in the user's language) and searches the guide by their Russian names.
export interface AiScreenLabel {
  label: string
  ru: string | null
}

const SELECTED = [
  '.nav-item.active',
  '.admin-shell-tab.active',
  '.admin-integrations-tab.active',
  '.tab.active',
  '[role="tab"][aria-selected="true"]',
  '[aria-current="page"]',
].join(', ')
const MAX_LABELS = 12
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

// Russian text of an interface text: the key with the same text in the current language, read in Russian.
function russianOf(i18n: I18n, language: string, texts: Map<string, string> | null, label: string): string | null {
  if (language === 'ru') {
    return label
  }
  const key = texts?.get(label)
  if (!key) {
    return null
  }
  const ru = i18n.getFixedT('ru')(key)
  return typeof ru === 'string' && ru !== key ? ru.trim().slice(0, MAX_LENGTH) : null
}

export function screenLabels(i18n: I18n, root: ParentNode = document): AiScreenLabel[] {
  const language = (i18n.language || 'ru').slice(0, 2)
  let texts: Map<string, string> | null = null
  if (language !== 'ru') {
    texts = new Map()
    strings(i18n.getResourceBundle(language, 'translation'), '', texts)
  }
  const seen = new Set<string>()
  const labels: AiScreenLabel[] = []
  for (const element of Array.from(root.querySelectorAll<HTMLElement>(SELECTED))) {
    if (element.closest('.ai-panel') || element.getClientRects().length === 0) {
      continue
    }
    const label = (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, MAX_LENGTH)
    if (!/\p{L}/u.test(label) || seen.has(label)) {
      continue
    }
    seen.add(label)
    labels.push({ label, ru: russianOf(i18n, language, texts, label) })
    if (labels.length >= MAX_LABELS) {
      break
    }
  }
  return labels
}
