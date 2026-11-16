// CSV of a report table (assistant answers and saved reports): ';' separator, UTF-8 with BOM for Excel.
import type { AiCell } from './aiApi'


export function cellText(value: AiCell): string {
  return value === null ? '' : String(value)
}


export function downloadReportCsv(columns: string[], rows: Record<string, AiCell>[], fileName: string) {
  const quote = (text: string) => `"${text.replace(/"/g, '""')}"`
  const lines = [columns.map(quote).join(';')]
  for (const row of rows) {
    lines.push(columns.map(column => quote(cellText(row[column] ?? null))).join(';'))
  }
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.click()
  URL.revokeObjectURL(url)
}
