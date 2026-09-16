import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Check, Loader2, MessageCircle, UserRoundCheck } from 'lucide-react'
import { apiGet, apiPost, ApiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'
import { Badge, type BadgeTone } from '@/components/ui/badge'

/**
 * The three actions the marketplace added to an order, shared by the desk drawer, the field
 * sheet and the assignment queue (docs/SHOP.md § Marketplace):
 *   • ConfirmEditor — confirm with changes (per-line confirmed qty / remove, expected delivery)
 *   • AssignBox     — assign or reassign (admins; a salesman only takes an unassigned order)
 *   • AssignmentQueue — unassigned open orders with a history-based suggestion
 */

export const STATUS_LABEL: Record<string, string> = {
  new: 'Received',
  confirmed: 'Confirmed',
  packed: 'Preparing',
  out_for_delivery: 'On the way',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
}
export const STATUS_TONE: Record<string, BadgeTone> = {
  new: 'ink',
  confirmed: 'accent',
  packed: 'amber',
  out_for_delivery: 'amber',
  delivered: 'green',
  cancelled: 'grey',
}
export const ACTION_LABEL: Record<string, string> = {
  confirmed: 'Confirm',
  packed: 'Mark preparing',
  out_for_delivery: 'On the way',
  delivered: 'Delivered',
  cancelled: 'Cancel order',
}

export interface EditableLine {
  id: number
  item_code: string
  display_name?: string | null
  qty: number
  qty_confirmed?: number | null
  line_status?: string | null
  unit_price_bhd?: number | null
  backorder?: boolean
}

export interface ConfirmResponse {
  ok: boolean
  order: { status: string; expected_delivery?: string | null; total_confirmed_bhd?: number | null }
  totals?: { total_bhd?: number | null } | null
  changed?: { item_code: string; from: number; to: number }[]
  removed?: string[]
  whatsapp_url?: string | null
}

interface SalesmanOpt { id: number; name: string; is_active?: boolean }

export function ConfirmEditor({
  orderId, lines, onDone, onCancel,
}: {
  orderId: number
  lines: EditableLine[]
  onDone: (res: ConfirmResponse) => void
  onCancel?: () => void
}) {
  const toast = useToast()
  const [draft, setDraft] = useState<Record<number, { qty: string; removed: boolean }>>(() =>
    Object.fromEntries(lines.map((l) => [l.id, { qty: String(l.qty_confirmed ?? l.qty), removed: (l.line_status || 'ok') === 'removed' }])),
  )
  const [eta, setEta] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const changes = useMemo(() => {
    const out: { line_id: number; qty_confirmed?: number; line_status?: string }[] = []
    for (const l of lines) {
      const d = draft[l.id]
      if (!d) continue
      if (d.removed) out.push({ line_id: l.id, line_status: 'removed' })
      else {
        const n = Math.max(0, Math.floor(Number(d.qty) || 0))
        if (n !== l.qty) out.push({ line_id: l.id, qty_confirmed: n })
      }
    }
    return out
  }, [draft, lines])
  const live = lines.filter((l) => !draft[l.id]?.removed && Math.floor(Number(draft[l.id]?.qty) || 0) > 0)
  const invalid = lines.some((l) => !draft[l.id]?.removed && Math.floor(Number(draft[l.id]?.qty) || 0) <= 0)
  const total = live.reduce((s, l) => s + Number(l.unit_price_bhd || 0) * Math.floor(Number(draft[l.id]?.qty) || 0), 0)

  async function submit() {
    if (invalid || !live.length) return
    setBusy(true)
    try {
      const res = await apiPost<ConfirmResponse>(`/shop/orders/${orderId}/confirm`, {
        lines: changes,
        expected_delivery: eta.trim() || undefined,
        note: note.trim() || undefined,
      })
      const n = (res.changed?.length || 0) + (res.removed?.length || 0)
      toast(n ? `Confirmed with ${n} change${n === 1 ? '' : 's'}.` : 'Order confirmed.', 'success')
      onDone(res)
    } catch (e) {
      toast(e instanceof ApiError ? e.body.slice(0, 160) : 'Could not confirm the order.', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-[#e9e2f8] bg-[#f9f7fd] p-3.5">
      <div className="text-[12px] font-semibold text-[#1a1430]">Confirm quantities</div>
      <ul className="divide-y divide-[#ece9f3] overflow-hidden rounded-xl border border-[#ece9f3] bg-white">
        {lines.map((l) => {
          const d = draft[l.id] || { qty: String(l.qty), removed: false }
          const n = Math.floor(Number(d.qty) || 0)
          return (
            <li key={l.id} className={cn('flex items-center gap-3 p-2.5', d.removed && 'opacity-60')}>
              <div className="min-w-0 flex-1">
                <div className={cn('truncate text-[13px] font-semibold text-[#1a1430]', d.removed && 'line-through')}>{l.display_name || l.item_code}</div>
                <div className="text-[11px] tabular-nums text-[#6b6480]">{l.item_code} · ordered {l.qty}{l.backorder ? ' · backorder' : ''}</div>
              </div>
              {!d.removed && (
                <input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={9999}
                  value={d.qty}
                  onChange={(e) => setDraft((s) => ({ ...s, [l.id]: { ...d, qty: e.target.value.replace(/[^\d]/g, '') } }))}
                  aria-label={`Confirmed quantity for ${l.item_code}`}
                  className={cn('h-10 w-[4.5rem] rounded-lg border bg-white px-2 text-center text-[15px] font-semibold tabular-nums outline-none focus:border-[#6d28d9]', n !== l.qty ? 'border-[#6d28d9] text-[#6d28d9]' : 'border-[#e4e0ee] text-[#1a1430]')}
                />
              )}
              <button
                type="button"
                onClick={() => setDraft((s) => ({ ...s, [l.id]: { ...d, removed: !d.removed } }))}
                className={cn('h-10 shrink-0 rounded-lg border px-2.5 text-[12px] font-semibold', d.removed ? 'border-[#6d28d9] bg-[#f3eefc] text-[#6d28d9]' : 'border-[#e4e0ee] bg-white text-[#9f1239]')}
              >
                {d.removed ? 'Keep' : 'Remove'}
              </button>
            </li>
          )
        })}
      </ul>
      <div className="grid gap-2 sm:grid-cols-2">
        <input value={eta} onChange={(e) => setEta(e.target.value)} placeholder="Expected delivery (e.g. Tomorrow with my route)" className="h-10 rounded-lg border border-[#e4e0ee] bg-white px-3 text-[13px] outline-none focus:border-[#6d28d9]" />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className="h-10 rounded-lg border border-[#e4e0ee] bg-white px-3 text-[13px] outline-none focus:border-[#6d28d9]" />
      </div>
      <div className="flex items-center justify-between gap-2 text-[12px] text-[#6b6480]">
        <span>{changes.length ? `${changes.length} change${changes.length === 1 ? '' : 's'}` : 'As ordered'} · est. {bhd(total, 3)}</span>
        {!live.length && <span className="inline-flex items-center gap-1 font-medium text-[#9f1239]"><AlertTriangle size={12} /> Remove every line? Cancel the order instead.</span>}
      </div>
      <div className="flex gap-2">
        {onCancel && <button type="button" onClick={onCancel} className="h-11 flex-1 rounded-xl border border-[#e4e0ee] bg-white text-[13px] font-semibold text-[#1a1430]">Back</button>}
        <button type="button" onClick={submit} disabled={busy || invalid || !live.length} className="flex h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-[#6d28d9] text-[14px] font-semibold text-white disabled:opacity-50">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />} {changes.length ? 'Confirm with changes' : 'Confirm order'}
        </button>
      </div>
    </div>
  )
}

export function useSalesmenOptions(enabled: boolean) {
  return useQuery({
    queryKey: ['shop-salesmen-options'],
    queryFn: async () => (await apiGet<{ salesmen: SalesmanOpt[] }>('/shop/salesmen')).salesmen.filter((s) => s.is_active !== false),
    enabled,
    staleTime: 5 * 60_000,
  })
}

export function AssignBox({
  orderId, currentSalesmanId, suggestedId, suggestedReason, compact, onDone,
}: {
  orderId: number
  currentSalesmanId?: number | null
  suggestedId?: number | null
  suggestedReason?: string | null
  compact?: boolean
  onDone: () => void
}) {
  const toast = useToast()
  const { data: options } = useSalesmenOptions(true)
  const [pick, setPick] = useState<number | ''>(suggestedId ?? currentSalesmanId ?? '')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (pick === '' && (suggestedId || currentSalesmanId)) setPick(suggestedId ?? currentSalesmanId ?? '') }, [suggestedId, currentSalesmanId, pick])

  async function assign() {
    if (pick === '') return
    setBusy(true)
    try {
      await apiPost(`/shop/orders/${orderId}/assign`, { salesman_id: Number(pick), reason: reason.trim() || undefined })
      toast(currentSalesmanId ? 'Order reassigned.' : 'Order assigned.', 'success')
      onDone()
    } catch (e) {
      toast(e instanceof ApiError ? e.body.slice(0, 160) : 'Could not assign the order.', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={cn('rounded-xl border p-3', currentSalesmanId ? 'border-[#ece9f3] bg-white' : 'border-[#f3c9d2] bg-[#fdf3f5]')}>
      {!compact && (
        <div className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-[#1a1430]">
          <UserRoundCheck size={14} className="text-[#6d28d9]" /> {currentSalesmanId ? 'Reassign' : 'Assign a salesman'}
        </div>
      )}
      {suggestedReason && <p className="mb-2 text-[11.5px] text-[#6b6480]">Suggested: {suggestedReason}</p>}
      <div className="flex flex-wrap gap-2">
        <select value={pick} onChange={(e) => setPick(e.target.value === '' ? '' : Number(e.target.value))} className="h-10 min-w-[10rem] flex-1 rounded-lg border border-[#e4e0ee] bg-white px-2.5 text-[13px] outline-none focus:border-[#6d28d9]" aria-label="Salesman">
          <option value="">Choose…</option>
          {(options || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {!compact && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (optional)" className="h-10 flex-1 rounded-lg border border-[#e4e0ee] bg-white px-3 text-[13px] outline-none focus:border-[#6d28d9]" />}
        <button type="button" onClick={assign} disabled={busy || pick === '' || pick === currentSalesmanId} className="flex h-10 items-center gap-1.5 rounded-lg bg-[#6d28d9] px-3.5 text-[13px] font-semibold text-white disabled:opacity-50">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} {currentSalesmanId ? 'Reassign' : 'Assign'}
        </button>
      </div>
    </div>
  )
}

interface QueueRow {
  id: number
  order_no: string
  status: string
  created_at?: string | null
  customer_shop?: string | null
  customer_area?: string | null
  total_bhd?: number | null
  units_count?: number | null
  items_count?: number | null
  session_ref?: string | null
  attribution_conflict?: boolean
  age_min?: number | null
  suggested_salesman_id?: number | null
  suggested_reason?: string | null
}
interface QueueResp { orders: QueueRow[]; count: number; sla_min?: number }

export function AssignmentQueue({ onChanged, highlight }: { onChanged: () => void; highlight?: boolean }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['shop-assignment-queue'],
    queryFn: () => apiGet<QueueResp>('/shop/assignment-queue'),
    refetchInterval: 60_000,
  })
  const rows = data?.orders || []
  if (isLoading || !rows.length) return null
  const sla = data?.sla_min ?? 30
  return (
    <section className={cn('mb-5 rounded-[18px] border p-4', highlight ? 'border-[#9f1239] bg-[#fdf3f5]' : 'border-[#f3c9d2] bg-[#fff7f8]')} aria-labelledby="assign-queue">
      <div className="flex items-center justify-between gap-2">
        <h2 id="assign-queue" className="flex items-center gap-2 font-display text-[15px] font-bold text-[#1a1430]">
          <AlertTriangle size={16} className="text-[#9f1239]" /> {rows.length} unassigned {rows.length === 1 ? 'order' : 'orders'}
        </h2>
        <span className="text-[11.5px] text-[#6b6480]">Reminder after {sla} min</span>
      </div>
      <ul className="mt-3 divide-y divide-[#f3c9d2]/60">
        {rows.map((r) => (
          <li key={r.id} className="grid gap-2 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-display text-[14px] font-bold tabular-nums text-[#1a1430]">{r.order_no}</span>
                <Badge tone={r.age_min != null && r.age_min > sla ? 'rose' : 'grey'}>{r.age_min != null ? `${r.age_min} min` : ''}</Badge>
                {r.attribution_conflict && <Badge tone="amber">Referral conflict</Badge>}
                {r.session_ref && <Badge tone="accent">via /{r.session_ref}</Badge>}
              </div>
              <div className="mt-0.5 text-[12px] text-[#6b6480]">
                {[r.customer_shop, r.customer_area].filter(Boolean).join(' · ') || 'No shop name'} · {bhd(r.total_bhd, 3)} · {r.units_count ?? 0} pcs
              </div>
            </div>
            <div className="sm:w-[22rem]">
              <AssignBox orderId={r.id} suggestedId={r.suggested_salesman_id} suggestedReason={r.suggested_reason} compact onDone={() => { refetch(); onChanged() }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The rep's one-tap WhatsApp to the merchant for the order's current stage. */
export function CustomerWhatsApp({ url, first }: { url?: string | null; first?: string | null }) {
  if (!url) return null
  return (
    <a href={url} target="_blank" rel="noreferrer" className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#25d366] text-[13.5px] font-semibold text-[#08331b] hover:opacity-90">
      <MessageCircle size={16} aria-hidden="true" /> WhatsApp {first || 'the shop'} the update
    </a>
  )
}
