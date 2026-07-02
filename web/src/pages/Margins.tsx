import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { bhd, num, pct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Skeleton } from '@/components/ui/skeleton'
import { PriceSimulator } from '@/components/PriceSimulator'

interface Row {
  item_name: string
  category_name: string
  gp_margin_pct: number
  np_margin_pct: number
  gross_profit_bhd: number
  net_amount_bhd: number
  cogs_bhd: number
}
interface Data {
  rows: Row[]
  count: number
  negative_count: number
  total_net_bhd: number
  total_gp_bhd: number
  gp_pct: number
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
  { key: 'net_amount_bhd', label: 'Net sales', align: 'right', money: true },
  { key: 'gross_profit_bhd', label: 'Gross profit', align: 'right', money: true },
  { key: 'cogs_bhd', label: 'COGS', align: 'right', money: true },
]

export default function Margins() {
  const { data, isLoading } = useQuery({ queryKey: ['report', 'margins'], queryFn: () => apiGet<Data>('/report/margins') })
  return (
    <div>
      <PageHeader title="Profitability" subtitle="Gross margin by product (Focus COGS basis)" />
      <PriceSimulator />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Overall GP %" value={pct(data.gp_pct)} tone="violet" />
            <Stat label="Gross profit" value={bhd(data.total_gp_bhd, 0)} />
            <Stat label="Products" value={num(data.count)} />
            <Stat label="Selling below cost" value={num(data.negative_count)} tone="rose" />
          </div>
          <DataTable
            rows={data.rows}
            cols={cols}
            exportName="profitability"
            rowClass={(r) => (Number(r.gp_margin_pct) < 0 ? 'bg-rose-50/60 dark:bg-rose-500/5' : undefined)}
            empty="No margin data."
          />
        </>
      )}
    </div>
  )
}
