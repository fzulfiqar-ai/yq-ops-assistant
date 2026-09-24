import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Check, Loader2, MessageCircle, UserRoundCheck } from 'lucide-react'
import { apiGet, apiPost, ApiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Stepper } from '@/components/ui/stepper'
import { apiDetail, CANCEL_REASONS, PAYMENT_LABEL, PAYMENT_METHODS, PAYMENT_TONE } from './pipeline'

/**
 * The actions the marketplace added to an order, shared by the desk drawer, the field sheet and
 * the assignment queue (docs/SHOP.md § Marketplace):
 *   • ConfirmEditor — confirm with changes (per-line confirmed qty / remove, expected delivery)
 *   • AssignBox     — assign or reassign (admins; a salesman only takes an unassigned order);
 *                     admins may also make the rep the SHOP's rep (R3, audited)
 *   • AssignmentQueue — unassigned open orders with a history-based suggestion
 *   • R3 pipeline: CancelReasonPicker (a staff cancel names its reason), PaymentPill / PaymentBox
 *     (paid | partly paid | unpaid as a recorded fact, admin), ReturnBox (a 'returned' event with
 *     lines and a reason on a delivered order, admin), InvoiceBox (the Focus invoice number).
 *     The constants and pure helpers live in ./pipeline.ts.
 */

/* ───────────────────────── R3: cancel reasons ───────────────────────── */

/** Reason chips (+ a note, required for "Other"). `onChange` gets what the status route needs. */
export function CancelReasonPicker({
  value, note, onChange, compact,
}: {
  value: string
  note: string
  onChange: (next: { reason_code: string; note: string }) => void
  compact?: boolean
}) {
  const needsNote = value === 'other'
  return (
    <div className="space-y-2">
      <div className={cn('text-[11.5px] font-semibold text-[#6b6480]', compact && 'sr-only')}>Why is it cancelled?</div>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Cancel reason">
        {CANCEL_REASONS.map((r) => (
          <button
            key={r.code}
            type="button"
            role="radio"
            aria-checked={value === r.code}
            onClick={() => onChange({ reason_code: value === r.code ? '' : r.code, note })}
            className={cn('h-9 rounded-full border px-3 text-[12.5px] font-semibold transition-colors duration-150 motion-reduce:transition-none',
              value === r.code ? 'border-[#9f1239] bg-[#9f1239] text-white' : 'border-[#E2DCEA] bg-white text-[#1A1428] hover:border-[#9f1239]')}
          >
            {r.label}
          </button>
        ))}
      </div>
      <input
        value={note}
        onChange={(e) => onChange({ reason_code: value, note: e.target.value })}
        placeholder={needsNote ? 'Say why (required for Other)' : 'Note for the record (optional)'}
        aria-label="Cancel note"
        aria-required={needsNote}
        maxLength={300}
        className="h-10 w-full rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#9f1239]"
      />
    </div>
  )
}

/* ───────────────────────── R3: payment ───────────────────────── */

/** Paid / Partly paid pill. Unpaid is shown only once the order is delivered (until then it is
 *  simply not due), so a card never shouts "Unpaid" at a rep who has not delivered yet. */
export function PaymentPill({ status, orderStatus }: { status?: string | null; orderStatus: string }) {
  const s = status || 'unpaid'
  if (s === 'unpaid' && orderStatus !== 'delivered') return null
  return <Badge tone={PAYMENT_TONE[s] || 'grey'}>{PAYMENT_LABEL[s] || s}</Badge>
}

export function PaymentBox({
  orderId, status, method, total, onDone,
}: {
  orderId: number
  status?: string | null
  method?: string | null
  total?: number | null
  onDone: () => void
}) {
  const toast = useToast()
  const [next, setNext] = useState(status || 'unpaid')
  const [how, setHow] = useState(method || '')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const changed = next !== (status || 'unpaid') || how !== (method || '') || amount.trim() !== '' || note.trim() !== ''

  async function save() {
    setBusy(true)
    try {
      await apiPost(`/shop/orders/${orderId}/payment`, {
        status: next, method: how || undefined,
        amount_bhd: amount.trim() === '' ? undefined : Number(amount),
        note: note.trim() || undefined,
      })
      toast(`Payment recorded: ${PAYMENT_LABEL[next] || next}.`, 'success')
      setAmount(''); setNote('')
      onDone()
    } catch (e) {
      toast(apiDetail(e, 'Could not record the payment.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl border border-[#E9E4EF] bg-white p-3">
      <div className="mb-2 flex items-center justify-between gap-2 text-[12px] font-semibold text-[#1A1428]">
        <span>Payment</span>
        <PaymentPill status={status} orderStatus="delivered" />
      </div>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Payment status">
        {(['unpaid', 'partial', 'paid'] as const).map((s) => (
          <button key={s} type="button" role="radio" aria-checked={next === s} onClick={() => setNext(s)}
            className={cn('h-9 rounded-full border px-3 text-[12.5px] font-semibold transition-colors duration-150',
              next === s ? 'border-[#6D4091] bg-[#6D4091] text-white' : 'border-[#E2DCEA] bg-white text-[#1A1428] hover:border-[#6D4091]')}>
            {PAYMENT_LABEL[s]}
          </button>
        ))}
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <select value={how} onChange={(e) => setHow(e.target.value)} aria-label="Payment method"
          className="h-10 rounded-lg border border-[#E2DCEA] bg-white px-2.5 text-[13px] outline-none focus:border-[#6D4091]">
          {PAYMENT_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
        <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" aria-label="Amount (BHD)"
          placeholder={next === 'paid' ? `BHD ${Number(total || 0).toFixed(3)}` : 'Amount (BHD)'}
          className="h-10 rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#6D4091]" />
        <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Payment note" placeholder="Note (optional)" maxLength={300}
          className="h-10 rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#6D4091]" />
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-[11px] text-[#6b6480]">A recorded fact — the order stays where it is. Every change is on the timeline.</span>
        <button type="button" onClick={save} disabled={busy || !changed}
          className="flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-[#6D4091] px-3 text-[12.5px] font-semibold text-white disabled:opacity-50">
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Record
        </button>
      </div>
    </div>
  )
}

/* ───────────────────────── R3: returns (an event, never a status) ───────────────────────── */

export interface ReturnableLine {
  id: number
  item_code: string
  display_name?: string | null
  qty: number
  qty_confirmed?: number | null
  line_status?: string | null
  unit_price_bhd?: number | null
  unit_price_confirmed?: number | null
}

export function ReturnBox({ orderId, lines, onDone, onCancel }: { orderId: number; lines: ReturnableLine[]; onDone: () => void; onCancel?: () => void }) {
  const toast = useToast()
  const live = lines.filter((l) => (l.line_status || 'ok') !== 'removed')
  const cap = (l: ReturnableLine) => (l.qty_confirmed != null ? l.qty_confirmed : l.qty)
  const price = (l: ReturnableLine) => Number(l.unit_price_confirmed ?? l.unit_price_bhd ?? 0)
  const [qty, setQty] = useState<Record<number, number>>({})
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const picked = live.filter((l) => (qty[l.id] || 0) > 0)
  const value = picked.reduce((s, l) => s + price(l) * (qty[l.id] || 0), 0)   // display only — the server keeps the exact figure
  const ready = picked.length > 0 && reason.trim().length >= 3

  async function submit() {
    if (!ready) return
    setBusy(true)
    try {
      const res = await apiPost<{ value_bhd: number }>(`/shop/orders/${orderId}/return`, {
        lines: picked.map((l) => ({ line_id: l.id, qty: qty[l.id] })), reason: reason.trim(),
      })
      toast(`Return recorded · ${bhd(res.value_bhd, 3)}.`, 'success')
      onDone()
    } catch (e) {
      toast(apiDetail(e, 'Could not record the return.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-[#f3c9d2] bg-[#fff7f8] p-3.5">
      <div className="text-[12px] font-semibold text-[#1A1428]">Record a return</div>
      <ul className="divide-y divide-[#E9E4EF] overflow-hidden rounded-xl border border-[#E9E4EF] bg-white">
        {live.map((l) => (
          <li key={l.id} className="flex items-center gap-3 p-2.5">
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-semibold text-[#1A1428]">{l.display_name || l.item_code}</div>
              <div className="text-[11px] tabular-nums text-[#6b6480]">{l.item_code} · delivered {cap(l)} · {bhd(price(l), 3)} each</div>
            </div>
            <Stepper size="sm" value={qty[l.id] || 0} min={0} max={cap(l)} label={`Return ${l.item_code}`}
              onChange={(v) => setQty((s) => ({ ...s, [l.id]: Math.max(0, Math.min(cap(l), v)) }))} />
          </li>
        ))}
      </ul>
      <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why (required) — damaged, wrong item, shop refused…" aria-label="Return reason" maxLength={300}
        className="h-10 w-full rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#9f1239]" />
      <div className="flex items-center justify-between gap-2 text-[12px] text-[#6b6480]">
        <span>{picked.length ? `${picked.reduce((s, l) => s + (qty[l.id] || 0), 0)} pcs · about ${bhd(value, 3)}` : 'Pick the quantities that came back'}</span>
        <span>The order stays Delivered; the return goes on its timeline.</span>
      </div>
      <div className="flex gap-2">
        {onCancel && <button type="button" onClick={onCancel} className="h-11 flex-1 rounded-xl border border-[#E2DCEA] bg-white text-[13px] font-semibold text-[#1A1428]">Back</button>}
        <button type="button" onClick={submit} disabled={busy || !ready} className="flex h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-[#9f1239] text-[14px] font-semibold text-white disabled:opacity-50">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />} Record return
        </button>
      </div>
    </div>
  )
}

/* ───────────────────────── R3: the Focus invoice ───────────────────────── */

export function InvoiceBox({ orderId, current, onDone }: { orderId: number; current?: string | null; onDone: () => void }) {
  const toast = useToast()
  const [value, setValue] = useState(current || '')
  const [busy, setBusy] = useState(false)
  async function save() {
    if (!value.trim() || value.trim() === (current || '')) return
    setBusy(true)
    try {
      await apiPost(`/shop/orders/${orderId}/invoice`, { focus_invoice_no: value.trim() })
      toast('Focus invoice recorded.', 'success')
      onDone()
    } catch (e) {
      toast(apiDetail(e, 'Could not record the invoice.'), 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Focus invoice no" aria-label="Focus invoice number" maxLength={40} spellCheck={false}
        className="h-10 min-w-[10rem] flex-1 rounded-lg border border-[#E2DCEA] bg-white px-3 font-mono text-[13px] outline-none focus:border-[#6D4091]" />
      <button type="button" onClick={save} disabled={busy || !value.trim() || value.trim() === (current || '')}
        className="flex h-10 items-center gap-1.5 rounded-lg border border-[#6D4091] px-3 text-[12.5px] font-semibold text-[#6D4091] disabled:opacity-50">
        {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} {current ? 'Update' : 'Record'}
      </button>
    </div>
  )
}


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

/** What a salesman actually says in the shop — one tap instead of typing on the road. */
const ETA_CHIPS = ['Today', 'Tomorrow', 'Day after tomorrow', 'With my next visit'] as const

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
    <div className="space-y-3 rounded-xl border border-[#E3DAEC] bg-[#F6F3F8] p-3.5">
      <div className="text-[12px] font-semibold text-[#1A1428]">Confirm quantities</div>
      <ul className="divide-y divide-[#E9E4EF] overflow-hidden rounded-xl border border-[#E9E4EF] bg-white">
        {lines.map((l) => {
          const d = draft[l.id] || { qty: String(l.qty), removed: false }
          const n = Math.floor(Number(d.qty) || 0)
          return (
            <li key={l.id} className={cn('flex items-center gap-3 p-2.5', d.removed && 'opacity-60')}>
              <div className="min-w-0 flex-1">
                <div className={cn('truncate text-[13px] font-semibold text-[#1A1428]', d.removed && 'line-through')}>{l.display_name || l.item_code}</div>
                <div className="text-[11px] tabular-nums text-[#6b6480]">{l.item_code} · ordered {l.qty}{l.backorder ? ' · backorder' : ''}</div>
              </div>
              {!d.removed && (
                <Stepper
                  size="sm"
                  value={n}
                  min={1}
                  max={9999}
                  label={l.item_code}
                  onChange={(v) => setDraft((s) => ({ ...s, [l.id]: { ...d, qty: String(v) } }))}
                  onRemove={() => setDraft((s) => ({ ...s, [l.id]: { ...d, removed: true } }))}
                  className={cn(n !== l.qty && 'border-[#6D4091] [&_span]:text-[#6D4091]')}
                />
              )}
              <button
                type="button"
                onClick={() => setDraft((s) => ({ ...s, [l.id]: { ...d, removed: !d.removed } }))}
                className={cn('h-10 shrink-0 rounded-lg border px-2.5 text-[12px] font-semibold', d.removed ? 'border-[#6D4091] bg-[#EEE8F4] text-[#6D4091]' : 'border-[#E2DCEA] bg-white text-[#9f1239]')}
              >
                {d.removed ? 'Keep' : 'Remove'}
              </button>
            </li>
          )
        })}
      </ul>
      <div>
        <div className="mb-1.5 text-[11.5px] font-semibold text-[#6b6480]">When will it reach the shop?</div>
        <div className="flex flex-wrap gap-1.5">
          {ETA_CHIPS.map((c) => (
            <button key={c} type="button" onClick={() => setEta(eta === c ? '' : c)} aria-pressed={eta === c} className={cn('h-9 rounded-full border px-3 text-[12.5px] font-semibold transition-colors duration-150', eta === c ? 'border-[#6D4091] bg-[#6D4091] text-white' : 'border-[#E2DCEA] bg-white text-[#1A1428] hover:border-[#6D4091]')}>
              {c}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <input value={eta} onChange={(e) => setEta(e.target.value)} placeholder="Or type it (e.g. Thursday morning)" aria-label="Expected delivery" className="h-10 rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#6D4091]" />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the shop (optional)" aria-label="Note" className="h-10 rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#6D4091]" />
      </div>
      <div className="flex items-center justify-between gap-2 text-[12px] text-[#6b6480]">
        <span>{changes.length ? `${changes.length} change${changes.length === 1 ? '' : 's'}` : 'As ordered'} · est. {bhd(total, 3)}</span>
        {!live.length && <span className="inline-flex items-center gap-1 font-medium text-[#9f1239]"><AlertTriangle size={12} /> Remove every line? Cancel the order instead.</span>}
      </div>
      <div className="flex gap-2">
        {onCancel && <button type="button" onClick={onCancel} className="h-11 flex-1 rounded-xl border border-[#E2DCEA] bg-white text-[13px] font-semibold text-[#1A1428]">Back</button>}
        <button type="button" onClick={submit} disabled={busy || invalid || !live.length} className="flex h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[14px] font-semibold text-white disabled:opacity-50">
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
  orderId, currentSalesmanId, suggestedId, suggestedReason, compact, onDone, canBindShop, shopName,
}: {
  orderId: number
  currentSalesmanId?: number | null
  suggestedId?: number | null
  suggestedReason?: string | null
  compact?: boolean
  onDone: () => void
  /** R3: admins may also make the rep the shop's rep (POST assign `also_customer`, audited). */
  canBindShop?: boolean
  shopName?: string | null
}) {
  const toast = useToast()
  const { data: options } = useSalesmenOptions(true)
  const [pick, setPick] = useState<number | ''>(suggestedId ?? currentSalesmanId ?? '')
  const [reason, setReason] = useState('')
  const [bindShop, setBindShop] = useState(false)
  const [busy, setBusy] = useState(false)
  // When the suggestion arrives after mount, adopt it once (render-phase derived state).
  const proposed = suggestedId ?? currentSalesmanId ?? ''
  const [proposedSeen, setProposedSeen] = useState(proposed)
  if (proposed !== proposedSeen) {
    setProposedSeen(proposed)
    if (pick === '' && proposed !== '') setPick(proposed)
  }

  async function assign() {
    if (pick === '') return
    setBusy(true)
    try {
      const res = await apiPost<{ customer_assign?: { changed?: boolean; to_name?: string | null; error?: string } | null }>(
        `/shop/orders/${orderId}/assign`,
        { salesman_id: Number(pick), reason: reason.trim() || undefined, also_customer: Boolean(canBindShop && bindShop) },
      )
      const ca = res?.customer_assign
      if (ca?.error) toast(`Order assigned, but the shop's rep was not changed: ${ca.error}`, 'error')
      else if (ca?.changed) toast(`Order assigned · ${ca.to_name || 'the rep'} is now this shop's rep.`, 'success')
      else toast(currentSalesmanId ? 'Order reassigned.' : 'Order assigned.', 'success')
      onDone()
    } catch (e) {
      toast(apiDetail(e, 'Could not assign the order.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={cn('rounded-xl border p-3', currentSalesmanId ? 'border-[#E9E4EF] bg-white' : 'border-[#f3c9d2] bg-[#fdf3f5]')}>
      {!compact && (
        <div className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-[#1A1428]">
          <UserRoundCheck size={14} className="text-[#6D4091]" /> {currentSalesmanId ? 'Reassign' : 'Assign a salesman'}
        </div>
      )}
      {suggestedReason && <p className="mb-2 text-[11.5px] text-[#6b6480]">Suggested: {suggestedReason}</p>}
      <div className="flex flex-wrap gap-2">
        <select value={pick} onChange={(e) => setPick(e.target.value === '' ? '' : Number(e.target.value))} className="h-10 min-w-[10rem] flex-1 rounded-lg border border-[#E2DCEA] bg-white px-2.5 text-[13px] outline-none focus:border-[#6D4091]" aria-label="Salesman">
          <option value="">Choose…</option>
          {(options || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {!compact && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={bindShop ? 'Reason (goes on the shop’s record)' : 'Reason (optional)'} className="h-10 flex-1 rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#6D4091]" />}
        <button type="button" onClick={assign} disabled={busy || pick === '' || (pick === currentSalesmanId && !bindShop)} className="flex h-10 items-center gap-1.5 rounded-lg bg-[#6D4091] px-3.5 text-[13px] font-semibold text-white disabled:opacity-50">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} {currentSalesmanId ? 'Reassign' : 'Assign'}
        </button>
      </div>
      {canBindShop && (
        <label className="mt-2 flex cursor-pointer items-start gap-2 text-[12px] text-[#1A1428]">
          <input type="checkbox" checked={bindShop} onChange={(e) => setBindShop(e.target.checked)} className="mt-0.5" />
          <span>
            Also make this rep <b>{shopName ? `${shopName}’s` : 'this shop’s'}</b> rep
            <span className="block text-[11px] text-[#6b6480]">Every future order from this shop routes to them, whatever link it arrives on. Recorded with your reason.</span>
          </span>
        </label>
      )}
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
        <h2 id="assign-queue" className="flex items-center gap-2 font-display text-[15px] font-bold text-[#1A1428]">
          <AlertTriangle size={16} className="text-[#9f1239]" /> {rows.length} unassigned {rows.length === 1 ? 'order' : 'orders'}
        </h2>
        <span className="text-[11.5px] text-[#6b6480]">Reminder after {sla} min</span>
      </div>
      <ul className="mt-3 divide-y divide-[#f3c9d2]/60">
        {rows.map((r) => (
          <li key={r.id} className="grid gap-2 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-display text-[14px] font-bold tabular-nums text-[#1A1428]">{r.order_no}</span>
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
