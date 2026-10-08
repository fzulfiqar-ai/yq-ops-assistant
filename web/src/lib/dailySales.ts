/** The Command Centre's daily sales chart, the pure part: which days carry an x label, the y ticks, the target
 *  line, and the three facts the card and its screen-reader label share, with the days they cover. No React,
 *  no fetch, no import (web/scripts/numbers_ui_test.mjs exercises it in node). Every figure is the API's — the
 *  sales.daily tile built by app/metrics.py; this file only picks and words them, it never works one out. */

/** One calendar day of the sales.daily series: Accessories, ex-VAT, giveaways out. `business_day` false is a
 *  Friday or a Saturday (the Bahrain weekend); `latest` marks the data date. */
export interface DayPoint {
  day: string; label: string; weekday: string; value: number; invoices: number; business_day: boolean; latest: boolean
}

/** the day a figure belongs to, as the API names it ("29 Sep") */
export interface BestDay { day: string; label: string; value: number }

export interface DailyFact { key: 'latest' | 'average' | 'best'; label: string; when?: string; value: string }

/** The data date's point (the API flags it; the last point when an older payload carries no flag). */
export function latestDay(days: DayPoint[]): DayPoint | undefined {
  return days.find((p) => p.latest) ?? days[days.length - 1]
}

/** "7 invoices", "1 invoice", "no invoices" */
export function invoicesText(n?: number | null): string {
  const k = Math.max(0, Math.round(Number(n) || 0))
  return k === 0 ? 'no invoices' : `${k.toLocaleString('en-US')} invoice${k === 1 ? '' : 's'}`
}

/** The card's three facts in reading order: the latest day with its weekday, the average per business day, the
 *  best day with its date. `bhd` formats an amount; a fact the API did not send is left out (no best day when
 *  nothing sold in the window). The average is the API's: the window's sales, weekend sales too, over its
 *  business days — the month pace's rule, so it reads like for like beside the target for one business day. */
export function dailyFacts(days: DayPoint[], t: { avg_business_day?: number | null; best?: BestDay | null },
  bhd: (n: number) => string): DailyFact[] {
  const out: DailyFact[] = []
  const latest = latestDay(days)
  if (latest) out.push({ key: 'latest', label: 'Latest day', when: `${latest.weekday} ${latest.label}`, value: bhd(latest.value) })
  if (t.avg_business_day != null) out.push({ key: 'average', label: 'Average per business day', value: bhd(t.avg_business_day) })
  if (t.best) out.push({ key: 'best', label: 'Best day', when: t.best.label, value: bhd(t.best.value) })
  return out
}

/** The days the facts cover, as the cards beside this one write a window: "30 days to 6 Oct" (`n` days ending on
 *  the data date). The facts stay on the card under the weekly view too, where nothing else names their days. */
export function windowText(days: DayPoint[], n: number): string {
  const latest = latestDay(days)
  return latest ? `${n} days to ${latest.label}` : `last ${n} days`
}

/** A y-axis tick in thousands with the decimals it needs: 1.05k, 1.95k, 7.5k, 15k. One decimal printed 1.1k
 *  beside the gridline at 1,050 (the Dashboard's formatter, fixed for the same reason). */
export function kTick(v: number): string {
  return v >= 1000 ? `${Number((v / 1000).toFixed(3))}k` : `${v}`
}

/** What a screen reader hears for the chart: what it shows, then the same three facts. */
export function dailySummary(facts: DailyFact[], days: number): string {
  return [`Accessories sales by day, ex-VAT, the last ${days} days`,
    ...facts.map((f) => `${f.label}${f.when ? `, ${f.when}` : ''}: ${f.value}`)].join('. ')
}

/** The company target for one business day: the monthly target over the month's business days (Sunday to
 *  Thursday) — the Dashboard's rule. null when no target is set, so no line is drawn. */
export function targetPerBusinessDay(target?: number | null, businessDays?: number | null): number | null {
  return target && target > 0 && businessDays && businessDays > 0 ? target / businessDays : null
}

/** the room one x label needs at the axis's 11 px: "30 Sep" is about 36 px wide, its neighbour is narrower */
const LABEL_PX = 38

/** Every how many days an x label fits in a plot this wide: every 2nd day on a desktop card, every 3rd on a
 *  narrow one, every 5th on a 360 px phone. An unknown width gets the sparse phone answer. */
export function dayTickStep(plotWidth: number, days: number): number {
  if (!(plotWidth > 0) || !(days > 0)) return 5
  return Math.max(1, Math.ceil(LABEL_PX / (plotWidth / days)))
}

/** The days that carry an x label, as { day: label }: every `step`-th day counted back from the latest, so the
 *  newest bar is always named; the day of the month, with the month on the first label and wherever it
 *  changes ("28", "30", "2 Oct"). */
export function dayTicks(days: DayPoint[], step: number): Record<string, string> {
  const out: Record<string, string> = {}
  const s = Math.max(1, Math.floor(step) || 1)
  let month = ''
  // the first index is the remainder, so the walk lands exactly on the last (latest) day; no days, no walk
  for (let i = Math.max(0, (days.length - 1) % s); i < days.length; i += s) {
    const p = days[i]
    const m = p.day.slice(0, 7)
    out[p.day] = m === month ? String(Number(p.day.slice(8, 10))) : p.label
    month = m
  }
  return out
}
