export const bhd = (n: number | null | undefined, dp = 2) =>
  `BHD ${Number(n ?? 0).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`

export const num = (n: number | null | undefined) => Number(n ?? 0).toLocaleString('en-US')

export const pct = (n: number | null | undefined, dp = 1) => `${Number(n ?? 0).toFixed(dp)}%`

export function fmtDate(d?: string | null): string {
  if (!d) return '—'
  try {
    return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
  } catch {
    return String(d)
  }
}

export function monthLabel(d?: string | null): string {
  if (!d) return ''
  try {
    return new Date(d).toLocaleDateString('en-GB', { month: 'short' })
  } catch {
    return String(d)
  }
}

export function daysSince(d?: string | null): number {
  if (!d) return 0
  return Math.floor((Date.now() - new Date(d).getTime()) / 86_400_000)
}
