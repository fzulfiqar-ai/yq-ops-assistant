import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDownRight, ArrowUpRight, LineChart } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd, fmtDate } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, type Column } from '@/components/DataTable'

interface Row {
  sku_code: string; item_name: string; brand: string; division: string; category: string
  sell_now: number | null; sell_prev: number | null; sell_changed_on: string | null; sell_change_pct: number | null
  cost_now: number | null; cost_prev: number | null; last_bought_on: string | null; cost_change_pct: number | null
  cost_source?: 'po' | 'mrn' | 'supplier_est' | null
  margin_now_pct: number | null; margin_before_pct: number | null
}

const SOURCE_LABEL: Record<string, string> = { po: 'PO', mrn: 'received', supplier_est: 'est.' }
interface TrackerData { rows: Row[]; count: number; divisions: string[]; brands: string[]; categories: string[] }

function Delta({ pct }: { pct: number | null }) {
  if (pct == null || pct === 0) return <span className="text-muted-foreground">—</span>
  const up = pct > 0
  return (
    <span className={cn('inline-flex items-center gap-0.5 font-semibold tabular-nums', up ? 'text-rose-600' : 'text-emerald-600')}>
      {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{Math.abs(pct).toFixed(1)}%
    </span>
  )
}
function SellDelta({ pct }: { pct: number | null }) {
  if (pct == null || pct === 0) return <span className="text-muted-foreground">—</span>
  const up = pct > 0
  return (
    <span className={cn('inline-flex items-center gap-0.5 font-semibold tabular-nums', up ? 'text-emerald-600' : 'text-rose-600')}>
      {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{Math.abs(pct).toFixed(1)}%
    </span>
  )
}

const money = (v: unknown) => (v == null ? '—' : bhd(Number(v), 3))

const COLS: Column<Row>[] = [
  { key: 'sku_code', label: 'SKU' },
  { key: 'category', label: 'Category', render: (v) => <span className="text-xs capitalize text-muted-foreground">{String(v ?? '').toLowerCase()}</span> },
  { key: 'sell_prev', label: 'Sold at (before)', align: 'right', render: money },
  { key: 'sell_now', label: 'Selling now', align: 'right', render: (v) => <b className="tabular-nums">{money(v)}</b> },
  { key: 'sell_change_pct', label: 'Sell Δ', align: 'right', render: (v) => <SellDelta pct={v as number | null} /> },
  { key: 'cost_prev', label: 'Cost (before)', align: 'right', render: money },
  {
    key: 'cost_now', label: 'Cost now', align: 'right',
    render: (v, r) => v == null ? <span className="text-muted-foreground">—</span> : (
      <span className="tabular-nums">
        {money(v)}
        {r.cost_source && <span className="ml-1 rounded bg-secondary px-1 text-[9px] uppercase text-muted-foreground">{SOURCE_LABEL[r.cost_source] || r.cost_source}</span>}
      </span>
    ),
  },
  { key: 'cost_change_pct', label: 'Cost Δ', align: 'right', render: (v) => <Delta pct={v as number | null} /> },
  {
    key: 'margin_now_pct', label: 'Margin now', align: 'right',
    render: (v, r) => v == null ? <span className="text-muted-foreground">—</span> : (
      <span className={cn('font-semibold tabular-nums', Number(v) < 20 ? 'text-amber-600' : 'text-foreground')}>
        {Number(v).toFixed(0)}%{r.margin_before_pct != null && <span className="ml-1 text-[11px] font-normal text-muted-foreground">was {Number(r.margin_before_pct).toFixed(0)}%</span>}
      </span>
    ),
  },
  { key: 'sell_changed_on', label: 'Price changed', align: 'right', render: (v) => <span className="text-xs text-muted-foreground">{v ? fmtDate(String(v)) : '—'}</span> },
]

function Chips({ label, all, value, onPick }: { label: string; all: string[]; value: string; onPick: (v: string) => void }) {
  if (all.length < 2) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
      {['All', ...all].map((v) => (
        <button key={v} onClick={() => onPick(v === 'All' ? '' : v)}
          className={cn('rounded-full border px-2.5 py-1 text-[12px] font-medium capitalize transition',
            (value || 'All') === v ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
          {v.toLowerCase()}
        </button>
      ))}
    </div>
  )
}

export default function PriceTracker() {
  const [division, setDivision] = useState('')
  const [brand, setBrand] = useState('')
  const [category, setCategory] = useState('')
  const [changedOnly, setChangedOnly] = useState(false)
  const qs = new URLSearchParams()
  if (division) qs.set('division', division)
  if (brand) qs.set('brand', brand)
  if (category) qs.set('category', category)
  if (changedOnly) qs.set('only_changed', 'true')
  const { data, isLoading } = useQuery({
    queryKey: ['price-tracker', division, brand, category, changedOnly],
    queryFn: () => apiGet<TrackerData>(`/prices/tracker?${qs.toString()}`),
  })

  const changed = (data?.rows || []).filter((r) => (r.sell_change_pct ?? 0) !== 0 || (r.cost_change_pct ?? 0) !== 0).length

  return (
    <div>
      <PageHeader title="Price Tracker"
        subtitle="Selling price & purchase cost — before vs now, per SKU. Updates itself with every report upload." />

      <div className="mb-4 space-y-2">
        <Chips label="Division" all={data?.divisions || []} value={division} onPick={setDivision} />
        <Chips label="Brand" all={data?.brands || []} value={brand} onPick={setBrand} />
        <Chips label="Category" all={data?.categories || []} value={category} onPick={setCategory} />
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
          <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
          Only items whose price or cost changed
          {changed > 0 && <span className="inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-accent-foreground"><LineChart size={11} />{changed} changed</span>}
        </label>
      </div>

      {isLoading ? <Skeleton className="h-96" /> : (
        <DataTable rows={data?.rows || []} cols={COLS} exportName="price-tracker"
          empty="No SKUs match these filters." />
      )}
      <p className="mt-3 text-[11px] text-muted-foreground">
        Sell Δ green = price went up (more margin) · Cost Δ red = supplier cost went up. Cost data appears once an
        item has purchase history (PO/MRN uploads). Margin = (sell − landed base cost) ÷ sell.
      </p>
    </div>
  )
}
