import { Trans } from 'react-i18next'
import { useState } from 'react'

import { useAuth } from './authContext'
import AdminModulesPage from './AdminModulesPage'
import AdminUsersPage from './AdminUsersPage'
import AdminRolesPage from './AdminRolesPage'
import AdminAccessPage from './AdminAccessPage'
import AdminIntegrationsPage from './AdminIntegrationsPage'

import './AdminPage.css'


type AdminSection =
  | 'modules'
  | 'users'
  | 'roles'
  | 'access'
  | 'integrations'


interface AdminSectionDefinition {
  key: AdminSection
  labelKey: string
  permission: string
  description: string
}


const adminSections: AdminSectionDefinition[] = [
  {
    key: 'modules',
    labelKey: 'adminTabs.modules',
    permission: 'ADMIN_MODULE_MANAGE',
    description:
      'Business module catalog and module lifecycle management.',
  },
  {
    key: 'users',
    labelKey: 'adminTabs.users',
    permission: 'ADMIN_USER_MANAGE',
    description:
      'User accounts, status and authentication administration.',
  },
  {
    key: 'roles',
    labelKey: 'adminTabs.roles',
    permission: 'ADMIN_ROLE_MANAGE',
    description:
      'Business roles and role membership administration.',
  },
  {
    key: 'access',
    labelKey: 'adminTabs.access',
    permission: 'ADMIN_ACCESS_MANAGE',
    description:
      'Permission and resource access policy administration.',
  },
  {
    key: 'integrations',
    labelKey: 'adminTabs.integrations',
    permission: 'ADMIN_INTEGRATION_MANAGE',
    description:
      'Source systems, source objects and integration configuration.',
  },
]


function AdminPage() {
  const {
    hasPermission,
  } = useAuth()

  const availableSections =
    adminSections.filter(
      section =>
        hasPermission(
          section.permission,
        ),
    )

  const [
    activeSection,
    setActiveSection,
  ] = useState<AdminSection>('modules')


  if (availableSections.length === 0) {
    return (
      <div className="admin-no-access">
        <Trans i18nKey="batch.tr0125" />
      </div>
    )
  }


  const activeDefinition =
    availableSections.find(
      section =>
        section.key === activeSection,
    )
    ?? availableSections[0]


  function renderSection() {
    if (
      activeDefinition.key
      === 'modules'
    ) {
      return (
        <AdminModulesPage />
      )
    }

    if (
      activeDefinition.key
      === 'users'
    ) {
      return (
        <AdminUsersPage />
      )
    }

    if (
      activeDefinition.key
      === 'roles'
    ) {
      return (
        <AdminRolesPage />
      )
    }

    if (
      activeDefinition.key
      === 'access'
    ) {
      return (
        <AdminAccessPage />
      )
    }

    if (
      activeDefinition.key
      === 'integrations'
    ) {
      return (
        <AdminIntegrationsPage />
      )
    }

    return null
  }


  return (
    <div className="admin-shell">
      <div className="admin-shell-title-row">
        <div>
          <div className="breadcrumb">
            <Trans i18nKey="batch.tr0128" />
          </div>

          <div className="admin-shell-title">
            <Trans i18nKey="batch.tr0124" />
          </div>
        </div>

        <span className="badge badge-accent">
          {
            availableSections.length
          } SECTIONS
        </span>
      </div>

      <nav className="admin-shell-nav">
        {
          availableSections.map(
            section => (
              <button
                type="button"
                key={section.key}
                className={
                  activeDefinition.key
                  === section.key
                    ? 'admin-shell-tab active'
                    : 'admin-shell-tab'
                }
                onClick={
                  () =>
                    setActiveSection(
                      section.key,
                    )
                }
              >
                <Trans i18nKey={section.labelKey} />
              </button>
            ),
          )
        }
      </nav>

      <div className="admin-shell-content">
        {renderSection()}
      </div>
    </div>
  )
}


export default AdminPage