import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { bhd, num, pct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Skeleton } from '@/components/ui/skeleton'
import { PriceSimulator } from '@/components/PriceSimulator'

// The margin shown is COMPUTED (R2): the item's ex-VAT sales on the latest Focus profitability
// report against its COGS. Focus's own "GP Margin %" column is not a percentage and its "Gross
// Profit" loses the minus sign on loss items, so neither is shown as a margin any more.
interface Row {
  item_name: string
  category_name: string
  net_amount_bhd: number
  net_ex_vat_bhd: number
  cogs_bhd: number
  gp_computed_bhd: number
  gp_ex_vat_bhd: number
  margin_ex_vat_pct: number | null
  is_below_cost: boolean
  ex_vat_source?: 'day_book' | 'vat_rate'
}
interface Data {
  rows: Row[]
  count: number
  negative_count: number
  total_net_bhd: number
  total_net_ex_vat_bhd: number
  total_gp_bhd: number
  gp_pct: number
  basis: 'view' | 'inline'
}

const marginCell = (v: unknown) => {
  if (v === null || v === undefined) return <span className="text-muted-foreground">—</span>
  const n = Number(v)
  return <span className={cn('font-semibold', n < 0 ? 'text-rose-600' : n < 5 ? 'text-amber-600' : 'text-emerald-600')}>{pct(n)}</span>
}

const cols: Column<Row>[] = [
  { key: 'item_name', label: 'Item' },
  { key: 'category_name', label: 'Category' },
  { key: 'margin_ex_vat_pct', label: 'Margin ex-VAT', align: 'right', render: marginCell },
  { key: 'net_ex_vat_bhd', label: 'Sales ex-VAT', align: 'right', money: true },
  { key: 'cogs_bhd', label: 'COGS', align: 'right', money: true },
  {
    key: 'gp_ex_vat_bhd', label: 'GP ex-VAT', align: 'right',
    render: (v) => <span className={cn('tabular-nums', Number(v) < 0 && 'font-semibold text-rose-600')}>{bhd(Number(v ?? 0))}</span>,
  },
  { key: 'net_amount_bhd', label: 'Sales incl. VAT', align: 'right', money: true },
]

export default function Margins() {
  const { data, isLoading } = useQuery({ queryKey: ['report', 'margins'], queryFn: () => apiGet<Data>('/report/margins') })
  return (
    <div>
      <PageHeader title="Profitability" subtitle="Gross margin by product — ex-VAT sales against Focus COGS, computed here (not the report's own GP % column)" />
      <PriceSimulator />
      {isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Overall margin (ex-VAT)" value={pct(data.gp_pct)} tone="violet"
              foot={data.basis === 'inline' ? 'estimated from sales ÷ 1.1 until the economics migration runs' : 'from the day book’s own taxable totals'} />
            <Stat label="Gross profit (ex-VAT)" value={bhd(data.total_gp_bhd, 0)} />
            <Stat label="Products" value={num(data.count)} />
            <Stat label="Selling below cost" value={num(data.negative_count)} tone="rose"
              foot={data.negative_count > 0 ? 'ex-VAT sales under COGS — review the price' : undefined} />
          </div>
          <DataTable
            rows={data.rows}
            cols={cols}
            exportName="profitability"
            rowClass={(r) => (r.is_below_cost ? 'bg-rose-50/60 dark:bg-rose-500/5' : undefined)}
            empty="No margin data."
          />
        </>
      )}
    </div>
  )
}
