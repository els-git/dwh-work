import type { TFunction } from 'i18next'

import { confirmAction } from './confirmAction'
import type { ObjectPublishResult, ObjectTablePlan } from './integrationAdminApi'

// I18 (package 021): the STG / ODS table of a draft is created or changed on publication. Before publishing,
// the confirmation dialog shows what happens to the table, what uses the changed columns and the SQL text.

export function tablePlanText(plan: ObjectTablePlan, t: TFunction): string {
  const table = `${plan.target_schema ?? '?'}.${plan.target_table ?? '?'}`
  const lines: string[] = []
  for (const change of plan.changes) {
    switch (change.change) {
      case 'CREATE_TABLE':
        lines.push(t('tablePlan.create', { table }))
        break
      case 'ADDED':
        lines.push(t('tablePlan.added', { column: change.column, type: change.new_type }))
        break
      case 'REMOVED':
        lines.push(t('tablePlan.removed', { column: change.column }))
        break
      case 'RETYPED':
        lines.push(t('tablePlan.retyped', { column: change.column, from: change.old_type, to: change.new_type }))
        break
      case 'KEY_CHANGED':
        lines.push(t('tablePlan.keyChanged', {
          from: (change.old_key ?? []).join(', ') || '—',
          to: (change.new_key ?? []).join(', '),
        }))
        break
      case 'KEPT':
        lines.push(t('tablePlan.kept', { column: change.column }))
        break
    }
  }
  if (lines.length === 0) {
    lines.push(t('tablePlan.same', { table }))
  }
  for (const item of plan.impact.ods_configs) {
    lines.push(t('tablePlan.usedByOds', { id: item.source_object_id, no: item.version_no, columns: item.columns.join(', ') }))
  }
  for (const item of plan.impact.marts) {
    lines.push(t('tablePlan.usedByMart', { mart: item.mart_code, no: item.version_no, columns: item.columns.join(', ') }))
  }
  if (plan.ddl_text !== null) {
    lines.push('', t('tablePlan.sql'), plan.ddl_text)
  }
  return lines.join('\n')
}

// Asks for the confirmation of a publication with the plan of its table, then publishes.
// Returns the notice to show; problems of the plan are thrown as an error.
export async function publishWithTablePlan(options: {
  plan: ObjectTablePlan
  title: string
  message: string
  t: TFunction
  publish: (confirm: boolean) => Promise<ObjectPublishResult>
}): Promise<string> {
  const { plan, t } = options
  if (plan.problems.length > 0) {
    throw new Error(t('tablePlan.problems', { problems: plan.problems.join('; ') }))
  }
  const confirmed = await confirmAction({
    title: options.title,
    message: plan.needs_confirm ? `${options.message} ${t('tablePlan.needsConfirm')}` : options.message,
    detail: tablePlanText(plan, t),
    confirmLabel: plan.needs_confirm ? t('tablePlan.confirm') : t('objectConfig.publish'),
    cancelLabel: t('objectConfig.cancel'),
  })
  if (!confirmed) {
    return t('tablePlan.cancelled')
  }
  const result = await options.publish(plan.needs_confirm)
  if (result.status === 'NEEDS_CONFIRM') {
    // The table changed after the plan was read: the new plan needs its own confirmation.
    return t('tablePlan.changedSinceRead')
  }
  return t('objectConfig.published', { version: result.version_no, status: result.status })
}
