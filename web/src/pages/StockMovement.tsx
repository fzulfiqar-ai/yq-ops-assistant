import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { ArrowRight, ArrowLeftRight, Truck, AlertTriangle, BarChart3, ChevronLeft, ChevronRight, PackageCheck, Warehouse } from 'lucide-react'
import { Bar, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd, num, fmtDate } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, type Column } from '@/components/DataTable'

interface Transfer {
  transfer_date: string
  voucher?: string
  item_name: string
  from_warehouse: string
  to_warehouse: string
  qty: number
  value_bhd: number
}
interface Van {
  salesman: string
  warehouse_type: string
  is_van: boolean
  transferred_in_qty: number
  transferred_out_qty: number
  sold_qty: number
  on_hand_qty: number
  shortage_qty: number
  shortage_value_bhd: number
  unexplained_qty: number
}

interface DailyDay {
  day: string; in_qty: number; out_qty: number; net_qty: number
  receipts_qty: number; transfer_out_qty: number; transfer_in_qty: number
  sales_qty: number; returns_qty: number; adjustment_qty: number
  sales_value_bhd: number; transfer_out_value_bhd: number; received_value_bhd: number
  in_value_bhd: number; out_value_bhd: number
  vouchers: number; item_lines: number; has_data: boolean
}
interface DailyData {
  month: string | null; months: string[]; warehouses: string[]
  days: DailyDay[]; gap_days: number; last_data_day: string | null
  snapshot_deltas: { day: string; delta_qty: number }[]
}

function monthTitle(m: string) {
  const [y, mm] = m.split('-')
  return new Date(Number(y), Number(mm) - 1, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
}

/** Daily movement dashboard — the storekeeper's month at a glance. */
function DailyMovement() {
  const [month, setMonth] = useState<string>('')     // '' = latest data month (backend default)
  const [wh, setWh] = useState<string>('')
  const { data, isLoading } = useQuery({
    queryKey: ['stock-daily', month, wh],
    queryFn: () => apiGet<DailyData>(
      `/stock/daily?month=${encodeURIComponent(month)}&warehouse=${encodeURIComponent(wh)}`),
  })

  // Company view ("All warehouses"): internal transfers appear on BOTH sides (issue
  // leg + receive leg), so we chart external in (supplier receipts + returns), sold,
  // and internal transfers as separate series — the totals then reconcile with the
  // transfers table and van recon below. A single warehouse keeps its own in/out.
  const allWh = wh === ''
  const days = useMemo(() => (data?.days || []).map((d) => ({
    ...d,
    external_in: Number(d.receipts_qty) + Number(d.returns_qty),
  })), [data])
  const totals = useMemo(() => ({
    in_qty: days.reduce((s, d) => s + Number(d.in_qty), 0),
    out_qty: days.reduce((s, d) => s + Number(d.out_qty), 0),
    external_in: days.reduce((s, d) => s + d.external_in, 0),
    sold: days.reduce((s, d) => s + Number(d.sales_qty), 0),
    transferred: days.reduce((s, d) => s + Number(d.transfer_out_qty), 0),
    // BHD value alongside units (owner asked to see value in movement)
    received_val: days.reduce((s, d) => s + Number(d.received_value_bhd), 0),
    sold_val: days.reduce((s, d) => s + Number(d.sales_value_bhd), 0),
    transferred_val: days.reduce((s, d) => s + Number(d.transfer_out_value_bhd), 0),
    in_val: days.reduce((s, d) => s + Number(d.in_value_bhd), 0),
    out_val: days.reduce((s, d) => s + Number(d.out_value_bhd), 0),
    busiest: days.reduce<DailyDay | null>((best, d) =>
      (Number(d.in_qty) + Number(d.out_qty)) > (best ? Number(best.in_qty) + Number(best.out_qty) : 0) ? d : best, null),
  }), [days])

  if (!isLoading && (!data?.months?.length)) return null
  const cur = data?.month || ''
  const idx = data ? data.months.indexOf(cur) : -1
  const prevM = idx >= 0 && idx < (data?.months.length ?? 0) - 1 ? data!.months[idx + 1] : null
  const nextM = idx > 0 ? data!.months[idx - 1] : null
  const whs = data?.warehouses || []

  return (
    <Card className="mb-6 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-display text-base font-semibold">
          <BarChart3 size={17} className="text-primary" /> Daily movement
          <span className="text-[12px] font-normal text-muted-foreground">· units in / out per day{wh ? ` · ${wh}` : ''}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <button onClick={() => prevM && setMonth(prevM)} disabled={!prevM}
            className="grid h-7 w-7 place-items-center rounded-lg border transition enabled:hover:bg-accent/50 disabled:opacity-30">
            <ChevronLeft size={15} />
          </button>
          <span className="min-w-[120px] text-center text-sm font-semibold">{cur ? monthTitle(cur) : '—'}</span>
          <button onClick={() => nextM && setMonth(nextM)} disabled={!nextM}
            className="grid h-7 w-7 place-items-center rounded-lg border transition enabled:hover:bg-accent/50 disabled:opacity-30">
            <ChevronRight size={15} />
          </button>
        </div>
      </div>
      {whs.length > 1 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {['', ...whs.slice(0, 8)].map((w) => (
            <button key={w || 'all'} onClick={() => setWh(w)}
              className={cn('rounded-full border px-3 py-1 text-[12px] font-medium transition',
                wh === w ? 'border-primary bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:bg-accent/50')}>
              {w || 'All warehouses'}
            </button>
          ))}
        </div>
      )}
      {isLoading ? (
        <Skeleton className="mt-4 h-[220px]" />
      ) : (
        <>
          <ResponsiveContainer width="100%" height={210}>
            <ComposedChart data={days} margin={{ top: 12, right: 8, left: 8, bottom: 0 }}>
              <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} interval="preserveStartEnd"
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
                width={44} tickFormatter={(v) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v}`)} />
              <Tooltip
                labelFormatter={(d) => fmtDate(String(d))}
                formatter={(v, name, entry) => {
                  const labels: Record<string, string> = {
                    external_in: 'Received (supplier + returns)', sales_qty: 'Sold',
                    transfer_out_qty: 'Transferred (internal)', in_qty: 'In', out_qty: 'Out',
                  }
                  const valKey: Record<string, string> = {
                    external_in: 'received_value_bhd', sales_qty: 'sales_value_bhd',
                    transfer_out_qty: 'transfer_out_value_bhd', in_qty: 'in_value_bhd', out_qty: 'out_value_bhd',
                  }
                  const p = entry?.payload as DailyDay | undefined
                  const val = p ? Number(p[valKey[String(name)] as keyof DailyDay] || 0) : 0
                  return [`${num(Number(v))} units · ${bhd(val, 3)}`, labels[String(name)] || String(name)]
                }}
                contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
              {allWh ? (
                <>
                  <Bar dataKey="external_in" fill="#059669" radius={[3, 3, 0, 0]} maxBarSize={12} />
                  <Bar dataKey="sales_qty" fill="#7c3aed" radius={[3, 3, 0, 0]} maxBarSize={12} />
                  <Bar dataKey="transfer_out_qty" fill="#3b82f6" radius={[3, 3, 0, 0]} maxBarSize={12} />
                </>
              ) : (
                <>
                  <Bar dataKey="in_qty" fill="#059669" radius={[3, 3, 0, 0]} maxBarSize={14} />
                  <Bar dataKey="out_qty" fill="#7c3aed" radius={[3, 3, 0, 0]} maxBarSize={14} />
                </>
              )}
            </ComposedChart>
          </ResponsiveContainer>
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 border-t pt-3 text-sm">
            {allWh ? (
              <>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-emerald-600" />
                  Received <b className="tabular-nums">{num(totals.external_in)}</b>
                  <span className="text-muted-foreground">· {bhd(totals.received_val, 0)}</span>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-violet-600" />
                  Sold <b className="tabular-nums">{num(totals.sold)}</b>
                  <span className="text-muted-foreground">· {bhd(totals.sold_val, 0)}</span>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-blue-500" />
                  Transferred <b className="tabular-nums">{num(totals.transferred)}</b>
                  <span className="text-muted-foreground">· {bhd(totals.transferred_val, 0)} <span className="text-[11px]">(internal, warehouse → van)</span></span>
                </span>
              </>
            ) : (
              <>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-emerald-600" />
                  In <b className="tabular-nums">{num(totals.in_qty)}</b>
                  <span className="text-muted-foreground">· {bhd(totals.in_val, 0)}</span>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-violet-600" />
                  Out <b className="tabular-nums">{num(totals.out_qty)}</b>
                  <span className="text-muted-foreground">· {bhd(totals.out_val, 0)}</span>
                </span>
                <span className="text-muted-foreground">Net <b className={cn('tabular-nums', totals.in_qty - totals.out_qty >= 0 ? 'text-emerald-600' : 'text-rose-600')}>
                  {num(totals.in_qty - totals.out_qty)}</b></span>
              </>
            )}
            {totals.busiest && (
              <span className="text-muted-foreground">Busiest day <b className="text-foreground">{fmtDate(totals.busiest.day)}</b> ({num(Number(totals.busiest.in_qty) + Number(totals.busiest.out_qty))} units)</span>
            )}
            {(data?.gap_days ?? 0) > 0 && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-0.5 text-[12px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                <AlertTriangle size={12} /> {data!.gap_days} day(s) with no ledger rows — likely a missing Stock_ledger upload, not zero movement
              </span>
            )}
          </div>
        </>
      )}
    </Card>
  )
}

interface FlowRow {
  issued_on: string; voucher_no: string; item_name: string
  from_warehouse: string; to_warehouse: string | null
  issued_qty: number; issued_value_bhd: number
  received_qty: number; received_by: string | null; received_on: string | null
  status: 'received' | 'partial' | 'pending'
}
interface FlowData {
  warehouse: string; direction: 'out' | 'in'; days: number
  rows: FlowRow[]; count: number
  summary: Record<string, { legs: number; qty: number }>
  partners: { name: string; qty: number }[]
}

const FLOW_STATUS: Record<FlowRow['status'], string> = {
  received: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  partial: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  pending: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300',
}

const FLOW_COLS: Column<FlowRow>[] = [
  { key: 'issued_on', label: 'Issued' },
  { key: 'voucher_no', label: 'Voucher' },
  { key: 'item_name', label: 'Item' },
  { key: 'from_warehouse', label: 'From' },
  { key: 'to_warehouse', label: 'To', render: (v, r) => String(r.received_by || v || '—') },
  { key: 'issued_qty', label: 'Issued', align: 'right', render: (v) => num(Number(v)) },
  { key: 'received_qty', label: 'Received', align: 'right', render: (v) => num(Number(v)) },
  {
    key: 'status', label: 'Status',
    render: (v) => (
      <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-semibold', FLOW_STATUS[v as FlowRow['status']])}>
        {String(v)}
      </span>
    ),
  },
]

/** Issue → Receive tracking for the main warehouse (the storekeeper's paper trail):
 *  every Stock Issue Voucher and whether the destination punched its Stock Receive. */
function WarehouseFlow() {
  const [direction, setDirection] = useState<'out' | 'in'>('out')
  const [days, setDays] = useState(30)
  const { data, isLoading } = useQuery({
    queryKey: ['stock-flow', direction, days],
    queryFn: () => apiGet<FlowData>(`/stock/flow?direction=${direction}&days=${days}`),
  })
  const s = data?.summary || {}
  return (
    <div className="mt-8">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ArrowLeftRight size={16} className="text-primary" />
        <h2 className="font-display text-base font-semibold">Issue → Receive tracking</h2>
        <span className="text-xs text-muted-foreground">Accessories Warehouse</span>
        <div className="ml-auto flex flex-wrap gap-1.5">
          <div className="flex overflow-hidden rounded-lg border text-[12.5px] font-semibold">
            {([['out', 'From main →'], ['in', '→ Into main']] as const).map(([d, label]) => (
              <button key={d} onClick={() => setDirection(d)}
                className={cn('px-3 py-1 transition',
                  direction === d ? 'bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:bg-accent/50')}>
                {label}
              </button>
            ))}
          </div>
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={cn('rounded-lg border px-3 py-1 text-[13px] font-medium transition',
                days === d ? 'border-primary bg-accent' : 'border-border')}>
              {d}d
            </button>
          ))}
        </div>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[12.5px]">
        {(['received', 'partial', 'pending'] as const).map((st) => s[st] && (
          <span key={st} className={cn('rounded-full px-2.5 py-1 font-medium', FLOW_STATUS[st])}>
            {st} · {num(s[st].qty)} units ({s[st].legs} lines)
          </span>
        ))}
        {(data?.partners || []).slice(0, 6).map((p) => (
          <span key={p.name} className="rounded-full border bg-secondary/40 px-2.5 py-1 text-muted-foreground">
            {direction === 'out' ? '→' : '←'} {p.name} <b className="text-foreground tabular-nums">{num(p.qty)}</b>
          </span>
        ))}
      </div>
      {isLoading ? (
        <Skeleton className="h-64" />
      ) : (
        <DataTable rows={data?.rows || []} cols={FLOW_COLS} exportName="stock-flow"
          empty="No issue vouchers in this window." />
      )}
      <p className="mt-2 text-xs text-muted-foreground">
        Flow: storekeeper punches a <b>Stock Issue Voucher</b> → the destination converts it to a{' '}
        <b>Stock Receive Voucher</b> (same voucher number). <b>Pending</b> = issued but no receive
        punched yet — chase the receiver.
      </p>
    </div>
  )
}

const COLS: Column<Transfer>[] = [
  { key: 'transfer_date', label: 'Date' },
  { key: 'item_name', label: 'Item' },
  { key: 'from_warehouse', label: 'From' },
  { key: 'to_warehouse', label: 'To' },
  { key: 'qty', label: 'Qty', align: 'right' },
  { key: 'value_bhd', label: 'Value', align: 'right', money: true },
]

function VanCard({ v }: { v: Van }) {
  const hasShortage = (v.shortage_value_bhd || 0) > 0
  const gap = Math.max(v.unexplained_qty || 0, 0)
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      <Card className={cn('p-4', hasShortage && 'border-rose-300 dark:border-rose-500/40')}>
        <div className="flex items-center gap-2">
          <div className={cn('grid h-9 w-9 place-items-center rounded-xl',
            hasShortage ? 'bg-rose-100 text-rose-600 dark:bg-rose-500/15' : 'bg-accent text-accent-foreground')}>
            {v.is_van ? <Truck size={17} /> : <Warehouse size={17} />}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{v.salesman}</div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{v.warehouse_type.replace('_', ' ')}</div>
          </div>
          {hasShortage && (
            <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-semibold text-rose-700 dark:bg-rose-500/15 dark:text-rose-300">
              <AlertTriangle size={11} /> {bhd(v.shortage_value_bhd)} short
            </span>
          )}
        </div>
        <div className="mt-3 grid grid-cols-4 gap-2 border-t pt-3 text-center">
          {[
            ['Issued in', v.transferred_in_qty],
            ['Sold', v.sold_qty],
            ['On hand', v.on_hand_qty],
            ['Gap', gap],
          ].map(([label, val]) => (
            <div key={String(label)}>
              <div className={cn('font-display text-base font-bold tabular-nums',
                label === 'Gap' && Number(val) > 0 && 'text-amber-600')}>{num(Number(val))}</div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
            </div>
          ))}
        </div>
      </Card>
    </motion.div>
  )
}

export default function StockMovement() {
  const [days, setDays] = useState(30)
  const transfers = useQuery({
    queryKey: ['stock-transfers', days],
    queryFn: () => apiGet<{ transfers: Transfer[] }>(`/stock/transfers?days=${days}`),
  })
  const recon = useQuery({
    queryKey: ['stock-recon'],
    queryFn: () => apiGet<{ vans: Van[] }>('/stock/recon'),
  })

  const vans = useMemo(() => (recon.data?.vans || []).filter((v) => v.is_van), [recon.data])
  const other = useMemo(() => (recon.data?.vans || []).filter((v) => !v.is_van), [recon.data])
  const totalShort = vans.reduce((s, v) => s + (v.shortage_value_bhd || 0), 0)

  return (
    <div>
      <PageHeader title="Stock Movement" subtitle="Daily movement, warehouse → van transfers, and whether each route's stock adds up" />

      {/* Daily movement dashboard (storekeeper performance, month by month) */}
      <DailyMovement />

      {/* Issue → Receive paper trail for the main warehouse */}
      <WarehouseFlow />

      {/* Van reconciliation */}
      <div className="mb-2 flex items-center gap-2">
        <PackageCheck size={16} className="text-primary" />
        <h2 className="font-display text-base font-semibold">Salesman van reconciliation</h2>
        {totalShort > 0 && (
          <span className="text-xs font-semibold text-rose-600">{bhd(totalShort)} in counted shortages</span>
        )}
      </div>
      {recon.isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-32" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {vans.map((v) => <VanCard key={v.salesman} v={v} />)}
        </div>
      )}
      <p className="mt-2 text-xs text-muted-foreground">
        Issued in − sold − on hand = gap. “Short” = physical-count shortages booked in Focus (hard signal);
        a gap can also include opening stock from before the data window.
      </p>

      {other.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
            Other locations (hub, damage, SIM, modern trade…) — {other.length}
          </summary>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {other.map((v) => <VanCard key={v.salesman} v={v} />)}
          </div>
        </details>
      )}

      {/* Transfers table */}
      <div className="mb-2 mt-8 flex flex-wrap items-center gap-2">
        <ArrowRight size={16} className="text-primary" />
        <h2 className="font-display text-base font-semibold">Transfers</h2>
        <div className="ml-auto flex gap-1.5">
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={cn('rounded-lg border px-3 py-1 text-[13px] font-medium transition',
                days === d ? 'border-primary bg-accent' : 'border-border')}>
              {d}d
            </button>
          ))}
        </div>
      </div>
      {transfers.isLoading ? (
        <Skeleton className="h-64" />
      ) : (
        <DataTable rows={transfers.data?.transfers || []} cols={COLS} exportName="stock-transfers"
          empty="No transfers in this window." />
      )}
    </div>
  )
}
