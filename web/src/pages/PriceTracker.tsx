import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDownRight, ArrowUpRight, History, LineChart, X } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd, num, fmtDate } from '@/lib/format'
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

interface HistEvent {
  source: 'po' | 'mrn' | 'supplier_invoice'; event_date: string | null; vendor: string | null
  ref_no: string | null; qty: number | null; unit_cost_bhd: number | null
  unit_price_rmb: number | null; est_bhd: number | null; detail: string | null
}
interface HistData {
  sku_code: string; events: HistEvent[]; count: number
  selling: { price_bhd: number; effective_from: string | null; effective_to: string | null }[]
}

const EVENT_STYLE: Record<HistEvent['source'], { label: string; cls: string }> = {
  po: { label: 'PO', cls: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300' },
  mrn: { label: 'Received', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' },
  supplier_invoice: { label: 'Supplier ¥', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300' },
}

/** Full purchase timeline for one SKU — every PO, receipt and supplier invoice price. */
function HistoryDialog({ row, onClose }: { row: Row; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['price-history', row.sku_code],
    queryFn: () => apiGet<HistData>(`/prices/history?sku=${encodeURIComponent(row.sku_code)}`),
  })
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border bg-card p-5 shadow-luxe"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-start justify-between gap-3">
          <div>
            <div className="font-display text-lg font-bold">{row.sku_code}</div>
            <div className="text-xs text-muted-foreground">{row.item_name}</div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent/60"><X size={16} /></button>
        </div>
        <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
          {row.sell_now != null && <span className="text-muted-foreground">Selling <b className="text-foreground tabular-nums">{bhd(Number(row.sell_now), 3)}</b></span>}
          {row.cost_now != null && <span className="text-muted-foreground">Cost now <b className="text-foreground tabular-nums">{bhd(Number(row.cost_now), 3)}</b></span>}
          {row.margin_now_pct != null && <span className="text-muted-foreground">Margin <b className="text-foreground tabular-nums">{Number(row.margin_now_pct).toFixed(0)}%</b></span>}
        </div>
        {isLoading ? <Skeleton className="h-48" /> : (
          <>
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Purchase history · {data?.count ?? 0} events
            </div>
            {(data?.events || []).length === 0 ? (
              <p className="text-sm text-muted-foreground">No purchase records yet — history builds from PO, MRN and supplier-invoice uploads on the Orders page.</p>
            ) : (
              <ol className="relative ml-2 space-y-3 border-l pl-4">
                {data!.events.map((e, i) => {
                  const st = EVENT_STYLE[e.source]
                  return (
                    <li key={i} className="relative">
                      <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-card bg-primary" />
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                        <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-bold uppercase', st.cls)}>{st.label}</span>
                        <b className="tabular-nums">
                          {e.unit_cost_bhd != null ? bhd(Number(e.unit_cost_bhd), 3)
                            : e.unit_price_rmb != null ? <>¥{Number(e.unit_price_rmb).toFixed(2)}
                              {e.est_bhd != null && <span className="font-normal text-muted-foreground"> ≈ {bhd(Number(e.est_bhd), 3)}</span>}</>
                            : '—'}
                        </b>
                        {e.qty != null && <span className="text-muted-foreground">× {num(Number(e.qty))}</span>}
                        <span className="ml-auto text-[11px] text-muted-foreground">{e.event_date ? fmtDate(e.event_date) : '—'}</span>
                      </div>
                      <div className="text-[11.5px] text-muted-foreground">{[e.vendor, e.ref_no].filter(Boolean).join(' · ')}</div>
                    </li>
                  )
                })}
              </ol>
            )}
            {(data?.selling || []).length > 1 && (
              <>
                <div className="mb-2 mt-4 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Selling price history</div>
                <ul className="space-y-1 text-sm">
                  {data!.selling.slice(0, 8).map((s, i) => (
                    <li key={i} className="flex items-center justify-between">
                      <b className="tabular-nums">{bhd(Number(s.price_bhd), 3)}</b>
                      <span className="text-[11px] text-muted-foreground">
                        {s.effective_from ? fmtDate(s.effective_from) : '—'} → {s.effective_to ? fmtDate(s.effective_to) : 'now'}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className="mt-4 text-[10.5px] text-muted-foreground">
              Supplier ¥ prices convert to BHD with the Settings costing chain (estimate, before freight variations).
            </p>
          </>
        )}
      </div>
    </div>
  )
}

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

/** SKU cell → opens the per-item purchase-history timeline. */
function skuCol(onPick: (r: Row) => void): Column<Row> {
  return {
    key: 'sku_code', label: 'SKU',
    render: (v, r) => (
      <button onClick={() => onPick(r)} title="Full purchase price history"
        className="group inline-flex items-center gap-1 font-medium text-foreground hover:text-primary hover:underline">
        {String(v)}
        <History size={11} className="text-muted-foreground opacity-0 transition group-hover:opacity-100" />
      </button>
    ),
  }
}

const categoryCol: Column<Row> = {
  key: 'category', label: 'Category',
  render: (v) => <span className="text-xs capitalize text-muted-foreground">{String(v ?? '').toLowerCase()}</span>,
}
const costNowCol: Column<Row> = {
  key: 'cost_now', label: 'Cost now', align: 'right',
  render: (v, r) => v == null ? <span className="text-muted-foreground">—</span> : (
    <span className="tabular-nums">
      {money(v)}
      {r.cost_source && <span className="ml-1 rounded bg-secondary px-1 text-[9px] uppercase text-muted-foreground">{SOURCE_LABEL[r.cost_source] || r.cost_source}</span>}
    </span>
  ),
}
const marginCol: Column<Row> = {
  key: 'margin_now_pct', label: 'Margin now', align: 'right',
  render: (v, r) => v == null ? <span className="text-muted-foreground">—</span> : (
    <span className={cn('font-semibold tabular-nums', Number(v) < 20 ? 'text-amber-600' : 'text-foreground')}>
      {Number(v).toFixed(0)}%{r.margin_before_pct != null && <span className="ml-1 text-[11px] font-normal text-muted-foreground">was {Number(r.margin_before_pct).toFixed(0)}%</span>}
    </span>
  ),
}

function sellingCols(onPick: (r: Row) => void): Column<Row>[] {
  return [
    skuCol(onPick),
    categoryCol,
    { key: 'sell_prev', label: 'Sold at (before)', align: 'right', render: money },
    { key: 'sell_now', label: 'Selling now', align: 'right', render: (v) => <b className="tabular-nums">{money(v)}</b> },
    { key: 'sell_change_pct', label: 'Sell Δ', align: 'right', render: (v) => <SellDelta pct={v as number | null} /> },
    { key: 'cost_prev', label: 'Cost (before)', align: 'right', render: money },
    costNowCol,
    { key: 'cost_change_pct', label: 'Cost Δ', align: 'right', render: (v) => <Delta pct={v as number | null} /> },
    marginCol,
    { key: 'sell_changed_on', label: 'Price changed', align: 'right', render: (v) => <span className="text-xs text-muted-foreground">{v ? fmtDate(String(v)) : '—'}</span> },
  ]
}

function purchaseCols(onPick: (r: Row) => void): Column<Row>[] {
  return [
    skuCol(onPick),
    categoryCol,
    { key: 'cost_prev', label: 'Bought at (before)', align: 'right', render: money },
    costNowCol,
    { key: 'cost_change_pct', label: 'Cost Δ', align: 'right', render: (v) => <Delta pct={v as number | null} /> },
    { key: 'last_bought_on', label: 'Last bought', align: 'right', render: (v) => <span className="text-xs text-muted-foreground">{v ? fmtDate(String(v)) : '—'}</span> },
    { key: 'sell_now', label: 'Selling now', align: 'right', render: (v) => <b className="tabular-nums">{money(v)}</b> },
    marginCol,
  ]
}

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
  const [tab, setTab] = useState<'selling' | 'purchase'>('selling')
  const [hist, setHist] = useState<Row | null>(null)
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
  const rows = tab === 'purchase'
    ? (data?.rows || []).filter((r) => r.cost_now != null || r.cost_prev != null || r.last_bought_on != null)
    : data?.rows || []
  const cols = tab === 'purchase' ? purchaseCols(setHist) : sellingCols(setHist)

  return (
    <div>
      <PageHeader title="Price Tracker"
        subtitle="Selling price & purchase cost — before vs now, per SKU. Updates itself with every report upload." />

      <div className="mb-3 flex overflow-hidden rounded-xl border text-sm font-semibold" style={{ width: 'fit-content' }}>
        {(['selling', 'purchase'] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn('px-4 py-1.5 capitalize transition',
              tab === t ? 'bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:bg-accent/50')}>
            {t === 'selling' ? 'Selling price' : 'Purchase price'}
          </button>
        ))}
      </div>

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
        <DataTable rows={rows} cols={cols} exportName={tab === 'purchase' ? 'purchase-tracker' : 'price-tracker'}
          empty={tab === 'purchase' ? 'No purchase history yet — upload POs / MRNs / supplier invoices on the Orders page.' : 'No SKUs match these filters.'} />
      )}
      <p className="mt-3 text-[11px] text-muted-foreground">
        {tab === 'purchase'
          ? 'Click a SKU for its full purchase timeline — every PO, goods receipt and supplier ¥ invoice. Cost source: PO rate → MRN landed → supplier estimate.'
          : 'Sell Δ green = price went up (more margin) · Cost Δ red = supplier cost went up. Click a SKU for its full purchase history. Margin = (sell − landed base cost) ÷ sell.'}
      </p>

      {hist && <HistoryDialog row={hist} onClose={() => setHist(null)} />}
    </div>
  )
}
