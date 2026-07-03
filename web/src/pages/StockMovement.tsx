import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { ArrowRight, Truck, AlertTriangle, PackageCheck, Warehouse } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd, num } from '@/lib/format'
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
      <PageHeader title="Stock Movement" subtitle="Warehouse → van transfers, and whether each route's stock adds up" />

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
