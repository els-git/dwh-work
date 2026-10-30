import { Trans } from 'react-i18next'
import {
  useState,
} from 'react'

import AdminAccessRolePolicies
  from './AdminAccessRolePolicies'

import AdminAccessUserAssignments
  from './AdminAccessUserAssignments'

import './AdminPage.css'


type AccessMode =
  | 'roles'
  | 'users'


function AdminAccessPage() {
  const [
    mode,
    setMode,
  ] = useState<AccessMode>('roles')


  return (
    <div>
      <nav className="admin-shell-nav">
        <button
          type="button"
          className={
            mode === 'roles'
              ? 'admin-shell-tab active'
              : 'admin-shell-tab'
          }
          onClick={
            () =>
              setMode('roles')
          }
        >
          <Trans i18nKey="batch.tr0167" />
        </button>

        <button
          type="button"
          className={
            mode === 'users'
              ? 'admin-shell-tab active'
              : 'admin-shell-tab'
          }
          onClick={
            () =>
              setMode('users')
          }
        >
          <Trans i18nKey="batch.tr0182" />
        </button>
      </nav>

      {
        mode === 'roles'
          ? (
              <AdminAccessRolePolicies />
            )
          : (
              <AdminAccessUserAssignments />
            )
      }
    </div>
  )
}


export default AdminAccessPage