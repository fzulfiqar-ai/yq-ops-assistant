import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Area, Bar, BarChart, Cell, ComposedChart, Line, Pie, PieChart, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'
import { motion } from 'motion/react'
import {
  DollarSign, FileText, Boxes, Landmark, TrendingUp, TrendingDown, Crown, TriangleAlert,
  CalendarDays, Store, Truck, ListChecks, ArrowRight, Percent, Clock, Snowflake,
  Flame, ArrowUpRight, ArrowDownRight, Smartphone, CheckCircle2, Loader2, RefreshCw,
} from 'lucide-react'
import { apiGet } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { bhd, num, monthLabel, fmtDate } from '@/lib/format'
import { deadUncostedNote } from '@/lib/basisText'
import { behindFocus } from '@/lib/dataDate'
import { panelState, refreshFailed } from '@/lib/dataState'
import { DURATION, ENTER_EASE, useFirstView, useSkeletonFade } from '@/lib/motionOnce'
import { ChartBox, FadeIn } from '@/lib/motionKit'
import { CountUp } from '@/components/CountUp'
import type { Freshness } from '@/components/FreshnessChip'
import { LoadError } from '@/components/LoadError'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { KpiTile, type KpiTone } from '@/components/ui/kpi-tile'
import { firstNameOf, greeting } from '@/lib/greeting'
import { Skeleton } from '@/components/ui/skeleton'

interface Kpis {
  rev_today: number; net_today: number; orders_today: number
  rev_yesterday: number; orders_yesterday: number
  rev_mtd: number; net_mtd: number; orders_mtd: number; rev_prev_month: number
  rev_prev_month_mtd?: number; prev_month_through?: string | null
  total_receivables: number; low_stock_count: number
  overdue_count: number; overdue_total_bhd: number
  current_receivables_bhd?: number
}
/** R7e: one Command Centre tile as app/metrics.py built it (app/reports.command_basis picks them): Accessories,
 *  ex-VAT, with the API's own basis line — the Dashboard recomputes nothing */
interface CcTile {
  key: string; label: string; basis: string; value: number | null
  compare?: { value: number; label: string } | null; delta_pct?: number | null; invoices?: number
  chips?: { label: string; value: number | null; share_pct?: number | null }[]
  source?: string; accounts?: number; over_90_bhd?: number; note?: string | null
}
interface CcSpan { start: string; end: string; label: string; business_days: number; basis?: string }
/** null when the Command Centre could not be built (every figure then reads "—"); a field is null when its
 *  module did not answer */
interface CommandBasis {
  sales_mtd: CcTile | null; sales_day: CcTile | null; channels: CcTile | null
  ar_total: CcTile | null; ar_over90: CcTile | null
  compare_basis: string | null; focus: CcSpan | null; day: CcSpan | null; day_compare: string | null
  /** the compared day's date (a weekday vs the previous business day; a Friday or Saturday vs the same weekday
   *  a week before), so the card names both weekdays */
  day_compare_on?: string | null
}
/** Accessories ex-VAT by calendar month, giveaways out; `partial` = the month the data stops in, `through` its last day */
interface TrendAccRow { period_month: string; acc_net_bhd: number; invoices: number; partial: boolean; through: string | null }
interface TopCustomerAcc { customer_name: string; net_bhd: number; invoices: number }
/** R3a: the dashboard's salesman rows are the CURRENT MONTH, ACCESSORIES ONLY (ex-VAT net beside gross);
 *  no_target comes from the attainment (an outlet such as Causeway has no target row). Tier, kickback and
 *  referral codes never travel here — they are Shop Admin data on the Salesmen page. */
interface SalesmanRow { salesman: string; orders: number; qty: number | null; revenue_bhd: number; net_bhd: number; no_target?: boolean }
interface SalesmanScope { division?: string; basis?: string; period?: string | null; data_through?: string | null; error?: string | null }
interface ActionItem { action: string; to: string; bhd: number; urgency: number }
/** R7d: gp_pct is THE official margin (ex-VAT sales vs Focus COGS, every item costed — app/metrics.py);
 *  landed_* is the secondary MRN margin with its coverage; dead stock is valued at COST. */
interface Health {
  /** null (with cost_hidden) for a login without 'Margins' — app/reports.dashboard_for_viewer */
  gp_bhd: number | null; gp_pct: number | null; ar_overdue_pct: number
  dso_days: number; dead_stock_bhd: number | null; dead_stock_count: number; cost_hidden?: boolean
  margin_basis?: string; margin_available?: boolean; cost_coverage_pct?: number | null; below_cost_count?: number
  landed_gp_pct?: number | null; landed_coverage_pct?: number | null
  dead_stock_uncosted?: number; dead_stock_sell_bhd?: number | null; stock_basis?: string | null
  /** R8: what the DSO divides — the Receivables card's figure (Focus's Grand Total when the ageing snapshot
   *  stored one, else the account rows) ÷ Accessories sales per day (VAT-incl) */
  dso_basis?: string; dso_receivables_bhd?: number; dso_sales_per_day_bhd?: number
}
/** current_stock: on hand in the latest snapshot (absent on an older API) */
interface MoverRow { item_name: string; sold_30d: number; sold_90d: number; momentum: number; status?: string; current_stock?: number }
/** acc_bhd = Mobile Accessories only, VAT-incl (SIM never counts); acc_net_bhd = the same ex-VAT, giveaways
 *  out — the basis the company target is read on, so the bars and the target lines compare like with like.
 *  The row's all-division totals also travel; this page is Accessories only and never reads them (R8) */
interface DailyRow { day: string; acc_bhd?: number; acc_net_bhd?: number }
/** R8: the month's Accessories sales by how they were paid — ex-VAT, giveaways out; giveaway_qty = the free
 *  (zero-priced) Accessories units of the month, counted apart (app/reports.payment_split_acc) */
interface PaymentSplitAcc { rows: { sale_type: string; net_bhd: number; invoices: number }[]; giveaway_qty: number }
/** R8: what the page counts, said by the API (app/reports.DASHBOARD_SCOPE) */
interface Scope { division: string; text: string }
/** The Command Centre's pace (app.metrics.month_pace): mtd_bhd is Accessories ex-VAT; the target is read as
 *  ex-VAT, Accessories, business days (Sun–Thu) — basis_text says so on the card */
interface Pace {
  target_bhd: number; mtd_bhd: number; prev_month_bhd: number
  projected_bhd: number | null; target_pct: number | null; on_track: boolean | null
  business_days_done?: number | null; business_days_total?: number | null; basis_text?: string; vat?: string
}

/** Sunday–Thursday: the Bahrain business week (Friday and Saturday off), as the API counts it */
function isBusinessDay(iso: string): boolean {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return false
  const dow = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).getUTCDay()
  return dow !== 5 && dow !== 6
}

/** Business days from the 1st of `iso`'s month up to and including `iso` */
function businessDaysTo(iso: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return 0
  let n = 0
  for (let d = 1; d <= Number(m[3]); d++) {
    if (isBusinessDay(`${m[1]}-${m[2]}-${String(d).padStart(2, '0')}`)) n++
  }
  return n
}
interface DashboardData {
  data_as_of?: string | null
  actions?: ActionItem[]
  health?: Health
  movers?: { rising: MoverRow[]; falling: MoverRow[] }
  kpis: Kpis
  /** R7e: the headline figures on the Command Centre's basis; the older VAT-inclusive, all-division
   *  fields (kpis.rev_*, revenue_trend, by_channel, top_customers, the split by payment and by division)
   *  still travel for their other readers, unread here */
  command?: CommandBasis | null
  revenue_trend_acc?: TrendAccRow[]
  /** null = the read failed (the card says so); [] = a month with no named-account sales */
  top_customers_acc?: TopCustomerAcc[] | null
  by_salesman: SalesmanRow[]
  by_salesman_scope?: SalesmanScope | null
  alerts: { negative_margin_count: number | null }
  daily_mtd?: DailyRow[]
  /** null = the read failed, absent = an older API: no chips either way */
  payment_split_acc?: PaymentSplitAcc | null
  /** absent on an older API: the page then makes no "Accessories only" claim */
  scope?: Scope | null
  pace?: Pace
}

// The KPI row's entrance (lib/motionOnce.ts): a short stagger of opacity and an 8 px rise on the enter curve,
// on its first view in the session only — KpiRow decides — and none under reduced motion
const container = { hidden: {}, show: { transition: { staggerChildren: 0.04 } } }
const item = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: DURATION.fade, ease: ENTER_EASE } },
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** '2026-09-27' → '27 Sep': the form the API's basis lines and the freshness chip use */
function dayLabel(iso?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '')
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : ''
}
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
/** '2026-09-26' → 'Sat 26 Sep': the Latest day card names the weekday, so a Friday or Saturday reads as one */
function weekdayLabel(iso?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '')
  if (!m) return ''
  const dow = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).getUTCDay()
  return `${WEEKDAYS[dow]} ${dayLabel(iso)}`
}

/** a thousands tick with the decimals it needs: 7.5k, 15k, 22.5k, 1.05k (toFixed(0) printed 8k and 23k, and
 *  toFixed(1) 1.1k for 1,050 once the daily target line widened the axis) */
const kTick = (v: number) => (v >= 1000 ? `${Number((v / 1000).toFixed(3))}k` : `${v}`)

/** A change against the comparison window, with that window in words ("vs the same 18 business days last month").
 *  The arrow never wraps away from its %, nor 'BHD' from its amount (`vs` may carry a nowrap amount). */
function Delta({ pct, vs }: { pct: number | null | undefined; vs?: React.ReactNode }) {
  if (pct == null) return <span className="font-semibold text-muted-foreground">No comparison{vs ? <> with {vs}</> : null}</span>
  const up = pct >= 0
  return (
    <span className={up ? 'font-semibold text-success' : 'font-semibold text-destructive'}>
      <span className="whitespace-nowrap">
        {up ? <TrendingUp className="mr-1 inline" size={14} /> : <TrendingDown className="mr-1 inline" size={14} />}
        {up ? '+' : ''}{pct.toFixed(1)}%
      </span>
      {vs && <span className="font-normal text-muted-foreground"> vs {vs}</span>}
    </span>
  )
}

/** 'BHD 682' kept on one line */
function Amount({ children }: { children: React.ReactNode }) {
  return <span className="whitespace-nowrap">{children}</span>
}

/** One figure of the KPI row: the portal's one tile, rising with the row on its first view in the session. */
function Kpi(props: Parameters<typeof KpiTile>[0]) {
  return (
    <motion.div variants={item} className="h-full">
      <KpiTile {...props} />
    </motion.div>
  )
}

/** Business health: the same tile, its icon coloured by whether the figure is good or bad news. */
const HEALTH_TONE: Record<'green' | 'amber' | 'red', KpiTone> = { green: 'success', amber: 'warning', red: 'destructive' }

function MoverList({ title, rows, up }: { title: string; rows: MoverRow[]; up?: boolean }) {
  const Icon = up ? ArrowUpRight : ArrowDownRight
  return (
    <div>
      <div className={cn('mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide',
        up ? 'text-success' : 'text-destructive')}>
        <Icon size={14} /> {title}
      </div>
      <ul className="space-y-0.5">
        {rows.map((r, i) => {
          const pct = Math.round((Number(r.momentum) - 1) * 100)
          // The units beside the %: the % stops at +200 (every sale of the 90 days fell in the last 30) and at
          // −100 (none did), so several rows carry the same one — the units are what tells them apart. A riser
          // shows what it sold in the 30 days, a fader what is still on hand to go dead.
          const units = up ? `${num(r.sold_30d)} sold` : r.current_stock != null ? `${num(r.current_stock)} on hand` : null
          const detail = `${num(r.sold_30d)} sold in the last 30 days, ${num(r.sold_90d)} in the last 90`
            + (r.current_stock != null ? ` · ${num(r.current_stock)} on hand` : '')
          return (
            <li key={i} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-accent/40">
              <span className="min-w-0 truncate font-medium" title={r.item_name}>{r.item_name}</span>
              <span className="shrink-0 whitespace-nowrap tabular-nums" title={detail}>
                {units && <span className="mr-2 text-[12px] text-muted-foreground">{units}</span>}
                <span className={cn('font-semibold', up ? 'text-success' : 'text-destructive')}>{pct > 0 ? '+' : ''}{pct}%</span>
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** The KPI row: its cards come in with a short stagger on the row's first view in the session (decided when the
 *  row itself mounts, i.e. when the figures arrive), and are simply there on every later visit. */
function KpiRow({ children }: { children: ReactNode }) {
  const enter = useFirstView('dashboard.kpis')
  return (
    <motion.div variants={container} initial={enter ? 'hidden' : false} animate="show"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      {children}
    </motion.div>
  )
}

// ── the page while its one read is under way ────────────────────────────────────────────────────────────
// Every card in its place and at about its height, so nothing jumps when the figures arrive: the actions card
// used to appear ABOVE the KPI skeletons only once the data came, and the day-by-day, health and momentum
// cards had no placeholder at all — the page moved by hundreds of pixels as it filled in. The heights are the
// cards' measured ones at 390, 1024 and 1366 px (three actions, five movers each way: the API sends up to five).

function CardSkeleton({ className, title = 'w-40', children }: { className?: string; title?: string; children: ReactNode }) {
  return (
    <Card className={cn('p-5', className)}>
      <Skeleton className={cn('h-5', title)} />
      <Skeleton className="mt-2 h-3 w-64 max-w-full" />
      <div className="mt-4">{children}</div>
    </Card>
  )
}

function ActionsSkeleton() {
  return (
    <Card className="mb-5 p-5">
      <Skeleton className="h-6 w-52" />
      <div className="mt-3 space-y-1">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-9" />)}</div>
    </Card>
  )
}

function DailySkeleton() {
  return (
    <CardSkeleton className="mt-4" title="w-48">
      {/* the pace figures and the Daily / Cumulative switch: one line on a wide screen, two up to xl, five on a phone */}
      <Skeleton className="h-7 w-[28rem] max-w-full" />
      <Skeleton className="mt-2 h-7 w-56 xl:hidden" />
      {[0, 1, 2].map((i) => <Skeleton key={i} className="mt-2 h-7 w-44 sm:hidden" />)}
      <Skeleton className="mt-3 h-[190px]" />
      <Skeleton className="mt-3 h-8 w-80 max-w-full" />
    </CardSkeleton>
  )
}

function HealthSkeleton() {
  return (
    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
      {[0, 1, 2].map((i) => <Skeleton key={i} className="h-[116px] rounded-xl sm:h-[160px] xl:h-[124px]" />)}
    </div>
  )
}

function MomentumSkeleton() {
  return (
    <CardSkeleton className="mt-5" title="w-32">
      <div className="grid grid-cols-1 gap-x-10 gap-y-2 md:grid-cols-2">
        {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => <Skeleton key={i} className="h-7" />)}
        {/* stacked on a phone, the second list's heading takes its own lines */}
        {[10, 11].map((i) => <Skeleton key={i} className="h-7 md:hidden" />)}
      </div>
    </CardSkeleton>
  )
}

function DashboardSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the dashboard">
      <ActionsSkeleton />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-[176px] rounded-xl xl:h-[220px]" />)}
      </div>
      <DailySkeleton />
      <HealthSkeleton />
      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* at lg the channel card is a narrow third and wraps its lines: the row is that card's height */}
        <CardSkeleton className="lg:col-span-2"><Skeleton className="h-[278px] lg:h-[306px] xl:h-[278px]" /></CardSkeleton>
        <CardSkeleton><Skeleton className="mx-auto h-[168px] w-[168px] rounded-full" /><Skeleton className="mt-3 h-12" /></CardSkeleton>
      </div>
      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <CardSkeleton className="lg:col-span-2"><Skeleton className="h-[278px]" /></CardSkeleton>
        <CardSkeleton><div className="space-y-2.5">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-9" />)}</div></CardSkeleton>
      </div>
      <MomentumSkeleton />
      <Skeleton className="mt-5 h-[146px] rounded-xl sm:h-[94px] xl:h-[62px]" />
    </div>
  )
}

/** how often the page asks again while its figures are behind the header chip's Focus date */
const CATCH_UP_MS = 30_000

export default function Dashboard() {
  const { me } = useAuth()
  const [dailyMode, setDailyMode] = useState<'daily' | 'cumulative'>('daily')
  // ONE freshness rule with the header chip (GET /freshness: Focus data date, the 3-day rule on the Bahrain
  // day): the chip's own query key, so the page and the chip read it once between them
  const { data: fresh } = useQuery({
    queryKey: ['freshness'],
    queryFn: () => apiGet<Freshness>('/freshness'),
    staleTime: 60_000,
    retry: 1,
  })
  // The figures change when a data load lands. The portal's default keeps a page five minutes and never asks
  // again by itself, so a tab left open went on showing the old month under a header chip that already read
  // the new date. This page asks again when the tab comes back to the front (its figures a minute old or
  // more), and — at once, then every 30 s until it has caught up — while the chip's Focus date is later than
  // the date its own figures carry (lib/dataDate.ts). No standing poll: between loads an open tab asks nothing.
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['report', 'dashboard'],
    queryFn: () => apiGet<DashboardData>('/report/dashboard'),
    staleTime: 60_000,
    refetchOnWindowFocus: true,
    refetchInterval: (q) => (behindFocus(fresh?.focus_to, q.state.data) ? CATCH_UP_MS : false),
  })
  const behind = behindFocus(fresh?.focus_to, data)
  useEffect(() => {
    if (behind) void refetch()
  }, [behind, refetch])

  // The same read's error and progress, for the Retry panel: a second, passive observer of the one query (it
  // never fetches; the read above stays as the R8 tests hold it). A failure used to leave the skeletons up for
  // good, under a header that said "All clear".
  const { isError, error, isFetching } = useQuery({
    queryKey: ['report', 'dashboard'],
    queryFn: () => apiGet<DashboardData>('/report/dashboard'),
    enabled: false,
  })
  const state = panelState({ data, isError })
  // the figures fade in over the skeleton (200 ms) when one was shown; a page opened from the cache draws at once
  const fade = useSkeletonFade(data !== undefined)

  // ONE basis with the pace and the Command Centre (R7b review): the target is read as ex-VAT,
  // Accessories, business days — so the daily target is the monthly target ÷ the month's business days
  // (Sun–Thu), the bars are Accessories ex-VAT, and the cumulative target only steps on business days
  // eslint-disable-next-line react-hooks/preserve-manual-memoization -- pre-existing memo; the compiler hint is advisory here
  const daysInMonth = useMemo(() => {
    const d = data?.data_as_of ? new Date(data.data_as_of) : new Date()
    return new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()
  }, [data?.data_as_of])
  const targetDays = data?.pace?.business_days_total || daysInMonth
  const dailyTarget = (data?.pace?.target_bhd ?? 0) > 0 ? data!.pace!.target_bhd / targetDays : null
  const exVat = (data?.daily_mtd || []).some((r) => r.acc_net_bhd != null)
  const barKey = exVat ? 'acc_net_bhd' : 'acc_bhd'
  const cumSeries = useMemo(() => {
    let run = 0
    return (data?.daily_mtd || []).map((r, i) => {
      // eslint-disable-next-line react-hooks/immutability -- a local running total inside map(), not render state
      run += Number(r.acc_net_bhd ?? r.acc_bhd ?? 0)
      const steps = data?.pace?.business_days_total ? businessDaysTo(r.day) : i + 1
      return { day: r.day, cum_bhd: Math.round(run), cum_target: dailyTarget ? Math.round(dailyTarget * steps) : null }
    })
  }, [data?.daily_mtd, data?.pace?.business_days_total, dailyTarget])

  const k = data?.kpis
  // R7e: the headline tiles are the Command Centre's own (Accessories · ex-VAT, the month to date vs the same
  // business days last month, the latest day vs the previous business day — a Friday or Saturday vs the same
  // weekday a week before, never a weekend day against Thursday) — never a second definition
  const cc = data?.command ?? null
  const salesMtd = cc?.sales_mtd ?? null
  const salesDay = cc?.sales_day ?? null
  // "Sat 19 Sep" when the API sends the compared day's date (an older API: its "19 Sep" label)
  const dayCompare = (cc?.day_compare_on ? weekdayLabel(cc.day_compare_on) : cc?.day_compare) || null
  const arTile = cc?.ar_total ?? null
  const arOver90 = cc?.ar_over90 ?? null
  // Focus's own Grand Total when the ageing snapshot stored one; else the account rows (the older kpi)
  const arValue = arTile?.value ?? k?.total_receivables ?? null
  const arLabel = arTile?.source === 'focus_total' ? 'Receivables · Focus Grand Total' : 'Receivables · sum of accounts'
  // R8: the page is Mobile Accessories only and says so when the API does (an older API sends no scope).
  // Receivables are the one figure that cannot be: Focus keeps one balance per customer, not per product
  const accOnly = data?.scope?.division === 'Accessories'
  const arBasis = [arTile?.basis, 'every customer balance in Focus, not split by product'].filter(Boolean).join(' · ')
  const split = data?.payment_split_acc ?? null
  const trendAcc = (data?.revenue_trend_acc || []).map((r) => ({ ...r, m: monthLabel(r.period_month) }))
  const partialMonth = trendAcc.find((r) => r.partial && r.through)
  const monthTarget = (data?.pace?.target_bhd ?? 0) > 0 ? data!.pace!.target_bhd : null
  const chTile = cc?.channels ?? null
  const chips = (chTile?.chips || []).filter((c) => c.label === 'B2B' || c.label === 'B2C')
  // null = the read failed: "could not be read just now", never "no sales this month"
  const topCustomersFailed = data?.top_customers_acc === null
  const topCustomers = data?.top_customers_acc || []
  // ex-VAT accessories sales this month — the kickback basis — with gross kept for the tooltip
  const salesmen = (data?.by_salesman || []).map((s) => ({ ...s, name: s.salesman, rev: Number(s.net_bhd ?? s.revenue_bhd ?? 0), gross: Number(s.revenue_bhd || 0) }))
  const smScope = data?.by_salesman_scope
  const smMonth = smScope?.period ? new Date(Number(smScope.period.slice(0, 4)), Number(smScope.period.slice(5, 7)) - 1, 1).toLocaleDateString('en-GB', { month: 'long' }) : ''

  const nActions = data?.actions?.length ?? 0
  // the top bar's greeting, word for word (lib/greeting: one helper, one set of hours)
  const hello = greeting(firstNameOf(me?.full_name, me?.email))
  const focus = nActions > 0
    ? `${nActions} thing${nActions === 1 ? '' : 's'} need${nActions === 1 ? 's' : ''} your attention today`
    : 'All clear — nothing needs your attention right now'

  return (
    <div>
      {/* the scope pill: what the page counts, in the API's own sentence on hover — quiet, the look of the
          header's freshness chip */}
      <PageHeader title={hello} subtitle={state === 'ready' ? focus : 'Mobile Accessories Intelligence'}
        actions={accOnly ? (
          <span title={data?.scope?.text}
            className="inline-flex items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-[11px] font-semibold text-muted-foreground">
            <Smartphone size={12} className="shrink-0 text-primary" aria-hidden="true" />
            Mobile Accessories only
          </span>
        ) : undefined} />

      {/* Stale-data guard: the header's freshness chip says the date; this says what to do about it */}
      {fresh?.stale && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-warning/30 bg-warning/10 px-4 py-2.5 text-sm font-medium text-warning">
          <TriangleAlert size={16} className="shrink-0" />
          {fresh.focus_to
            ? `Focus data is ${fresh.focus_days_behind ?? '—'} day(s) old (to ${fresh.focus_label || dayLabel(fresh.focus_to)})`
            : 'No Focus sales are loaded'} — upload the latest Focus exports for accurate figures.
        </div>
      )}

      {state === 'error' ? (
        <LoadError error={error} onRetry={() => refetch()} isRetrying={isFetching}
          fallback="Could not load the dashboard from the server." />
      ) : state === 'loading' ? (
        <>
          {/* nothing read and nothing on its way: the read is paused until the connection is back (react-query
              resumes it by itself) — said, rather than a skeleton that never ends */}
          {!isLoading && (
            <p role="status" className="mb-4 rounded-xl border bg-card px-4 py-2.5 text-sm text-muted-foreground">
              Waiting for a connection — the figures load as soon as it is back.
            </p>
          )}
          <DashboardSkeleton />
        </>
      ) : (
      <FadeIn on={fade}>
      {/* a re-read that failed (a poll while behind the chip, a tab coming back): the figures shown are the last
          ones read, and the page says so instead of passing them off as current */}
      {refreshFailed({ data, isError }) && (
        <div role="status" className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-warning/30 bg-warning/10 px-4 py-2 text-[13px] text-warning">
          <TriangleAlert size={15} className="shrink-0" aria-hidden="true" />
          Could not refresh just now — these are the figures read earlier.
          <button type="button" onClick={() => refetch()} disabled={isFetching}
            className="ml-auto inline-flex items-center gap-1 font-semibold hover:underline disabled:opacity-60">
            {isFetching ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Retry
          </button>
        </div>
      )}

      {/* Today's priority actions — the morning brief, on screen. The card keeps its place when there is nothing
          to do (an answer without the list, an older API, shows none — never a false all-clear). */}
      {Array.isArray(data?.actions) && (
        <Card className="mb-5 p-5">
          <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
            <ListChecks size={18} className="text-primary" /> Today's priority actions
          </div>
          {data.actions.length === 0 ? (
            <p className="flex items-center gap-2 px-2 text-sm text-muted-foreground">
              <CheckCircle2 size={16} className="shrink-0 text-success" aria-hidden="true" /> Nothing needs your attention right now.
            </p>
          ) : (
            <div className="space-y-1">
              {data.actions.map((a, i) => (
                <Link key={i} to={a.to}
                  className="group flex items-center gap-3 rounded-lg px-2 py-2 text-sm transition hover:bg-accent/50">
                  <span className={cn('h-2 w-2 shrink-0 rounded-full',
                    a.urgency >= 3 ? 'bg-destructive' : a.urgency === 2 ? 'bg-target' : 'bg-muted-foreground/50')} />
                  <span className="min-w-0 flex-1 truncate font-medium" title={a.action}>{a.action}</span>
                  {a.bhd > 0 && <span className="shrink-0 tabular-nums text-muted-foreground">{bhd(a.bhd, 0)}</span>}
                  <ArrowRight size={15} className="shrink-0 text-muted-foreground opacity-0 transition-opacity duration-200 group-hover:opacity-100" />
                </Link>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* KPI row */}
      {k && (
        <KpiRow>
          <Kpi icon={DollarSign} label="Accessories sales this month · ex-VAT" tone="primary" to="/sales"
            title={salesMtd?.basis}
            value={salesMtd?.value != null ? <CountUp id="dashboard.sales_mtd" value={salesMtd.value} format={(n) => bhd(n, 0)} /> : '—'}
            foot={salesMtd
              ? <Delta pct={salesMtd.delta_pct} vs={cc?.compare_basis || salesMtd.compare?.label} />
              : <span className="text-muted-foreground">Not available just now · the Command Centre figures did not load</span>} />
          <Kpi icon={CalendarDays} label={cc?.day ? `Latest day · ${weekdayLabel(cc.day.end)}` : 'Latest day'} to="/sales"
            title={salesDay?.basis}
            value={salesDay?.value != null ? <CountUp id="dashboard.sales_day" value={salesDay.value} format={(n) => bhd(n, 0)} /> : '—'}
            foot={salesDay ? (
              <span className="text-muted-foreground">
                Accessories ex-VAT · <Delta pct={salesDay.delta_pct}
                  vs={salesDay.compare ? <>{dayCompare || salesDay.compare.label} <Amount>({bhd(salesDay.compare.value, 0)})</Amount></> : dayCompare} />
                {' '}· {num(salesDay.invoices)} invoices
              </span>
            ) : <span className="text-muted-foreground">Accessories · ex-VAT · not available just now</span>} />
          <Kpi icon={FileText} label="Focus invoices this month (Accessories)" to="/sales"
            title={salesMtd?.basis}
            value={salesMtd?.invoices != null ? <CountUp id="dashboard.invoices" value={salesMtd.invoices} /> : '—'}
            foot={<span className="text-muted-foreground">Month to date{cc?.focus ? ` · ${cc.focus.label}` : ''} · the Focus sales day book</span>} />
          <Kpi icon={Landmark} label={arLabel} to="/receivables"
            title={arBasis}
            value={arValue != null ? <CountUp id="dashboard.receivables" value={arValue} format={(n) => bhd(n, 0)} /> : '—'}
            foot={<span className="text-muted-foreground">
              {arOver90?.value != null && <><span className="font-semibold text-destructive">{arOver90.value.toFixed(1)}% over 90 days</span>{' '}· </>}
              <Amount>{bhd(k.overdue_total_bhd, 0)}</Amount> overdue &gt;30d · {k.overdue_count} accts</span>} />
          <Kpi icon={Boxes} label={accOnly ? 'Low-stock items · Accessories' : 'Low-stock items'} to="/inventory"
            title={accOnly ? 'Accessories items with under 30 days of stock cover, sold-out items with sales in the last 90 days included · the latest stock snapshot' : undefined}
            value={<CountUp id="dashboard.low_stock" value={k.low_stock_count} />}
            foot={<span className="font-medium text-warning">&lt; 30 days cover</span>} />
        </KpiRow>
      )}

      {/* This month, day by day — daily sales + pace vs target + the cash / credit split, all Accessories ex-VAT */}
      {data?.daily_mtd && data.daily_mtd.length > 0 && (
        <Card className="mt-4 p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <div className="font-display text-base font-semibold">This month, day by day</div>
              <div className="text-xs text-muted-foreground">
                {exVat ? 'Mobile Accessories ex-VAT per day (SIM and giveaways excluded)' : 'Mobile Accessories gross per day (VAT-incl, SIM excluded)'} · {monthLabel(data.data_as_of || '')}
              </div>
              {data.pace?.basis_text && <div className="text-[11px] text-muted-foreground">{data.pace.basis_text}</div>}
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
              {data.pace && (<>
                <span className="text-muted-foreground">Accessories MTD{data.pace.vat ? ` ${data.pace.vat}` : ''} <b className="text-foreground tabular-nums">{bhd(data.pace.mtd_bhd, 0)}</b></span>
                {data.pace.projected_bhd != null && (
                  <span className="text-muted-foreground">Projected <b className="text-foreground tabular-nums">{bhd(data.pace.projected_bhd, 0)}</b></span>
                )}
                {data.pace.target_bhd > 0 ? (
                  <span className={cn('rounded-full px-2.5 py-0.5 text-[12px] font-semibold',
                    data.pace.on_track ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>
                    {data.pace.target_pct}% of {bhd(data.pace.target_bhd, 0)} target so far
                  </span>
                ) : me?.role === 'admin' ? (
                  <Link to="/settings" className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
                    Set a monthly target in Settings <ArrowRight size={12} aria-hidden="true" />
                  </Link>
                ) : (
                  <span className="text-xs text-muted-foreground">No monthly target set</span>
                )}
              </>)}
              <div className="flex overflow-hidden rounded-lg border text-[12px] font-semibold">
                {(['daily', 'cumulative'] as const).map((m) => (
                  <button key={m} onClick={() => setDailyMode(m)}
                    className={cn('px-2.5 py-1 capitalize transition',
                      dailyMode === m ? 'bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:bg-accent/50')}>
                    {m}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {dailyMode === 'daily' ? (
            <ChartBox id="dashboard.daily" height={190}>{(m) => (
              <BarChart data={data.daily_mtd} margin={{ top: 18, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval="preserveStartEnd"
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
                  width={44} tickFormatter={kTick} />
                <Tooltip
                  formatter={(v, name) => (name === barKey ? [bhd(Number(v)), exVat ? 'Accessories ex-VAT' : 'Accessories'] : [String(v), String(name)])}
                  labelFormatter={(d) => fmtDate(String(d))}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                {/* extendDomain: the line shows when the target is above every bar; its label sits ABOVE the line
                    (insideBottomRight) so it never lands on a bar's top */}
                {dailyTarget && (
                  <ReferenceLine y={dailyTarget} stroke="hsl(var(--target))" strokeDasharray="5 4" ifOverflow="extendDomain"
                    label={{ value: `target per business day ${bhd(dailyTarget, 0)}`, position: 'insideBottomRight', offset: 5, fontSize: 10, fill: 'hsl(var(--target))' }} />
                )}
                <Bar {...m} dataKey={barKey} radius={[4, 4, 0, 0]} maxBarSize={26}>
                  {data.daily_mtd!.map((r, i) => (
                    <Cell key={i} fill={dailyTarget && Number(r.acc_net_bhd ?? r.acc_bhd ?? 0) >= dailyTarget ? 'hsl(var(--success))' : 'hsl(var(--primary))'} />
                  ))}
                </Bar>
              </BarChart>
            )}</ChartBox>
          ) : (
            <ChartBox id="dashboard.cumulative" height={190}>{(m) => (
              <ComposedChart data={cumSeries} margin={{ top: 10, right: 8, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="cumFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval="preserveStartEnd"
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
                  width={44} tickFormatter={kTick} />
                <Tooltip
                  formatter={(v, name) => [bhd(Number(v), 0), name === 'cum_bhd' ? (exVat ? 'Accessories MTD ex-VAT' : 'Accessories MTD') : 'Target to date (business days)']}
                  labelFormatter={(d) => fmtDate(String(d))}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Area {...m} type="monotone" dataKey="cum_bhd" stroke="hsl(var(--primary))" strokeWidth={2.5} fill="url(#cumFill)" />
                {dailyTarget && (
                  <Line {...m} type="monotone" dataKey="cum_target" stroke="hsl(var(--target))" strokeWidth={2}
                    strokeDasharray="6 4" dot={false} />
                )}
              </ComposedChart>
            )}</ChartBox>
          )}
          {split && (split.rows.length > 0 || split.giveaway_qty > 0) && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3 text-[13px]">
              {/* R8: the same basis as the bars above — Accessories, ex-VAT, giveaways out; nothing else is shown */}
              <span className="text-[11px] text-muted-foreground">Month to date · Accessories · ex-VAT:</span>
              {split.rows.map((p) => (
                <span key={p.sale_type} title={`${num(p.invoices)} invoice${p.invoices === 1 ? '' : 's'} · ${bhd(p.net_bhd, 3)} ex-VAT`}
                  className="inline-flex items-center gap-1.5 rounded-full border bg-secondary/40 px-3 py-1">
                  <span className={cn('h-2 w-2 rounded-full', p.sale_type === 'cash' ? 'bg-success' : 'bg-info')} />
                  <span className="capitalize">{p.sale_type}</span>
                  <b className="tabular-nums">{bhd(p.net_bhd, 0)}</b>
                </span>
              ))}
              {split.giveaway_qty > 0 && (
                <span title="Accessories units given free this month (zero-priced lines): not in the sales figures"
                  className="inline-flex items-center gap-1.5 rounded-full border bg-secondary/40 px-3 py-1 text-warning">
                  <b className="tabular-nums">{num(split.giveaway_qty)}</b>
                  <span>free unit{split.giveaway_qty === 1 ? '' : 's'}</span>
                </span>
              )}
            </div>
          )}
        </Card>
      )}

      {/* Business health — the CEO truth the totals hide: margin, cash speed, frozen capital */}
      {data?.health && (
        <div className={cn('mt-4 grid grid-cols-1 gap-4', data.health.cost_hidden ? 'sm:grid-cols-2' : 'sm:grid-cols-3')}>
          {/* margin and gross profit are for admins and 'Margins' logins only (the API sends null otherwise) */}
          {!data.health.cost_hidden && (
          <KpiTile icon={Percent} label="Gross margin · ex-VAT on Focus COGS" iconTone={HEALTH_TONE[(data.health.gp_pct ?? 0) < 20 ? 'amber' : 'green']}
            value={data.health.margin_available === false || data.health.gp_pct == null ? '—' : `${data.health.gp_pct.toFixed(1)}%`}
            foot={<>{bhd(data.health.gp_bhd, 0)} GP · every item costed
              {data.health.landed_gp_pct != null && <> · landed {data.health.landed_gp_pct.toFixed(1)}% on {Math.round(data.health.landed_coverage_pct ?? 0)}% of sales</>}
              {(data.health.below_cost_count ?? 0) > 0 && <> · <span className="font-semibold text-destructive">{data.health.below_cost_count} below cost</span></>}</>}
            to="/margins" />
          )}
          {/* what the days are made of is on the tile itself, not only on hover (a phone has none): the page's one
              receivables figure, the Receivables card's, over a day of Accessories sales */}
          <KpiTile icon={Clock} label="Collection speed · DSO" iconTone={HEALTH_TONE[data.health.ar_overdue_pct > 40 ? 'red' : 'amber']}
            title={data.health.dso_basis && `${data.health.dso_basis} · ${bhd(data.health.dso_receivables_bhd, 0)} ÷ ${bhd(data.health.dso_sales_per_day_bhd, 0)} a day`}
            value={`${Math.round(data.health.dso_days)} days`}
            foot={<>{data.health.dso_basis && <>receivables ÷ a day of Accessories sales · </>}
              <span className="font-semibold text-destructive">{data.health.ar_overdue_pct.toFixed(0)}%</span> of receivables overdue — chase to free cash</>} to="/receivables" />
          <KpiTile icon={Snowflake} label={data.health.cost_hidden ? 'Dead stock · at selling price' : 'Capital frozen in dead stock · at cost'} iconTone="destructive"
            value={data.health.stock_basis === 'cost' && data.health.dead_stock_bhd != null ? bhd(data.health.dead_stock_bhd, 0)
              : data.health.cost_hidden && data.health.dead_stock_sell_bhd != null ? bhd(data.health.dead_stock_sell_bhd, 0) : '—'}
            foot={<>{data.health.dead_stock_count} items not selling — liquidate to release cash
              {data.health.stock_basis === 'cost' && deadUncostedNote(data.health.dead_stock_count, data.health.dead_stock_uncosted)}
              {data.health.dead_stock_sell_bhd != null && <> · {bhd(data.health.dead_stock_sell_bhd, 0)} at selling price</>}</>} to="/inventory" />
        </div>
      )}

      {/* Trend + channel split */}
      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <div className="mb-1 font-display text-base font-semibold">Accessories sales by month</div>
          <div className="mb-4 text-xs text-muted-foreground">
            Accessories · ex-VAT · giveaways out · 12 calendar months
            {partialMonth ? ` · ${partialMonth.m} so far (to ${dayLabel(partialMonth.through)}), the lighter bar` : ''}
            {monthTarget ? ' · the line is the monthly target' : ''}
          </div>
          {trendAcc.length === 0 ? (
            <p className="grid h-[260px] place-items-center text-sm text-muted-foreground">Monthly Accessories sales could not be read just now.</p>
          ) : (
            <ChartBox id="dashboard.trend" height={260}>{(m) => (
              <BarChart data={trendAcc} margin={{ top: 18, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="m" tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false}
                  width={48} tickFormatter={kTick} />
                <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Accessories ex-VAT']}
                  labelFormatter={(m, payload) => {
                    const row = payload?.[0]?.payload as { partial?: boolean; through?: string | null } | undefined
                    return row?.partial && row.through ? `${m} · so far (to ${dayLabel(row.through)})` : String(m)
                  }}
                  cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                {monthTarget && (
                  <ReferenceLine y={monthTarget} stroke="hsl(var(--target))" strokeDasharray="5 4" ifOverflow="extendDomain"
                    label={{ value: `monthly target ${bhd(monthTarget, 0)}`, position: 'insideBottomRight', offset: 5, fontSize: 10, fill: 'hsl(var(--target))' }} />
                )}
                <Bar {...m} dataKey="acc_net_bhd" radius={[4, 4, 0, 0]} maxBarSize={34}>
                  {/* the month still running is lighter: it is not a whole month yet */}
                  {trendAcc.map((r, i) => <Cell key={i} fill={r.partial ? 'hsl(var(--primary) / 0.35)' : 'hsl(var(--primary))'} />)}
                </Bar>
              </BarChart>
            )}</ChartBox>
          )}
        </Card>

        <Card className="p-5">
          <div className="font-display text-base font-semibold">Sales by channel</div>
          <div className="mb-2 text-xs text-muted-foreground">Accessories · ex-VAT · month to date · B2C = Causeway + Roadshow</div>
          {!chTile ? (
            <p className="text-sm text-muted-foreground">Not available just now · the Command Centre figures did not load.</p>
          ) : (
            <div title={chTile.basis}>
              <div className="relative">
                <ChartBox id="dashboard.channels" height={168}>{(m) => (
                  <PieChart>
                    <Pie {...m} data={chips.map((c) => ({ name: c.label, value: Number(c.value) || 0 }))}
                      dataKey="value" nameKey="name" innerRadius={54} outerRadius={78} paddingAngle={2} stroke="none">
                      {chips.map((c, i) => <Cell key={i} fill={c.label === 'B2C' ? 'hsl(var(--sash))' : 'hsl(var(--info))'} />)}
                    </Pie>
                    <Tooltip formatter={(v) => [bhd(Number(v), 0), 'Accessories ex-VAT']}
                      contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                  </PieChart>
                )}</ChartBox>
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                  <div className="font-display text-lg font-extrabold tabular-nums">{bhd(chTile.value, 0)}</div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">ex-VAT · MTD</div>
                </div>
              </div>
              <div className="mt-2 space-y-1.5">
                {chips.map((c) => {
                  const isB2C = c.label === 'B2C'
                  return (
                    <div key={c.label} className="flex items-center justify-between text-sm">
                      <span className="flex items-center gap-2 font-medium">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: isB2C ? 'hsl(var(--sash))' : 'hsl(var(--info))' }} />
                        {isB2C ? <Store size={14} className="text-sash" /> : <Truck size={14} className="text-info" />}
                        {c.label} · {isB2C ? 'Retail' : 'Wholesale'}
                      </span>
                      <span className="font-semibold tabular-nums">{bhd(c.value, 0)} <span className="text-muted-foreground">({c.share_pct != null ? `${c.share_pct.toFixed(0)}%` : '—'})</span></span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* Salesmen + top customers */}
      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <div className="mb-1 font-display text-base font-semibold">
            Top salesmen · Accessories{smMonth ? ` · ${smMonth}` : ' · this month'} (ex-VAT)
          </div>
          <div className="mb-3 text-[12px] text-muted-foreground">
            The kickback basis: accessories only, giveaways excluded, SIM never counts{smScope?.data_through ? ` · sales data to ${fmtDate(smScope.data_through)}` : ''}.
            The all-time, all-division rollup is on the Sales page.
          </div>
          {smScope?.error ? (
            <p className="text-sm text-destructive">{smScope.error}</p>
          ) : salesmen.length === 0 ? (
            <p className="text-sm text-muted-foreground">No accessories sales loaded for this month yet.</p>
          ) : (
            <ChartBox id="dashboard.salesmen" height={260}>{(m) => (
              <BarChart data={salesmen} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
                <XAxis type="number" hide tickFormatter={(v) => bhd(v, 0)} />
                <YAxis type="category" dataKey="name" width={110} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(value, _name, item) => [`${bhd(Number(value), 3)} ex-VAT · ${bhd(Number((item?.payload as { gross?: number } | undefined)?.gross || 0), 3)} gross`, 'Accessories']} cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Bar {...m} dataKey="rev" radius={[0, 6, 6, 0]}>
                  {salesmen.map((s, i) => <Cell key={i} fill={s.no_target ? 'hsl(var(--primary) / 0.35)' : i === 0 ? 'hsl(var(--primary))' : 'hsl(var(--sash))'} />)}
                </Bar>
              </BarChart>
            )}</ChartBox>
          )}
        </Card>

        <Card className="p-5">
          <div className="flex items-center gap-2 font-display text-base font-semibold">
            <Crown size={18} className="text-target" /> Top customers
          </div>
          <div className="mb-3 text-[12px] text-muted-foreground">Accessories · ex-VAT · month to date · Cash Customer left out</div>
          {topCustomersFailed ? (
            <p className="text-sm text-muted-foreground">Top customers could not be read just now.</p>
          ) : topCustomers.length === 0 ? (
            <p className="text-sm text-muted-foreground">No named-account Accessories sales this month.</p>
          ) : (
            <ul className="space-y-1.5">
              {topCustomers.slice(0, 7).map((c, i) => (
                <li key={i} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 hover:bg-accent/50"
                  title={`${num(c.invoices)} invoice${c.invoices === 1 ? '' : 's'} this month`}>
                  <span className="flex min-w-0 items-center gap-2.5">
                    <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-bold text-accent-foreground">{i + 1}</span>
                    <span className="truncate text-sm font-medium">{c.customer_name}</span>
                  </span>
                  <span className="shrink-0 text-sm font-semibold text-primary">{bhd(c.net_bhd, 0)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Momentum — what's accelerating vs fading (restock risers, investigate faders) */}
      {data?.movers && (data.movers.rising?.length > 0 || data.movers.falling?.length > 0) && (
        <Card className="mt-5 p-5">
          <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
            <Flame size={18} className="text-primary" /> Momentum
            <span className="text-[12px] font-normal text-muted-foreground">· last 30 days vs the 90-day run-rate</span>
          </div>
          {/* grid-cols-1 = minmax(0,1fr): on a phone a long accessory name is cut with "…" and its % stays on
              the card (an auto column grew to the name's full width and pushed the % off the edge) */}
          <div className="grid grid-cols-1 gap-x-10 gap-y-4 md:grid-cols-2">
            <MoverList title="Rising — restock & push" rows={data.movers.rising} up />
            <MoverList title="Fading — investigate before it goes dead" rows={data.movers.falling} />
          </div>
        </Card>
      )}

      {/* Alerts strip */}
      {k && (
        <Card className="mt-5 flex flex-wrap items-center gap-x-8 gap-y-3 p-5">
          <div className="flex items-center gap-2 text-sm">
            <TriangleAlert size={16} className="text-warning" />
            <span className="font-semibold">{num(k.low_stock_count)}</span>
            <span className="text-muted-foreground">{accOnly ? 'Accessories items' : 'items'} under 30 days cover</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <Landmark size={16} className="text-destructive" />
            <span className="whitespace-nowrap font-semibold">{bhd(k.overdue_total_bhd, 0)}</span>
            <span className="text-muted-foreground">overdue &gt;30 days across {k.overdue_count} accounts</span>
          </div>
          {data?.alerts?.negative_margin_count != null && (
          <div className="flex items-center gap-2 text-sm">
            <TrendingDown size={16} className="text-primary" />
            <span className="font-semibold">{num(data.alerts.negative_margin_count)}</span>
            <span className="text-muted-foreground">products below cost</span>
          </div>
          )}
        </Card>
      )}
      </FadeIn>
      )}
    </div>
  )
}
