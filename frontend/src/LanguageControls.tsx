import { useTranslation } from 'react-i18next'

type SidebarTextKey =
  | 'navigation.integrations'
  | 'navigation.etl'
  | 'navigation.dq'
  | 'navigation.loads'
  | 'navigation.reporting'
  | 'navigation.administration'
  | 'sidebar.integrations'
  | 'sidebar.signOut'
  | 'reportingPage.title'

interface LocalizedTextProps {
  translationKey: SidebarTextKey
}

export function LocalizedText({
  translationKey,
}: LocalizedTextProps) {
  const { t } = useTranslation()

  return <>{t(translationKey)}</>
}

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()

  const language = i18n.resolvedLanguage ?? i18n.language

  const selectedLanguage =
    language === 'ru' || language === 'az'
      ? language
      : 'en'

  return (
    <div className="sidebar-language">
      <label htmlFor="dwh-sidebar-language">
        {t('sidebar.language')}
      </label>

      <select
        id="dwh-sidebar-language"
        value={selectedLanguage}
        onChange={event => {
          void i18n.changeLanguage(event.currentTarget.value)
        }}
      >
        <option value="ru">Русский</option>
        <option value="en">English</option>
        <option value="az">Azərbaycanca</option>
      </select>
    </div>
  )
}
