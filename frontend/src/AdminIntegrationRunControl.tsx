import { Trans } from 'react-i18next'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'

import type {
  FormEvent,
} from 'react'

import {
  cancelIntegrationRunRequest,
  createIntegrationRunRequest,
  getIntegrationRunCandidates,
  getIntegrationRunRequests,
} from './integrationAdminApi'

import type {
  IntegrationRunCandidate,
  IntegrationRunRequestAdmin,
} from './integrationAdminApi'

import {
  confirmAction,
} from './confirmAction'

import {
  formatDateTime,
} from './dateTime'


const TASKS = [
  'VALIDATE_STG',
  'TRANSFORM_ODS',
  'REFRESH_MARTS',
  'RUN_DQ',
] as const


const STATUSES = [
  '',
  'QUEUED',
  'CLAIMED',
  'RUNNING',
  'SUCCESS',
  'FAILED',
  'CANCELLED',
] as const


function errorMessage(
  error: unknown,
): string {
  return error instanceof Error
    ? error.message
    : 'Unknown error'
}


function nullable(
  value: string,
): string | null {
  const normalized =
    value.trim()

  return normalized === ''
    ? null
    : normalized
}


export default function AdminIntegrationRunControl() {
  const [
    requests,
    setRequests,
  ] = useState<IntegrationRunRequestAdmin[]>([])

  const [
    candidateRuns,
    setCandidateRuns,
  ] = useState<IntegrationRunCandidate[]>([])

  const [
    statusFilter,
    setStatusFilter,
  ] = useState('')

  const [
    loading,
    setLoading,
  ] = useState(true)

  const [
    error,
    setError,
  ] = useState<string | null>(null)

  const [
    refreshError,
    setRefreshError,
  ] = useState<string | null>(null)

  const [
    creating,
    setCreating,
  ] = useState(false)

  const [
    saving,
    setSaving,
  ] = useState(false)

  const submissionInProgress = useRef(false)

  const [
    cancellingId,
    setCancellingId,
  ] = useState<number | null>(null)

  const [
    taskCode,
    setTaskCode,
  ] = useState<
    typeof TASKS[number]
  >('VALIDATE_STG')

  const [
    sourceBatchRunId,
    setSourceBatchRunId,
  ] = useState('')

  const [
    loadMode,
    setLoadMode,
  ] = useState('FULL')


  const load = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)

      const [
        requestResult,
        candidateResult,
      ] = await Promise.all([
        getIntegrationRunRequests(
          statusFilter || undefined,
          100,
        ),
        getIntegrationRunCandidates(
          taskCode,
          200,
        ),
      ])

      setRequests(requestResult)
      setCandidateRuns(candidateResult)
    } catch (loadError) {
      setError(
        errorMessage(loadError),
      )
    } finally {
      setLoading(false)
    }
  }, [statusFilter, taskCode])


  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load()
    }, 0)

    return () => {
      window.clearTimeout(timer)
    }
  }, [load])


  const selectedCandidate = candidateRuns.find(
    run =>
      String(run.batch_run_id) === sourceBatchRunId,
  ) ?? candidateRuns[0]

  const selectedSourceBatchRunId = selectedCandidate
    ? String(selectedCandidate.batch_run_id)
    : ''


  useEffect(() => {
    let active = true
    let refreshing = false

    async function refreshRequests() {
      if (refreshing) {
        return
      }

      refreshing = true

      try {
        const result = await getIntegrationRunRequests(
          statusFilter || undefined,
          100,
        )

        if (active) {
          setRequests(result)
          setRefreshError(null)
        }
      } catch (refreshFailure) {
        if (active) {
          setRefreshError(
            errorMessage(refreshFailure),
          )
        }
      } finally {
        refreshing = false
      }
    }

    const timer = window.setInterval(() => {
      void refreshRequests()
    }, 5000)

    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [statusFilter])


  async function submit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    if (submissionInProgress.current) {
      return
    }

    const sourceBatch = Number.parseInt(
      selectedSourceBatchRunId,
      10,
    )

    if (
      !Number.isInteger(sourceBatch)
      || sourceBatch < 1
    ) {
      setError(
        'A successful source batch run is required.',
      )
      return
    }

    const selectedTask = taskCode
    const selectedMode =
      selectedTask === 'TRANSFORM_ODS'
        ? loadMode
        : null

    submissionInProgress.current = true

    try {
      const confirmed = await confirmAction({
        title: 'Queue a real ETL / DQ task?',
        message:
          `Task: ${selectedTask}; source batch #${sourceBatch}`,
        detail:
          (selectedMode
            ? `Load mode: ${selectedMode}. `
            : '')
          + 'This starts real data processing and can write '
          + 'to DWH. If submission fails or times out, '
          + 'check the request queue before trying again.',
        confirmLabel: 'Queue request',
      })

      if (!confirmed) {
        return
      }

      setSaving(true)
      setError(null)

      await createIntegrationRunRequest({
        task_code: selectedTask,
        source_batch_run_id: sourceBatch,
        load_mode: selectedMode,
      })

      setCreating(false)
      await load()
    } catch (saveError) {
      setError(
        errorMessage(saveError)
        + '. Check the request queue before retrying.',
      )
    } finally {
      setSaving(false)
      submissionInProgress.current = false
    }
  }


  async function cancelRequest(
    item: IntegrationRunRequestAdmin,
  ) {
    const confirmed =
      await confirmAction({
        title:
          'Cancel queued run request?',
        message:
          `Request #${item.run_request_id}  ${item.task_code}`,
        detail:
          'Only QUEUED requests can be cancelled. This does not terminate a running ETL process.',
        confirmLabel:
          'Cancel request',
      })

    if (!confirmed) {
      return
    }

    try {
      setCancellingId(
        item.run_request_id,
      )
      setError(null)

      await cancelIntegrationRunRequest(
        item.run_request_id,
        {
          reason:
            nullable(
              'Cancelled from Integration Control UI',
            ),
        },
      )

      await load()
    } catch (cancelError) {
      setError(
        errorMessage(cancelError),
      )
    } finally {
      setCancellingId(null)
    }
  }


  return (
    <div className="integration-admin-panel">
      <div className="integration-run-warning">
        <strong>
          <Trans i18nKey="batch.tr0420" />
        </strong>

        <span>
          <Trans i18nKey="batch.tr0418" />
        </span>
      </div>

      <div className="admin-integrations-toolbar">
        <label className="admin-integrations-filter">
          <span><Trans i18nKey="batch.tr0036" /></span>

          <select
            value={statusFilter}
            onChange={
              event =>
                setStatusFilter(
                  event.target.value,
                )
            }
          >
            {
              STATUSES.map(
                status => (
                  <option
                    key={
                      status || 'ALL'
                    }
                    value={status}
                  >
                    {
                      status || 'All statuses'
                    }
                  </option>
                ),
              )
            }
          </select>
        </label>

        <div className="integration-run-toolbar-actions">
          <button
            type="button"
            className="admin-secondary-button"
            disabled={loading}
            onClick={
              () => {
                void load()
              }
            }
          >
            <Trans i18nKey="batch.tr0280" />
          </button>

          <button
            type="button"
            className="admin-primary-button"
            disabled={saving}
            onClick={
              () =>
                setCreating(true)
            }
          >
            <Trans i18nKey="batch.tr0404" />
          </button>
        </div>
      </div>

      {
        refreshError
          ? (
              <div className="error">
                <strong>
                  <Trans i18nKey="batch.tr0398" />
                </strong>
                <span>
                  {refreshError}
                </span>
              </div>
            )
          : null
      }

      {
        error
          ? (
              <div className="error">
                <strong>
                  <Trans i18nKey="batch.tr0419" />
                </strong>

                <span>
                  {error}
                </span>
              </div>
            )
          : null
      }

      {
        creating
          ? (
              <section className="admin-integrations-editor">
                <h3>
                  <Trans i18nKey="batch.tr0404" />
                </h3>

                <form
                  className="admin-integrations-form"
                  onSubmit={submit}
                >
                  <label>
                    <span><Trans i18nKey="batch.tr0422" /></span>

                    <select
                      value={taskCode}
                      onChange={
                        event => {
                          const value =
                            event.target.value as typeof taskCode

                          setTaskCode(value)
                          setCandidateRuns([])
                          setSourceBatchRunId('')
                        }
                      }
                    >
                      {
                        TASKS.map(
                          task => (
                            <option
                              key={task}
                              value={task}
                            >
                              {task}
                            </option>
                          ),
                        )
                      }
                    </select>
                  </label>

                  <label>
                    <span>
                      <Trans i18nKey="batch.tr0421" />
                    </span>

                    <select
                      required
                      value={selectedSourceBatchRunId}
                      onChange={
                        event =>
                          setSourceBatchRunId(
                            event.target.value,
                          )
                      }
                    >
                      {
                        candidateRuns.length === 0
                          ? (
                              <option value="">
                                <Trans i18nKey="batch.tr0408" />
                              </option>
                            )
                          : candidateRuns.map(
                              run => (
                                <option
                                  key={run.batch_run_id}
                                  value={run.batch_run_id}
                                >
                                  #{run.batch_run_id}
                                  {'  '}
                                  {
                                    run.pipeline_code
                                    || 'LEGACY'
                                  }
                                  {'  '}
                                  {
                                    run.business_date
                                    ?? 'no business date'
                                  }
                                </option>
                              ),
                            )
                      }
                    </select>
                  </label>

                  {
                    taskCode === 'TRANSFORM_ODS'
                      ? (
                          <label>
                            <span><Trans i18nKey="batch.tr0405" /></span>

                            <select
                              value={loadMode}
                              onChange={
                                event =>
                                  setLoadMode(
                                    event.target.value,
                                  )
                              }
                            >
                              <option value="FULL">
                                FULL
                              </option>

                              <option value="CONTINUE">
                                CONTINUE
                              </option>
                            </select>
                          </label>
                        )
                      : null
                  }

                  <div className="wide integration-run-help">
                    <strong>
                      <Trans i18nKey="batch.tr0423" />
                    </strong>

                    <span>
                      <Trans i18nKey="batch.tr0426" />
                    </span>
                  </div>

                  <div className="admin-integrations-form-actions wide">
                    <button
                      type="button"
                      className="admin-secondary-button"
                      disabled={saving}
                      onClick={
                        () =>
                          setCreating(false)
                      }
                    >
                      <Trans i18nKey="batch.tr0222" />
                    </button>

                    <button
                      type="submit"
                      className="admin-primary-button"
                      disabled={
                        saving
                        || candidateRuns.length === 0
                      }
                    >
                      {
                        saving
                          ?<Trans i18nKey="batch.tr0415" />
                          :<Trans i18nKey="batch.tr0413" />
                      }
                    </button>
                  </div>
                </form>
              </section>
            )
          : null
      }

      {
        loading && requests.length === 0
          ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0406" />
              </div>
            )
          : requests.length === 0
            ? (
                <div className="integration-admin-empty">
                  <Trans i18nKey="batch.tr0409" />
                </div>
              )
            : (
                <div className="data-table-wrapper">
                  <table className="data-table integration-admin-wide-table">
                    <thead>
                      <tr>
                        <th><Trans i18nKey="batch.tr0416" /></th>
                        <th><Trans i18nKey="batch.tr0422" /></th>
                        <th><Trans i18nKey="batch.tr0047" /></th>
                        <th><Trans i18nKey="batch.tr0075" /></th>
                        <th><Trans i18nKey="batch.tr0036" /></th>
                        <th><Trans i18nKey="batch.tr0417" /></th>
                        <th><Trans i18nKey="batch.tr0056" /></th>
                        <th><Trans i18nKey="batch.tr0048" /></th>
                        <th><Trans i18nKey="batch.tr0215" /></th>
                      </tr>
                    </thead>

                    <tbody>
                      {
                        requests.map(
                          item => (
                            <tr key={item.run_request_id}>
                              <td>
                                <strong>
                                  #{item.run_request_id}
                                </strong>
                                <br />
                                <span className="integration-run-muted">
                                  {item.requested_by_username}
                                </span>
                              </td>

                              <td>
                                {item.task_code}
                              </td>

                              <td>
                                #{item.source_batch_run_id}
                              </td>

                              <td>
                                {item.load_mode ?? ''}
                              </td>

                              <td>
                                <span
                                  className={
                                    'badge integration-run-status '
                                    + (
                                      item.status
                                      === 'SUCCESS'
                                        ? 'badge-success'
                                        : ''
                                    )
                                  }
                                >
                                  {item.status}
                                </span>
                              </td>

                              <td>
                                {
                                  formatDateTime(
                                    item.requested_at,
                                  )
                                }
                              </td>

                              <td>
                                {
                                  item.result_batch_run_id
                                    ? (
                                        <>
                                          <div>
                                            <strong>
                                              {
                                                item.result_pipeline_code
                                                ?? 'ETL'
                                              }
                                            </strong>
                                          </div>

                                          <div>
                                            {
                                              `Batch #${item.result_batch_run_id}`
                                            }
                                          </div>

                                          {
                                            item.result_batch_status
                                              ? (
                                                  <div className="integration-run-muted">
                                                    {
                                                      item.result_batch_status
                                                    }
                                                  </div>
                                                )
                                              : null
                                          }
                                        </>
                                      )
                                    : item.result_dq_run_id
                                      ? (
                                          <>
                                            <div>
                                              <strong>
                                                DQ
                                              </strong>
                                            </div>

                                            <div>
                                              {
                                                `Run #${item.result_dq_run_id}`
                                              }
                                            </div>
                                          </>
                                        )
                                      : (
                                          <span className="integration-run-muted">
                                            <Trans i18nKey="batch.tr0411" />
                                          </span>
                                        )
                                }
                              </td>

                              <td className="integration-run-message">
                                {
                                  item.error_message
                                  ?? item.message
                                  ?? ''
                                }
                              </td>

                              <td>
                                {
                                  item.status === 'QUEUED'
                                    ? (
                                        <button
                                          type="button"
                                          className="admin-link-button"
                                          disabled={
                                            cancellingId !== null
                                          }
                                          onClick={
                                            () => {
                                              void cancelRequest(
                                                item,
                                              )
                                            }
                                          }
                                        >
                                          {
                                            cancellingId
                                            === item.run_request_id
                                              ?<Trans i18nKey="batch.tr0402" />
                                              :<Trans i18nKey="batch.tr0222" />
                                          }
                                        </button>
                                      )
                                    : (
                                        <span className="integration-admin-readonly">
                                          
                                        </span>
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
      }
    </div>
  )
}
