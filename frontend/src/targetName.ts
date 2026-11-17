// I18: name proposed for a new STG / ODS table in "Mapping": <source schema>_<source object> (decision of the user
// 17.11.2026). Lower case, letters, digits and "_", at most 63 characters; the administrator may change it.
export function suggestedTargetTable(sourceSchema: string, sourceObject: string): string {
  let name = `${sourceSchema}_${sourceObject}`
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')

  if (name === '' || /^[0-9]/.test(name)) {
    name = `t_${name}`
  }

  return name.slice(0, 63).replace(/_+$/, '')
}
