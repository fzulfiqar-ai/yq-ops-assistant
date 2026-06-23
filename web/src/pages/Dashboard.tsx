import { useQuery } from '@tanstack/react-query'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { motion } from 'motion/react'
import { DollarSign, FileText, Boxes, Landmark, TrendingUp, TrendingDown, Crown, TriangleAlert } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { bhd, num, monthLabel } from '@/lib/format'
import { CountUp } from '@/components/CountUp'
import { DataBanner } from '@/components/DataBanner'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface Kpis {
  rev_mtd: number
  rev_prev_month: number
  orders_mtd: number
  total_receivables: number
  low_stock_count: number
  overdue_count: number
  overdue_total_bhd: number
}
interface DashboardData {
  data_as_of?: string | null
  kpis: Kpis
  top_customers: { customer_name: string; total_revenue_bhd: number; order_count: number }[]
  revenue_trend: { period_month: string; net_revenue_bhd: number; order_count: number }[]
  alerts: { negative_margin_count: number }
}

const container = { hidden: {}, show: { transition: { staggerChildren: 0.07 } } }
const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] as const } },
}

const ACCENTS = {
  purple: 'linear-gradient(90deg,#7c3aed,#a78bfa)',
  blue: 'linear-gradient(90deg,#2563eb,#60a5fa)',
  amber: 'linear-gradient(90deg,#d97706,#fbbf24)',
  green: 'linear-gradient(90deg,#059669,#34d399)',
}

function KpiCard({
  accent,
  icon: Icon,
  label,
  value,
  foot,
}: {
  accent: string
  icon: typeof DollarSign
  label: string
  value: React.ReactNode
  foot?: React.ReactNode
}) {
  return (
    <motion.div variants={item}>
      <Card className="relative h-full overflow-hidden p-5 transition-shadow hover:shadow-lift">
        <div className="absolute inset-x-0 top-0 h-1" style={{ background: accent }} />
        <Icon className="text-muted-foreground" size={22} />
        <div className="mt-3 font-display text-[1.7rem] font-extrabold leading-none tracking-tight">{value}</div>
        <div className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
        {foot && <div className="mt-2 text-[13px]">{foot}</div>}
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

  return (
    <div>
      <PageHeader title="AI Operations Center" subtitle="Mobile Accessories Intelligence" />
      <DataBanner date={data?.data_as_of} />

      {isLoading || !k ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-[140px]" />
          ))}
        </div>
      ) : (
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
        >
          <KpiCard
            accent={ACCENTS.purple}
            icon={DollarSign}
            label="Revenue this month"
            value={<CountUp value={k.rev_mtd} format={(n) => bhd(n, 0)} />}
            foot={
              <span className={up ? 'font-semibold text-emerald-600' : 'font-semibold text-rose-600'}>
                {up ? <TrendingUp className="mr-1 inline" size={14} /> : <TrendingDown className="mr-1 inline" size={14} />}
                {up ? '+' : ''}
                {deltaPct.toFixed(1)}% vs last month
              </span>
            }
          />
          <KpiCard
            accent={ACCENTS.blue}
            icon={FileText}
            label="Orders this month"
            value={<CountUp value={k.orders_mtd} />}
            foot={<span className="text-muted-foreground">Invoices processed</span>}
          />
          <KpiCard
            accent={ACCENTS.amber}
            icon={Boxes}
            label="Low-stock items"
            value={<CountUp value={k.low_stock_count} />}
            foot={<span className="font-medium text-amber-600">Needs attention</span>}
          />
          <KpiCard
            accent={ACCENTS.green}
            icon={Landmark}
            label="Outstanding receivables"
            value={<CountUp value={k.total_receivables} format={(n) => bhd(n, 0)} />}
            foot={<span className="text-muted-foreground">{k.overdue_count} accounts overdue</span>}
          />
        </motion.div>
      )}

      {/* Revenue trend + top customers */}
      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <div className="mb-1 font-display text-base font-semibold">Revenue trend</div>
          <div className="mb-4 text-xs text-muted-foreground">Net revenue by month</div>
          {isLoading ? (
            <Skeleton className="h-[260px]" />
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={trend} margin={{ top: 6, right: 8, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#7c3aed" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="m" tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  width={48}
                  tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`)}
                />
                <Tooltip
                  formatter={(value) => [bhd(Number(value), 0), 'Revenue']}
                  contentStyle={{
                    borderRadius: 12,
                    border: '1px solid hsl(var(--border))',
                    background: 'hsl(var(--card))',
                    color: 'hsl(var(--foreground))',
                    fontSize: 13,
                  }}
                />
                <Area type="monotone" dataKey="net_revenue_bhd" stroke="#7c3aed" strokeWidth={2.5} fill="url(#rev)" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-5">
          <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
            <Crown size={18} className="text-amber-500" /> Top customers
          </div>
          {isLoading ? (
            <div className="space-y-3">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : (data?.top_customers || []).length === 0 ? (
            <p className="text-sm text-muted-foreground">No orders this month yet.</p>
          ) : (
            <ul className="space-y-1.5">
              {(data?.top_customers || []).slice(0, 6).map((c, i) => (
                <li key={i} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 hover:bg-accent/50">
                  <span className="flex min-w-0 items-center gap-2.5">
                    <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-bold text-accent-foreground">
                      {i + 1}
                    </span>
                    <span className="truncate text-sm font-medium">{c.customer_name}</span>
                  </span>
                  <span className="shrink-0 text-sm font-semibold text-primary">{bhd(c.total_revenue_bhd, 0)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Alerts strip */}
      {k && (
        <Card className="mt-5 flex flex-wrap items-center gap-x-8 gap-y-3 p-5">
          <div className="flex items-center gap-2 text-sm">
            <TriangleAlert size={16} className="text-amber-500" />
            <span className="font-semibold">{num(k.low_stock_count)}</span>
            <span className="text-muted-foreground">low-stock items</span>
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
        </Card>
      )}
    </div>
  )
}
