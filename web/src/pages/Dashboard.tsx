import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Area, AreaChart, Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { motion } from 'motion/react'
import {
  DollarSign, FileText, Boxes, Landmark, TrendingUp, TrendingDown, Crown, TriangleAlert,
  CalendarDays, Bot, Store, Truck, ListChecks, ArrowRight, Percent, Clock, Snowflake,
  Flame, ArrowUpRight, ArrowDownRight,
} from 'lucide-react'
import { apiGet } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { bhd, num, monthLabel, fmtDate } from '@/lib/format'
import { CountUp } from '@/components/CountUp'
import { DataBanner } from '@/components/DataBanner'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface Kpis {
  rev_today: number; net_today: number; orders_today: number
  rev_yesterday: number; orders_yesterday: number
  rev_mtd: number; net_mtd: number; orders_mtd: number; rev_prev_month: number
  total_receivables: number; low_stock_count: number
  overdue_count: number; overdue_total_bhd: number
  current_receivables_bhd?: number
}
interface ChannelRow { channel: string; orders: number; qty: number; revenue_bhd: number; net_bhd: number }
interface SalesmanRow { salesman: string; orders: number; qty: number; revenue_bhd: number; net_bhd: number }
interface AgentRow { agent: string; last_run: string; summary: string }
interface ActionItem { action: string; to: string; bhd: number; urgency: number }
interface Health {
  gp_bhd: number; gp_pct: number; ar_overdue_pct: number
  dso_days: number; dead_stock_bhd: number; dead_stock_count: number
  margin_basis?: string; cost_coverage_pct?: number; below_cost_count?: number
}
interface MoverRow { item_name: string; sold_30d: number; sold_90d: number; momentum: number; status?: string }
interface DailyRow { day: string; gross_bhd: number; net_bhd: number; orders: number }
interface PaymentRow { sale_type: string; orders: number; revenue_bhd: number }
interface DivisionRow { division: string; orders: number; revenue_bhd: number; giveaway_qty: number }
interface Pace {
  target_bhd: number; mtd_bhd: number; prev_month_bhd: number
  projected_bhd: number | null; target_pct: number | null; on_track: boolean | null
}
interface DashboardData {
  data_as_of?: string | null
  data_stale?: boolean
  data_days_behind?: number | null
  actions?: ActionItem[]
  health?: Health
  movers?: { rising: MoverRow[]; falling: MoverRow[] }
  kpis: Kpis
  top_customers: { customer_name: string; total_revenue_bhd: number; order_count: number }[]
  revenue_trend: { period_month: string; gross_bhd: number; net_revenue_bhd: number }[]
  by_channel: ChannelRow[]
  by_salesman: SalesmanRow[]
  agents: AgentRow[]
  alerts: { negative_margin_count: number }
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

function agentLabel(a: string) {
  return a.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
function relTime(iso?: string) {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  const h = Math.floor(ms / 3.6e6)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function KpiCard({ accent, icon: Icon, label, value, foot, hero, to }: {
  accent: string; icon: typeof DollarSign; label: string; value: React.ReactNode
  foot?: React.ReactNode; hero?: boolean; to?: string
}) {
  const card = (
    <Card className="group relative flex h-full flex-col overflow-hidden p-4 transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-luxe-hover">
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
              <span className="truncate font-medium">{r.item_name}</span>
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
  const { data, isLoading } = useQuery({
    queryKey: ['report', 'dashboard'],
    queryFn: () => apiGet<DashboardData>('/report/dashboard'),
  })

  const k = data?.kpis
  const deltaPct = k && k.rev_prev_month > 0 ? ((k.rev_mtd - k.rev_prev_month) / k.rev_prev_month) * 100 : 0
  const up = deltaPct >= 0
  const trend = (data?.revenue_trend || []).map((r) => ({ ...r, m: monthLabel(r.period_month) }))
  const channels = data?.by_channel || []
  const channelTotal = channels.reduce((s, c) => s + Number(c.revenue_bhd || 0), 0) || 1
  const salesmen = (data?.by_salesman || []).map((s) => ({ ...s, name: s.salesman, rev: Number(s.revenue_bhd || 0) }))

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
      <DataBanner date={data?.data_as_of} />

      {/* Stale-data guard */}
      {data?.data_stale && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm font-medium text-amber-700">
          <TriangleAlert size={16} className="shrink-0" />
          Data is {data.data_days_behind ?? '—'} day(s) old — upload the latest Focus exports for accurate figures.
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
                <span className="flex-1 font-medium">{a.action}</span>
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
          <KpiCard accent={ACCENTS.purple} icon={DollarSign} label="Revenue this month (gross)" hero to="/sales"
            value={<CountUp value={k.rev_mtd} format={(n) => bhd(n, 0)} />}
            foot={<span className={up ? 'font-semibold text-emerald-600' : 'font-semibold text-rose-600'}>
              {up ? <TrendingUp className="mr-1 inline" size={14} /> : <TrendingDown className="mr-1 inline" size={14} />}
              {up ? '+' : ''}{deltaPct.toFixed(1)}% MoM · ex-VAT {bhd(k.net_mtd, 0)}</span>} />
          <KpiCard accent={ACCENTS.blue} icon={CalendarDays} label="Latest day" to="/sales"
            value={<CountUp value={k.rev_today} format={(n) => bhd(n, 0)} />}
            foot={<span className="text-muted-foreground">Yesterday {bhd(k.rev_yesterday, 0)} · {k.orders_today} orders</span>} />
          <KpiCard accent={ACCENTS.slate} icon={FileText} label="Orders this month" to="/sales"
            value={<CountUp value={k.orders_mtd} />}
            foot={<span className="text-muted-foreground">Invoices processed</span>} />
          <KpiCard accent={ACCENTS.green} icon={Landmark} label="Receivables (total)" to="/receivables"
            value={<CountUp value={k.total_receivables} format={(n) => bhd(n, 0)} />}
            foot={<span className="text-muted-foreground">
              <span className="font-semibold text-rose-600">{bhd(k.overdue_total_bhd, 0)} overdue &gt;30d</span>
              {' '}· {bhd(k.current_receivables_bhd ?? Math.max(k.total_receivables - k.overdue_total_bhd, 0), 0)} current · {k.overdue_count} accts</span>} />
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
              <div className="text-xs text-muted-foreground">Gross sales per day (VAT-incl) · {monthLabel(data.data_as_of || '')}</div>
            </div>
            {data.pace && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="text-muted-foreground">MTD <b className="text-foreground tabular-nums">{bhd(data.pace.mtd_bhd, 0)}</b></span>
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
              </div>
            )}
          </div>
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={data.daily_mtd} margin={{ top: 10, right: 8, left: 8, bottom: 0 }}>
              <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval="preserveStartEnd"
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
                width={44} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v}`)} />
              <Tooltip
                formatter={(v, name) => (name === 'gross_bhd' ? [bhd(Number(v)), 'Gross'] : [String(v), String(name)])}
                labelFormatter={(d) => fmtDate(String(d))}
                contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
              <Bar dataKey="gross_bhd" fill="#7c3aed" radius={[4, 4, 0, 0]} maxBarSize={26} />
            </BarChart>
          </ResponsiveContainer>
          {(data.by_payment?.length || data.by_division?.length) && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3 text-[13px]">
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
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <HealthStat icon={Percent} label="True gross margin · landed cost" tone={data.health.gp_pct < 20 ? 'amber' : 'green'}
            value={`${data.health.gp_pct.toFixed(1)}%`}
            sub={<>{bhd(data.health.gp_bhd, 0)} GP · {Math.round(data.health.cost_coverage_pct ?? 0)}% of revenue costed
              {(data.health.below_cost_count ?? 0) > 0 && <> · <span className="font-semibold text-rose-600">{data.health.below_cost_count} below cost</span></>}</>}
            to="/margins" />
          <HealthStat icon={Clock} label="Collection speed · DSO" tone={data.health.ar_overdue_pct > 40 ? 'red' : 'amber'}
            value={`${Math.round(data.health.dso_days)} days`}
            sub={<><span className="font-semibold text-rose-600">{data.health.ar_overdue_pct.toFixed(0)}%</span> of receivables overdue — chase to free cash</>} to="/receivables" />
          <HealthStat icon={Snowflake} label="Capital frozen in dead stock" tone="red"
            value={bhd(data.health.dead_stock_bhd, 0)}
            sub={<>{data.health.dead_stock_count} items not selling — liquidate to release cash</>} to="/inventory" />
        </div>
      )}

      {/* Trend + channel split */}
      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <div className="mb-1 font-display text-base font-semibold">Revenue trend</div>
          <div className="mb-4 text-xs text-muted-foreground">Gross revenue by month (VAT-incl)</div>
          {isLoading ? <Skeleton className="h-[260px]" /> : (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={trend} margin={{ top: 6, right: 8, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#7c3aed" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="m" tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false}
                  width={48} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`)} />
                <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Gross']}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Area type="monotone" dataKey="gross_bhd" stroke="#7c3aed" strokeWidth={2.5} fill="url(#rev)" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-5">
          <div className="mb-2 font-display text-base font-semibold">Sales by channel</div>
          {isLoading ? <Skeleton className="h-[220px]" /> : (
            <div>
              <div className="relative">
                <ResponsiveContainer width="100%" height={168}>
                  <PieChart>
                    <Pie data={channels.map((c) => ({ name: c.channel, value: Number(c.revenue_bhd) || 0 }))}
                      dataKey="value" nameKey="name" innerRadius={54} outerRadius={78} paddingAngle={2} stroke="none">
                      {channels.map((c, i) => <Cell key={i} fill={c.channel === 'B2C' ? '#8b5cf6' : '#3b82f6'} />)}
                    </Pie>
                    <Tooltip formatter={(v) => [bhd(Number(v), 0), 'Gross']}
                      contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                  <div className="font-display text-lg font-extrabold tabular-nums">{bhd(channelTotal, 0)}</div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">total gross</div>
                </div>
              </div>
              <div className="mt-2 space-y-1.5">
                {channels.map((c) => {
                  const isB2C = c.channel === 'B2C'
                  const share = (Number(c.revenue_bhd) / channelTotal) * 100
                  return (
                    <div key={c.channel} className="flex items-center justify-between text-sm">
                      <span className="flex items-center gap-2 font-medium">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: isB2C ? '#8b5cf6' : '#3b82f6' }} />
                        {isB2C ? <Store size={14} className="text-violet-500" /> : <Truck size={14} className="text-blue-500" />}
                        {c.channel} · {isB2C ? 'Retail' : 'Wholesale'}
                      </span>
                      <span className="font-semibold tabular-nums">{bhd(c.revenue_bhd, 0)} <span className="text-muted-foreground">({share.toFixed(0)}%)</span></span>
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
          <div className="mb-4 font-display text-base font-semibold">Top salesmen (gross revenue)</div>
          {isLoading ? <Skeleton className="h-[260px]" /> : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={salesmen} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
                <XAxis type="number" hide tickFormatter={(v) => bhd(v, 0)} />
                <YAxis type="category" dataKey="name" width={110} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Gross']} cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Bar dataKey="rev" radius={[0, 6, 6, 0]}>
                  {salesmen.map((_, i) => <Cell key={i} fill={i === 0 ? '#7c3aed' : '#a78bfa'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-5">
          <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
            <Crown size={18} className="text-amber-500" /> Top customers
          </div>
          {isLoading ? (
            <div className="space-y-3">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10" />)}</div>
          ) : (data?.top_customers || []).length === 0 ? (
            <p className="text-sm text-muted-foreground">No named-account orders this month.</p>
          ) : (
            <ul className="space-y-1.5">
              {(data?.top_customers || []).slice(0, 7).map((c, i) => (
                <li key={i} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 hover:bg-accent/50">
                  <span className="flex min-w-0 items-center gap-2.5">
                    <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-bold text-accent-foreground">{i + 1}</span>
                    <span className="truncate text-sm font-medium">{c.customer_name}</span>
                  </span>
                  <span className="shrink-0 text-sm font-semibold text-primary">{bhd(c.total_revenue_bhd, 0)}</span>
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

      {/* Agent Performance panel */}
      <Card className="mt-5 p-5">
        <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
          <Bot size={18} className="text-primary" /> AI Agent Team
          <span className="ml-1 rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-accent-foreground">
            {(data?.agents || []).length} active
          </span>
        </div>
        {isLoading ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-20" />)}</div>
        ) : (data?.agents || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No agent runs recorded yet.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(data?.agents || []).map((a) => (
              <div key={a.agent} className="rounded-xl border bg-card p-3.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm font-semibold">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" /> {agentLabel(a.agent)}
                  </div>
                  <span className="text-[11px] text-muted-foreground">{relTime(a.last_run)}</span>
                </div>
                <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-snug text-muted-foreground">{a.summary}</p>
              </div>
            ))}
          </div>
        )}
      </Card>

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
            <span className="text-muted-foreground">overdue across {k.overdue_count} accounts</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <TrendingDown size={16} className="text-violet-500" />
            <span className="font-semibold">{num(data?.alerts?.negative_margin_count ?? 0)}</span>
            <span className="text-muted-foreground">products below cost</span>
          </div>
          <div className="ml-auto text-[11px] text-muted-foreground">Data as of {fmtDate(data?.data_as_of)}</div>
        </Card>
      )}
    </div>
  )
}
