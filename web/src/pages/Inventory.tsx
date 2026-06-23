import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { bhd, num } from '@/lib/format'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface Row {
  item_name: string
  current_stock: number
  stock_value: number
  sold_90d: number
  days_cover: number | null
  suggested_reorder_qty: number
  status: string
}
interface Warehouse { warehouse_name: string; value_bhd: number; qty: number; items: number }
interface Data {
  rows: Row[]
  by_status: Record<string, number>
  stock_value: number
  stock_qty: number
  by_warehouse: Warehouse[]
}

const STATUS: Record<string, { label: string; cls: string }> = {
  urgent_out_of_stock: { label: 'Urgent — out of stock', cls: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300' },
  low_stock: { label: 'Low stock', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300' },
  dead_stock: { label: 'Dead stock', cls: 'bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300' },
  overstock: { label: 'Overstock', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300' },
  healthy: { label: 'Healthy', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' },
}

const cols: Column<Row>[] = [
  { key: 'item_name', label: 'Item' },
  {
    key: 'status', label: 'Status',
    render: (v) => {
      const s = STATUS[String(v)] || { label: String(v), cls: 'bg-muted text-muted-foreground' }
      return <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-semibold', s.cls)}>{s.label}</span>
    },
  },
  { key: 'current_stock', label: 'On hand', align: 'right', render: (v) => num(Number(v)) },
  { key: 'sold_90d', label: 'Sold 90d', align: 'right', render: (v) => num(Number(v)) },
  {
    key: 'days_cover', label: 'Days cover', align: 'right',
    render: (v) => {
      if (v === null || v === undefined) return <span className="text-rose-600 font-semibold">0</span>
      const n = Number(v)
      return <span className={cn('font-semibold', n < 30 ? 'text-amber-600' : n > 120 ? 'text-blue-600' : '')}>{n.toFixed(0)}</span>
    },
  },
  { key: 'suggested_reorder_qty', label: 'Reorder', align: 'right', render: (v) => (Number(v) > 0 ? <span className="font-semibold text-primary">{num(Number(v))}</span> : '—') },
]

export default function Inventory() {
  const { data, isLoading } = useQuery({ queryKey: ['report', 'inventory'], queryFn: () => apiGet<Data>('/report/inventory') })
  const s = data?.by_status || {}
  const alerts = (s.urgent_out_of_stock || 0) + (s.low_stock || 0)
  return (
    <div>
      <PageHeader title="Inventory Health" subtitle="Velocity-aware stock — what to reorder, what's stuck" />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Stock value (selling)" value={bhd(data.stock_value, 0)} tone="violet" />
            <Stat label="Units on hand" value={num(data.stock_qty)} />
            <Stat label="Low stock (<30d)" value={num(alerts)} tone="amber" />
            <Stat label="Urgent out-of-stock" value={num(s.urgent_out_of_stock || 0)} tone="rose" />
            <Stat label="Dead stock" value={num(s.dead_stock || 0)} />
          </div>

          {data.by_warehouse?.length > 0 && (
            <Card className="mb-4 p-5">
              <div className="mb-3 font-display text-base font-semibold">Stock value by warehouse / salesman</div>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {data.by_warehouse.slice(0, 12).map((w) => (
                  <div key={w.warehouse_name} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
                    <span className="truncate font-medium">{w.warehouse_name}</span>
                    <span className="shrink-0 font-semibold text-primary">{bhd(w.value_bhd, 0)}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          <DataTable
            rows={data.rows}
            cols={cols}
            rowClass={(r) => (r.status === 'urgent_out_of_stock' ? 'bg-rose-50/60 dark:bg-rose-500/5'
              : r.status === 'low_stock' ? 'bg-amber-50/50 dark:bg-amber-500/5' : undefined)}
            empty="No stock records."
          />
        </>
      )}
    </div>
  )
}
