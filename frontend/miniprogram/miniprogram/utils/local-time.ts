function twoDigits(value: number): string {
  return String(value).padStart(2, '0')
}

function localParts(value: string): {
  year: string
  month: string
  day: string
  hour: string
  minute: string
} | null {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return {
    year: String(parsed.getFullYear()),
    month: twoDigits(parsed.getMonth() + 1),
    day: twoDigits(parsed.getDate()),
    hour: twoDigits(parsed.getHours()),
    minute: twoDigits(parsed.getMinutes()),
  }
}

/** 将后端 UTC 时间按用户设备本地时区显示。 */
export function formatLocalDateTime(
  value: string | null | undefined,
): string {
  if (!value) return '—'
  const parts = localParts(value)
  if (!parts) return value
  return `${parts.year}-${parts.month}-${parts.day} `
    + `${parts.hour}:${parts.minute}`
}

/** 首页和列表使用的紧凑本地时间。 */
export function formatLocalShortDateTime(value: string): string {
  const parts = localParts(value)
  if (!parts) return value
  return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}
