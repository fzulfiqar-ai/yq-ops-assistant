import { useQuery } from '@tanstack/react-query'
import { Area, AreaChart, Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Store, Truck } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { bhd, num, monthLabel, fmtDate } from '@/lib/format'
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
  const { data, isLoading } = useQuery({ queryKey: ['report', 'sales'], queryFn: () => apiGet<Data>('/report/sales') })
  const trend = (data?.trend || []).map((r) => ({ ...r, m: monthLabel(r.period_month) }))
  const salesmen = (data?.by_salesman || []).slice(0, 12).map((s) => ({ ...s, name: s.salesman, rev: Number(s.revenue_bhd || 0) }))
  const channels = data?.by_channel || []
  const channelTotal = channels.reduce((s, c) => s + Number(c.revenue_bhd || 0), 0) || 1

  return (
    <div>
      <PageHeader title="Sales Performance" subtitle="Revenue, salesmen and channel — gross (VAT-incl)" />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="p-5 lg:col-span-2">
              <div className="mb-4 font-display text-base font-semibold">Revenue by month (gross)</div>
              <ResponsiveContainer width="100%" height={260}>
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
                  <Area type="monotone" dataKey="gross_bhd" stroke="#7c3aed" strokeWidth={2.5} fill="url(#sales)" />
                </AreaChart>
              </ResponsiveContainer>
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

          <Card className="p-5">
            <div className="mb-4 font-display text-base font-semibold">Salesman performance (gross revenue)</div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={salesmen} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" width={120} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(value) => [bhd(Number(value), 0), 'Gross']} cursor={{ fill: 'hsl(var(--accent))' }}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
                <Bar dataKey="rev" radius={[0, 6, 6, 0]}>
                  {salesmen.map((_, i) => <Cell key={i} fill={i === 0 ? '#7c3aed' : '#a78bfa'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card className="p-5">
              <div className="mb-4 font-display text-base font-semibold">Top sellers (last 90 days)</div>
              <DataTable rows={data.top_sellers} cols={sellerCols} empty="No sales yet." />
            </Card>
            <Card className="p-5">
              <div className="mb-4 font-display text-base font-semibold">Top customers (named accounts)</div>
              <DataTable rows={data.top_customers} cols={custCols} empty="No customers yet." />
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
