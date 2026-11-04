import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  confirmAction,
} from './confirmAction'
import {
  formatDateTime,
} from './dateTime'
import {
  getSourceConnection,
  revokeSourceCredential,
  saveSourceConnection,
  setSourceConnectionStatus,
  setSourceCredential,
} from './integrationAdminApi'
import type {
  IntegrationSourceSystem,
  SourceConnection,
  SourceConnectionKind,
} from './integrationAdminApi'


interface SourceConnectionPanelProps {
  system: IntegrationSourceSystem
  onClose: () => void
}


// Non-secret settings per kind; the credential is entered separately and is
// never part of connection_config.
const fieldsByKind: Record<SourceConnectionKind, string[]> = {
  DATABASE: ['host', 'port', 'database', 'username', 'sslmode'],
  FILE: ['folder', 'file_mask', 'username'],
}


// PostgreSQL sslmode values; empty means the driver default.
const SSL_MODES = ['', 'disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full']


function textValue(
  value: unknown,
): string {
  return value === undefined || value === null
    ? ''
    : String(value)
}


function SourceConnectionPanel({
  system,
  onClose,
}: SourceConnectionPanelProps) {
  const { t } = useTranslation()

  const isApiSource =
    system.source_type.trim().toUpperCase() === 'API'

  const [
    connection,
    setConnection,
  ] = useState<SourceConnection | null>(null)

  const [
    loading,
    setLoading,
  ] = useState(!isApiSource)

  const [
    busy,
    setBusy,
  ] = useState(false)

  const [
    error,
    setError,
  ] = useState<string | null>(null)

  const [
    notice,
    setNotice,
  ] = useState<string | null>(null)

  const [
    kind,
    setKind,
  ] = useState<SourceConnectionKind>(
    system.source_type.trim().toUpperCase() === 'FILE'
      ? 'FILE'
      : 'DATABASE',
  )

  const [
    fields,
    setFields,
  ] = useState<Record<string, string>>({})

  const [
    credential,
    setCredential,
  ] = useState('')

  // Responses for a previously selected system or an older refresh are ignored.
  const requestSequence = useRef(0)

  const applyConnection = useCallback(
    (value: SourceConnection | null) => {
      setConnection(value)

      if (value !== null) {
        setKind(value.source_kind)
        setFields(
          Object.fromEntries(
            fieldsByKind[value.source_kind].map(
              name => [
                name,
                textValue(value.connection_config[name]),
              ],
            ),
          ),
        )
      }
    },
    [],
  )

  useEffect(
    () => {
      if (isApiSource) {
        return undefined
      }

      const sequence = ++requestSequence.current

      void getSourceConnection(
        system.source_system_id,
      )
        .then(value => {
          if (sequence === requestSequence.current) {
            applyConnection(value)
          }
        })
        .catch(reason => {
          if (sequence === requestSequence.current) {
            setError(
              reason instanceof Error
                ? reason.message
                : String(reason),
            )
          }
        })
        .finally(() => {
          if (sequence === requestSequence.current) {
            setLoading(false)
          }
        })

      return () => {
        requestSequence.current += 1
      }
    },
    [
      applyConnection,
      isApiSource,
      system.source_system_id,
    ],
  )

  async function refresh() {
    const sequence = ++requestSequence.current
    const value =
      await getSourceConnection(
        system.source_system_id,
      )

    if (sequence === requestSequence.current) {
      applyConnection(value)
    }
  }

  async function run(
    action: () => Promise<unknown>,
    success: string,
  ) {
    setBusy(true)
    setError(null)
    setNotice(null)

    try {
      await action()
      setNotice(success)
      await refresh()
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : String(reason),
      )
    } finally {
      setBusy(false)
    }
  }

  function buildConfig(): Record<string, unknown> {
    const config: Record<string, unknown> = {}

    for (const name of fieldsByKind[kind]) {
      const value = (fields[name] ?? '').trim()

      if (value === '') {
        continue
      }

      config[name] =
        name === 'port' && /^\d+$/.test(value)
          ? Number(value)
          : value
    }

    return config
  }

  async function submitSettings(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    await run(
      () =>
        saveSourceConnection(
          system.source_system_id,
          {
            source_kind: kind,
            connection_config: buildConfig(),
            expected_row_version:
              connection?.row_version ?? null,
          },
        ),
      t('sourceConnection.saved'),
    )
  }

  async function submitCredential(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()

    const value = credential

    // The value leaves the component state before the request is sent.
    setCredential('')

    await run(
      () =>
        setSourceCredential(
          system.source_system_id,
          value,
        ),
      t('sourceConnection.credentialSaved'),
    )
  }

  async function revokeCredential() {
    const confirmed =
      await confirmAction({
        title: t('sourceConnection.revokeTitle'),
        message: t('sourceConnection.revokeMessage', { code: system.code }),
        confirmLabel: t('sourceConnection.revoke'),
        cancelLabel: t('sourceConnection.cancel'),
      })

    if (!confirmed) {
      return
    }

    await run(
      () =>
        revokeSourceCredential(
          system.source_system_id,
        ),
      t('sourceConnection.credentialRevoked'),
    )
  }

  async function toggleStatus() {
    if (connection === null) {
      return
    }

    await run(
      () =>
        setSourceConnectionStatus(
          system.source_system_id,
          connection.status === 'DISABLED'
            ? 'DRAFT'
            : 'DISABLED',
          connection.row_version,
        ),
      t('sourceConnection.statusChanged'),
    )
  }

  const credentialState =
    connection?.credential_state ?? 'NONE'

  // Save is enabled only when the settings differ from the saved ones
  // (new connection: when at least one field is filled).
  const settingsChanged =
    connection === null
      ? fieldsByKind[kind].some(name => (fields[name] ?? '').trim() !== '')
      : fieldsByKind[kind].some(
          name =>
            (fields[name] ?? '').trim()
            !== textValue(connection.connection_config[name]),
        )

  return (
    <section className="admin-integrations-editor">
      <h3>
        {t('sourceConnection.title', { code: system.code })}
      </h3>

      {
        isApiSource
          ? (
            <p>{t('sourceConnection.apiNote')}</p>
          )
          : loading
            ? (
              <div className="loading-panel">
                {t('sourceConnection.loading')}
              </div>
            )
            : (
              <>
                <p>
                  {t('sourceConnection.statusLabel')}
                  {' '}
                  <span className="badge">
                    {connection?.status ?? t('sourceConnection.notConfigured')}
                  </span>
                  {' '}
                  {t('sourceConnection.credentialLabel')}
                  {' '}
                  <span
                    className={
                      credentialState === 'ACTIVE'
                        ? 'badge badge-success'
                        : 'badge'
                    }
                  >
                    {t(`sourceConnection.credential${credentialState}`)}
                  </span>
                  {
                    connection?.credential_activated_at
                      ? ` ${formatDateTime(connection.credential_activated_at)}`
                      : ''
                  }
                  {
                    credentialState === 'ACTIVE' && connection?.credential_key_id
                      ? ` · ${connection.credential_key_id}`
                      : ''
                  }
                </p>

                <form
                  className="admin-integrations-form"
                  onSubmit={submitSettings}
                >
                  <label>
                    <span>{t('sourceConnection.kind')}</span>
                    <select
                      value={kind}
                      disabled={connection !== null || busy}
                      onChange={
                        event =>
                          setKind(
                            event.target.value as SourceConnectionKind,
                          )
                      }
                    >
                      <option value="DATABASE">{t('sourceConnection.kindDATABASE')}</option>
                      <option value="FILE">{t('sourceConnection.kindFILE')}</option>
                    </select>
                  </label>

                  {
                    fieldsByKind[kind].map(
                      name => (
                        <label key={name}>
                          <span>{t(`sourceConnection.field.${name}`)}</span>
                          {
                            name === 'sslmode'
                              ? (
                                <select
                                  value={fields[name] ?? ''}
                                  onChange={
                                    event =>
                                      setFields(
                                        current => ({
                                          ...current,
                                          [name]: event.target.value,
                                        }),
                                      )
                                  }
                                >
                                  {
                                    (
                                      SSL_MODES.includes(fields[name] ?? '')
                                        ? SSL_MODES
                                        : [fields[name] ?? '', ...SSL_MODES]
                                    ).map(
                                      value => (
                                        <option key={value} value={value}>
                                          {value === '' ? t('sourceConnection.sslDefault') : value}
                                        </option>
                                      ),
                                    )
                                  }
                                </select>
                              )
                              : (
                                <input
                                  value={fields[name] ?? ''}
                                  inputMode={name === 'port' ? 'numeric' : undefined}
                                  onChange={
                                    event =>
                                      setFields(
                                        current => ({
                                          ...current,
                                          [name]: event.target.value,
                                        }),
                                      )
                                  }
                                />
                              )
                          }
                        </label>
                      ),
                    )
                  }

                  <div className="admin-integrations-form-actions wide">
                    <button
                      type="submit"
                      className="admin-primary-button"
                      disabled={busy || !settingsChanged}
                    >
                      {t('sourceConnection.saveSettings')}
                    </button>

                    {
                      connection !== null && (
                        <button
                          type="button"
                          className="admin-secondary-button"
                          disabled={busy}
                          onClick={() => { void toggleStatus() }}
                        >
                          {
                            connection.status === 'DISABLED'
                              ? t('sourceConnection.enable')
                              : t('sourceConnection.disable')
                          }
                        </button>
                      )
                    }

                    <button
                      type="button"
                      className="admin-link-button"
                      onClick={onClose}
                    >
                      {t('sourceConnection.close')}
                    </button>
                  </div>
                </form>

                {
                  connection !== null && (
                    <form
                      className="admin-integrations-form"
                      onSubmit={submitCredential}
                      autoComplete="off"
                    >
                      <label className="wide">
                        <span>{t('sourceConnection.credentialInput')}</span>
                        <input
                          type="password"
                          autoComplete="new-password"
                          maxLength={4096}
                          value={credential}
                          onChange={
                            event =>
                              setCredential(
                                event.target.value,
                              )
                          }
                        />
                      </label>

                      <div className="admin-integrations-form-actions wide">
                        <button
                          type="submit"
                          className="admin-primary-button"
                          disabled={busy || credential === '' || connection.status === 'DISABLED'}
                        >
                          {
                            credentialState === 'ACTIVE'
                              ? t('sourceConnection.replaceCredential')
                              : t('sourceConnection.setCredential')
                          }
                        </button>

                        {
                          credentialState === 'ACTIVE' && (
                            <button
                              type="button"
                              className="admin-secondary-button"
                              disabled={busy}
                              onClick={() => { void revokeCredential() }}
                            >
                              {t('sourceConnection.revoke')}
                            </button>
                          )
                        }
                      </div>
                    </form>
                  )
                }
              </>
            )
      }

      {
        isApiSource && (
          <div className="admin-integrations-form-actions">
            <button
              type="button"
              className="admin-link-button"
              onClick={onClose}
            >
              {t('sourceConnection.close')}
            </button>
          </div>
        )
      }

      {
        notice && (
          <p>{notice}</p>
        )
      }

      {
        error && (
          <div className="error">
            <strong>{t('sourceConnection.error')}</strong>
            {' '}
            {error}
          </div>
        )
      }
    </section>
  )
}


export default SourceConnectionPanel
