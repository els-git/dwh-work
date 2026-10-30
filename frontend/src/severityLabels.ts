import { useTranslation } from 'react-i18next'

export function useSeverityLabel() {
  const { t } = useTranslation()

  return (severityCode: string): string => {
    if (severityCode === 'ERROR') {
      return t('severity.ERROR')
    }

    if (severityCode === 'WARNING') {
      return t('severity.WARNING')
    }

    return severityCode
  }
}
