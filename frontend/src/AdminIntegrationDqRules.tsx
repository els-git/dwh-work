import { Trans } from 'react-i18next'
import { useSeverityLabel } from './severityLabels'
import {
  useEffect,
  useState,
} from 'react'

import type {
  FormEvent,
} from 'react'

import {
  createIntegrationDqRule,
  getIntegrationAdminDqRules,
  setIntegrationDqRuleActive,
  updateIntegrationDqRule,
} from './integrationAdminApi'

import type {
  IntegrationDqRuleAdmin,
} from './integrationAdminApi'

import {
  confirmAction,
} from './confirmAction'


function nullable(
  value: string,
): string | null {
  const normalized = value.trim()

  return normalized === ''
    ? null
    : normalized
}


function errorMessage(
  error: unknown,
): string {
  return error instanceof Error
    ? error.message
    : 'Unknown error'
}


export default function AdminIntegrationDqRules() {
  const getSeverityLabel = useSeverityLabel()

  const [
    rules,
    setRules,
  ] = useState<IntegrationDqRuleAdmin[]>([])

  const [
    loading,
    setLoading,
  ] = useState(true)

  const [
    error,
    setError,
  ] = useState<string | null>(null)

  const [
    creating,
    setCreating,
  ] = useState(false)

  const [
    editing,
    setEditing,
  ] = useState<IntegrationDqRuleAdmin | null>(null)

  const [
    saving,
    setSaving,
  ] = useState(false)

  const [
    changingId,
    setChangingId,
  ] = useState<number | null>(null)

  const [
    ruleCode,
    setRuleCode,
  ] = useState('')

  const [
    ruleName,
    setRuleName,
  ] = useState('')

  const [
    severity,
    setSeverity,
  ] = useState('ERROR')

  const [
    category,
    setCategory,
  ] = useState('')

  const [
    sourceObject,
    setSourceObject,
  ] = useState('')

  const [
    targetObject,
    setTargetObject,
  ] = useState('')

  const [
    targetColumn,
    setTargetColumn,
  ] = useState('')

  const [
    description,
    setDescription,
  ] = useState('')

  const [
    issueLimit,
    setIssueLimit,
  ] = useState('1000')

  const [
    isActive,
    setIsActive,
  ] = useState(true)

  const [
    changeReason,
    setChangeReason,
  ] = useState('')


  async function load() {
    try {
      setLoading(true)
      setError(null)

      setRules(
        await getIntegrationAdminDqRules(),
      )
    } catch (loadError) {
      setError(
        errorMessage(loadError),
      )
    } finally {
      setLoading(false)
    }
  }


  useEffect(() => {
    let cancelled = false

    void getIntegrationAdminDqRules()
      .then(items => {
        if (!cancelled) {
          setRules(items)
        }
      })
      .catch(loadError => {
        if (!cancelled) {
          setError(errorMessage(loadError))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])


  function closeEditor() {
    setCreating(false)
    setEditing(null)
  }


  function openCreate() {
    setCreating(true)
    setEditing(null)
    setRuleCode('')
    setRuleName('')
    setSeverity('ERROR')
    setCategory('')
    setSourceObject('')
    setTargetObject('')
    setTargetColumn('')
    setDescription('')
    setIssueLimit('1000')
    setIsActive(true)
    setChangeReason('')
    setError(null)
  }


  function openEdit(
    item: IntegrationDqRuleAdmin,
  ) {
    setCreating(false)
    setEditing(item)
    setRuleCode(item.rule_code)
    setRuleName(item.rule_name)
    setSeverity(item.severity)
    setCategory(item.category)
    setSourceObject(
      item.source_object ?? '',
    )
    setTargetObject(
      item.target_object ?? '',
    )
    setTargetColumn(
      item.target_column ?? '',
    )
    setDescription(
      item.description ?? '',
    )
    setIssueLimit(
      String(item.issue_limit),
    )
    setIsActive(item.is_active)
    setChangeReason('')
    setError(null)
  }


  async function submit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    const limit =
      Number.parseInt(
        issueLimit,
        10,
      )

    if (
      !Number.isInteger(limit)
      || limit < 1
    ) {
      setError(
        'Issue limit must be a positive integer.',
      )
      return
    }

    try {
      setSaving(true)
      setError(null)

      if (creating) {
        await createIntegrationDqRule({
          rule_code:
            ruleCode.trim().toUpperCase(),
          rule_name:
            ruleName.trim(),
          severity,
          category:
            category.trim(),
          source_object:
            nullable(sourceObject),
          target_object:
            nullable(targetObject),
          target_column:
            nullable(targetColumn),
          description:
            nullable(description),
          issue_limit:
            limit,
          is_active:
            isActive,
          change_reason:
            nullable(changeReason),
        })
      } else if (editing) {
        await updateIntegrationDqRule(
          editing.rule_id,
          {
            rule_name:
              ruleName.trim(),
            severity,
            category:
              category.trim(),
            source_object:
              nullable(sourceObject),
            target_object:
              nullable(targetObject),
            target_column:
              nullable(targetColumn),
            description:
              nullable(description),
            issue_limit:
              limit,
            is_active:
              isActive,
            change_reason:
              nullable(changeReason),
          },
        )
      }

      closeEditor()
      await load()
    } catch (saveError) {
      setError(
        errorMessage(saveError),
      )
    } finally {
      setSaving(false)
    }
  }


  async function changeState(
    item: IntegrationDqRuleAdmin,
  ) {
    const nextState =
      !item.is_active

    const confirmed =
      await confirmAction({
        title:
          nextState
            ? 'Enable DQ rule?'
            : 'Disable DQ rule?',
        message:
          `${item.rule_code}  ${item.rule_name}`,
        detail:
          'The change creates a new rule version. Previous versions remain in history.',
        confirmLabel:
          nextState
            ? 'Enable'
            : 'Disable',
      })

    if (!confirmed) {
      return
    }

    try {
      setChangingId(item.rule_id)
      setError(null)

      await setIntegrationDqRuleActive(
        item.rule_id,
        nextState,
      )

      await load()
    } catch (stateError) {
      setError(
        errorMessage(stateError),
      )
    } finally {
      setChangingId(null)
    }
  }


  return (
    <div className="integration-admin-panel">
      <div className="admin-integrations-toolbar">
        <div>
          <Trans i18nKey="batch.tr0358" />
        </div>

        <button
          type="button"
          className="admin-primary-button"
          onClick={openCreate}
        >
          <Trans i18nKey="batch.tr0347" />
        </button>
      </div>

      {
        error
          ? (
              <div className="error">
                <strong><Trans i18nKey="batch.tr0349" /></strong>
                <span>{error}</span>
              </div>
            )
          : null
      }

      {
        creating || editing
          ? (
              <section className="admin-integrations-editor">
                <h3>
                  {
                    creating
                      ?<Trans i18nKey="batch.tr0347" />
                      : (
                          `Edit ${editing?.rule_code}`
                        )
                  }
                </h3>

                <form
                  className="admin-integrations-form"
                  onSubmit={submit}
                >
                  <label>
                    <span><Trans i18nKey="batch.tr0355" /></span>
                    <input
                      required
                      readOnly={!creating}
                      value={ruleCode}
                      onChange={
                        event =>
                          setRuleCode(
                            event.target.value
                              .toUpperCase(),
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0356" /></span>
                    <input
                      required
                      value={ruleName}
                      onChange={
                        event =>
                          setRuleName(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0046" /></span>
                    <select
                      value={severity}
                      onChange={
                        event =>
                          setSeverity(
                            event.target.value,
                          )
                      }
                    >
                      <option value="ERROR">
                        {getSeverityLabel('ERROR')}
                      </option>
                      <option value="WARNING">
                        {getSeverityLabel('WARNING')}
                      </option>
                    </select>
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0055" /></span>
                    <input
                      required
                      value={category}
                      onChange={
                        event =>
                          setCategory(
                            event.target.value
                              .toUpperCase(),
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0224" /></span>
                    <input
                      value={sourceObject}
                      onChange={
                        event =>
                          setSourceObject(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0225" /></span>
                    <input
                      value={targetObject}
                      onChange={
                        event =>
                          setTargetObject(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0052" /></span>
                    <input
                      value={targetColumn}
                      onChange={
                        event =>
                          setTargetColumn(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label>
                    <span><Trans i18nKey="batch.tr0351" /></span>
                    <input
                      type="number"
                      min={1}
                      required
                      value={issueLimit}
                      onChange={
                        event =>
                          setIssueLimit(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label className="integration-admin-check">
                    <input
                      type="checkbox"
                      checked={isActive}
                      onChange={
                        event =>
                          setIsActive(
                            event.target.checked,
                          )
                      }
                    />
                    <span><Trans i18nKey="batch.tr0289" /></span>
                  </label>

                  <label className="wide">
                    <span><Trans i18nKey="batch.tr0223" /></span>
                    <textarea
                      rows={3}
                      value={description}
                      onChange={
                        event =>
                          setDescription(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <label className="wide">
                    <span><Trans i18nKey="batch.tr0290" /></span>
                    <input
                      value={changeReason}
                      onChange={
                        event =>
                          setChangeReason(
                            event.target.value,
                          )
                      }
                    />
                  </label>

                  <div className="admin-integrations-form-actions wide">
                    <button
                      type="button"
                      className="admin-secondary-button"
                      disabled={saving}
                      onClick={closeEditor}
                    >
                      <Trans i18nKey="batch.tr0222" />
                    </button>

                    <button
                      type="submit"
                      className="admin-primary-button"
                      disabled={saving}
                    >
                      {
                        saving
                          ?<Trans i18nKey="batch.tr0221" />
                          :<Trans i18nKey="batch.tr0220" />
                      }
                    </button>
                  </div>
                </form>
              </section>
            )
          : null
      }

      {
        loading && rules.length === 0
          ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0354" />
              </div>
            )
          : (
              <div className="data-table-wrapper">
                <table className="data-table integration-admin-wide-table">
                  <thead>
                    <tr>
                      <th><Trans i18nKey="batch.tr0045" /></th>
                      <th><Trans i18nKey="batch.tr0046" /></th>
                      <th><Trans i18nKey="batch.tr0055" /></th>
                      <th><Trans i18nKey="batch.tr0038" /></th>
                      <th><Trans i18nKey="batch.tr0353" /></th>
                      <th><Trans i18nKey="batch.tr0053" /></th>
                      <th><Trans i18nKey="batch.tr0036" /></th>
                      <th><Trans i18nKey="batch.tr0215" /></th>
                    </tr>
                  </thead>

                  <tbody>
                    {
                      rules.map(
                        item => (
                          <tr key={item.rule_id}>
                            <td>
                              <strong>
                                {item.rule_code}
                              </strong>
                              <br />
                              {item.rule_name}
                            </td>

                            <td>
                              <span
                                className={
                                  item.severity === 'ERROR'
                                    ? 'badge integration-dq-error'
                                    : 'badge integration-dq-warning'
                                }
                              >
                                {getSeverityLabel(item.severity)}
                              </span>
                            </td>

                            <td>
                              {item.category}
                            </td>

                            <td>
                              {
                                item.target_object
                                ?? item.source_object
                                ?? ''
                              }
                              {
                                item.target_column
                                  ? (
                                      '.'
                                      + item.target_column
                                    )
                                  : ''
                              }
                            </td>

                            <td>
                              {item.issue_limit}
                            </td>

                            <td>
                              v{item.version_no}
                            </td>

                            <td>
                              <span
                                className={
                                  item.is_active
                                    ? 'badge badge-success'
                                    : 'badge'
                                }
                              >
                                {
                                  item.is_active
                                    ? 'ACTIVE'
                                    : 'DISABLED'
                                }
                              </span>
                            </td>

                            <td>
                              <div className="admin-integration-row-actions">
                                <button
                                  type="button"
                                  className="admin-link-button"
                                  onClick={
                                    () =>
                                      openEdit(item)
                                  }
                                >
                                  <Trans i18nKey="batch.tr0218" />
                                </button>

                                <button
                                  type="button"
                                  className="admin-link-button"
                                  disabled={
                                    changingId !== null
                                  }
                                  onClick={
                                    () => {
                                      void changeState(
                                        item,
                                      )
                                    }
                                  }
                                >
                                  {
                                    changingId === item.rule_id
                                      ?<Trans i18nKey="batch.tr0115" />
                                      : item.is_active
                                        ?<Trans i18nKey="batch.tr0217" />
                                        :<Trans i18nKey="batch.tr0219" />
                                  }
                                </button>
                              </div>
                            </td>
                          </tr>
                        ),
                      )
                    }
                  </tbody>
                </table>
              </div>
            )
      }
    </div>
  )
}
