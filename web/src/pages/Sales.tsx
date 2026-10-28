import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { Area, AreaChart, Bar, BarChart, Cell, Tooltip, XAxis, YAxis } from 'recharts'
import { CalendarDays, Store, Truck, UserRound, X } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd, num, monthLabel, fmtDate } from '@/lib/format'
import { panelState } from '@/lib/dataState'
import { useSkeletonFade } from '@/lib/motionOnce'
import { ChartBox, FadeIn } from '@/lib/motionKit'
import { REP_PARAM, matchSalesman } from '@/lib/searchRoute'
import { LoadError } from '@/components/LoadError'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, type Column } from '@/components/DataTable'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface Customer { customer_name: string; total_revenue_bhd: number; order_count: number; last_order_date: string }
interface Seller { item_name: string; category_name: string; qty: number; revenue_bhd: number }
interface Salesman { salesman: string; orders: number; qty: number; revenue_bhd: number; net_bhd: number }
interface Channel { channel: string; orders: number; qty: number; revenue_bhd: number; net_bhd: number }
interface Data {
  trend: { period_month: string; gross_bhd: number; net_revenue_bhd: number }[]
  by_salesman: Salesman[]
  by_channel: Channel[]
  top_sellers: Seller[]
  top_customers: Customer[]
  daily_by_salesman?: { day: string; salesman: string; gross_bhd: number }[]
}

/** Per-salesman daily gross this month. (Targets were retired 21-Sep-2026 until the owner's
 *  target file is loaded -- see scripts/import_targets.py.) `rep` is the salesman a link names
 *  (?rep=, the command palette's salesman result): he is the one shown until another is picked. */
function DailyBySalesman({ data, rep }: { data: Data; rep: string | null }) {
  const daily = useMemo(() => data.daily_by_salesman || [], [data.daily_by_salesman])
  const names = useMemo(() => [...new Set(daily.map((r) => r.salesman))].sort((a, b) => a.localeCompare(b)), [daily])
  // a pick belongs to the link it was made under: a new ?rep= starts from the rep it names
  const [pick, setPick] = useState<{ rep: string | null; name: string | null }>({ rep, name: null })
  const sel = pick.rep === rep ? pick.name : null
  const wanted = matchSalesman(names, rep)
  const who = sel && names.includes(sel) ? sel : wanted ?? names[0]
  const days = useMemo(() => {
    const dates = [...new Set(daily.map((r) => r.day))].sort()
    const mine = new Map(daily.filter((r) => r.salesman === who).map((r) => [r.day, Number(r.gross_bhd)]))
    return dates.map((d) => ({ day: d, gross_bhd: mine.get(d) ?? 0 }))
  }, [daily, who])

  if (!daily.length) return null
  const mtd = days.reduce((s, r) => s + r.gross_bhd, 0)

  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-display text-base font-semibold">
          <CalendarDays size={17} className="text-primary" /> Daily sales this month
        </div>
        <span className="rounded-full bg-secondary px-2.5 py-0.5 text-[12px] font-semibold tabular-nums">
          MTD {bhd(mtd, 0)}
        </span>
      </div>
      <div role="group" aria-label="Salesman" className="mb-3 flex flex-wrap gap-1.5">
        {names.map((n) => (
          <button key={n} type="button" onClick={() => setPick({ rep, name: n })} aria-pressed={n === who}
            className={cn('rounded-full border px-3 py-1 text-[12.5px] font-medium transition',
              n === who ? 'border-primary bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:bg-accent/50')}>
            {n}
          </button>
        ))}
      </div>
      <ChartBox id="sales.daily" height={180}>{(m) => (
        <BarChart data={days} margin={{ top: 10, right: 8, left: 8, bottom: 0 }}>
          <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval="preserveStartEnd"
            tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
            width={44} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v}`)} />
          <Tooltip formatter={(v) => [bhd(Number(v)), 'Gross']} labelFormatter={(d) => fmtDate(String(d))}
            contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
          <Bar {...m} dataKey="gross_bhd" radius={[4, 4, 0, 0]} maxBarSize={24} fill="#7c3aed" />
        </BarChart>
      )}</ChartBox>
    </Card>
  )
}

/** The page while its one read is under way: the cards in their places, at their heights (it was a 60vh block). */
function SalesSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading sales">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2"><Skeleton className="h-5 w-48" /><Skeleton className="mt-4 h-[260px]" /></Card>
        <Card className="p-5">
          <Skeleton className="h-5 w-32" />
          <div className="mt-4 space-y-5">
            {[0, 1].map((i) => (
              <div key={i}><Skeleton className="h-4" /><Skeleton className="mt-2 h-2" /><Skeleton className="mt-2 h-3 w-40" /></div>
            ))}
          </div>
        </Card>
      </div>
      <Card className="p-5"><Skeleton className="h-5 w-48" /><Skeleton className="mt-3 h-7 w-80 max-w-full" /><Skeleton className="mt-3 h-[180px]" /></Card>
      <Card className="p-5"><Skeleton className="h-5 w-64" /><Skeleton className="mt-4 h-[300px]" /></Card>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <Card key={i} className="p-5"><Skeleton className="h-5 w-48" /><Skeleton className="mt-4 h-9 w-40" /><Skeleton className="mt-3 h-56" /></Card>
        ))}
      </div>
    </div>
  )
}

const custCols: Column<Customer>[] = [
  { key: 'customer_name', label: 'Customer' },
  { key: 'order_count', label: 'Orders', align: 'right' },
  { key: 'last_order_date', label: 'Last order', align: 'right', render: (v) => fmtDate(v as string) },
  { key: 'total_revenue_bhd', label: 'Revenue', align: 'right', money: true },
]
const sellerCols: Column<Seller>[] = [
  { key: 'item_name', label: 'Item' },
  { key: 'category_name', label: 'Category' },
  { key: 'qty', label: 'Units (90d)', align: 'right', render: (v) => num(Number(v)) },
  { key: 'revenue_bhd', label: 'Revenue', align: 'right', money: true },
]

export default function Sales() {
  const { data, isError, error, refetch, isFetching } = useQuery({ queryKey: ['report', 'sales'], queryFn: () => apiGet<Data>('/report/sales') })
  const state = panelState({ data, isError })
  const fade = useSkeletonFade(data !== undefined)
  // ?rep=<name>: one salesman, as the command palette's salesman result opens the page
  const [sp] = useSearchParams()
  const rep = sp.get(REP_PARAM)
  const trend = (data?.trend || []).map((r) => ({ ...r, m: monthLabel(r.period_month) }))
  const salesmen = (data?.by_salesman || []).slice(0, 12).map((s) => ({ ...s, name: s.salesman, rev: Number(s.revenue_bhd || 0) }))
  const repName = rep ? matchSalesman([...(data?.by_salesman || []).map((s) => s.salesman), ...(data?.daily_by_salesman || []).map((r) => r.salesman)], rep) : null
  const channels = data?.by_channel || []
  const channelTotal = channels.reduce((s, c) => s + Number(c.revenue_bhd || 0), 0) || 1

  return (
    <div>
      <PageHeader title="Sales" subtitle="Performance · salesmen · B2C/B2B channel — gross (VAT-incl)"
        actions={rep ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border bg-card py-1 pl-2.5 pr-1 text-[12px] font-semibold">
            <UserRound size={13} className="shrink-0 text-primary" aria-hidden="true" />
            {repName ?? rep}{state === 'ready' && !repName && <span className="font-normal text-muted-foreground"> · not in these figures</span>}
            <Link to="/sales" aria-label="Clear the rep" title="Clear the rep"
              className="grid h-6 w-6 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
              <X size={13} />
            </Link>
          </span>
        ) : undefined} />
      {state === 'error' ? (
        <LoadError error={error} onRetry={() => refetch()} isRetrying={isFetching} fallback="Could not load sales." />
      ) : state === 'loading' || !data ? (
        <SalesSkeleton />
      ) : (
        <FadeIn on={fade} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="p-5 lg:col-span-2">
              <div className="mb-4 font-display text-base font-semibold">Revenue by month (gross)</div>
              <ChartBox id="sales.trend" height={260}>{(m) => (
                <AreaChart data={trend} margin={{ top: 6, right: 8, left: 8, bottom: 0 }}>
                  <defs>
                    <linearGradient id="sales" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#7c3aed" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="m" tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false}
                    width={48} tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`)} />
                  <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Gross']}
                    contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                  <Area {...m} type="monotone" dataKey="gross_bhd" stroke="#7c3aed" strokeWidth={2.5} fill="url(#sales)" />
                </AreaChart>
              )}</ChartBox>
            </Card>
            <Card className="p-5">
              <div className="mb-4 font-display text-base font-semibold">Channel mix</div>
              <div className="space-y-4">
                {channels.map((c) => {
                  const isB2C = c.channel === 'B2C'
                  const share = (Number(c.revenue_bhd) / channelTotal) * 100
                  return (
                    <div key={c.channel}>
                      <div className="mb-1.5 flex items-center justify-between text-sm">
                        <span className="flex items-center gap-2 font-medium">
                          {isB2C ? <Store size={15} className="text-violet-500" /> : <Truck size={15} className="text-blue-500" />}
                          {c.channel} {isB2C ? '· Retail' : '· Wholesale'}
                        </span>
                        <span className="font-semibold">{bhd(c.revenue_bhd, 0)}</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full" style={{ width: `${share}%`, background: isB2C ? '#8b5cf6' : '#3b82f6' }} />
                      </div>
                      <div className="mt-1 text-[11px] text-muted-foreground">{share.toFixed(0)}% · {num(c.orders)} orders · {num(c.qty)} units</div>
                    </div>
                  )
                })}
              </div>
            </Card>
          </div>

          <DailyBySalesman data={data} rep={rep} />

          <Card className="p-5">
            <div className="mb-4 font-display text-base font-semibold">Salesman performance (gross revenue)</div>
            <ChartBox id="sales.salesmen" height={300}>{(m) => (
              <BarChart data={salesmen} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" width={120} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Gross']} cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Bar {...m} dataKey="rev" radius={[0, 6, 6, 0]}>
                  {/* the rep a link names stands out; otherwise the top seller does */}
                  {salesmen.map((s, i) => <Cell key={i} fill={(repName ? s.salesman === repName : i === 0) ? '#7c3aed' : '#a78bfa'} />)}
                </Bar>
              </BarChart>
            )}</ChartBox>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card className="p-5">
              <div className="mb-4 font-display text-base font-semibold">Top sellers (last 90 days)</div>
              <DataTable rows={data.top_sellers} cols={sellerCols} exportName="top-sellers" searchable={false} empty="No sales yet." />
            </Card>
            <Card className="p-5">
              <div className="mb-4 font-display text-base font-semibold">Top customers (named accounts)</div>
              <DataTable rows={data.top_customers} cols={custCols} exportName="top-customers" searchable={false} empty="No customers yet." />
            </Card>
          </div>
        </FadeIn>
      )}
    </div>
  )
}
