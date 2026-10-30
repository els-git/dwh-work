function pad2(
  value: number,
): string {
  return String(value).padStart(2, '0')
}


export function formatDateTime(
  value: string | null | undefined,
): string {
  if (!value) {
    return ''
  }

  const date = new Date(value)

  if (
    Number.isNaN(
      date.getTime(),
    )
  ) {
    return value
  }

  const day =
    pad2(date.getDate())

  const month =
    pad2(date.getMonth() + 1)

  const year =
    date.getFullYear()

  const hours =
    pad2(date.getHours())

  const minutes =
    pad2(date.getMinutes())

  const seconds =
    pad2(date.getSeconds())

  return (
    `${day}.${month}.${year} `
    + `${hours}:${minutes}:${seconds}`
  )
}