// I17-10: screens ask the assistant to analyse a load error ("Analyse with the assistant" buttons). The chat
// listens to this window event; without the chat (no AI_ASSISTANT_USE) the screens do not show the buttons.
import type { AiDiagnoseKind } from './aiApi'

export interface AiDiagnoseRequest {
  kind: AiDiagnoseKind
  id: number
  label: string
}

export const AI_DIAGNOSE_EVENT = 'dwh-ai-diagnose'

export function requestDiagnosis(detail: AiDiagnoseRequest): void {
  window.dispatchEvent(new CustomEvent<AiDiagnoseRequest>(AI_DIAGNOSE_EVENT, { detail }))
}
