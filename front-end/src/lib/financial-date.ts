/** SQL DATE values stay calendar dates; timestamps are grouped in Vietnam time. */
export function vietnamDate(value: unknown = new Date()): string {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  const date = value instanceof Date ? value : new Date(String(value))
  if (Number.isNaN(date.getTime())) return ""
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date)
  return ["year", "month", "day"].map(type => parts.find(p => p.type === type)?.value).join("-")
}

export function displayDate(value: string): string {
  return value ? value.split("-").reverse().join("/") : "Tất cả ngày"
}

export function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}
