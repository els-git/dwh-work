import { Trans } from 'react-i18next'
import { useSeverityLabel } from './severityLabels'
import {
  useEffect,
  useState,
} from 'react'

import {
  getIntegrationHistoryDqIssues,
  getIntegrationHistoryDqRuns,
  getIntegrationHistoryEtlErrors,
  getIntegrationHistoryEtlRuns,
} from './integrationAdminApi'

import type {
  IntegrationHistoryDqIssue,
  IntegrationHistoryDqRun,
  IntegrationHistoryEtlError,
  IntegrationHistoryEtlRun,
} from './integrationAdminApi'

import {
  formatDateTime,
} from './dateTime'


type HistoryMode =
  | 'etl-runs'
  | 'etl-errors'
  | 'dq-runs'
  | 'dq-issues'


function errorMessage(
  error: unknown,
): string {
  if (error instanceof Error) {
    return error.message
  }

  return 'Unknown error'
}


function statusBadgeClass(
  status: string | null | undefined,
): string {
  const normalized =
    status?.toUpperCase() ?? ''

  if (
    normalized === 'SUCCESS'
    || normalized === 'PASS'
    || normalized === 'RESOLVED'
  ) {
    return 'badge badge-success'
  }

  if (
    normalized === 'FAILED'
    || normalized === 'FAIL'
    || normalized === 'ERROR'
  ) {
    return 'badge badge-danger'
  }

  if (
    normalized === 'WARNING'
    || normalized === 'WARN'
    || normalized === 'OPEN'
  ) {
    return 'badge badge-warning'
  }

  return 'badge'
}


function dwhLandingId(
  item: IntegrationHistoryEtlError,
): string | null {
  const key = item.record_key

  if (
    key === null
    || typeof key !== 'object'
    || Array.isArray(key)
  ) {
    return null
  }

  const value = (
    key as Record<string, unknown>
  ).dwh_landing_id

  if (
    typeof value === 'number'
    && Number.isFinite(value)
  ) {
    return String(value)
  }

  if (
    typeof value === 'string'
    && value.trim() !== ''
  ) {
    return value
  }

  return null
}


function hasRowIdentity(
  item: IntegrationHistoryEtlError,
): boolean {
  return (
    item.source_pk !== null
    || item.application_id !== null
    || item.applicant_id !== null
    || dwhLandingId(item) !== null
    || item.column_name !== null
    || item.bad_value !== null
  )
}


function diagnosticJson(
  value: unknown,
): string {
  if (value === null || value === undefined) {
    return ''
  }

  try {
    return JSON.stringify(
      value,
      null,
      2,
    )
  } catch {
    return String(value)
  }
}



export default function AdminIntegrationHistory() {
  const getSeverityLabel = useSeverityLabel()

  const [
    mode,
    setMode,
  ] = useState<HistoryMode>('etl-runs')

  const [
    etlRuns,
    setEtlRuns,
  ] = useState<IntegrationHistoryEtlRun[]>([])

  const [
    etlErrors,
    setEtlErrors,
  ] = useState<IntegrationHistoryEtlError[]>([])

  const [
    dqRuns,
    setDqRuns,
  ] = useState<IntegrationHistoryDqRun[]>([])

  const [
    dqIssues,
    setDqIssues,
  ] = useState<IntegrationHistoryDqIssue[]>([])

  const [
    loading,
    setLoading,
  ] = useState(true)

  const [
    error,
    setError,
  ] = useState<string | null>(null)

  const [
    errorBatchRunId,
    setErrorBatchRunId,
  ] = useState<number | null>(null)


  async function loadAll(
    batchRunId: number | null = errorBatchRunId,
  ) {
    try {
      setLoading(true)
      setError(null)

      const [
        etlRunResult,
        etlErrorResult,
        dqRunResult,
        dqIssueResult,
      ] = await Promise.all([
        getIntegrationHistoryEtlRuns(100),
        getIntegrationHistoryEtlErrors(
          100,
          batchRunId ?? undefined,
        ),
        getIntegrationHistoryDqRuns(100),
        getIntegrationHistoryDqIssues(100),
      ])

      setEtlRuns(etlRunResult)
      setEtlErrors(etlErrorResult)
      setDqRuns(dqRunResult)
      setDqIssues(dqIssueResult)
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

    void Promise.all([
      getIntegrationHistoryEtlRuns(100),
      getIntegrationHistoryEtlErrors(
        100,
        undefined,
      ),
      getIntegrationHistoryDqRuns(100),
      getIntegrationHistoryDqIssues(100),
    ])
      .then(([
        etlRunResult,
        etlErrorResult,
        dqRunResult,
        dqIssueResult,
      ]) => {
        if (cancelled) {
          return
        }

        setEtlRuns(etlRunResult)
        setEtlErrors(etlErrorResult)
        setDqRuns(dqRunResult)
        setDqIssues(dqIssueResult)
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


  async function viewErrors(
    batchRunId: number,
  ) {
    setErrorBatchRunId(
      batchRunId,
    )

    setMode(
      'etl-errors',
    )

    await loadAll(
      batchRunId,
    )
  }


  async function clearErrorFilter() {
    setErrorBatchRunId(
      null,
    )

    await loadAll(
      null,
    )
  }


  return (
    <section className="integration-admin-panel">
      <div className="admin-integrations-toolbar">
        <div>
          <Trans i18nKey="batch.tr0265" />
        </div>

        <button
          type="button"
          className="admin-secondary-button"
          disabled={loading}
          onClick={
            () => {
              void loadAll(
                errorBatchRunId,
              )
            }
          }
        >
          {
            loading
              ?<Trans i18nKey="batch.tr0271" />
              :<Trans i18nKey="batch.tr0280" />
          }
        </button>
      </div>

      <nav className="integration-history-tabs">
        <button
          type="button"
          className={
            mode === 'etl-runs'
              ? 'integration-history-tab active'
              : 'integration-history-tab'
          }
          onClick={
            () => setMode('etl-runs')
          }
        >
          <Trans i18nKey="batch.tr0247" />{etlRuns.length})
        </button>

        <button
          type="button"
          className={
            mode === 'etl-errors'
              ? 'integration-history-tab active'
              : 'integration-history-tab'
          }
          onClick={
            () => setMode('etl-errors')
          }
        >
          <Trans i18nKey="batch.tr0246" />{etlErrors.length})
        </button>

        <button
          type="button"
          className={
            mode === 'dq-runs'
              ? 'integration-history-tab active'
              : 'integration-history-tab'
          }
          onClick={
            () => setMode('dq-runs')
          }
        >
          <Trans i18nKey="batch.tr0243" />{dqRuns.length})
        </button>

        <button
          type="button"
          className={
            mode === 'dq-issues'
              ? 'integration-history-tab active'
              : 'integration-history-tab'
          }
          onClick={
            () => setMode('dq-issues')
          }
        >
          <Trans i18nKey="batch.tr0242" />{dqIssues.length})
        </button>
      </nav>

      {
        error
          ? (
              <div className="error">
                <strong>
                  <Trans i18nKey="batch.tr0252" />
                </strong>

                <span>
                  {error}
                </span>
              </div>
            )
          : null
      }

      {
        loading
          ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0257" />
              </div>
            )
          : null
      }

      {
        !loading && mode === 'etl-runs'
          ? (
              <div className="data-table-wrapper">
                <table className="data-table integration-admin-wide-table">
                  <thead>
                    <tr>
                      <th><Trans i18nKey="batch.tr0076" /></th>
                      <th><Trans i18nKey="batch.tr0047" /></th>
                      <th><Trans i18nKey="batch.tr0266" /></th>
                      <th><Trans i18nKey="batch.tr0075" /></th>
                      <th><Trans i18nKey="batch.tr0036" /></th>
                      <th><Trans i18nKey="batch.tr0043" /></th>
                      <th><Trans i18nKey="batch.tr0274" /></th>
                      <th><Trans i18nKey="batch.tr0039" /></th>
                      <th><Trans i18nKey="batch.tr0256" /></th>
                      <th><Trans i18nKey="batch.tr0048" /></th>
                      <th><Trans i18nKey="batch.tr0215" /></th>
                    </tr>
                  </thead>

                  <tbody>
                    {
                      etlRuns.map(
                        item => (
                          <tr key={item.batch_run_id}>
                            <td>
                              #{item.batch_run_id}
                            </td>

                            <td>
                              #{item.source_batch_run_id}
                            </td>

                            <td>
                              <code>
                                {item.pipeline_code}
                              </code>
                            </td>

                            <td>
                              {item.run_mode ?? ''}
                            </td>

                            <td>
                              <span
                                className={
                                  statusBadgeClass(
                                    item.status,
                                  )
                                }
                              >
                                {item.status}
                              </span>
                            </td>

                            <td>
                              {item.business_date ?? ''}
                            </td>

                            <td>
                              {
                                formatDateTime(
                                  item.started_at,
                                )
                              }
                            </td>

                            <td>
                              {
                                formatDateTime(
                                  item.finished_at,
                                )
                              }
                            </td>

                            <td>
                              {
                                `${item.jobs_success} OK / `
                                + `${item.jobs_failed} FAIL / `
                                + `${item.jobs_skipped} SKIP`
                              }
                            </td>

                            <td className="integration-history-message">
                              {item.message ?? ''}
                            </td>

                            <td>
                              {
                                item.status.toUpperCase()
                                !== 'SUCCESS'
                                  ? (
                                      <button
                                        type="button"
                                        className="admin-link-button"
                                        onClick={
                                          () => {
                                            void viewErrors(
                                              item.batch_run_id,
                                            )
                                          }
                                        }
                                      >
                                        <Trans i18nKey="batch.tr0279" />
                                      </button>
                                    )
                                  : null
                              }
                            </td>
                          </tr>
                        ),
                      )
                    }
                  </tbody>
                </table>
              </div>
            )
          : null
      }

      {
        !loading && mode === 'etl-errors'
          ? (
              <>
                {
                  errorBatchRunId !== null
                    ? (
                        <div className="integration-history-filter">
                          <span>
                            <Trans i18nKey="batch.tr0251" />{errorBatchRunId}
                          </span>

                          <button
                            type="button"
                            className="admin-link-button"
                            onClick={
                              () => {
                                void clearErrorFilter()
                              }
                            }
                          >
                            <Trans i18nKey="batch.tr0237" />
                          </button>
                        </div>
                      )
                    : null
                }

                {
                  etlErrors.length === 0
                    ? (
                        <div className="integration-admin-empty">
                          <Trans i18nKey="batch.tr0260" />
                        </div>
                      )
                    : (
                        <div className="data-table-wrapper">
                          <table className="data-table integration-admin-wide-table">
                            <thead>
                              <tr>
                                <th><Trans i18nKey="batch.tr0245" /></th>
                                <th><Trans i18nKey="batch.tr0272" /></th>
                                <th><Trans i18nKey="batch.tr0269" /></th>
                                <th><Trans i18nKey="batch.tr0094" /></th>
                                <th><Trans i18nKey="batch.tr0267" /></th>
                                <th><Trans i18nKey="batch.tr0241" /></th>
                                <th><Trans i18nKey="batch.tr0249" /></th>
                                <th><Trans i18nKey="batch.tr0264" /></th>
                                <th><Trans i18nKey="batch.tr0275" /></th>
                              </tr>
                            </thead>

                            <tbody>
                              {
                                etlErrors.map(
                                  item => {
                                    const landingId =
                                      dwhLandingId(item)

                                    const rowLevel =
                                      hasRowIdentity(item)

                                    return (
                                      <tr key={item.error_id}>
                                        <td>
                                          <strong>
                                            #{item.error_id}
                                          </strong>
                                        </td>

                                        <td>
                                          <strong>
                                            {
                                              item.source_system_code
                                              ?? 'Unknown source'
                                            }
                                          </strong>

                                          {
                                            item.source_system_name
                                            && item.source_system_name
                                              !== item.source_system_code
                                              ? (
                                                  <>
                                                    <br />
                                                    {
                                                      item.source_system_name
                                                    }
                                                  </>
                                                )
                                              : null
                                          }

                                          <br />

                                          <code>
                                            {
                                              item.source_object
                                                ? (
                                                    (
                                                      item.source_schema
                                                        ? (
                                                            item.source_schema
                                                            + '.'
                                                          )
                                                        : ''
                                                    )
                                                    + item.source_object
                                                  )
                                                : (
                                                    item.source_object_id
                                                    === null
                                                      ?<Trans i18nKey="batch.tr0277" />
                                                      : (
                                                          'Object #'
                                                          + item.source_object_id
                                                        )
                                                  )
                                            }
                                          </code>
                                        </td>

                                        <td>
                                          {
                                            rowLevel
                                              ? (
                                                  <>
                                                    {
                                                      item.source_pk
                                                        ? (
                                                            <div>
                                                              <strong>
                                                                <Trans i18nKey="batch.tr0273" />
                                                              </strong>
                                                              {' '}
                                                              <code>
                                                                {
                                                                  item.source_pk
                                                                }
                                                              </code>
                                                            </div>
                                                          )
                                                        : null
                                                    }

                                                    {
                                                      item.application_id
                                                      !== null
                                                        ? (
                                                            <div>
                                                              <strong>
                                                                <Trans i18nKey="batch.tr0234" />
                                                              </strong>
                                                              {' #'}
                                                              {
                                                                item.application_id
                                                              }
                                                            </div>
                                                          )
                                                        : null
                                                    }

                                                    {
                                                      item.applicant_id
                                                      !== null
                                                        ? (
                                                            <div>
                                                              <strong>
                                                                <Trans i18nKey="batch.tr0233" />
                                                              </strong>
                                                              {' #'}
                                                              {
                                                                item.applicant_id
                                                              }
                                                            </div>
                                                          )
                                                        : null
                                                    }

                                                    {
                                                      landingId !== null
                                                        ? (
                                                            <div>
                                                              <strong>
                                                                <Trans i18nKey="batch.tr0244" />
                                                              </strong>
                                                              {' #'}
                                                              {landingId}
                                                            </div>
                                                          )
                                                        : null
                                                    }
                                                  </>
                                                )
                                              : (
                                                  <span>
                                                    <Trans i18nKey="batch.tr0261" />
                                                    {'  '}
                                                    <Trans i18nKey="batch.tr0268" />
                                                  </span>
                                                )
                                          }
                                        </td>

                                        <td>
                                          <code>
                                            {
                                              item.error_stage
                                              ?? 'UNKNOWN'
                                            }
                                          </code>
                                        </td>

                                        <td className="integration-history-message">
                                          <code>
                                            {
                                              item.error_code
                                              ?? 'ETL_ERROR'
                                            }
                                          </code>

                                          <br />

                                          {
                                            item.error_message
                                            ?? 'No error message'
                                          }

                                          {
                                            item.error_detail
                                              ? (
                                                  <>
                                                    <br />
                                                    <small>
                                                      {
                                                        item.error_detail
                                                      }
                                                    </small>
                                                  </>
                                                )
                                              : null
                                          }
                                        </td>

                                        <td className="integration-history-message">
                                          {
                                            rowLevel
                                              ? (
                                                  <>
                                                    <div>
                                                      <strong>
                                                        <Trans i18nKey="batch.tr0238" />
                                                      </strong>
                                                      {' '}
                                                      {
                                                        item.column_name
                                                        ?? ''
                                                      }
                                                    </div>

                                                    <div>
                                                      <strong>
                                                        <Trans i18nKey="batch.tr0235" />
                                                      </strong>
                                                      {' '}
                                                      {
                                                        item.bad_value
                                                        ?? ''
                                                      }
                                                    </div>

                                                    <div>
                                                      <strong>
                                                        <Trans i18nKey="batch.tr0250" />
                                                      </strong>
                                                      {' '}
                                                      {
                                                        item.expected_value
                                                        ?? ''
                                                      }
                                                    </div>
                                                  </>
                                                )
                                              : (
                                                  <span>
                                                    <Trans i18nKey="batch.tr0261" />
                                                    {'  '}
                                                    <Trans i18nKey="batch.tr0268" />
                                                  </span>
                                                )
                                          }
                                        </td>

                                        <td>
                                          <div>
                                            <strong>
                                              <Trans i18nKey="batch.tr0236" />
                                            </strong>
                                            {' '}
                                            {
                                              item.batch_run_id
                                              === null
                                                ? ''
                                                : `#${item.batch_run_id}`
                                            }
                                          </div>

                                          <div>
                                            <strong>
                                              <Trans i18nKey="batch.tr0255" />
                                            </strong>
                                            {' '}
                                            {
                                              item.job_run_id
                                              === null
                                                ? ''
                                                : `#${item.job_run_id}`
                                            }
                                          </div>
                                        </td>

                                        <td>
                                          {
                                            formatDateTime(
                                              item.occurred_at,
                                            )
                                          }
                                        </td>

                                        <td>
                                          <details>
                                            <summary>
                                              <Trans i18nKey="batch.tr0276" />
                                            </summary>

                                            <div>
                                              <strong>
                                                <Trans i18nKey="batch.tr0254" />
                                              </strong>
                                              {' '}
                                              {
                                                item.job_id
                                                ?? ''
                                              }
                                            </div>

                                            <div>
                                              <strong>
                                                <Trans i18nKey="batch.tr0263" />
                                              </strong>
                                              {' '}
                                              {
                                                item.source_object_id
                                                ?? ''
                                              }
                                            </div>

                                            <div>
                                              <strong>
                                                SQLSTATE:
                                              </strong>
                                              {' '}
                                              {
                                                item.sql_state
                                                ?? ''
                                              }
                                            </div>

                                            <div>
                                              <strong>
                                                <Trans i18nKey="batch.tr0248" />
                                              </strong>
                                              {' '}
                                              {
                                                item.exception_type
                                                ?? ''
                                              }
                                            </div>

                                            {
                                              item.record_key !== null
                                                ? (
                                                    <div>
                                                      <strong>
                                                        <Trans i18nKey="batch.tr0270" />
                                                      </strong>
                                                      <pre>
                                                        {
                                                          diagnosticJson(
                                                            item.record_key,
                                                          )
                                                        }
                                                      </pre>
                                                    </div>
                                                  )
                                                : null
                                            }

                                            {
                                              item.diagnostic_context
                                              !== null
                                                ? (
                                                    <div>
                                                      <strong>
                                                        <Trans i18nKey="batch.tr0239" />
                                                      </strong>
                                                      <pre>
                                                        {
                                                          diagnosticJson(
                                                            item.diagnostic_context,
                                                          )
                                                        }
                                                      </pre>
                                                    </div>
                                                  )
                                                : null
                                            }
                                          </details>
                                        </td>
                                      </tr>
                                    )
                                  },
                                )
                              }
                            </tbody>
                          </table>
                        </div>
                      )
                }
              </>
            )
          : null
      }

      {
        !loading && mode === 'dq-runs'
          ? (
              <div className="data-table-wrapper">
                <table className="data-table integration-admin-wide-table">
                  <thead>
                    <tr>
                      <th><Trans i18nKey="batch.tr0044" /></th>
                      <th><Trans i18nKey="batch.tr0047" /></th>
                      <th><Trans i18nKey="batch.tr0036" /></th>
                      <th><Trans i18nKey="batch.tr0043" /></th>
                      <th><Trans i18nKey="batch.tr0274" /></th>
                      <th><Trans i18nKey="batch.tr0039" /></th>
                      <th><Trans i18nKey="batch.tr0240" /></th>
                      <th><Trans i18nKey="batch.tr0048" /></th>
                    </tr>
                  </thead>

                  <tbody>
                    {
                      dqRuns.map(
                        item => (
                          <tr key={item.dq_run_id}>
                            <td>
                              #{item.dq_run_id}
                            </td>

                            <td>
                              #{item.source_batch_run_id}
                            </td>

                            <td>
                              <span
                                className={
                                  statusBadgeClass(
                                    item.status,
                                  )
                                }
                              >
                                {item.status}
                              </span>
                            </td>

                            <td>
                              {item.business_date ?? ''}
                            </td>

                            <td>
                              {
                                formatDateTime(
                                  item.started_at,
                                )
                              }
                            </td>

                            <td>
                              {
                                formatDateTime(
                                  item.finished_at,
                                )
                              }
                            </td>

                            <td>
                              {item.created_by ?? ''}
                            </td>

                            <td className="integration-history-message">
                              {item.message ?? ''}
                            </td>
                          </tr>
                        ),
                      )
                    }
                  </tbody>
                </table>
              </div>
            )
          : null
      }

      {
        !loading && mode === 'dq-issues'
          ? (
              dqIssues.length === 0
                ? (
                    <div className="integration-admin-empty">
                      <Trans i18nKey="batch.tr0258" />
                    </div>
                  )
                : (
                    <div className="data-table-wrapper">
                      <table className="data-table integration-admin-wide-table">
                        <thead>
                          <tr>
                            <th><Trans i18nKey="batch.tr0253" /></th>
                            <th><Trans i18nKey="batch.tr0044" /></th>
                            <th><Trans i18nKey="batch.tr0045" /></th>
                            <th><Trans i18nKey="batch.tr0046" /></th>
                            <th><Trans i18nKey="batch.tr0036" /></th>
                            <th><Trans i18nKey="batch.tr0042" /></th>
                            <th><Trans i18nKey="batch.tr0050" /></th>
                            <th><Trans i18nKey="batch.tr0049" /></th>
                            <th><Trans i18nKey="batch.tr0048" /></th>
                            <th><Trans i18nKey="batch.tr0051" /></th>
                          </tr>
                        </thead>

                        <tbody>
                          {
                            dqIssues.map(
                              item => (
                                <tr key={item.dq_issue_id}>
                                  <td>
                                    #{item.dq_issue_id}
                                  </td>

                                  <td>
                                    #{item.dq_run_id}
                                  </td>

                                  <td>
                                    <code>
                                      {item.rule_code}
                                    </code>
                                    <br />
                                    {item.rule_name}
                                  </td>

                                  <td>
                                    <span
                                      className={
                                        statusBadgeClass(
                                          item.severity,
                                        )
                                      }
                                    >
                                      {getSeverityLabel(item.severity)}
                                    </span>
                                  </td>

                                  <td>
                                    <span
                                      className={
                                        statusBadgeClass(
                                          item.issue_status,
                                        )
                                      }
                                    >
                                      {item.issue_status}
                                    </span>
                                  </td>

                                  <td>
                                    {
                                      `${item.source_system} / `
                                      + `${item.source_schema}.`
                                      + item.source_object
                                    }
                                  </td>

                                  <td>
                                    {item.column_name ?? ''}
                                  </td>

                                  <td className="integration-history-message">
                                    {item.bad_value ?? ''}
                                  </td>

                                  <td className="integration-history-message">
                                    {item.error_message ?? ''}
                                  </td>

                                  <td>
                                    {
                                      formatDateTime(
                                        item.created_at,
                                      )
                                    }
                                  </td>
                                </tr>
                              ),
                            )
                          }
                        </tbody>
                      </table>
                    </div>
                  )
            )
          : null
      }
    </section>
  )
}
