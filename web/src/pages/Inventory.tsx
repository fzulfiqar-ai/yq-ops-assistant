import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { num } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Skeleton } from '@/components/ui/skeleton'

interface Row {
  item_name: string
  warehouse_name: string
  category_name: string
  balance_qty: number
  avg_rate_bhd: number
  as_of_date: string
}
interface Data {
  rows: Row[]
  count: number
  low_stock_count: number
}

const cols: Column<Row>[] = [
  { key: 'item_name', label: 'Item' },
  { key: 'category_name', label: 'Category' },
  { key: 'warehouse_name', label: 'Warehouse' },
  { key: 'balance_qty', label: 'Balance', align: 'right' },
  { key: 'avg_rate_bhd', label: 'Avg rate', align: 'right', money: true },
]

export default function Inventory() {
  const { data, isLoading } = useQuery({ queryKey: ['report', 'inventory'], queryFn: () => apiGet<Data>('/report/inventory') })
  return (
    <div>
      <PageHeader title="Inventory" subtitle="Stock levels and reorder signals" />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:max-w-md">
            <Stat label="Stock lines" value={num(data.count)} />
            <Stat label="Low-stock items" value={num(data.low_stock_count)} tone="amber" />
          </div>
          <DataTable
            rows={data.rows}
            cols={cols}
            rowClass={(r) => (Number(r.balance_qty) <= 10 ? 'bg-amber-50/60 dark:bg-amber-500/5' : undefined)}
            empty="No stock records."
          />
        </>
      )}
    </div>
  )
}
