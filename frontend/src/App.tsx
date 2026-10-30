import { Trans } from 'react-i18next'
import {
  Fragment,
  useEffect,
  useEffectEvent,
  useMemo,
  useState,
} from 'react'

import {
  getDqRunIssues,
  getDqRunResults,
  getDqRuns,
  getEtlRunJobs,
  getEtlRuns,
  getHealth,
  getIntegrationDqRules,
  getIntegrationErrors,
  getIntegrationIssues,
  getIntegrationMappings,
  getIntegrationOverview,
  getIntegrationRuns,
  getIntegrationStructure,
  getIntegrationValidations,
  getIntegrations,
  getRetailReportingOverview,
  downloadRetailReport,
} from './api'

import type {
  DqIssue,
  DqResult,
  DqRun,
  EtlError,
  EtlJobRun,
  EtlRun,
  HealthResponse,
  IntegrationDqRule,
  IntegrationMapping,
  IntegrationObject,
  IntegrationOverview,
  RetailReportingOverview,
  SourceColumn,
  ValidationContract,
} from './types'

import { formatDateTime } from './dateTime'
import { useSeverityLabel } from './severityLabels'
import { useTranslation } from 'react-i18next'
import { LanguageSwitcher, LocalizedText } from './LanguageControls'

import './App.css'
import { useAuth } from './authContext'
import AdminPage from './AdminPage'


type PageKey =
  | 'integrations'
  | 'etl'
  | 'dq'
  | 'reporting'
  | 'administration'


type ReportKey =
  | 'status'
  | 'stage'
  | 'channel'
  | 'product'

type TabKey =
  | 'overview'
  | 'structure'
  | 'mapping'
  | 'validations'
  | 'dq'
  | 'runs'
  | 'errors'
  | 'issues'


interface TabDefinition {
  key: TabKey
  labelKey: string
}


const tabs: TabDefinition[] = [
  {
    key: 'overview',
    labelKey: 'integrationTabs.overview',
  },
  {
    key: 'structure',
    labelKey: 'integrationTabs.structure',
  },
  {
    key: 'mapping',
    labelKey: 'integrationTabs.mapping',
  },
  {
    key: 'validations',
    labelKey: 'integrationTabs.validations',
  },
  {
    key: 'dq',
    labelKey: 'integrationTabs.dq',
  },
  {
    key: 'runs',
    labelKey: 'integrationTabs.runs',
  },
  {
    key: 'errors',
    labelKey: 'integrationTabs.errors',
  },
  {
    key: 'issues',
    labelKey: 'integrationTabs.issues',
  },
]


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


function yesNo(
  value: boolean,
): string {
  return value ? 'YES' : 'NO'
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
  const getSeverityLabel = useSeverityLabel()
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
    )

  const hasWorkspaceAccess =
    canIntegration
    || canEtl
    || canDq
    || canReporting
    || canAdministration
  const [
    activePage,
    setActivePage,
  ] = useState<PageKey>('integrations')

  const [
    reportingOverview,
    setReportingOverview,
  ] = useState<RetailReportingOverview | null>(null)

  const [
    reportingLoading,
    setReportingLoading,
  ] = useState(false)

  const [
    reportingError,
    setReportingError,
  ] = useState<string | null>(null)

  const [
    openReportingSections,
    setOpenReportingSections,
  ] = useState<ReportKey[]>([])

  const [
    reportingSearch,
    setReportingSearch,
  ] = useState('')

  const [
    reportingSearchIndex,
    setReportingSearchIndex,
  ] = useState(0)

  const [
    highlightedReport,
    setHighlightedReport,
  ] = useState<ReportKey | null>(null)

  const [
    exportMenuReport,
    setExportMenuReport,
  ] = useState<ReportKey | null>(null)

  const [
    exportingReport,
    setExportingReport,
  ] = useState<ReportKey | null>(null)

  const [
    health,
    setHealth,
  ] = useState<HealthResponse | null>(null)

  const [
    integrations,
    setIntegrations,
  ] = useState<IntegrationObject[]>([])

  const [
    selectedId,
    setSelectedId,
  ] = useState<number | null>(null)

  const [
    activeTab,
    setActiveTab,
  ] = useState<TabKey>('overview')

  const [
    overview,
    setOverview,
  ] = useState<IntegrationOverview | null>(null)

  const [
    structure,
    setStructure,
  ] = useState<SourceColumn[]>([])

  const [
    mappings,
    setMappings,
  ] = useState<IntegrationMapping[]>([])

  const [
    validations,
    setValidations,
  ] = useState<ValidationContract[]>([])

  const [
    dqRules,
    setDqRules,
  ] = useState<IntegrationDqRule[]>([])

  const [
    runs,
    setRuns,
  ] = useState<EtlJobRun[]>([])

  const [
    errors,
    setErrors,
  ] = useState<EtlError[]>([])

  const [
    issues,
    setIssues,
  ] = useState<DqIssue[]>([])

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
    globalDqRuns,
    setGlobalDqRuns,
  ] = useState<DqRun[]>([])

  const [
    selectedDqRunId,
    setSelectedDqRunId,
  ] = useState<number | null>(null)

  const readSelectedDqRunId = useEffectEvent(
    () => selectedDqRunId,
  )

  const [
    selectedDqResultId,
    setSelectedDqResultId,
  ] = useState<number | null>(null)

  const [
    globalDqResults,
    setGlobalDqResults,
  ] = useState<DqResult[]>([])

  const [
    globalDqIssues,
    setGlobalDqIssues,
  ] = useState<DqIssue[]>([])

  const [
    dqLoading,
    setDqLoading,
  ] = useState(false)

  const [
    initialLoading,
    setInitialLoading,
  ] = useState(true)

  const [
    detailLoading,
    setDetailLoading,
  ] = useState(false)

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


        if (canIntegration) {
          const integrationsResult =
            await getIntegrations()

          if (cancelled) {
            return
          }

          setIntegrations(
            integrationsResult,
          )

          if (
            integrationsResult.length > 0
          ) {
            setSelectedId(
              integrationsResult[0]
                .source_object_id,
            )
          }

          setActivePage(
            'integrations',
          )

          return
        }


        setIntegrations([])
        setSelectedId(null)


        if (canEtl) {
          setActivePage(
            'etl',
          )

          return
        }


        if (canDq) {
          setActivePage(
            'dq',
          )

          return
        }


        if (canReporting) {
          setActivePage(
            'reporting',
          )

          setReportingLoading(
            true,
          )

          try {
            const reportingResult =
              await getRetailReportingOverview()

            if (!cancelled) {
              setReportingOverview(
                reportingResult,
              )
            }
          } catch (reportError) {
            if (!cancelled) {
              setReportingError(
                reportError instanceof Error
                  ? reportError.message
                  : 'Failed to load reporting data',
              )
            }
          } finally {
            if (!cancelled) {
              setReportingLoading(
                false,
              )
            }
          }
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
    canDq,
    canEtl,
    canIntegration,
    canReporting,
  ])


  useEffect(() => {
    if (selectedId === null) {
      return
    }

    let cancelled = false

    async function loadIntegrationDetails() {
      try {
        setDetailLoading(true)
        setError(null)

        const [
          overviewResult,
          structureResult,
          mappingsResult,
          validationsResult,
          dqRulesResult,
          runsResult,
          errorsResult,
          issuesResult,
        ] = await Promise.all([
          getIntegrationOverview(selectedId!),
          getIntegrationStructure(selectedId!),
          getIntegrationMappings(selectedId!),
          getIntegrationValidations(selectedId!),
          getIntegrationDqRules(selectedId!),
          getIntegrationRuns(selectedId!),
          getIntegrationErrors(selectedId!),
          getIntegrationIssues(selectedId!),
        ])

        if (cancelled) {
          return
        }

        setOverview(
          overviewResult,
        )

        setStructure(
          structureResult,
        )

        setMappings(
          mappingsResult,
        )

        setValidations(
          validationsResult,
        )

        setDqRules(
          dqRulesResult,
        )

        setRuns(
          runsResult,
        )

        setErrors(
          errorsResult,
        )

        setIssues(
          issuesResult,
        )
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
          setDetailLoading(false)
        }
      }
    }

    void loadIntegrationDetails()

    return () => {
      cancelled = true
    }
  }, [selectedId])


  useEffect(() => {
    if (activePage !== 'etl') {
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
  }, [activePage])


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

  useEffect(() => {
    if (activePage !== 'dq') {
      return
    }

    let cancelled = false

    async function loadDataQuality() {
      try {
        setDqLoading(true)
        setError(null)

        const runsResult =
          await getDqRuns()

        if (cancelled) {
          return
        }

        setGlobalDqRuns(
          runsResult,
        )

        const firstRunId =
          readSelectedDqRunId()
          ?? runsResult[0]?.dq_run_id
          ?? null

        if (firstRunId === null) {
          setSelectedDqRunId(null)
          setGlobalDqResults([])
          setGlobalDqIssues([])
          return
        }

        setSelectedDqRunId(
          firstRunId,
        )

        const [
          resultsResult,
          issuesResult,
        ] = await Promise.all([
          getDqRunResults(firstRunId),
          getDqRunIssues(firstRunId),
        ])

        if (!cancelled) {
          setGlobalDqResults(
            resultsResult,
          )

          setGlobalDqIssues(
            issuesResult,
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
          setDqLoading(false)
        }
      }
    }

    void loadDataQuality()

    return () => {
      cancelled = true
    }
  }, [activePage])


  async function selectDqRun(
    dqRunId: number,
  ) {
    try {
      setSelectedDqResultId(null)

      setSelectedDqRunId(
        dqRunId,
      )

      setDqLoading(true)
      setError(null)

      const [
        resultsResult,
        issuesResult,
      ] = await Promise.all([
        getDqRunResults(dqRunId),
        getDqRunIssues(dqRunId),
      ])

      setGlobalDqResults(
        resultsResult,
      )

      setGlobalDqIssues(
        issuesResult,
      )
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : 'Unknown error',
      )
    } finally {
      setDqLoading(false)
    }
  }

  const selectedIntegration = useMemo(
    () => integrations.find(
      item =>
        item.source_object_id
        === selectedId,
    ) ?? null,
    [
      integrations,
      selectedId,
    ],
  )


  function selectIntegration(
    sourceObjectId: number,
  ) {
    setSelectedId(
      sourceObjectId,
    )

    setActiveTab(
      'overview',
    )
  }


  function renderOverview() {
    if (!overview) {
      return null
    }

    return (
      <>
        <div className="metric-grid">
          <article className="metric-card">
            <span><Trans i18nKey="batch.tr0463" /></span>
            <strong>
              {
                overview.integration.source_columns
              }
            </strong>
            <small>
              PK: {
                overview.integration.pk_columns
              }
            </small>
          </article>

          <article className="metric-card">
            <span><Trans i18nKey="batch.tr0074" /></span>
            <strong>
              {
                overview.integration.total_mappings
              }
            </strong>
            <small>
              <Trans i18nKey="batch.tr0464" /> {
                overview.integration.mapped_source_columns
              }
            </small>
          </article>

          <article className="metric-card">
            <span><Trans i18nKey="batch.tr0073" /></span>
            <strong>
              {
                overview.integration.active_contracts
              }
            </strong>
            <small>
              <Trans i18nKey="batch.tr0460" /> {
                overview.integration.required_columns
              }
            </small>
          </article>

          <article className="metric-card">
            <span><Trans i18nKey="batch.tr0229" /></span>
            <strong>
              {
                overview.dq_rules_total
              }
            </strong>
            <small>
              {
                overview.dq_error_rules
              } {getSeverityLabel('ERROR')} / {
                overview.dq_warning_rules
              } {getSeverityLabel('WARNING')}
            </small>
          </article>

          <article className="metric-card">
            <span><Trans i18nKey="batch.tr0457" /></span>
            <strong>
              {
                overview.open_dq_issues
              }
            </strong>
            <small>
              <Trans i18nKey="batch.tr0433" />
            </small>
          </article>

          <article className="metric-card">
            <span><Trans i18nKey="batch.tr0467" /></span>
            <strong>
              {
                overview.technical_errors
              }
            </strong>
            <small>
              <Trans i18nKey="batch.tr0439" />
            </small>
          </article>
        </div>

        <div className="overview-grid">
          <section className="info-card">
            <h3><Trans i18nKey="batch.tr0441" /></h3>

            <dl>
              <dt><Trans i18nKey="batch.tr0042" /></dt>
              <dd>
                {
                  overview.integration.source_schema
                }.{
                  overview.integration.source_object
                }
              </dd>

              <dt>STG</dt>
              <dd>
                {
                  overview.integration.stg_schema
                }.{
                  overview.integration.stg_table
                }
              </dd>

              <dt>ODS</dt>
              <dd>
                {overview.ods_schema}.{
                  overview.ods_object
                }
              </dd>

              <dt><Trans i18nKey="batch.tr0231" /></dt>
              <dd>
                {
                  overview.integration.load_strategy
                }
              </dd>

              <dt><Trans i18nKey="batch.tr0232" /></dt>
              <dd>
                {
                  overview.integration.watermark_column
                  ?? ''
                }
              </dd>

              <dt><Trans i18nKey="batch.tr0449" /></dt>
              <dd>
                {
                  formatDate(
                    overview.integration.metadata_captured_at,
                  )
                }
              </dd>
            </dl>
          </section>

          <section className="info-card">
            <h3><Trans i18nKey="batch.tr0445" /></h3>

            {
              overview.latest_job
                ? (
                  <dl>
                    <dt><Trans i18nKey="batch.tr0036" /></dt>
                    <dd>
                      <span
                        className={
                          statusClass(
                            overview.latest_job.job_status,
                          )
                        }
                      >
                        {
                          overview.latest_job.job_status
                        }
                      </span>
                    </dd>

                    <dt><Trans i18nKey="batch.tr0076" /></dt>
                    <dd>
                      {
                        overview.latest_job.batch_run_id
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0075" /></dt>
                    <dd>
                      {
                        overview.latest_job.run_mode
                        ?? ''
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0077" /></dt>
                    <dd>
                      {
                        overview.latest_job.attempt_no
                        ?? ''
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0082" /></dt>
                    <dd>
                      {
                        overview.latest_job.rows_read
                        ?? 0
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0039" /></dt>
                    <dd>
                      {
                        formatDate(
                          overview.latest_job.finished_at,
                        )
                      }
                    </dd>
                  </dl>
                )
                : (
                  <p className="empty">
                    <Trans i18nKey="batch.tr0452" />
                  </p>
                )
            }
          </section>

          <section className="info-card">
            <h3><Trans i18nKey="batch.tr0444" /></h3>

            {
              overview.latest_dq_run
                ? (
                  <dl>
                    <dt><Trans i18nKey="batch.tr0036" /></dt>
                    <dd>
                      <span
                        className={
                          statusClass(
                            overview.latest_dq_run.status,
                          )
                        }
                      >
                        {
                          overview.latest_dq_run.status
                        }
                      </span>
                    </dd>

                    <dt><Trans i18nKey="batch.tr0057" /></dt>
                    <dd>
                      {
                        overview.latest_dq_run.dq_run_id
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0054" /></dt>
                    <dd>
                      {
                        overview.latest_dq_run.rules_total
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0060" /></dt>
                    <dd>
                      {
                        overview.latest_dq_run.rules_passed
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0041" /></dt>
                    <dd>
                      {
                        overview.latest_dq_run.rules_failed
                      }
                    </dd>

                    <dt><Trans i18nKey="batch.tr0039" /></dt>
                    <dd>
                      {
                        formatDate(
                          overview.latest_dq_run.finished_at,
                        )
                      }
                    </dd>
                  </dl>
                )
                : (
                  <p className="empty">
                    <Trans i18nKey="batch.tr0450" />
                  </p>
                )
            }
          </section>
        </div>
      </>
    )
  }


  function renderStructure() {
    return (
      <div className="data-table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th><Trans i18nKey="batch.tr0050" /></th>
              <th><Trans i18nKey="batch.tr0230" /></th>
              <th><Trans i18nKey="batch.tr0456" /></th>
              <th>PK</th>
              <th><Trans i18nKey="batch.tr0469" /></th>
              <th><Trans i18nKey="batch.tr0440" /></th>
              <th><Trans i18nKey="batch.tr0438" /></th>
            </tr>
          </thead>

          <tbody>
            {
              structure.map(
                column => (
                  <tr
                    key={
                      `${column.ordinal_position}-${column.source_column}`
                    }
                  >
                    <td>
                      {
                        column.ordinal_position
                      }
                    </td>

                    <td className="code-value">
                      {
                        column.source_column
                      }
                    </td>

                    <td>
                      {
                        column.source_type
                      }
                    </td>

                    <td>
                      {
                        yesNo(
                          column.is_nullable,
                        )
                      }
                    </td>

                    <td>
                      {
                        yesNo(
                          column.is_primary_key,
                        )
                      }
                    </td>

                    <td>
                      {
                        yesNo(
                          column.is_unique_key,
                        )
                      }
                    </td>

                    <td>
                      {
                        yesNo(
                          column.is_identity,
                        )
                      }
                    </td>

                    <td>
                      {
                        yesNo(
                          column.is_generated,
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


  function renderMappings() {
    return (
      <div className="data-table-wrapper">
        <table className="data-table mapping-table">
          <thead>
            <tr>
              <th>#</th>
              <th><Trans i18nKey="batch.tr0185" /></th>
              <th><Trans i18nKey="batch.tr0042" /></th>
              <th><Trans i18nKey="batch.tr0038" /></th>
              <th><Trans i18nKey="batch.tr0459" /></th>
              <th><Trans i18nKey="batch.tr0437" /></th>
              <th><Trans i18nKey="batch.tr0053" /></th>
            </tr>
          </thead>

          <tbody>
            {
              mappings.map(
                mapping => (
                  <tr
                    key={
                      mapping.mapping_id
                    }
                  >
                    <td>
                      {
                        mapping.mapping_position
                      }
                    </td>

                    <td>
                      <span className="badge badge-accent">
                        {
                          mapping.mapping_type
                        }
                      </span>
                    </td>

                    <td>
                      {
                        mapping.sources.length > 0
                          ? mapping.sources.map(
                            source => (
                              <div
                                className="source-ref"
                                key={
                                  `${source.source_layer}-${source.source_position}-${source.source_column}`
                                }
                              >
                                <small>
                                  {
                                    source.source_layer
                                  }
                                </small>

                                <span>
                                  {
                                    source.source_schema
                                  }.{
                                    source.source_object
                                  }.{
                                    source.source_column
                                  }
                                </span>
                              </div>
                            ),
                          )
                          : ''
                      }
                    </td>

                    <td className="code-value">
                      {
                        mapping.target_schema
                      }.{
                        mapping.target_object
                      }.{
                        mapping.target_column
                      }
                    </td>

                    <td>
                      {
                        mapping.expected_pg_type
                        ?? ''
                      }
                    </td>

                    <td className="expression">
                      {
                        mapping.transform_expression
                        ?? ''
                      }
                    </td>

                    <td>
                      v{
                        mapping.version_no
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


  function renderValidations() {
    return (
      <div className="data-table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th><Trans i18nKey="batch.tr0050" /></th>
              <th><Trans i18nKey="batch.tr0230" /></th>
              <th><Trans i18nKey="batch.tr0436" /></th>
              <th><Trans i18nKey="batch.tr0294" /></th>
              <th><Trans i18nKey="batch.tr0293" /></th>
              <th><Trans i18nKey="batch.tr0038" /></th>
              <th><Trans i18nKey="batch.tr0053" /></th>
              <th><Trans i18nKey="batch.tr0036" /></th>
            </tr>
          </thead>

          <tbody>
            {
              validations.map(
                validation => (
                  <tr
                    key={
                      validation.contract_id
                    }
                  >
                    <td className="code-value">
                      {
                        validation.source_column
                      }
                    </td>

                    <td>
                      {
                        validation.source_type
                      }
                    </td>

                    <td>
                      {
                        validation.expected_pg_type
                      }
                    </td>

                    <td>
                      {
                        yesNo(
                          validation.is_required,
                        )
                      }
                    </td>

                    <td>
                      {
                        yesNo(
                          validation.allow_blank,
                        )
                      }
                    </td>

                    <td className="code-value">
                      {
                        validation.contract_target_schema
                      }.{
                        validation.contract_target_object
                      }.{
                        validation.contract_target_column
                      }
                    </td>

                    <td>
                      v{
                        validation.contract_version
                      }
                    </td>

                    <td>
                      <span
                        className={
                          validation.contract_active
                            ? 'badge badge-success'
                            : 'badge'
                        }
                      >
                        {
                          validation.contract_active
                            ? 'ACTIVE'
                            : 'CLOSED'
                        }
                      </span>
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


  function renderDqRules() {
    return (
      <div className="data-table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th><Trans i18nKey="batch.tr0045" /></th>
              <th><Trans i18nKey="batch.tr0046" /></th>
              <th><Trans i18nKey="batch.tr0055" /></th>
              <th><Trans i18nKey="batch.tr0024" /></th>
              <th><Trans i18nKey="batch.tr0042" /></th>
              <th><Trans i18nKey="batch.tr0038" /></th>
              <th><Trans i18nKey="batch.tr0050" /></th>
            </tr>
          </thead>

          <tbody>
            {
              dqRules.map(
                rule => (
                  <tr
                    key={
                      rule.rule_id
                    }
                  >
                    <td>
                      <strong>
                        {
                          rule.rule_name
                        }
                      </strong>

                      <small className="block-muted">
                        {
                          rule.rule_code
                        }
                      </small>
                    </td>

                    <td>
                      <span
                        className={
                          rule.severity === 'ERROR'
                            ? 'badge badge-danger'
                            : 'badge badge-warning'
                        }
                      >
                        {
                          getSeverityLabel(rule.severity)
                        }
                      </span>
                    </td>

                    <td>
                      {
                        rule.category
                      }
                    </td>

                    <td>
                      <span className="badge badge-accent">
                        {
                          rule.relation_role
                        }
                      </span>
                    </td>

                    <td className="code-value">
                      {
                        rule.rule_source_object
                      }
                    </td>

                    <td className="code-value">
                      {
                        rule.rule_target_object
                      }
                    </td>

                    <td className="code-value">
                      {
                        rule.target_column
                        ?? ''
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


  function renderRuns() {
    if (runs.length === 0) {
      return (
        <div className="empty-state">
          <Trans i18nKey="batch.tr0453" />
        </div>
      )
    }

    return (
      <div className="data-table-wrapper">
        <table className="data-table history-table">
          <thead>
            <tr>
              <th><Trans i18nKey="batch.tr0076" /></th>
              <th><Trans i18nKey="batch.tr0077" /></th>
              <th><Trans i18nKey="batch.tr0075" /></th>
              <th><Trans i18nKey="batch.tr0432" /></th>
              <th><Trans i18nKey="batch.tr0443" /></th>
              <th><Trans i18nKey="batch.tr0081" /></th>
              <th><Trans i18nKey="batch.tr0082" /></th>
              <th><Trans i18nKey="batch.tr0079" /></th>
              <th><Trans i18nKey="batch.tr0072" /></th>
              <th><Trans i18nKey="batch.tr0080" /></th>
              <th><Trans i18nKey="batch.tr0078" /></th>
              <th><Trans i18nKey="batch.tr0039" /></th>
              <th><Trans i18nKey="batch.tr0048" /></th>
            </tr>
          </thead>

          <tbody>
            {
              runs.map(
                run => (
                  <tr key={run.job_run_id}>
                    <td className="code-value">
                      {run.batch_run_id}
                    </td>

                    <td>
                      {run.attempt_no ?? ''}
                    </td>

                    <td>
                      <span className="badge badge-accent">
                        {run.run_mode ?? ''}
                      </span>
                    </td>

                    <td>
                      <span
                        className={
                          statusClass(
                            run.batch_status,
                          )
                        }
                      >
                        {run.batch_status}
                      </span>
                    </td>

                    <td>
                      <span
                        className={
                          statusClass(
                            run.job_status,
                          )
                        }
                      >
                        {run.job_status}
                      </span>
                    </td>

                    <td className="code-value">
                      {
                        run.reused_from_job_run_id
                        ?? ''
                      }
                    </td>

                    <td>
                      {run.rows_read ?? 0}
                    </td>

                    <td>
                      {run.rows_inserted ?? 0}
                    </td>

                    <td>
                      {run.rows_updated ?? 0}
                    </td>

                    <td>
                      {run.rows_rejected ?? 0}
                    </td>

                    <td>
                      {run.error_count ?? 0}
                    </td>

                    <td>
                      {
                        formatDate(
                          run.finished_at,
                        )
                      }
                    </td>

                    <td className="message-cell">
                      {run.message ?? ''}
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


  function renderErrors() {
    if (errors.length === 0) {
      return (
        <div className="empty-state success-empty">
          <Trans i18nKey="batch.tr0455" />
        </div>
      )
    }

    return (
      <div className="data-table-wrapper">
        <table className="data-table history-table">
          <thead>
            <tr>
              <th><Trans i18nKey="batch.tr0468" /></th>
              <th><Trans i18nKey="batch.tr0076" /></th>
              <th><Trans i18nKey="batch.tr0094" /></th>
              <th><Trans i18nKey="batch.tr0093" /></th>
              <th><Trans i18nKey="batch.tr0465" /></th>
              <th><Trans i18nKey="batch.tr0050" /></th>
              <th><Trans i18nKey="batch.tr0049" /></th>
              <th><Trans i18nKey="batch.tr0058" /></th>
              <th><Trans i18nKey="batch.tr0435" /></th>
              <th><Trans i18nKey="batch.tr0048" /></th>
            </tr>
          </thead>

          <tbody>
            {
              errors.map(
                errorItem => (
                  <tr key={errorItem.error_id}>
                    <td>
                      {
                        formatDate(
                          errorItem.occurred_at,
                        )
                      }
                    </td>

                    <td className="code-value">
                      {
                        errorItem.batch_run_id
                        ?? ''
                      }
                    </td>

                    <td>
                      {
                        errorItem.error_stage
                        ?? ''
                      }
                    </td>

                    <td>
                      <span className="badge badge-danger">
                        {
                          errorItem.error_code
                          ?? 'ERROR'
                        }
                      </span>
                    </td>

                    <td className="code-value">
                      {
                        errorItem.source_pk
                        ?? ''
                      }
                    </td>

                    <td className="code-value">
                      {
                        errorItem.column_name
                        ?? ''
                      }
                    </td>

                    <td className="bad-value">
                      {
                        errorItem.bad_value
                        ?? ''
                      }
                    </td>

                    <td>
                      {
                        errorItem.expected_value
                        ?? ''
                      }
                    </td>

                    <td>
                      {
                        errorItem.exception_type
                        ?? ''
                      }
                    </td>

                    <td className="message-cell">
                      {
                        errorItem.error_message
                        ?? ''
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


  function renderIssues() {
    if (issues.length === 0) {
      return (
        <div className="empty-state success-empty">
          <Trans i18nKey="batch.tr0451" />
        </div>
      )
    }

    return (
      <div className="data-table-wrapper">
        <table className="data-table history-table">
          <thead>
            <tr>
              <th><Trans i18nKey="batch.tr0051" /></th>
              <th><Trans i18nKey="batch.tr0057" /></th>
              <th><Trans i18nKey="batch.tr0045" /></th>
              <th><Trans i18nKey="batch.tr0046" /></th>
              <th><Trans i18nKey="batch.tr0036" /></th>
              <th><Trans i18nKey="batch.tr0465" /></th>
              <th><Trans i18nKey="batch.tr0050" /></th>
              <th><Trans i18nKey="batch.tr0049" /></th>
              <th><Trans i18nKey="batch.tr0058" /></th>
              <th><Trans i18nKey="batch.tr0048" /></th>
              <th><Trans i18nKey="batch.tr0061" /></th>
            </tr>
          </thead>

          <tbody>
            {
              issues.map(
                issue => (
                  <tr key={issue.dq_issue_id}>
                    <td>
                      {
                        formatDate(
                          issue.created_at,
                        )
                      }
                    </td>

                    <td className="code-value">
                      {issue.dq_run_id}
                    </td>

                    <td>
                      <strong>
                        {issue.rule_name}
                      </strong>

                      <small className="block-muted">
                        {issue.rule_code}
                      </small>
                    </td>

                    <td>
                      <span
                        className={
                          issue.severity === 'ERROR'
                            ? 'badge badge-danger'
                            : 'badge badge-warning'
                        }
                      >
                        {getSeverityLabel(issue.severity)}
                      </span>
                    </td>

                    <td>
                      <span
                        className={
                          issue.issue_status === 'OPEN'
                            ? 'badge badge-danger'
                            : 'badge badge-success'
                        }
                      >
                        {issue.issue_status}
                      </span>
                    </td>

                    <td className="code-value">
                      {issue.source_pk ?? ''}
                    </td>

                    <td className="code-value">
                      {issue.column_name ?? ''}
                    </td>

                    <td className="bad-value">
                      {issue.bad_value ?? ''}
                    </td>

                    <td>
                      {issue.expected_value ?? ''}
                    </td>

                    <td className="message-cell">
                      {issue.error_message ?? ''}
                    </td>

                    <td>
                      {
                        formatDate(
                          issue.resolved_at,
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

  function renderActiveTab() {
    switch (activeTab) {
      case 'overview':
        return renderOverview()

      case 'structure':
        return renderStructure()

      case 'mapping':
        return renderMappings()

      case 'validations':
        return renderValidations()

      case 'dq':
        return renderDqRules()

      case 'runs':
        return renderRuns()

      case 'errors':
        return renderErrors()

      case 'issues':
        return renderIssues()

      default:
        return null
    }
  }


  function renderDqPage() {
    return (
      <>
        <header className="workspace-header">
          <div>
            <div className="breadcrumb">
              <Trans i18nKey="batch.tr0035" /> <LocalizedText translationKey="navigation.dq" />
            </div>

            <h1>
              <LocalizedText translationKey="navigation.dq" />
            </h1>

            <p>
              <Trans i18nKey="batch.tr0007" />
            </p>
          </div>

          <div className="header-meta">
            <span className="badge badge-accent">
              {globalDqRuns.length} RUNS
            </span>
          </div>
        </header>

        {
          error
            ? (
              <div className="error">
                <strong><Trans i18nKey="batch.tr0040" /></strong>
                <span>{error}</span>
              </div>
            )
            : null
        }

        {
          dqLoading
          && globalDqRuns.length === 0
            ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0015" />
              </div>
            )
            : (
              <>
                <div className="data-table-wrapper">
                  <table className="data-table dq-run-table">
                    <thead>
                      <tr>
                        <th><Trans i18nKey="batch.tr0044" /></th>
                        <th><Trans i18nKey="batch.tr0047" /></th>
                        <th><Trans i18nKey="batch.tr0043" /></th>
                        <th><Trans i18nKey="batch.tr0036" /></th>
                        <th><Trans i18nKey="batch.tr0054" /></th>
                        <th><Trans i18nKey="batch.tr0060" /></th>
                        <th><Trans i18nKey="batch.tr0034" /></th>
                        <th><Trans i18nKey="batch.tr0041" /></th>
                        <th><Trans i18nKey="batch.tr0059" /></th>
                        <th><Trans i18nKey="batch.tr0022" /></th>
                        <th><Trans i18nKey="batch.tr0013" /></th>
                        <th><Trans i18nKey="batch.tr0039" /></th>
                      </tr>
                    </thead>

                    <tbody>
                      {
                        globalDqRuns.map(
                          run => (
                            <tr
                              key={run.dq_run_id}
                              className={
                                selectedDqRunId
                                  === run.dq_run_id
                                  ? 'clickable-row selected-row'
                                  : 'clickable-row'
                              }
                              onClick={
                                () => {
                                  void selectDqRun(
                                    run.dq_run_id,
                                  )
                                }
                              }
                            >
                              <td className="code-value">
                                {run.dq_run_id}
                              </td>

                              <td className="code-value">
                                {
                                  run.source_batch_run_id
                                }
                              </td>

                              <td>
                                {
                                  run.business_date
                                  ?? ''
                                }
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

                              <td>
                                {run.rules_total}
                              </td>

                              <td>
                                {run.rules_passed}
                              </td>

                              <td>
                                {run.rules_warned}
                              </td>

                              <td>
                                {run.rules_failed}
                              </td>

                              <td>
                                {run.issues_total}
                              </td>

                              <td>
                                {run.issues_open}
                              </td>

                              <td>
                                {
                                  run.frozen_rule_versions
                                }
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

                <div className="section-heading">
                  <div>
                    <span><Trans i18nKey="batch.tr0029" /></span>

                    <strong>
                      {
                        selectedDqRunId
                        ?? ''
                      }
                    </strong>
                  </div>

                  <small>
                    {
                      globalDqResults.length
                    } rules
                  </small>
                </div>

                <p
                  className="block-muted"
                  style={{
                    margin: '8px 0 12px',
                  }}
                >
                  <Trans i18nKey="batch.tr0032" />
                </p>

                <div className="data-table-wrapper">
                  <table className="data-table dq-result-table">
                    <thead>
                      <tr>
                        <th><Trans i18nKey="batch.tr0045" /></th>
                        <th><Trans i18nKey="batch.tr0053" /></th>
                        <th><Trans i18nKey="batch.tr0046" /></th>
                        <th><Trans i18nKey="batch.tr0055" /></th>
                        <th><Trans i18nKey="batch.tr0050" /></th>
                        <th><Trans i18nKey="batch.tr0056" /></th>
                        <th><Trans i18nKey="batch.tr0002" /></th>
                        <th><Trans i18nKey="batch.tr0041" /></th>
                        <th><Trans i18nKey="batch.tr0059" /></th>
                        <th><Trans i18nKey="batch.tr0016" /></th>
                        <th><Trans i18nKey="batch.tr0058" /></th>
                        <th><Trans i18nKey="batch.tr0003" /></th>
                      </tr>
                    </thead>

                    <tbody>
                      {
                        globalDqResults.map(
                          result => (
                            <Fragment key={result.dq_result_id}>
                              <tr
                                className={
                                  selectedDqResultId
                                  === result.dq_result_id
                                    ? 'clickable-row selected-row'
                                    : 'clickable-row'
                                }
                                role="button"
                                tabIndex={0}
                                aria-expanded={
                                  selectedDqResultId
                                  === result.dq_result_id
                                }
                                onClick={() => {
                                  setSelectedDqResultId(current =>
                                    current === result.dq_result_id
                                      ? null
                                      : result.dq_result_id,
                                  )
                                }}
                                onKeyDown={event => {
                                  if (
                                    event.key === 'Enter'
                                    || event.key === ' '
                                  ) {
                                    event.preventDefault()

                                    setSelectedDqResultId(current =>
                                      current === result.dq_result_id
                                        ? null
                                        : result.dq_result_id,
                                    )
                                  }
                                }}
                              >
                              <td>
                                <strong>
                                  {result.rule_name}
                                </strong>

                                <small className="block-muted">
                                  {result.rule_code}
                                </small>
                              </td>

                              <td>
                                v{result.rule_version}
                              </td>

                              <td>
                                <span
                                  className={
                                    result.severity === 'ERROR'
                                      ? 'badge badge-danger'
                                      : 'badge badge-warning'
                                  }
                                >
                                  {getSeverityLabel(result.severity)}
                                </span>
                              </td>

                              <td>
                                {result.category}
                              </td>

                              <td className="code-value">
                                {
                                  result.target_column
                                  ?? ''
                                }
                              </td>

                              <td>
                                <span
                                  className={
                                    result.status === 'PASS'
                                      ? 'badge badge-success'
                                      : 'badge badge-danger'
                                  }
                                >
                                  {result.status}
                                </span>
                              </td>

                              <td>
                                {
                                  result.checked_rows
                                  ?? ''
                                }
                              </td>

                              <td>
                                {result.failed_rows}
                              </td>

                              <td>
                                {result.issue_count}
                              </td>

                              <td>
                                {
                                  result.metric_value
                                  ?? ''
                                }
                              </td>

                              <td>
                                {
                                  result.expected_value
                                  ?? ''
                                }
                              </td>

                              <td>
                                {
                                  formatDate(
                                    result.checked_at,
                                  )
                                }
                              </td>
                            </tr>
                              {
                                selectedDqResultId
                                  === result.dq_result_id
                                && selectedDqRunId
                                  === result.dq_run_id
                                  ? (
                                      <tr className="dq-rule-detail-row">
                                        <td colSpan={12}>
                                          <section
                                            aria-label="DQ rule execution details"
                                            style={{
                                              background: '#102b40',
                                              border: '1px solid #31556a',
                                              borderRadius: 10,
                                              padding: 18,
                                              margin: '8px 0',
                                            }}
                                          >
                                            <div
                                              style={{
                                                display: 'flex',
                                                justifyContent: 'space-between',
                                                alignItems: 'center',
                                                gap: 12,
                                              }}
                                            >
                                              <div>
                                                <strong>
                                                  <Trans i18nKey="batch.tr0027" />
                                                </strong>
                                                <div>
                                                  {result.rule_name}
                                                </div>
                                                <small className="block-muted">
                                                  {result.rule_code}
                                                </small>
                                              </div>

                                              <button
                                                type="button"
                                                className="admin-secondary-button"
                                                onClick={() => {
                                                  setSelectedDqResultId(null)
                                                }}
                                              >
                                                <Trans i18nKey="batch.tr0005" />
                                              </button>
                                            </div>

                                            <dl
                                              style={{
                                                display: 'grid',
                                                gridTemplateColumns:
                                                  'repeat(auto-fit, minmax(min(100%, 245px), 1fr))',
                                                gap: 12,
                                                maxWidth: 1140,
                                                margin: '18px 0 0',
                                              }}
                                            >
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0057" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >#{result.dq_run_id}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0031" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >v{result.rule_version}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0028" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >{result.rule_id}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0025" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >{result.status}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0030" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >{getSeverityLabel(result.severity)}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0055" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >{result.category}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0052" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >
                                                  {result.target_column ?? ''}
                                                </dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0004" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >
                                                  {result.checked_rows ?? ''}
                                                </dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0012" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >{result.failed_rows}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0014" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >{result.issue_count}</dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0017" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >
                                                  {result.metric_value ?? ''}
                                                </dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0011" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >
                                                  {result.expected_value ?? ''}
                                                </dd>
                                              </div>
                                              <div
                                                style={{
                                                  minWidth: 0,
                                                  padding: '12px 14px',
                                                  background: 'rgba(5, 22, 37, 0.55)',
                                                  border: '1px solid #31556a',
                                                  borderRadius: 8,
                                                  textAlign: 'left',
                                                }}
                                              >
                                                <dt
                                                  style={{
                                                    margin: '0 0 7px',
                                                    color: '#aac0cf',
                                                    fontSize: 12,
                                                    fontWeight: 500,
                                                    lineHeight: 1.4,
                                                    textAlign: 'left',
                                                  }}
                                                ><Trans i18nKey="batch.tr0003" /></dt>
                                                <dd
                                                  style={{
                                                    margin: 0,
                                                    color: '#f2f6fa',
                                                    fontSize: 14,
                                                    fontWeight: 600,
                                                    lineHeight: 1.5,
                                                    textAlign: 'left',
                                                    overflowWrap: 'anywhere',
                                                  }}
                                                >{formatDate(result.checked_at)}</dd>
                                              </div>
                                            </dl>

                                            <p
                                              className="block-muted"
                                              style={{
                                                margin: '14px 0 0',
                                                lineHeight: 1.6,
                                              }}
                                            >
                                              <strong><Trans i18nKey="batch.tr0025" /></strong>
                                              {' '}
                                              <Trans i18nKey="batch.tr0033" />
                                              {' '}
                                              <strong><Trans i18nKey="batch.tr0030" /></strong>
                                              {' '}
                                              <Trans i18nKey="batch.tr0006" />
                                            </p>

                                            <div style={{ marginTop: 18 }}>
                                              <strong><Trans i18nKey="batch.tr0010" /></strong>

                                              <pre
                                                style={{
                                                  whiteSpace: 'pre-wrap',
                                                  overflowWrap: 'anywhere',
                                                  marginTop: 8,
                                                }}
                                              >
                                                {
                                                  result.details
                                                  ?? 'No additional execution details recorded.'
                                                }
                                              </pre>
                                            </div>

                                            <div style={{ marginTop: 18 }}>
                                              <strong>
                                                <Trans i18nKey="batch.tr0023" />
                                              </strong>

                                              {
                                                globalDqIssues.filter(
                                                  issue =>
                                                    issue.dq_result_id
                                                    === result.dq_result_id,
                                                ).length === 0
                                                  ? (
                                                      <p className="block-muted">
                                                        {
                                                          result.issue_count === 0
                                                            ?<Trans i18nKey="batch.tr0019" />
                                                            :<Trans i18nKey="batch.tr0020" />
                                                        }
                                                      </p>
                                                    )
                                                  : (
                                                      <ul>
                                                        {
                                                          globalDqIssues
                                                            .filter(
                                                              issue =>
                                                                issue.dq_result_id
                                                                === result.dq_result_id,
                                                            )
                                                            .map(
                                                              issue => (
                                                                <li
                                                                  key={issue.dq_issue_id}
                                                                >
                                                                  #{issue.dq_issue_id}
                                                                  {'  '}
                                                                  {
                                                                    issue.error_message
                                                                    ?? issue.error_code
                                                                    ?? 'DQ issue'
                                                                  }
                                                                  {'  '}
                                                                  {issue.source_schema}
                                                                  .
                                                                  {issue.source_object}
                                                                </li>
                                                              ),
                                                            )
                                                        }
                                                      </ul>
                                                    )
                                              }
                                            </div>
                                          </section>
                                        </td>
                                      </tr>
                                    )
                                  : null
                              }
                            </Fragment>
                          ),
                        )
                      }
                    </tbody>
                  </table>
                </div>

                <div className="section-heading">
                  <div>
                    <span><Trans i18nKey="batch.tr0026" /></span>

                    <strong>
                      {globalDqIssues.length}
                    </strong>
                  </div>
                </div>

                {
                  globalDqIssues.length === 0
                    ? (
                      <div className="empty-state success-empty">
                        <Trans i18nKey="batch.tr0021" />
                      </div>
                    )
                    : (
                      <div className="data-table-wrapper">
                        <table className="data-table history-table">
                          <thead>
                            <tr>
                              <th><Trans i18nKey="batch.tr0045" /></th>
                              <th><Trans i18nKey="batch.tr0046" /></th>
                              <th><Trans i18nKey="batch.tr0036" /></th>
                              <th><Trans i18nKey="batch.tr0042" /></th>
                              <th>PK</th>
                              <th><Trans i18nKey="batch.tr0001" /></th>
                              <th><Trans i18nKey="batch.tr0050" /></th>
                              <th><Trans i18nKey="batch.tr0049" /></th>
                              <th><Trans i18nKey="batch.tr0058" /></th>
                              <th><Trans i18nKey="batch.tr0048" /></th>
                              <th><Trans i18nKey="batch.tr0051" /></th>
                              <th><Trans i18nKey="batch.tr0061" /></th>
                            </tr>
                          </thead>

                          <tbody>
                            {
                              globalDqIssues.map(
                                issue => (
                                  <tr key={issue.dq_issue_id}>
                                    <td>
                                      <strong>
                                        {issue.rule_name}
                                      </strong>

                                      <small className="block-muted">
                                        {issue.rule_code}
                                      </small>
                                    </td>

                                    <td>
                                      <span
                                        className={
                                          issue.severity === 'ERROR'
                                            ? 'badge badge-danger'
                                            : 'badge badge-warning'
                                        }
                                      >
                                        {getSeverityLabel(issue.severity)}
                                      </span>
                                    </td>

                                    <td>
                                      {issue.issue_status}
                                    </td>

                                    <td className="code-value">
                                      {issue.source_schema}.{
                                        issue.source_object
                                      }
                                    </td>

                                    <td className="code-value">
                                      {issue.source_pk ?? ''}
                                    </td>

                                    <td className="code-value">
                                      {
                                        issue.application_id
                                        ?? ''
                                      }
                                    </td>

                                    <td className="code-value">
                                      {
                                        issue.column_name
                                        ?? ''
                                      }
                                    </td>

                                    <td className="bad-value">
                                      {
                                        issue.bad_value
                                        ?? ''
                                      }
                                    </td>

                                    <td>
                                      {
                                        issue.expected_value
                                        ?? ''
                                      }
                                    </td>

                                    <td className="message-cell">
                                      {
                                        issue.error_message
                                        ?? ''
                                      }
                                    </td>

                                    <td>
                                      {
                                        formatDate(
                                          issue.created_at,
                                        )
                                      }
                                    </td>

                                    <td>
                                      {
                                        formatDate(
                                          issue.resolved_at,
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
              </>
            )
        }
      </>
    )
  }

  async function selectReportingPage() {
    setActivePage('reporting')

    // Every entry into Reporting starts collapsed.
    setOpenReportingSections([])

    // Reset report navigation.
    setReportingSearch('')
    setReportingSearchIndex(0)
    setHighlightedReport(null)
    setExportMenuReport(null)

    setReportingLoading(true)
    setReportingError(null)

    try {
      const data =
        await getRetailReportingOverview()

      setReportingOverview(data)
    } catch (err) {
      setReportingError(
        err instanceof Error
          ? err.message
          : 'Failed to load reporting data',
      )
    } finally {
      setReportingLoading(false)
    }
  }


  function getReportingSections(): Array<{
    key: ReportKey
    title: string
    items: RetailReportingOverview['by_status']
  }> {
    if (!reportingOverview) {
      return []
    }

    return [
      {
        key: 'status',
        title: 'Status',
        items: reportingOverview.by_status,
      },
      {
        key: 'stage',
        title: 'Stage',
        items: reportingOverview.by_stage,
      },
      {
        key: 'channel',
        title: 'Sales channel',
        items: reportingOverview.by_channel,
      },
      {
        key: 'product',
        title: 'Bank pre-approved product',
        items: reportingOverview.by_product,
      },
    ]
  }


  function getReportingSearchMatches() {
    const query =
      reportingSearch
        .trim()
        .toLocaleLowerCase()

    // Search starts only after at least
    // three non-space characters are entered.
    if (query.length < 3) {
      return []
    }

    return getReportingSections().filter(
      section =>
        section.title
          .toLocaleLowerCase()
          .includes(query),
    )
  }


  function toggleReportingSection(
    reportKey: ReportKey,
  ) {
    setOpenReportingSections(
      current =>
        current.includes(reportKey)
          ? current.filter(
              key => key !== reportKey,
            )
          : [
              ...current,
              reportKey,
            ],
    )
  }


  function goToNextReportingMatch() {
    const matches =
      getReportingSearchMatches()

    if (matches.length === 0) {
      return
    }

    const targetIndex =
      reportingSearchIndex
      % matches.length

    const target =
      matches[targetIndex]

    setReportingSearchIndex(
      (targetIndex + 1)
      % matches.length,
    )

    // Remove the class first so the animation
    // can be restarted even for the same report.
    setHighlightedReport(null)

    window.setTimeout(
      () => {
        const element =
          document.getElementById(
            `report-${target.key}`,
          )

        element?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        })

        setHighlightedReport(
          target.key,
        )
      },
      30,
    )
  }


  async function exportReportingSection(
    reportKey: ReportKey,
    fileFormat: 'xlsx' | 'pdf' | 'txt',
  ) {
    setExportMenuReport(null)
    setExportingReport(reportKey)
    setReportingError(null)

    try {
      await downloadRetailReport(
        reportKey,
        fileFormat,
      )
    } catch (err) {
      setReportingError(
        err instanceof Error
          ? err.message
          : 'Failed to export report',
      )
    } finally {
      setExportingReport(null)
    }
  }


  function renderReportingDimension(
    reportKey: ReportKey,
    title: string,
    items: RetailReportingOverview['by_status'],
  ) {
    const showCode =
      items.some(
        item => item.code !== null,
      )

    const isOpen =
      openReportingSections.includes(
        reportKey,
      )

    const isHighlighted =
      highlightedReport === reportKey

    const exportMenuOpen =
      exportMenuReport === reportKey

    const isExporting =
      exportingReport === reportKey

    return (
      <section
        id={`report-${reportKey}`}
        className={
          [
            'reporting-panel',

            isHighlighted
              ? 'reporting-search-hit'
              : '',

            exportMenuOpen
              ? 'reporting-export-open'
              : '',
          ]
            .filter(Boolean)
            .join(' ')
        }
      >
        <div className="reporting-panel-header-row">

          <button
            type="button"
            className="reporting-panel-toggle"
            aria-expanded={isOpen}
            onClick={
              () =>
                toggleReportingSection(
                  reportKey,
                )
            }
          >
            <div className="reporting-panel-title">
              <h2>
                {title}
              </h2>
            </div>
          </button>


          <div className="reporting-panel-actions">

            <span className="reporting-record-count">
              {items.length} <Trans i18nKey="batch.tr0088" />
            </span>


            <div className="reporting-export-wrap">

              <button
                type="button"
                className="reporting-export-button"
                disabled={isExporting}
                aria-expanded={exportMenuOpen}
                aria-haspopup="menu"
                onClick={
                  () => {
                    setExportMenuReport(
                      current =>
                        current === reportKey
                          ? null
                          : reportKey,
                    )
                  }
                }
              >
                <span
                  className="reporting-export-icon"
                  aria-hidden="true"
                >
                  
                </span>

                {
                  isExporting
                    ? t('reportingPage.exportingLabel')
                    : t('reportingPage.exportLabel')
                }

                <span
                  className="reporting-export-caret"
                  aria-hidden="true"
                >
                  ▾
                </span>
              </button>


              {
                exportMenuOpen
                  ? (
                    <div
                      className="reporting-export-menu"
                      role="menu"
                    >
                      <button
                        type="button"
                        role="menuitem"
                        onClick={
                          () => {
                            void exportReportingSection(
                              reportKey,
                              'xlsx',
                            )
                          }
                        }
                      >
                        <strong>
                          Excel
                        </strong>

                        <span>
                          .xlsx
                        </span>
                      </button>

                      <button
                        type="button"
                        role="menuitem"
                        onClick={
                          () => {
                            void exportReportingSection(
                              reportKey,
                              'pdf',
                            )
                          }
                        }
                      >
                        <strong>
                          PDF
                        </strong>

                        <span>
                          .pdf
                        </span>
                      </button>

                      <button
                        type="button"
                        role="menuitem"
                        onClick={
                          () => {
                            void exportReportingSection(
                              reportKey,
                              'txt',
                            )
                          }
                        }
                      >
                        <strong>
                          <Trans i18nKey="batch.tr0091" />
                        </strong>

                        <span>
                          .txt
                        </span>
                      </button>
                    </div>
                  )
                  : null
              }

            </div>


            <button
              type="button"
              className="reporting-chevron-button"
              aria-label={
                isOpen
                  ? `Collapse ${title}`
                  : `Expand ${title}`
              }
              aria-expanded={isOpen}
              onClick={
                () =>
                  toggleReportingSection(
                    reportKey,
                  )
              }
            >
              <span
                className={
                  isOpen
                    ? 'reporting-chevron open'
                    : 'reporting-chevron'
                }
                aria-hidden="true"
              >
                {isOpen ? '-' : '+'}
              </span>
            </button>

          </div>
        </div>


        {
          isOpen
            ? (
              <div className="reporting-panel-body">
                <div className="data-table-wrapper">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>ID</th>

                        {
                          showCode
                            ? <th><Trans i18nKey="batch.tr0093" /></th>
                            : null
                        }

                        <th><Trans i18nKey="batch.tr0092" /></th>
                        <th><Trans i18nKey="batch.tr0083" /></th>
                      </tr>
                    </thead>

                    <tbody>
                      {
                        items.map(
                          item => (
                            <tr
                              key={
                                [
                                  title,
                                  item.id ?? '',
                                  item.code ?? '',
                                ].join('-')
                              }
                            >
                              <td className="code-value">
                                {item.id ?? ''}
                              </td>

                              {
                                showCode
                                  ? (
                                    <td className="code-value">
                                      {item.code ?? ''}
                                    </td>
                                  )
                                  : null
                              }

                              <td>
                                {item.name ?? ''}
                              </td>

                              <td>
                                {item.applications}
                              </td>
                            </tr>
                          ),
                        )
                      }
                    </tbody>
                  </table>
                </div>
              </div>
            )
            : null
        }
      </section>
    )
  }

  function renderReportingPage() {
    const sections =
      getReportingSections()

    const searchMatches =
      getReportingSearchMatches()

    return (
      <div className="reporting-page">

        <div className="reporting-sticky-top">

          <header className="workspace-header">
            <div>
              <div className="breadcrumb">
                <Trans i18nKey="batch.tr0035" /> <LocalizedText translationKey="navigation.reporting" />
              </div>

              <h1>
                <LocalizedText translationKey="reportingPage.title" />
              </h1>

              <p>
                <Trans i18nKey="batch.tr0085" />
              </p>
            </div>

            {
              reportingOverview
                ? (
                  <div className="header-meta">
                    <span className="badge badge-accent">
                      {
                        reportingOverview.applications
                      } APPLICATIONS
                    </span>
                  </div>
                )
                : null
            }
          </header>


          {
            reportingError
              ? (
                <div className="error">
                  <strong>
                    <Trans i18nKey="batch.tr0089" />
                  </strong>

                  <span>
                    {reportingError}
                  </span>
                </div>
              )
              : null
          }


          {
            reportingOverview
              ? (
                <>
                  <section className="reporting-kpi-grid">

                    <article className="reporting-kpi-card">
                      <span className="reporting-kpi-label">
                        {t('reportingPage.kpiApplications')}
                      </span>

                      <strong className="reporting-kpi-value">
                        {
                          reportingOverview.applications
                        }
                      </strong>

                      <small>
                        {t('reportingPage.kpiSnapshot')}
                      </small>
                    </article>


                    <article className="reporting-kpi-card">
                      <span className="reporting-kpi-label">
                        {t('reportingPage.kpiPreapproved')}
                      </span>

                      <strong className="reporting-kpi-value">
                        {
                          reportingOverview
                            .requested_amount_rows
                        }
                      </strong>

                      <small>
                        {t('reportingPage.kpiOfApplications', {
                          total: reportingOverview.applications,
                        })}
                      </small>
                    </article>


                    <article className="reporting-kpi-card">
                      <span className="reporting-kpi-label">
                        {t('reportingPage.kpiSelected')}
                      </span>

                      <strong className="reporting-kpi-value">
                        {
                          reportingOverview
                            .chosen_amount_rows
                        }
                      </strong>

                      <small>
                        {t('reportingPage.kpiOfApplications', {
                          total: reportingOverview.applications,
                        })}
                      </small>
                    </article>


                    <article className="reporting-kpi-card">
                      <span className="reporting-kpi-label">
                        {t('reportingPage.kpiFinal')}
                      </span>

                      <strong className="reporting-kpi-value">
                        {
                          reportingOverview
                            .final_amount_rows
                        }
                      </strong>

                      <small>
                        {t('reportingPage.kpiOfApplications', {
                          total: reportingOverview.applications,
                        })}
                      </small>
                    </article>

                  </section>


                  <div className="reporting-search-bar">

                    <label className="reporting-search-field">
                      <span>
                        {t('reportingPage.searchLabel')}
                      </span>

                      <input
                        type="text"
                        value={reportingSearch}
                        placeholder={t('reportingPage.searchPlaceholder')}
                        onChange={
                          event => {
                            setReportingSearch(
                              event.target.value,
                            )

                            setReportingSearchIndex(0)
                            setHighlightedReport(null)
                          }
                        }
                        onKeyDown={
                          event => {
                            if (event.key === 'Enter') {
                              goToNextReportingMatch()
                            }
                          }
                        }
                      />
                    </label>


                    <div className="reporting-search-count">
                      {t('reportingPage.searchFound')}
                      {' '}
                      <strong>
                        {searchMatches.length}
                      </strong>
                    </div>


                    <button
                      type="button"
                      className="reporting-search-next"
                      disabled={
                        searchMatches.length === 0
                      }
                      onClick={
                        goToNextReportingMatch
                      }
                    >
                      {t('reportingPage.searchNext')}
                    </button>

                  </div>
                </>
              )
              : null
          }

        </div>


        {
          reportingLoading
          && reportingOverview === null
            ? (
              <div className="loading-panel">
                <Trans i18nKey="batch.tr0087" />
              </div>
            )
            : null
        }


        {
          reportingOverview
            ? (
              <div className="reporting-reports">

                <div className="reporting-grid">
                  {
                    sections.map(
                      section =>
                        renderReportingDimension(
                          section.key,
                          section.title,
                          section.items,
                        ),
                    )
                  }
                </div>

              </div>
            )
            : null
        }

      </div>
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
            DWH
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
            canIntegration
              ? (
                <button
                  type="button"
                  className={
                    activePage === 'integrations'
                      ? 'nav-item active'
                      : 'nav-item'
                  }
                  onClick={
                    () =>
                      setActivePage(
                        'integrations',
                      )
                  }
                >
                  <LocalizedText translationKey="navigation.integrations" />
                </button>
              )
              : null
          }

          {
            canEtl
              ? (
                <button
                  type="button"
                  className={
                    activePage === 'etl'
                      ? 'nav-item active'
                      : 'nav-item'
                  }
                  onClick={
                    () =>
                      setActivePage(
                        'etl',
                      )
                  }
                >
                  <LocalizedText translationKey="navigation.etl" />
                </button>
              )
              : null
          }

          {
            canDq
              ? (
                <button
                  type="button"
                  className={
                    activePage === 'dq'
                      ? 'nav-item active'
                      : 'nav-item'
                  }
                  onClick={
                    () =>
                      setActivePage(
                        'dq',
                      )
                  }
                >
                  <LocalizedText translationKey="navigation.dq" />
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
                      void selectReportingPage()
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

        <div
          className={
            activePage === 'integrations'
              ? 'sidebar-integration-area'
              : 'sidebar-integration-area hidden'
          }
        >
        <div className="sidebar-section-title">
          <LocalizedText translationKey="sidebar.integrations" />
          <span>
            {
              integrations.length
            }
          </span>
        </div>

        <div className="integration-list">
          {
            integrations.map(
              integration => (
                <button
                  type="button"
                  key={
                    integration.source_object_id
                  }
                  className={
                    integration.source_object_id
                      === selectedId
                      ? 'integration-item selected'
                      : 'integration-item'
                  }
                  onClick={
                    () =>
                      selectIntegration(
                        integration.source_object_id,
                      )
                  }
                >
                  <span className="integration-name">
                    {
                      integration.source_object
                    }
                  </span>

                  <small>
                    {
                      integration.load_strategy
                    }
                  </small>
                </button>
              ),
            )
          }
        </div>

        </div>

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
          activePage === 'etl'
            ? renderEtlPage()
            : activePage === 'dq'
              ? renderDqPage()
              : activePage === 'reporting'
                ? renderReportingPage()
                : activePage === 'administration'
                  ? <AdminPage />
                  : (
              <>
        <header className="workspace-header">
          <div>
            <div className="breadcrumb">
              <Trans i18nKey="batch.tr0434" />
            </div>

            <h1>
              {
                selectedIntegration?.source_object
                ?? 'Integration Control'
              }
            </h1>

            <p>
              {
                selectedIntegration?.description
                ?? 'Select integration object'
              }
            </p>
          </div>

          {
            selectedIntegration
              ? (
                <div className="header-meta">
                  <span
                    className={
                      selectedIntegration.integration_active
                        ? 'badge badge-success'
                        : 'badge'
                    }
                  >
                    {
                      selectedIntegration.integration_active
                        ? 'ACTIVE'
                        : 'DISABLED'
                    }
                  </span>

                  <span className="header-object-type">
                    {
                      selectedIntegration.object_type
                    }
                  </span>
                </div>
              )
              : null
          }
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


        <div className="tabs">
          {
            tabs.map(
              tab => (
                <button
                  type="button"
                  key={
                    tab.key
                  }
                  className={
                    activeTab === tab.key
                      ? 'tab active'
                      : 'tab'
                  }
                  onClick={
                    () =>
                      setActiveTab(
                        tab.key,
                      )
                  }
                >
                  {
                    t(tab.labelKey)
                  }
                </button>
              ),
            )
          }
        </div>


        <section className="content-panel">
          {
            detailLoading
              ? (
                <div className="loading-panel">
                  <Trans i18nKey="batch.tr0447" />
                </div>
              )
              : renderActiveTab()
          }
        </section>
              </>
            )
        }
      </main>
    </div>
  )
}

export default App