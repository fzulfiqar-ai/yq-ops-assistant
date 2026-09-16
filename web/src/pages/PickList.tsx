import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, PackageCheck, RefreshCw, Truck } from 'lucide-react'
import { apiGet, apiPost, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd, num } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { STATUS_LABEL, STATUS_TONE } from '@/pages/shop-ops/OrderActions'

/**
 * The storekeeper's page (feature "Storekeeper"): every confirmed marketplace order, grouped by
 * the salesman who will take the goods, with the CONFIRMED quantities and a total per item to
 * pick. Two taps per order: Preparing (goods issued to the rep) and On the way. Focus stock
 * vouchers are still entered in Focus — this mirrors the physical hand-over, it does not move stock.
 */

interface PickLine { item_code: string; display_name?: string | null; qty: number }
interface PickOrder {
  id: number
  order_no: string
  status: string
  status_label?: string
  customer_shop?: string | null
  customer_name?: string | null
  customer_area?: string | null
  salesman_name?: string | null
  expected_delivery?: string | null
  confirmed_at?: string | null
  total_confirmed_bhd?: number | null
  total_bhd?: number | null
  units_count?: number | null
  lines: PickLine[]
}
interface PickGroup { salesman_id?: number | null; salesman: string; orders: PickOrder[] }
interface PickResp { groups: PickGroup[]; totals_by_item: { item_code: string; display_name?: string | null; qty: number; orders: number }[]; count: number }

function OrderBlock({ o, onChanged }: { o: PickOrder; onChanged: () => void }) {
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  async function move(next: 'packed' | 'out_for_delivery') {
    setBusy(next)
    try {
      await apiPost(`/shop/orders/${o.id}/status`, { status: next })
      toast(next === 'packed' ? `${o.order_no} is being prepared.` : `${o.order_no} is on the way.`, 'success')
      onChanged()
    } catch (e) {
      toast(e instanceof ApiError ? e.body.slice(0, 160) : 'Could not update the order.', 'error')
    } finally {
      setBusy(null)
    }
  }
  const title = o.customer_shop || o.customer_name || o.order_no
  return (
    <li className="rounded-[16px] border border-[#ece9f3] bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-display text-[15px] font-bold text-[#1a1430]">{title}</span>
            <Badge tone={STATUS_TONE[o.status] || 'grey'}>{o.status_label || STATUS_LABEL[o.status] || o.status}</Badge>
          </div>
          <div className="mt-0.5 text-[12px] text-[#6b6480]">
            {o.order_no}{o.customer_area ? ` · ${o.customer_area}` : ''}{o.expected_delivery ? ` · ${o.expected_delivery}` : ''}
          </div>
        </div>
        <div className="text-right">
          <div className="font-display text-[15px] font-bold tabular-nums text-[#1a1430]">{bhd(o.total_confirmed_bhd ?? o.total_bhd, 3)}</div>
          <div className="text-[11px] text-[#6b6480]">{num(o.lines.reduce((s, l) => s + l.qty, 0))} pcs</div>
        </div>
      </div>
      <ul className="mt-3 divide-y divide-[#f4f2f9] overflow-hidden rounded-xl border border-[#ece9f3]">
        {o.lines.map((l) => (
          <li key={l.item_code} className="flex items-center justify-between gap-3 px-3 py-2 text-[13px]">
            <span className="min-w-0 truncate"><b className="font-semibold text-[#1a1430]">{l.item_code}</b>{l.display_name && l.display_name !== l.item_code ? <span className="ml-1.5 text-[#6b6480]">{l.display_name}</span> : null}</span>
            <span className="shrink-0 font-display text-[15px] font-bold tabular-nums text-[#1a1430]">× {l.qty}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-2">
        {o.status === 'confirmed' && (
          <button type="button" onClick={() => move('packed')} disabled={busy !== null} className="flex h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-[#6d28d9] text-[13.5px] font-semibold text-white disabled:opacity-60">
            {busy === 'packed' ? <Loader2 size={15} className="animate-spin" /> : <PackageCheck size={15} />} Preparing (issued to {o.salesman_name?.split(' ')[0] || 'rep'})
          </button>
        )}
        {(o.status === 'confirmed' || o.status === 'packed') && (
          <button type="button" onClick={() => move('out_for_delivery')} disabled={busy !== null} className={cn('flex h-11 flex-1 items-center justify-center gap-2 rounded-xl border text-[13.5px] font-semibold disabled:opacity-60', o.status === 'packed' ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : 'border-[#e4e0ee] bg-white text-[#1a1430]')}>
            {busy === 'out_for_delivery' ? <Loader2 size={15} className="animate-spin" /> : <Truck size={15} />} On the way
          </button>
        )}
      </div>
    </li>
  )
}

export default function PickList() {
  const qc = useQueryClient()
  const { me } = useAuth()
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['shop-picklist'],
    queryFn: () => apiGet<PickResp>('/shop/picklist'),
    refetchInterval: 60_000,
  })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['shop-picklist'] }); qc.invalidateQueries({ queryKey: ['shop-orders'] }) }
  const groups = data?.groups || []
  const totals = data?.totals_by_item || []

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Pick list"
        subtitle={me?.role === 'storekeeper' ? 'Confirmed marketplace orders, grouped by the salesman who takes them' : 'What the warehouse is preparing'}
        actions={<button type="button" onClick={() => refetch()} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-[#e4e0ee] bg-white px-3 text-[12.5px] font-semibold text-[#1a1430]"><RefreshCw size={14} className={cn(isFetching && 'animate-spin')} /> Refresh</button>}
      />
      {isLoading ? (
        <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-[16px]" />)}</div>
      ) : isError ? (
        <p className="rounded-xl bg-[#fdecef] px-4 py-3 text-[13px] text-[#9f1239]">Could not load the pick list. Check the connection and refresh.</p>
      ) : !groups.length ? (
        <div className="rounded-[20px] border border-[#ece9f3] bg-white px-6 py-14 text-center">
          <Check size={28} className="mx-auto text-[#137a48]" aria-hidden="true" />
          <p className="mt-3 font-display text-[15px] font-bold text-[#1a1430]">Nothing to pick</p>
          <p className="mt-1 text-[12.5px] text-[#6b6480]">Confirmed orders appear here the moment a salesman confirms them.</p>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
          <div className="space-y-6">
            {groups.map((g) => (
              <section key={g.salesman} aria-labelledby={`grp-${g.salesman_id ?? g.salesman}`}>
                <h2 id={`grp-${g.salesman_id ?? g.salesman}`} className="mb-2 flex items-center gap-2 font-display text-[16px] font-bold text-[#1a1430]">
                  {g.salesman} <Badge tone="grey">{g.orders.length} {g.orders.length === 1 ? 'order' : 'orders'}</Badge>
                </h2>
                <ul className="space-y-3">{g.orders.map((o) => <OrderBlock key={o.id} o={o} onChanged={refresh} />)}</ul>
              </section>
            ))}
          </div>
          <aside className="lg:sticky lg:top-20 lg:self-start">
            <div className="rounded-[16px] border border-[#ece9f3] bg-white p-4">
              <h2 className="font-display text-[14px] font-bold text-[#1a1430]">Total to pick</h2>
              <ul className="mt-2 divide-y divide-[#f4f2f9]">
                {totals.map((t) => (
                  <li key={t.item_code} className="flex items-center justify-between gap-2 py-1.5 text-[13px]">
                    <span className="min-w-0 truncate"><b className="font-semibold text-[#1a1430]">{t.item_code}</b><span className="ml-1 text-[11px] text-[#6b6480]">{t.orders} {t.orders === 1 ? 'order' : 'orders'}</span></span>
                    <span className="shrink-0 font-display font-bold tabular-nums text-[#1a1430]">× {t.qty}</span>
                  </li>
                ))}
              </ul>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
