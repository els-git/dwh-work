import { Trans } from 'react-i18next'
import {
  useEffect,
  useEffectEvent,
  useState,
} from 'react'

import {
  getEtlRunJobs,
  getEtlRuns,
  getHealth,
} from './api'

import type {
  EtlJobRun,
  EtlRun,
  HealthResponse,
} from './types'

import { formatDateTime } from './dateTime'
import { useTranslation } from 'react-i18next'
import { LanguageSwitcher, LocalizedText } from './LanguageControls'

import './App.css'
import { useAuth } from './authContext'
import AdminPage from './AdminPage'
import AdminIntegrationHistory from './AdminIntegrationHistory'
import LoadsObjectsPanel from './LoadsObjectsPanel'
import DmMartsPanel from './DmMartsPanel'
import ObjectSchedulesPanel from './ObjectSchedulesPanel'
import AiAssistantPanel from './AiAssistantPanel'
import ReportingPage from './ReportingPage'
import ServerLoadBadge from './ServerLoadBadge'


type PageKey =
  | 'loads'
  | 'reporting'
  | 'administration'

// I08: tabs of the section "Data loads" (decision R4: one section instead of Integration Control,
// ETL Runs, Data Quality, Run Control and History / Errors).
type LoadsTab =
  | 'objects'
  | 'schedules'
  | 'marts'
  | 'runs'
  | 'errors'

function formatDate(
  value: string | null | undefined,
): string {
  if (!value) {
    return ''
  }

  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return value
  }

  return formatDateTime(value)
}


function statusClass(
  status: string | null | undefined,
): string {
  const normalized =
    status?.toUpperCase() ?? ''

  if (
    normalized === 'SUCCESS'
    || normalized === 'ACTIVE'
    || normalized === 'OK'
  ) {
    return 'badge badge-success'
  }

  if (
    normalized === 'FAILED'
    || normalized === 'ERROR'
  ) {
    return 'badge badge-danger'
  }

  if (
    normalized === 'WARNING'
    || normalized === 'RUNNING'
  ) {
    return 'badge badge-warning'
  }

  return 'badge'
}


function App() {
  const { t } = useTranslation()

  const {
    auth,
    hasPermission,
    hasResource,
    logout,
  } = useAuth()

  const hasCdpAccess =
    hasResource(
      'MODULE',
      'CDP',
    )

  const canIntegration =
    hasCdpAccess
    && hasPermission(
      'NAV_INTEGRATION_VIEW',
    )
    && hasPermission(
      'INTEGRATION_VIEW',
    )

  const canEtl =
    hasCdpAccess
    && hasPermission(
      'NAV_ETL_VIEW',
    )
    && hasPermission(
      'ETL_VIEW',
    )

  const canDq =
    hasCdpAccess
    && hasPermission(
      'NAV_DQ_VIEW',
    )
    && hasPermission(
      'DQ_VIEW',
    )

  const canReporting =
    hasCdpAccess
    && hasPermission(
      'NAV_REPORTING_VIEW',
    )
    && hasPermission(
      'REPORT_VIEW',
    )

  const isAdministrator =
    auth.user?.account_level
      === 'SUPERADMIN'
    || auth.user?.account_level
      === 'ADMIN'

  const canAdministration =
    isAdministrator
    && (
      hasPermission(
        'ADMIN_MODULE_MANAGE',
      )
      || hasPermission(
        'ADMIN_USER_MANAGE',
      )
      || hasPermission(
        'ADMIN_ROLE_MANAGE',
      )
      || hasPermission(
        'ADMIN_ACCESS_MANAGE',
      )
      || hasPermission(
        'ADMIN_INTEGRATION_MANAGE',
      )
      || hasPermission(
        'AI_ADMIN_MANAGE',
      )
    )

  const hasWorkspaceAccess =
    canIntegration
    || canEtl
    || canDq
    || canReporting
    || canAdministration
  // I08: Run Control and History / Errors moved here from Administration -> Integrations.
  const canIntegrationAdmin =
    isAdministrator
    && hasPermission(
      'ADMIN_INTEGRATION_MANAGE',
    )

  // I17-10: "Analyse with the assistant" needs the chat (AI_ASSISTANT_USE) and the right to see load errors.
  const canAiDiagnose =
    hasPermission(
      'AI_ASSISTANT_USE',
    )
    && hasPermission(
      'ETL_VIEW',
    )

  const loadsTabs: LoadsTab[] = [
    ...(canIntegration ? ['objects' as const] : []),
    // I09: schedules — the same right as manual runs (P9)
    ...(canIntegrationAdmin ? ['schedules' as const] : []),
    ...(canIntegration ? ['marts' as const] : []),
    ...(canEtl ? ['runs' as const] : []),
    ...(canIntegrationAdmin ? ['errors' as const] : []),
  ]

  const defaultLoadsTab: LoadsTab | null =
    loadsTabs[0] ?? null

  const [
    activePage,
    setActivePage,
  ] = useState<PageKey>('loads')

  const [
    loadsTab,
    setLoadsTab,
  ] = useState<LoadsTab>('objects')

  const [
    health,
    setHealth,
  ] = useState<HealthResponse | null>(null)

  const [
    etlRuns,
    setEtlRuns,
  ] = useState<EtlRun[]>([])

  const [
    selectedBatchId,
    setSelectedBatchId,
  ] = useState<number | null>(null)

  const readSelectedBatchId = useEffectEvent(
    () => selectedBatchId,
  )

  const [
    etlJobs,
    setEtlJobs,
  ] = useState<EtlJobRun[]>([])

  const [
    etlLoading,
    setEtlLoading,
  ] = useState(false)

  const [
    initialLoading,
    setInitialLoading,
  ] = useState(true)

  const [
    error,
    setError,
  ] = useState<string | null>(null)


  useEffect(() => {
    let cancelled = false

    async function loadInitialData() {
      try {
        setError(null)

        const healthResult =
          await getHealth()

        if (cancelled) {
          return
        }

        setHealth(
          healthResult,
        )


        if (defaultLoadsTab !== null) {
          setLoadsTab(
            defaultLoadsTab,
          )

          setActivePage(
            'loads',
          )

          return
        }


        if (!canReporting && canAdministration) {
          setActivePage(
            'administration',
          )

          return
        }


        if (canReporting) {
          setActivePage(
            'reporting',
          )
        }
      } catch (loadError) {
        if (cancelled) {
          return
        }

        setError(
          loadError instanceof Error
            ? loadError.message
            : 'Unknown error',
        )
      } finally {
        if (!cancelled) {
          setInitialLoading(
            false,
          )
        }
      }
    }

    void loadInitialData()

    return () => {
      cancelled = true
    }
  }, [
    canAdministration,
    canReporting,
    defaultLoadsTab,
  ])




  useEffect(() => {
    if (activePage !== 'loads' || loadsTab !== 'runs') {
      return
    }

    let cancelled = false

    async function loadEtlRuns() {
      try {
        setEtlLoading(true)
        setError(null)

        const runsResult =
          await getEtlRuns()

        if (cancelled) {
          return
        }

        setEtlRuns(
          runsResult,
        )

        const firstBatchId =
          readSelectedBatchId()
          ?? runsResult[0]?.batch_run_id
          ?? null

        if (firstBatchId === null) {
          setSelectedBatchId(null)
          setEtlJobs([])
          return
        }

        setSelectedBatchId(
          firstBatchId,
        )

        const jobsResult =
          await getEtlRunJobs(
            firstBatchId,
          )

        if (!cancelled) {
          setEtlJobs(
            jobsResult,
          )
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : 'Unknown error',
          )
        }
      } finally {
        if (!cancelled) {
          setEtlLoading(false)
        }
      }
    }

    void loadEtlRuns()

    return () => {
      cancelled = true
    }
  }, [activePage, loadsTab])


  async function selectBatch(
    batchRunId: number,
  ) {
    try {
      setSelectedBatchId(
        batchRunId,
      )

      setEtlLoading(true)
      setError(null)

      const jobsResult =
        await getEtlRunJobs(
          batchRunId,
        )

      setEtlJobs(
        jobsResult,
      )
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : 'Unknown error',
      )
    } finally {
      setEtlLoading(false)
    }
  }

  function renderLoadsPage() {
    const current: LoadsTab | null =
      loadsTabs.includes(loadsTab)
        ? loadsTab
        : defaultLoadsTab

    return (
      <>
        <header className="workspace-header">
          <div>
            <h1>
              <LocalizedText translationKey="navigation.loads" />
            </h1>

            <p>
              {t('loads.sectionHint')}
            </p>
          </div>
        </header>

        <div className="tabs">
          {
            loadsTabs.map(
              tab => (
                <button
                  type="button"
                  key={tab}
                  className={
                    current === tab
                      ? 'tab active'
                      : 'tab'
                  }
                  onClick={
                    () =>
                      setLoadsTab(
                        tab,
                      )
                  }
                >
                  {t(`loads.tab.${tab}`)}
                </button>
              ),
            )
          }
        </div>

        {
          current === 'objects'
            ? <LoadsObjectsPanel canRun={canIntegrationAdmin} canDiagnose={canAiDiagnose} />
            : current === 'schedules'
              ? <ObjectSchedulesPanel />
            : current === 'marts'
              ? <DmMartsPanel canRun={canIntegrationAdmin} canDiagnose={canAiDiagnose} />
            : current === 'runs'
              ? renderEtlPage()
              : current === 'errors'
                ? <AdminIntegrationHistory canDiagnose={canAiDiagnose} />
                : null
        }
      </>
    )
  }


  function renderEtlPage() {
    return (
      <>
        <header className="workspace-header">
          <div>
            <div className="breadcrumb">
              <Trans i18nKey="batch.tr0035" /> <LocalizedText translationKey="navigation.etl" />
            </div>

            <h1>
              <LocalizedText translationKey="navigation.etl" />
            </h1>

            <p>
              <Trans i18nKey="batch.tr0062" />
            </p>
          </div>

          <div className="header-meta">
            <span className="badge badge-accent">
              {etlRuns.length} RUNS
            </span>
          </div>
        </header>

        {
          error
            ? (
              <div className="error">
                <strong>
                  <Trans i18nKey="batch.tr0040" />
                </strong>

                <span>
                  {error}
                </span>
              </div>
            )
            : null
        }

        {
          etlLoading && etlRuns.length === 0
            ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0065" />
              </div>
            )
            : (
              <>
                <section className="etl-layout">
                  <div className="data-table-wrapper">
                    <table className="data-table etl-run-table">
                      <thead>
                        <tr>
                          <th><Trans i18nKey="batch.tr0076" /></th>
                          <th><Trans i18nKey="batch.tr0075" /></th>
                          <th><Trans i18nKey="batch.tr0077" /></th>
                          <th><Trans i18nKey="batch.tr0036" /></th>
                          <th><Trans i18nKey="batch.tr0067" /></th>
                          <th><Trans i18nKey="batch.tr0066" /></th>
                          <th><Trans i18nKey="batch.tr0070" /></th>
                          <th><Trans i18nKey="batch.tr0041" /></th>
                          <th><Trans i18nKey="batch.tr0069" /></th>
                          <th><Trans i18nKey="batch.tr0073" /></th>
                          <th><Trans i18nKey="batch.tr0074" /></th>
                          <th><Trans i18nKey="batch.tr0039" /></th>
                        </tr>
                      </thead>

                      <tbody>
                        {
                          etlRuns.map(
                            run => (
                              <tr
                                key={run.batch_run_id}
                                className={
                                  selectedBatchId
                                    === run.batch_run_id
                                    ? 'clickable-row selected-row'
                                    : 'clickable-row'
                                }
                                onClick={
                                  () => {
                                    void selectBatch(
                                      run.batch_run_id,
                                    )
                                  }
                                }
                              >
                                <td className="code-value">
                                  {run.batch_run_id}
                                </td>

                                <td>
                                  <span className="badge badge-accent">
                                    {run.run_mode ?? ''}
                                  </span>
                                </td>

                                <td>
                                  {run.attempt_no ?? ''}
                                </td>

                                <td>
                                  <span
                                    className={
                                      statusClass(
                                        run.status,
                                      )
                                    }
                                  >
                                    {run.status}
                                  </span>
                                </td>

                                <td className="code-value">
                                  {
                                    run.root_batch_run_id
                                    ?? ''
                                  }
                                </td>

                                <td className="code-value">
                                  {
                                    run.resume_from_batch_run_id
                                    ?? ''
                                  }
                                </td>

                                <td>
                                  {run.jobs_success}
                                </td>

                                <td>
                                  {run.jobs_failed}
                                </td>

                                <td>
                                  {run.jobs_skipped}
                                </td>

                                <td>
                                  {run.contract_versions}
                                </td>

                                <td>
                                  {run.mapping_versions}
                                </td>

                                <td>
                                  {
                                    formatDate(
                                      run.finished_at,
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
                </section>

                <div className="section-heading">
                  <div>
                    <span>
                      <Trans i18nKey="batch.tr0068" />
                    </span>

                    <strong>
                      {
                        selectedBatchId
                        ?? ''
                      }
                    </strong>
                  </div>

                  <small>
                    {
                      etlJobs.length
                    } <Trans i18nKey="batch.tr0064" />
                  </small>
                </div>

                <div className="data-table-wrapper">
                  <table className="data-table etl-jobs-table">
                    <thead>
                      <tr>
                        <th><Trans i18nKey="batch.tr0071" /></th>
                        <th><Trans i18nKey="batch.tr0063" /></th>
                        <th><Trans i18nKey="batch.tr0036" /></th>
                        <th><Trans i18nKey="batch.tr0038" /></th>
                        <th><Trans i18nKey="batch.tr0081" /></th>
                        <th><Trans i18nKey="batch.tr0082" /></th>
                        <th><Trans i18nKey="batch.tr0079" /></th>
                        <th><Trans i18nKey="batch.tr0072" /></th>
                        <th><Trans i18nKey="batch.tr0080" /></th>
                        <th><Trans i18nKey="batch.tr0078" /></th>
                        <th><Trans i18nKey="batch.tr0039" /></th>
                      </tr>
                    </thead>

                    <tbody>
                      {
                        etlJobs.map(
                          job => (
                            <tr key={job.job_run_id}>
                              <td>
                                {job.execution_order}
                              </td>

                              <td>
                                <strong>
                                  {job.job_code}
                                </strong>

                                <small className="block-muted">
                                  job_run_id={
                                    job.job_run_id
                                  }
                                </small>
                              </td>

                              <td>
                                <span
                                  className={
                                    statusClass(
                                      job.job_status,
                                    )
                                  }
                                >
                                  {job.job_status}
                                </span>
                              </td>

                              <td className="code-value">
                                {job.target_schema}.{
                                  job.target_object
                                }
                              </td>

                              <td className="code-value">
                                {
                                  job.reused_from_job_run_id
                                  ?? ''
                                }
                              </td>

                              <td>
                                {job.rows_read ?? 0}
                              </td>

                              <td>
                                {job.rows_inserted ?? 0}
                              </td>

                              <td>
                                {job.rows_updated ?? 0}
                              </td>

                              <td>
                                {job.rows_rejected ?? 0}
                              </td>

                              <td>
                                {job.error_count ?? 0}
                              </td>

                              <td>
                                {
                                  formatDate(
                                    job.finished_at,
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
              </>
            )
        }
      </>
    )
  }

  if (initialLoading) {
    return (
      <div className="startup-screen">
        <div className="startup-logo">
          DWH
        </div>

        <p>
          <Trans i18nKey="batch.tr0446" />
        </p>
      </div>
    )
  }


  if (!hasWorkspaceAccess) {
    return (
      <div className="startup-screen">
        <div className="startup-logo">
          DWH
        </div>

        <p>
          <Trans i18nKey="batch.tr0454" />
        </p>
      </div>
    )
  }


  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <img className="brand-logo" src="/asgar-systems-mark.svg" alt="ASGAR Systems" />
          </div>

          <div>
            <strong>
              <Trans i18nKey="batch.tr0123" />
            </strong>

            <small>
              <Trans i18nKey="batch.tr0122" />
            </small>
          </div>
        </div>

        <nav className="main-nav">
          {
            loadsTabs.length > 0
              ? (
                <button
                  type="button"
                  className={
                    activePage === 'loads'
                      ? 'nav-item active'
                      : 'nav-item'
                  }
                  onClick={
                    () =>
                      setActivePage(
                        'loads',
                      )
                  }
                >
                  <LocalizedText translationKey="navigation.loads" />
                </button>
              )
              : null
          }

          {
            canReporting
              ? (
                <button
                  type="button"
                  className={
                    activePage === 'reporting'
                      ? 'nav-item active'
                      : 'nav-item'
                  }
                  onClick={
                    () => {
                      setActivePage('reporting')
                    }
                  }
                >
                  <LocalizedText translationKey="navigation.reporting" />
                </button>
              )
              : null
          }

          {
            canAdministration
              ? (
                <button
                  type="button"
                  className={
                    activePage === 'administration'
                      ? 'nav-item active'
                      : 'nav-item'
                  }
                  onClick={
                    () =>
                      setActivePage(
                        'administration',
                      )
                  }
                >
                  <LocalizedText translationKey="navigation.administration" />
                </button>
              )
              : null
          }
        </nav>

        <div className="sidebar-footer">
          <LanguageSwitcher />
          <div className="sidebar-user">
            <div className="sidebar-user-text">
              <strong>
                {
                  auth.user?.display_name
                  ?? auth.user?.username
                  ?? ''
                }
              </strong>

              <small>
                {
                  auth.user?.account_level
                  ?? ''
                }
              </small>
            </div>

            <button
              type="button"
              className="sidebar-signout"
              onClick={
                () => {
                  void logout()
                }
              }
            >
              <LocalizedText translationKey="sidebar.signOut" />
            </button>
          </div>

          {
            // I13-13: server load, administrators only (the API refuses everybody else).
            auth.user?.account_level === 'SUPERADMIN' || auth.user?.account_level === 'ADMIN'
              ? <ServerLoadBadge />
              : null
          }

          <div className="sidebar-health">
            <span
              className={
                statusClass(
                  health?.status,
                )
              }
            >
              {
                health?.status
                ?? 'UNKNOWN'
              }
            </span>

            <small>
              {
                health?.database_user
                ?? ''
              } @ {
                health?.database_name
                ?? ''
              }
            </small>
          </div>
        </div>
      </aside>


      <main
        className={
          activePage === 'reporting'
            ? 'workspace workspace-reporting'
            : 'workspace'
        }
      >
        {
          activePage === 'reporting'
            ? (
              <>
                <ReportingPage />
              </>
            )
            : activePage === 'administration'
              ? <AdminPage />
              : renderLoadsPage()
        }
      </main>

      {
        // I17: the assistant is available on every screen to users with AI_ASSISTANT_USE.
        hasPermission(
          'AI_ASSISTANT_USE',
        )
          ? <AiAssistantPanel />
          : null
      }
    </div>
  )
}

export default App