import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { num, pct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Skeleton } from '@/components/ui/skeleton'

interface Row {
  item_name: string
  category_name: string
  gp_margin_pct: number
  np_margin_pct: number
  cogs_bhd: number
  list_price_bhd: number
}
interface Data {
  rows: Row[]
  count: number
  negative_count: number
}

const marginCell = (v: unknown) => {
  const n = Number(v ?? 0)
  return <span className={cn('font-semibold', n < 0 ? 'text-rose-600' : n < 5 ? 'text-amber-600' : 'text-emerald-600')}>{pct(n)}</span>
}

const cols: Column<Row>[] = [
  { key: 'item_name', label: 'Item' },
  { key: 'category_name', label: 'Category' },
  { key: 'gp_margin_pct', label: 'GP %', align: 'right', render: marginCell },
  { key: 'np_margin_pct', label: 'NP %', align: 'right', render: marginCell },
  { key: 'cogs_bhd', label: 'COGS', align: 'right', money: true },
  { key: 'list_price_bhd', label: 'List', align: 'right', money: true },
]

export default function Margins() {
  const { data, isLoading } = useQuery({ queryKey: ['report', 'margins'], queryFn: () => apiGet<Data>('/report/margins') })
  return (
    <div>
      <PageHeader title="Margins" subtitle="Profitability by product" />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:max-w-md">
            <Stat label="Products" value={num(data.count)} />
            <Stat label="Selling below cost" value={num(data.negative_count)} tone="rose" />
          </div>
          <DataTable
            rows={data.rows}
            cols={cols}
            rowClass={(r) => (Number(r.gp_margin_pct) < 0 ? 'bg-rose-50/60 dark:bg-rose-500/5' : undefined)}
            empty="No margin data."
          />
        </>
      )}
    </div>
  )
}
