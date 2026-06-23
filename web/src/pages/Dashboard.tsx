import { useQuery } from '@tanstack/react-query'
import { Area, AreaChart, Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { motion } from 'motion/react'
import {
  DollarSign, FileText, Boxes, Landmark, TrendingUp, TrendingDown, Crown, TriangleAlert,
  CalendarDays, Bot, Store, Truck,
} from 'lucide-react'
import { apiGet } from '@/lib/api'
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
}
interface ChannelRow { channel: string; orders: number; qty: number; revenue_bhd: number; net_bhd: number }
interface SalesmanRow { salesman: string; orders: number; qty: number; revenue_bhd: number; net_bhd: number }
interface AgentRow { agent: string; last_run: string; summary: string }
interface DashboardData {
  data_as_of?: string | null
  kpis: Kpis
  top_customers: { customer_name: string; total_revenue_bhd: number; order_count: number }[]
  revenue_trend: { period_month: string; gross_bhd: number; net_revenue_bhd: number }[]
  by_channel: ChannelRow[]
  by_salesman: SalesmanRow[]
  agents: AgentRow[]
  alerts: { negative_margin_count: number }
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

function Sparkline({ data, color = '#7c3aed' }: { data: number[]; color?: string }) {
  const d = data.map((v, i) => ({ i, v }))
  const id = `sp-${color.replace('#', '')}`
  return (
    <ResponsiveContainer width="100%" height={34}>
      <AreaChart data={d} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.6} fill={`url(#${id})`} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function KpiCard({ accent, icon: Icon, label, value, foot, spark }: {
  accent: string; icon: typeof DollarSign; label: string; value: React.ReactNode; foot?: React.ReactNode; spark?: number[]
}) {
  return (
    <motion.div variants={item}>
      <Card className="relative flex h-full flex-col overflow-hidden p-5 transition-shadow hover:shadow-lift">
        <div className="absolute inset-x-0 top-0 h-1" style={{ background: accent }} />
        <Icon className="text-muted-foreground" size={20} />
        <div className="mt-3 font-display text-[1.6rem] font-extrabold leading-none tracking-tight tabular-nums">{value}</div>
        <div className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
        {foot && <div className="mt-2 text-[12.5px]">{foot}</div>}
        {spark && spark.length > 1 && <div className="-mx-1 mt-auto pt-3">{<Sparkline data={spark} />}</div>}
      </Card>
    </motion.div>
  )
}

export default function Dashboard() {
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

  return (
    <div>
      <PageHeader title="AI Operations Center" subtitle="Mobile Accessories Intelligence" />
      <DataBanner date={data?.data_as_of} />

      {/* KPI row */}
      {isLoading || !k ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-[140px]" />)}
        </div>
      ) : (
        <motion.div variants={container} initial="hidden" animate="show"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <KpiCard accent={ACCENTS.purple} icon={DollarSign} label="Revenue this month (gross)"
            value={<CountUp value={k.rev_mtd} format={(n) => bhd(n, 0)} />}
            spark={trend.map((t) => Number(t.gross_bhd) || 0)}
            foot={<span className={up ? 'font-semibold text-emerald-600' : 'font-semibold text-rose-600'}>
              {up ? <TrendingUp className="mr-1 inline" size={14} /> : <TrendingDown className="mr-1 inline" size={14} />}
              {up ? '+' : ''}{deltaPct.toFixed(1)}% MoM · ex-VAT {bhd(k.net_mtd, 0)}</span>} />
          <KpiCard accent={ACCENTS.blue} icon={CalendarDays} label="Latest day"
            value={<CountUp value={k.rev_today} format={(n) => bhd(n, 0)} />}
            foot={<span className="text-muted-foreground">Yesterday {bhd(k.rev_yesterday, 0)} · {k.orders_today} orders</span>} />
          <KpiCard accent={ACCENTS.slate} icon={FileText} label="Orders this month"
            value={<CountUp value={k.orders_mtd} />}
            foot={<span className="text-muted-foreground">Invoices processed</span>} />
          <KpiCard accent={ACCENTS.green} icon={Landmark} label="Receivables"
            value={<CountUp value={k.total_receivables} format={(n) => bhd(n, 0)} />}
            foot={<span className="text-muted-foreground">{k.overdue_count} accounts overdue</span>} />
          <KpiCard accent={ACCENTS.amber} icon={Boxes} label="Low-stock items"
            value={<CountUp value={k.low_stock_count} />}
            foot={<span className="font-medium text-amber-600">&lt; 30 days cover</span>} />
        </motion.div>
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
