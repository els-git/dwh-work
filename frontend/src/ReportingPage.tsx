import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import MartViewPanel from './MartViewPanel'
import SavedReportsPanel from './SavedReportsPanel'


// "Reporting": saved reports of the assistant (I17-09) and the marts of the role (I22).
type ReportingTab = 'saved' | 'marts'


function ReportingPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<ReportingTab>('saved')

  return (
    <>
      <div className="tabs">
        {(['saved', 'marts'] as ReportingTab[]).map(item => (
          <button type="button" key={item} className={tab === item ? 'tab active' : 'tab'} onClick={() => setTab(item)}>
            {item === 'saved' ? t('savedReports.tab') : t('martView.tab')}
          </button>
        ))}
      </div>
      {tab === 'saved' ? <SavedReportsPanel /> : <MartViewPanel />}
    </>
  )
}


export default ReportingPage
