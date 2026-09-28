import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Area, Bar, BarChart, Cell, ComposedChart, Line, Pie, PieChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { motion } from 'motion/react'
import {
  DollarSign, FileText, Boxes, Landmark, TrendingUp, TrendingDown, Crown, TriangleAlert,
  CalendarDays, Store, Truck, ListChecks, ArrowRight, Percent, Clock, Snowflake,
  Flame, ArrowUpRight, ArrowDownRight,
} from 'lucide-react'
import { apiGet } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { bhd, num, monthLabel, fmtDate } from '@/lib/format'
import { deadUncostedNote } from '@/lib/basisText'
import { CountUp } from '@/components/CountUp'
import type { Freshness } from '@/components/FreshnessChip'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
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
}
interface MoverRow { item_name: string; sold_30d: number; sold_90d: number; momentum: number; status?: string }
/** acc_bhd = Mobile Accessories only, VAT-incl (SIM never counts); acc_net_bhd = the same ex-VAT, giveaways
 *  out — the basis the company target is read on, so the bars and the target lines compare like with like */
interface DailyRow { day: string; gross_bhd: number; acc_bhd?: number; acc_net_bhd?: number; net_bhd: number; orders: number }
interface PaymentRow { sale_type: string; orders: number; revenue_bhd: number }
interface DivisionRow { division: string; orders: number; revenue_bhd: number; giveaway_qty: number }
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
  /** R7e: the headline figures on the Command Centre's basis; the older VAT-inclusive, every-division
   *  fields (kpis.rev_*, revenue_trend, by_channel, top_customers) still travel for the digests, unread here */
  command?: CommandBasis | null
  revenue_trend_acc?: TrendAccRow[]
  top_customers_acc?: TopCustomerAcc[]
  by_salesman: SalesmanRow[]
  by_salesman_scope?: SalesmanScope | null
  alerts: { negative_margin_count: number | null }
  daily_mtd?: DailyRow[]
  by_payment?: PaymentRow[]
  by_division?: DivisionRow[]
  pace?: Pace
}

const container = { hidden: {}, show: { transition: { staggerChildren: 0.06 } } }
const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] as const } },
}
const ACCENTS = {
  purple: 'linear-gradient(90deg,#7c3aed,#a78bfa)',
  blue: 'linear-gradient(90deg,#2563eb,#60a5fa)',
  amber: 'linear-gradient(90deg,#d97706,#fbbf24)',
  green: 'linear-gradient(90deg,#059669,#34d399)',
  slate: 'linear-gradient(90deg,#475569,#94a3b8)',
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** '2026-09-27' → '27 Sep': the form the API's basis lines and the freshness chip use */
function dayLabel(iso?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '')
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : ''
}

/** A change against the comparison window, with that window in words ("vs the same 18 business days last month") */
function Delta({ pct, vs }: { pct: number | null | undefined; vs?: string | null }) {
  if (pct == null) return <span className="font-semibold text-muted-foreground">No comparison{vs ? ` with ${vs}` : ''}</span>
  const up = pct >= 0
  return (
    <span className={up ? 'font-semibold text-emerald-600' : 'font-semibold text-rose-600'}>
      {up ? <TrendingUp className="mr-1 inline" size={14} /> : <TrendingDown className="mr-1 inline" size={14} />}
      {up ? '+' : ''}{pct.toFixed(1)}%
      {vs && <span className="font-normal text-muted-foreground"> vs {vs}</span>}
    </span>
  )
}

function KpiCard({ accent, icon: Icon, label, value, foot, hero, to, basis }: {
  accent: string; icon: typeof DollarSign; label: string; value: React.ReactNode
  foot?: React.ReactNode; hero?: boolean; to?: string
  /** the API's full basis line (the Command Centre's words), on hover */
  basis?: string
}) {
  const card = (
    <Card title={basis} className="group relative flex h-full flex-col overflow-hidden p-4 transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-luxe-hover">
      {/* top accent rail */}
      <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: accent }} />
      {/* soft accent glow that intensifies on hover */}
      <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full opacity-40 blur-2xl transition-opacity duration-500 group-hover:opacity-70"
        style={{ background: accent }} />
      <div className="relative flex items-start justify-between">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-accent/60 text-accent-foreground ring-1 ring-inset ring-white/40">
          <Icon size={18} />
        </div>
        {to && <ArrowUpRight size={15} className="text-muted-foreground/50 transition group-hover:text-primary" />}
      </div>
      <div className={cn(
        'relative mt-3 font-display font-extrabold leading-none tracking-tight tabular-nums',
        hero ? 'text-gradient text-[1.85rem]' : 'text-[1.6rem]',
      )}>{value}</div>
      <div className="relative mt-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      {foot && <div className="relative mt-1.5 text-[12.5px]">{foot}</div>}
    </Card>
  )
  return (
    <motion.div variants={item}>
      {to ? <Link to={to} className="block h-full">{card}</Link> : card}
    </motion.div>
  )
}

const TONES = {
  green: { glow: '#10b981', text: 'text-emerald-600' },
  amber: { glow: '#f59e0b', text: 'text-amber-600' },
  red: { glow: '#f43f5e', text: 'text-rose-600' },
  violet: { glow: '#8b5cf6', text: 'text-violet-600' },
}

function HealthStat({ icon: Icon, label, value, sub, tone, to }: {
  icon: typeof Percent; label: string; value: React.ReactNode; sub?: React.ReactNode
  tone: keyof typeof TONES; to?: string
}) {
  const t = TONES[tone]
  const inner = (
    <Card className="group relative h-full overflow-hidden p-4 transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-luxe-hover">
      <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full opacity-30 blur-2xl transition-opacity group-hover:opacity-60" style={{ background: t.glow }} />
      <div className="relative flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <Icon size={15} className={t.text} /> {label}
      </div>
      <div className="relative mt-2 font-display text-[1.5rem] font-extrabold leading-none tabular-nums">{value}</div>
      {sub && <div className="relative mt-1.5 text-[12px] text-muted-foreground">{sub}</div>}
    </Card>
  )
  return to ? <Link to={to} className="block h-full">{inner}</Link> : inner
}

function MoverList({ title, rows, up }: { title: string; rows: MoverRow[]; up?: boolean }) {
  const Icon = up ? ArrowUpRight : ArrowDownRight
  return (
    <div>
      <div className={cn('mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide',
        up ? 'text-emerald-600' : 'text-rose-600')}>
        <Icon size={14} /> {title}
      </div>
      <ul className="space-y-0.5">
        {rows.map((r, i) => {
          const pct = Math.round((Number(r.momentum) - 1) * 100)
          return (
            <li key={i} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-accent/40">
              <span className="min-w-0 truncate font-medium" title={r.item_name}>{r.item_name}</span>
              <span className={cn('shrink-0 font-semibold tabular-nums', up ? 'text-emerald-600' : 'text-rose-600')}>
                {pct > 0 ? '+' : ''}{pct}%
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export default function Dashboard() {
  const { me } = useAuth()
  const [dailyMode, setDailyMode] = useState<'daily' | 'cumulative'>('daily')
  const { data, isLoading } = useQuery({
    queryKey: ['report', 'dashboard'],
    queryFn: () => apiGet<DashboardData>('/report/dashboard'),
  })

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
      run += Number(r.acc_net_bhd ?? r.acc_bhd ?? r.gross_bhd ?? 0)
      const steps = data?.pace?.business_days_total ? businessDaysTo(r.day) : i + 1
      return { day: r.day, cum_bhd: Math.round(run), cum_target: dailyTarget ? Math.round(dailyTarget * steps) : null }
    })
  }, [data?.daily_mtd, data?.pace?.business_days_total, dailyTarget])

  // ONE freshness rule with the header chip (GET /freshness: Focus data date, the 3-day rule on the Bahrain
  // day): the chip's own query key, so the page and the chip read it once between them
  const { data: fresh } = useQuery({
    queryKey: ['freshness'],
    queryFn: () => apiGet<Freshness>('/freshness'),
    staleTime: 60_000,
    retry: 1,
  })

  const k = data?.kpis
  // R7e: the headline tiles are the Command Centre's own (Accessories · ex-VAT, the month to date vs the same
  // business days last month, the latest day vs the previous business day) — never a second definition
  const cc = data?.command ?? null
  const salesMtd = cc?.sales_mtd ?? null
  const salesDay = cc?.sales_day ?? null
  const arTile = cc?.ar_total ?? null
  const arOver90 = cc?.ar_over90 ?? null
  // Focus's own Grand Total when the ageing snapshot stored one; else the account rows (the older kpi)
  const arValue = arTile?.value ?? k?.total_receivables ?? null
  const arLabel = arTile?.source === 'focus_total' ? 'Receivables · Focus Grand Total' : 'Receivables · sum of accounts'
  const trendAcc = (data?.revenue_trend_acc || []).map((r) => ({ ...r, m: monthLabel(r.period_month) }))
  const partialMonth = trendAcc.find((r) => r.partial && r.through)
  const monthTarget = (data?.pace?.target_bhd ?? 0) > 0 ? data!.pace!.target_bhd : null
  const chTile = cc?.channels ?? null
  const chips = (chTile?.chips || []).filter((c) => c.label === 'B2B' || c.label === 'B2C')
  const topCustomers = data?.top_customers_acc || []
  // ex-VAT accessories sales this month — the kickback basis — with gross kept for the tooltip
  const salesmen = (data?.by_salesman || []).map((s) => ({ ...s, name: s.salesman, rev: Number(s.net_bhd ?? s.revenue_bhd ?? 0), gross: Number(s.revenue_bhd || 0) }))
  const smScope = data?.by_salesman_scope
  const smMonth = smScope?.period ? new Date(Number(smScope.period.slice(0, 4)), Number(smScope.period.slice(5, 7)) - 1, 1).toLocaleDateString('en-GB', { month: 'long' }) : ''

  const hour = new Date().getHours()
  const daypart = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const firstName = (me?.full_name || me?.email || '').split(/[@ ]/)[0]
  const nActions = data?.actions?.length ?? 0
  const greeting = firstName ? `${daypart}, ${firstName.charAt(0).toUpperCase()}${firstName.slice(1)}` : daypart
  const focus = nActions > 0
    ? `${nActions} thing${nActions === 1 ? '' : 's'} need${nActions === 1 ? 's' : ''} your attention today`
    : 'All clear — nothing needs your attention right now'

  return (
    <div>
      <PageHeader title={greeting} subtitle={isLoading ? 'Mobile Accessories Intelligence' : focus} />

      {/* Stale-data guard: the header's freshness chip says the date; this says what to do about it */}
      {fresh?.stale && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm font-medium text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
          <TriangleAlert size={16} className="shrink-0" />
          {fresh.focus_to
            ? `Focus data is ${fresh.focus_days_behind ?? '—'} day(s) old (to ${fresh.focus_label || dayLabel(fresh.focus_to)})`
            : 'No Focus sales are loaded'} — upload the latest Focus exports for accurate figures.
        </div>
      )}

      {/* Today's priority actions — the morning brief, on screen */}
      {!!data?.actions?.length && (
        <Card className="mb-5 p-5">
          <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
            <ListChecks size={18} className="text-primary" /> Today's priority actions
          </div>
          <div className="space-y-1">
            {data.actions.map((a, i) => (
              <Link key={i} to={a.to}
                className="group flex items-center gap-3 rounded-lg px-2 py-2 text-sm transition hover:bg-accent/50">
                <span className={cn('h-2 w-2 shrink-0 rounded-full',
                  a.urgency >= 3 ? 'bg-rose-500' : a.urgency === 2 ? 'bg-amber-500' : 'bg-slate-400')} />
                <span className="min-w-0 flex-1 truncate font-medium" title={a.action}>{a.action}</span>
                {a.bhd > 0 && <span className="shrink-0 tabular-nums text-muted-foreground">{bhd(a.bhd, 0)}</span>}
                <ArrowRight size={15} className="shrink-0 text-muted-foreground opacity-0 transition group-hover:opacity-100" />
              </Link>
            ))}
          </div>
        </Card>
      )}

      {/* KPI row */}
      {isLoading || !k ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-[140px]" />)}
        </div>
      ) : (
        <motion.div variants={container} initial="hidden" animate="show"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <KpiCard accent={ACCENTS.purple} icon={DollarSign} label="Accessories sales this month · ex-VAT" hero to="/sales"
            basis={salesMtd?.basis}
            value={salesMtd?.value != null ? <CountUp value={salesMtd.value} format={(n) => bhd(n, 0)} /> : '—'}
            foot={salesMtd
              ? <Delta pct={salesMtd.delta_pct} vs={cc?.compare_basis || salesMtd.compare?.label} />
              : <span className="text-muted-foreground">Not available just now · the Command Centre figures did not load</span>} />
          <KpiCard accent={ACCENTS.blue} icon={CalendarDays} label={cc?.day ? `Latest day · ${dayLabel(cc.day.end)}` : 'Latest day'} to="/sales"
            basis={salesDay?.basis}
            value={salesDay?.value != null ? <CountUp value={salesDay.value} format={(n) => bhd(n, 0)} /> : '—'}
            foot={salesDay ? (
              <span className="text-muted-foreground">
                Accessories ex-VAT · <Delta pct={salesDay.delta_pct}
                  vs={salesDay.compare ? `${cc?.day_compare || salesDay.compare.label} (${bhd(salesDay.compare.value, 0)})` : cc?.day_compare} />
                {' '}· {num(salesDay.invoices)} invoices
              </span>
            ) : <span className="text-muted-foreground">Accessories · ex-VAT · not available just now</span>} />
          <KpiCard accent={ACCENTS.slate} icon={FileText} label="Focus invoices this month (Accessories)" to="/sales"
            basis={salesMtd?.basis}
            value={salesMtd?.invoices != null ? <CountUp value={salesMtd.invoices} /> : '—'}
            foot={<span className="text-muted-foreground">Month to date{cc?.focus ? ` · ${cc.focus.label}` : ''} · the Focus sales day book</span>} />
          <KpiCard accent={ACCENTS.green} icon={Landmark} label={arLabel} to="/receivables"
            basis={arTile?.basis}
            value={arValue != null ? <CountUp value={arValue} format={(n) => bhd(n, 0)} /> : '—'}
            foot={<span className="text-muted-foreground">
              {arOver90?.value != null && <><span className="font-semibold text-rose-600">{arOver90.value.toFixed(1)}% over 90 days</span>{' '}· </>}
              {bhd(k.overdue_total_bhd, 0)} overdue &gt;30d · {k.overdue_count} accts</span>} />
          <KpiCard accent={ACCENTS.amber} icon={Boxes} label="Low-stock items" to="/inventory"
            value={<CountUp value={k.low_stock_count} />}
            foot={<span className="font-medium text-amber-600">&lt; 30 days cover</span>} />
        </motion.div>
      )}

      {/* This month, day by day — daily sales + pace vs target + cash/credit/division split */}
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
                    data.pace.on_track ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                      : 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300')}>
                    {data.pace.target_pct}% of {bhd(data.pace.target_bhd, 0)} target
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">Set a monthly target in Settings →</span>
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
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={data.daily_mtd} margin={{ top: 10, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval="preserveStartEnd"
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
                  width={44} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v}`)} />
                <Tooltip
                  formatter={(v, name) => (name === barKey ? [bhd(Number(v)), exVat ? 'Accessories ex-VAT' : 'Accessories'] : [String(v), String(name)])}
                  labelFormatter={(d) => fmtDate(String(d))}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                {dailyTarget && (
                  <ReferenceLine y={dailyTarget} stroke="#d97706" strokeDasharray="5 4"
                    label={{ value: `target per business day ${bhd(dailyTarget, 0)}`, position: 'insideTopRight', fontSize: 10, fill: '#d97706' }} />
                )}
                <Bar dataKey={barKey} radius={[4, 4, 0, 0]} maxBarSize={26}>
                  {data.daily_mtd.map((r, i) => (
                    <Cell key={i} fill={dailyTarget && Number(r.acc_net_bhd ?? r.acc_bhd ?? r.gross_bhd) >= dailyTarget ? '#059669' : '#7c3aed'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <ResponsiveContainer width="100%" height={190}>
              <ComposedChart data={cumSeries} margin={{ top: 10, right: 8, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="cumFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#7c3aed" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval="preserveStartEnd"
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
                  width={44} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v}`)} />
                <Tooltip
                  formatter={(v, name) => [bhd(Number(v), 0), name === 'cum_bhd' ? (exVat ? 'Accessories MTD ex-VAT' : 'Accessories MTD') : 'Target to date (business days)']}
                  labelFormatter={(d) => fmtDate(String(d))}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Area type="monotone" dataKey="cum_bhd" stroke="#7c3aed" strokeWidth={2.5} fill="url(#cumFill)" />
                {dailyTarget && (
                  <Line type="monotone" dataKey="cum_target" stroke="#d97706" strokeWidth={2}
                    strokeDasharray="6 4" dot={false} />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          )}
          {(data.by_payment?.length || data.by_division?.length) && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3 text-[13px]">
              {/* these chips are the Focus month as invoiced: VAT-inclusive, every division (SIM included) */}
              <span className="text-[11px] text-muted-foreground">Month to date · gross, VAT-incl · every division:</span>
              {(data.by_payment || []).map((p) => (
                <span key={p.sale_type} className="inline-flex items-center gap-1.5 rounded-full border bg-secondary/40 px-3 py-1">
                  <span className={cn('h-2 w-2 rounded-full', p.sale_type === 'cash' ? 'bg-emerald-500' : 'bg-blue-500')} />
                  <span className="capitalize">{p.sale_type}</span>
                  <b className="tabular-nums">{bhd(p.revenue_bhd, 0)}</b>
                </span>
              ))}
              <span className="mx-1 hidden h-4 w-px bg-border sm:block" />
              {(data.by_division || []).map((d) => (
                <span key={d.division} className="inline-flex items-center gap-1.5 rounded-full border bg-secondary/40 px-3 py-1">
                  <span className="text-muted-foreground">{d.division}</span>
                  <b className="tabular-nums">{bhd(d.revenue_bhd, 0)}</b>
                  {Number(d.giveaway_qty) > 0 && (
                    <span className="text-[11px] text-amber-600">+{num(d.giveaway_qty)} free</span>
                  )}
                </span>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Business health — the CEO truth the totals hide: margin, cash speed, frozen capital */}
      {data?.health && (
        <div className={cn('mt-4 grid grid-cols-1 gap-4', data.health.cost_hidden ? 'sm:grid-cols-2' : 'sm:grid-cols-3')}>
          {/* margin and gross profit are for admins and 'Margins' logins only (the API sends null otherwise) */}
          {!data.health.cost_hidden && (
          <HealthStat icon={Percent} label="Gross margin · ex-VAT on Focus COGS" tone={(data.health.gp_pct ?? 0) < 20 ? 'amber' : 'green'}
            value={data.health.margin_available === false || data.health.gp_pct == null ? '—' : `${data.health.gp_pct.toFixed(1)}%`}
            sub={<>{bhd(data.health.gp_bhd, 0)} GP · every item costed
              {data.health.landed_gp_pct != null && <> · landed {data.health.landed_gp_pct.toFixed(1)}% on {Math.round(data.health.landed_coverage_pct ?? 0)}% of sales</>}
              {(data.health.below_cost_count ?? 0) > 0 && <> · <span className="font-semibold text-rose-600">{data.health.below_cost_count} below cost</span></>}</>}
            to="/margins" />
          )}
          <HealthStat icon={Clock} label="Collection speed · DSO" tone={data.health.ar_overdue_pct > 40 ? 'red' : 'amber'}
            value={`${Math.round(data.health.dso_days)} days`}
            sub={<><span className="font-semibold text-rose-600">{data.health.ar_overdue_pct.toFixed(0)}%</span> of receivables overdue — chase to free cash</>} to="/receivables" />
          <HealthStat icon={Snowflake} label={data.health.cost_hidden ? 'Dead stock · at selling price' : 'Capital frozen in dead stock · at cost'} tone="red"
            value={data.health.stock_basis === 'cost' && data.health.dead_stock_bhd != null ? bhd(data.health.dead_stock_bhd, 0)
              : data.health.cost_hidden && data.health.dead_stock_sell_bhd != null ? bhd(data.health.dead_stock_sell_bhd, 0) : '—'}
            sub={<>{data.health.dead_stock_count} items not selling — liquidate to release cash
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
          {isLoading ? <Skeleton className="h-[260px]" /> : trendAcc.length === 0 ? (
            <p className="grid h-[260px] place-items-center text-sm text-muted-foreground">Monthly Accessories sales could not be read just now.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={trendAcc} margin={{ top: 14, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="m" tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false}
                  width={48} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`)} />
                <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Accessories ex-VAT']}
                  labelFormatter={(m, payload) => {
                    const row = payload?.[0]?.payload as { partial?: boolean; through?: string | null } | undefined
                    return row?.partial && row.through ? `${m} · so far (to ${dayLabel(row.through)})` : String(m)
                  }}
                  cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                {monthTarget && (
                  <ReferenceLine y={monthTarget} stroke="#d97706" strokeDasharray="5 4" ifOverflow="extendDomain"
                    label={{ value: `monthly target ${bhd(monthTarget, 0)}`, position: 'insideTopRight', fontSize: 10, fill: '#d97706' }} />
                )}
                <Bar dataKey="acc_net_bhd" radius={[4, 4, 0, 0]} maxBarSize={34}>
                  {/* the month still running is lighter: it is not a whole month yet */}
                  {trendAcc.map((r, i) => <Cell key={i} fill={r.partial ? '#c4b5fd' : '#7c3aed'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-5">
          <div className="font-display text-base font-semibold">Sales by channel</div>
          <div className="mb-2 text-xs text-muted-foreground">Accessories · ex-VAT · month to date · B2C = Causeway + Roadshow</div>
          {isLoading ? <Skeleton className="h-[220px]" /> : !chTile ? (
            <p className="text-sm text-muted-foreground">Not available just now · the Command Centre figures did not load.</p>
          ) : (
            <div title={chTile.basis}>
              <div className="relative">
                <ResponsiveContainer width="100%" height={168}>
                  <PieChart>
                    <Pie data={chips.map((c) => ({ name: c.label, value: Number(c.value) || 0 }))}
                      dataKey="value" nameKey="name" innerRadius={54} outerRadius={78} paddingAngle={2} stroke="none">
                      {chips.map((c, i) => <Cell key={i} fill={c.label === 'B2C' ? '#8b5cf6' : '#3b82f6'} />)}
                    </Pie>
                    <Tooltip formatter={(v) => [bhd(Number(v), 0), 'Accessories ex-VAT']}
                      contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                  </PieChart>
                </ResponsiveContainer>
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
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: isB2C ? '#8b5cf6' : '#3b82f6' }} />
                        {isB2C ? <Store size={14} className="text-violet-500" /> : <Truck size={14} className="text-blue-500" />}
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
          {isLoading ? <Skeleton className="h-[260px]" /> : smScope?.error ? (
            <p className="text-sm text-destructive">{smScope.error}</p>
          ) : salesmen.length === 0 ? (
            <p className="text-sm text-muted-foreground">No accessories sales loaded for this month yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={salesmen} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
                <XAxis type="number" hide tickFormatter={(v) => bhd(v, 0)} />
                <YAxis type="category" dataKey="name" width={110} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(value, _name, item) => [`${bhd(Number(value), 3)} ex-VAT · ${bhd(Number((item?.payload as { gross?: number } | undefined)?.gross || 0), 3)} gross`, 'Accessories']} cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Bar dataKey="rev" radius={[0, 6, 6, 0]}>
                  {salesmen.map((s, i) => <Cell key={i} fill={s.no_target ? '#c4b5fd' : i === 0 ? '#7c3aed' : '#a78bfa'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-5">
          <div className="flex items-center gap-2 font-display text-base font-semibold">
            <Crown size={18} className="text-amber-500" /> Top customers
          </div>
          <div className="mb-3 text-[12px] text-muted-foreground">Accessories · ex-VAT · month to date · Cash Customer left out</div>
          {isLoading ? (
            <div className="space-y-3">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10" />)}</div>
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
          <div className="grid gap-x-10 gap-y-4 md:grid-cols-2">
            <MoverList title="Rising — restock & push" rows={data.movers.rising} up />
            <MoverList title="Fading — investigate before it goes dead" rows={data.movers.falling} />
          </div>
        </Card>
      )}

      {/* Alerts strip */}
      {k && (
        <Card className="mt-5 flex flex-wrap items-center gap-x-8 gap-y-3 p-5">
          <div className="flex items-center gap-2 text-sm">
            <TriangleAlert size={16} className="text-amber-500" />
            <span className="font-semibold">{num(k.low_stock_count)}</span>
            <span className="text-muted-foreground">items under 30 days cover</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <Landmark size={16} className="text-rose-500" />
            <span className="font-semibold">{bhd(k.overdue_total_bhd, 0)}</span>
            <span className="text-muted-foreground">overdue &gt;30 days across {k.overdue_count} accounts</span>
          </div>
          {data?.alerts?.negative_margin_count != null && (
          <div className="flex items-center gap-2 text-sm">
            <TrendingDown size={16} className="text-violet-500" />
            <span className="font-semibold">{num(data.alerts.negative_margin_count)}</span>
            <span className="text-muted-foreground">products below cost</span>
          </div>
          )}
        </Card>
      )}
    </div>
  )
}
