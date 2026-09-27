import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { bhd, num, pct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { LoadError } from '@/components/LoadError'
import { PageHeader } from '@/components/PageHeader'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { PriceSimulator } from '@/components/PriceSimulator'

// The margin shown is COMPUTED (R2): the item's ex-VAT sales on the latest Focus profitability
// report against its COGS. Focus's own "GP Margin %" column is not a percentage and its "Gross
// Profit" loses the minus sign on loss items, so neither is shown as a margin any more.
// R7d: that ex-VAT margin on Focus COGS is THE official margin (the same figure as the Command
// Centre and the Dashboard, app/metrics.py); the landed (MRN) margin is secondary and always shows
// its coverage; "Check cost" lists the SKUs whose unit cost is missing or implausible.
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
interface MarginFigure { pct: number | null; gp_bhd: number; coverage_pct: number | null }
interface CheckCostRow {
  sku_code: string
  item_name: string | null
  price_bhd: number | null
  cost_bhd: number | null
  cost_source: string | null
  cost_flag: 'missing' | 'implausible'
  reason: string
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
  official?: (MarginFigure & { items: number; below_cost: number }) | null
  landed?: (MarginFigure & { costed_net_bhd: number }) | null
  check_cost?: CheckCostRow[]
  returns_note?: string
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

const money3 = (v: number | null | undefined) => (v == null ? '—' : bhd(v, 3))

/** Price-book SKUs whose unit cost cannot be trusted: no cost at all, or one under 10% of the price.
 *  The marketplace gives them no margin floor until the cost is fixed (app/shop.py floor_state). */
function CheckCost({ rows }: { rows: CheckCostRow[] }) {
  const implausible = rows.filter((r) => r.cost_flag === 'implausible').length
  return (
    <Card className="mb-4 p-5">
      <div className="mb-1 flex flex-wrap items-center gap-2 font-display text-base font-semibold">
        <AlertTriangle size={18} className="text-amber-600" /> Check cost
        <span className="text-[12px] font-normal text-muted-foreground">
          · {num(implausible)} implausible · {num(rows.length - implausible)} missing · no margin floor until fixed
        </span>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        A cost under 10% of the price is almost always a typo or a per-carton figure. Fix it at the next MRN or purchase-cost
        upload; until then these items are left out of stock-at-cost and no discount is clamped against them.
      </p>
      <div className="overflow-auto rounded-xl border">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-secondary/90">
            <tr className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              <th className="px-3 py-2 text-left">Code</th>
              <th className="px-3 py-2 text-left">Item</th>
              <th className="px-3 py-2 text-right">Price incl. VAT</th>
              <th className="px-3 py-2 text-right">Cost on file</th>
              <th className="px-3 py-2 text-left">Why</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.sku_code} className="border-t">
                <td className="px-3 py-1.5 font-medium">{r.sku_code}</td>
                <td className="max-w-[240px] truncate px-3 py-1.5 text-muted-foreground" title={r.item_name || ''}>{r.item_name || '—'}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{money3(r.price_bhd)}</td>
                <td className={cn('px-3 py-1.5 text-right tabular-nums', r.cost_flag === 'implausible' && 'font-semibold text-amber-600')}>
                  {r.cost_bhd == null ? '—' : bhd(r.cost_bhd, 4)}
                  {r.cost_source && <span className="ml-1 rounded bg-secondary px-1 text-[9px] uppercase text-muted-foreground">{r.cost_source === 'mrn' ? 'MRN' : 'purchase'}</span>}
                </td>
                <td className="px-3 py-1.5 text-[12.5px] text-muted-foreground">{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export default function Margins() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({ queryKey: ['report', 'margins'], queryFn: () => apiGet<Data>('/report/margins') })
  const landed = data?.landed
  return (
    <div>
      <PageHeader title="Profitability" subtitle="Official gross margin: ex-VAT sales against Focus COGS, every item costed. Landed margin (MRN) shown beside it with its coverage." />
      <PriceSimulator />
      {isError && !data ? (
        <LoadError error={error} onRetry={() => refetch()} isRetrying={isFetching} fallback="Could not load profitability." />
      ) : isLoading || !data ? (
        <Skeleton className="h-[60vh]" />
      ) : (
        <>
          <div className="mb-2 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Gross margin (official)" value={pct(data.gp_pct)} tone="violet"
              foot={data.basis === 'inline'
                ? `ex-VAT sales vs Focus COGS · estimated from sales ÷ 1.1 until the economics migration runs · ${num(data.count)} items`
                : `ex-VAT sales vs Focus COGS · every item costed · ${num(data.count)} items`} />
            <Stat label="Landed margin (MRN)" value={landed?.pct != null ? pct(landed.pct) : '—'} tone="blue"
              foot={landed?.pct != null
                ? `on ${pct(landed.coverage_pct ?? 0, 0)} of Accessories sales · items with a receipt only · 12 months`
                : 'not available right now'} />
            <Stat label="Gross profit (ex-VAT)" value={bhd(data.total_gp_bhd, 0)} />
            <Stat label="Selling below cost" value={num(data.negative_count)} tone="rose"
              foot={data.negative_count > 0 ? 'ex-VAT sales under COGS — review the price' : undefined} />
          </div>
          <p className="mb-4 text-[12px] text-muted-foreground">
            {data.returns_note || 'Returns not yet deducted.'} Margins in the Price Tracker are unit margins on the ex-VAT book price.{' '}
            <Link to="/prices" className="font-medium text-primary hover:underline">Price Tracker</Link>
          </p>
          {(data.check_cost?.length ?? 0) > 0 && <CheckCost rows={data.check_cost!} />}
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
