import { useQuery } from '@tanstack/react-query'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { apiGet } from '@/lib/api'
import { bhd, monthLabel, fmtDate } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, type Column } from '@/components/DataTable'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface Customer {
  customer_name: string
  total_revenue_bhd: number
  order_count: number
  last_order_date: string
}
interface Data {
  trend: { period_month: string; net_revenue_bhd: number; order_count: number }[]
  top_customers: Customer[]
}

const cols: Column<Customer>[] = [
  { key: 'customer_name', label: 'Customer' },
  { key: 'order_count', label: 'Orders', align: 'right' },
  { key: 'last_order_date', label: 'Last order', align: 'right', render: (v) => fmtDate(v as string) },
  { key: 'total_revenue_bhd', label: 'Revenue', align: 'right', money: true },
]

export default function Sales() {
  const { data, isLoading } = useQuery({ queryKey: ['report', 'sales'], queryFn: () => apiGet<Data>('/report/sales') })
  const trend = (data?.trend || []).map((r) => ({ ...r, m: monthLabel(r.period_month) }))
  return (
    <div>
      <PageHeader title="Sales" subtitle="Revenue trends and top customers" />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <div className="space-y-4">
          <Card className="p-5">
            <div className="mb-4 font-display text-base font-semibold">Revenue by month</div>
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={trend} margin={{ top: 6, right: 8, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="sales" x1="0" y1="0" x2="0" y2="1">
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
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }}
                />
                <Area type="monotone" dataKey="net_revenue_bhd" stroke="#7c3aed" strokeWidth={2.5} fill="url(#sales)" />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
          <Card className="p-5">
            <div className="mb-4 font-display text-base font-semibold">Top customers</div>
            <DataTable rows={data.top_customers} cols={cols} empty="No customers yet." />
          </Card>
        </div>
      )}
    </div>
  )
}
