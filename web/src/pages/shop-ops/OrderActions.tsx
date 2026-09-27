import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, ChevronDown, Loader2, UserRoundCheck, X } from 'lucide-react'
import { apiGet, apiPost } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd, fmtDate } from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Stepper } from '@/components/ui/stepper'
import {
  apiDetail, CANCEL_REASONS, confidencePct, lineDiffSummary, PAYMENT_LABEL, PAYMENT_METHODS,
  PAYMENT_PILL_LABEL, PAYMENT_TONE, useFocusCandidates,
  type FocusCandidate, type FocusLinkResp, type FocusOrderRow,
} from './pipeline'

/**
 * The actions the marketplace added to an order, shared by the desk drawer, the field sheet and
 * the assignment queue (docs/SHOP.md § Marketplace). The order heart itself (R7c — Confirm / Amend
 * editor, Deliver with changes, Reopen, Tell the shop) lives in ./OrderHeart.tsx.
 *   • AssignBox     — assign or reassign (admins; a salesman only takes an unassigned order);
 *                     admins may also make the rep the SHOP's rep (R3, audited)
 *   • AssignmentQueue — unassigned open orders with a history-based suggestion
 *   • R3 pipeline: CancelReasonPicker (a staff cancel names its reason), PaymentPill / PaymentBox
 *     (paid | partly paid | unpaid as a recorded fact, admin), ReturnBox (a 'returned' event with
 *     lines and a reason on a delivered order, admin), InvoiceBox (the Focus invoice number).
 *     The constants and pure helpers live in ./pipeline.ts.
 */

/* ───────────────────────── R3: cancel reasons ───────────────────────── */

/** Reason chips (+ an INTERNAL note, required for "Other"). `onChange` gets what the status route
 *  needs. The shop's cancel email carries the reason's label only (app/shop_pipeline.py
 *  customer_cancel_text); the note goes to cancel_reason and the timeline and never leaves. */
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
        placeholder={needsNote ? 'Internal note — say why (required for Other)' : 'Internal note (optional)'}
        aria-label="Internal cancel note"
        aria-describedby="cancel-note-hint"
        aria-required={needsNote}
        maxLength={300}
        className="h-10 w-full rounded-lg border border-[#E2DCEA] bg-white px-3 text-[13px] outline-none focus:border-[#9f1239]"
      />
      <p id="cancel-note-hint" className="text-[11px] leading-snug text-[#6b6480]">
        The shop is told the reason only (for example “Out of stock”). This note stays in the office record.
      </p>
    </div>
  )
}

/* ───────────────────────── R3: payment ───────────────────────── */

/** Paid / Partly paid pill. The default state is shown only once the order is delivered (until
 *  then nothing is due), and it says "Payment not recorded" rather than "Unpaid": 'unpaid' is
 *  the column default, and a delivered order may well have been paid in Focus without anyone
 *  recording it here. */
export function PaymentPill({ status, orderStatus }: { status?: string | null; orderStatus: string }) {
  const s = status || 'unpaid'
  if (s === 'unpaid' && orderStatus !== 'delivered') return null
  const title = s === 'unpaid' ? 'No payment has been recorded on this order here — the Focus ledger is the record until the office records one.' : undefined
  return <Badge tone={PAYMENT_TONE[s] || 'grey'} title={title}>{PAYMENT_PILL_LABEL[s] || s}</Badge>
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
        <span>Payment{method ? <span className="ml-1.5 font-normal text-[#6b6480]">· {PAYMENT_METHODS.find((m) => m.value === method)?.label || method}</span> : null}</span>
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
  /** R7c: what was handed over (null on an order delivered before the column existed) */
  qty_delivered?: number | null
  line_status?: string | null
  unit_price_bhd?: number | null
  unit_price_confirmed?: number | null
}

export function ReturnBox({ orderId, lines, onDone, onCancel }: { orderId: number; lines: ReturnableLine[]; onDone: () => void; onCancel?: () => void }) {
  const toast = useToast()
  // R7c: only what was handed over can come back (qty_delivered; else the confirmed quantity, which
  // is 0 for a line that was unavailable or substituted) — the server refuses the rest
  const cap = (l: ReturnableLine) => (l.qty_delivered != null ? l.qty_delivered
    : l.qty_confirmed != null ? l.qty_confirmed
      : ['removed', 'unavailable', 'substituted'].includes(l.line_status || 'ok') ? 0 : l.qty)
  const live = lines.filter((l) => cap(l) > 0)
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


/* ───────────────────────── labels (R7c: three visible stages) ─────────────────────────
 * The words live in ./heart.ts (Preparing / On the way read "Confirmed" for a rep and the desk; the
 * storekeeper's pick list keeps its own stamp words from the API's status_label). Re-exported here
 * because PickList.tsx and sales/Today.tsx import them from this file. */
export { STATUS_LABEL, STATUS_TONE } from './heart'

interface SalesmanOpt { id: number; name: string; is_active?: boolean }

// Not exported: nothing outside this file uses it (react-refresh/only-export-components wants a
// components-only file; the one constant re-export above is for PickList.tsx and sales/Today.tsx).
function useSalesmenOptions(enabled: boolean) {
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

/** A delivered order the matcher did not suggest anything for (another warehouse name, a partial
 *  invoice): the office types the Focus invoice number and it is linked by hand. The server checks
 *  the number is in the uploaded Focus sales before it accepts it (decide_focus_link, method 'manual'). */
export function FocusLinkBox({ orderId, onDone }: { orderId: number; onDone: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  async function link() {
    if (!val.trim()) return
    setBusy(true)
    try {
      const res = await apiPost<FocusLinkResp>(`/shop/orders/${orderId}/focus-link`, { invoice_key: val.trim(), action: 'accept' })
      toast(`Linked to ${res.invoice_key}.`, 'success')
      setVal('')
      qc.invalidateQueries({ queryKey: ['shop-focus-candidates'] })
      onDone()
    } catch (e) {
      toast(apiDetail(e, 'Could not link the invoice.'), 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex gap-1.5">
      <input value={val} onChange={(e) => setVal(e.target.value)} placeholder="Focus invoice no, e.g. SI-YQ-26-09-119"
        aria-label="Focus invoice number" className="h-10 min-w-0 flex-1 rounded-xl border px-3 text-[13px]" />
      <button type="button" onClick={link} disabled={busy || !val.trim()}
        className="flex h-10 items-center gap-1 rounded-xl bg-primary px-3 text-[12.5px] font-semibold text-primary-foreground disabled:opacity-50">
        {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Link invoice
      </button>
    </div>
  )
}

/* ───────────────────────── R7a: Focus exceptions (item 6) ───────────────────────── */

function FocusOrderCandidates({
  row, onOpenOrder, onChanged, readOnly,
}: {
  row: FocusOrderRow
  onOpenOrder: (id: number) => void
  onChanged: () => void
  readOnly?: boolean
}) {
  const toast = useToast()
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  // "Not this one" is a real POST (action: reject) — the server never suggests that pair again.
  // The next-best candidate is already in hand, so this row swaps to it immediately rather than
  // waiting on the refetch below (which still runs, so the panel's own order/suggestion counts
  // catch up too).
  const [turnedDown, setTurnedDown] = useState<Set<string>>(new Set())
  const best = row.candidates.find((c) => !turnedDown.has(c.invoice_key))

  async function decide(c: FocusCandidate, action: 'accept' | 'reject') {
    setBusy(`${action}:${c.invoice_key}`)
    try {
      const res = await apiPost<FocusLinkResp>(`/shop/orders/${row.order_id}/focus-link`, { invoice_key: c.invoice_key, action })
      if (action === 'accept') {
        toast(res.advanced
          ? `${row.order_no} linked to ${res.invoice_key} and marked ${res.status_label}.`
          : `${row.order_no} linked to ${res.invoice_key}. Status unchanged: the rep confirms what was supplied.`, 'success')
        onChanged()
      } else {
        setTurnedDown((s) => new Set(s).add(c.invoice_key))
        toast('Noted — not this one.', 'success')
      }
      qc.invalidateQueries({ queryKey: ['shop-focus-candidates'] })
    } catch (e) {
      toast(apiDetail(e, 'Could not record the decision.'), 'error')
    } finally {
      setBusy(null)
    }
  }

  if (!best) {
    return (
      <li className="p-3 text-[12.5px] text-muted-foreground">
        <button type="button" onClick={() => onOpenOrder(row.order_id)} className="font-semibold text-foreground hover:text-primary hover:underline">{row.order_no}</button>
        {' '}— every suggestion here was turned down.
      </li>
    )
  }

  // the server moves Confirmed / Preparing / On the way to Delivered, and a Received order only
  // when the invoice matches it exactly (app/shop_pipeline.decide_focus_link) — say which it will be
  const willDeliver = ['confirmed', 'packed', 'out_for_delivery'].includes(row.status) || (row.status === 'new' && best.exact)
  const others = Math.max(0, best.invoice_orders_n - 1)

  return (
    <li className="grid gap-2 p-3 text-[12.5px] sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <button type="button" onClick={() => onOpenOrder(row.order_id)} className="font-semibold text-foreground hover:text-primary hover:underline">{row.order_no}</button>
          <Badge tone="grey">{row.status_label}</Badge>
          <Badge tone={best.exact ? 'green' : 'amber'}>{best.invoice_key}</Badge>
          <Badge tone="grey">{best.method_label} · {confidencePct(best.confidence)}</Badge>
          {row.candidates.length > 1 && <span className="text-muted-foreground">+{row.candidates.length - 1} more</span>}
        </div>
        <div className="mt-0.5 text-muted-foreground">
          {[row.customer_shop, row.salesman_name].filter(Boolean).join(' · ')} · order {bhd(row.order_total_bhd, 3)}
          {best.invoice_date ? ` · invoice ${fmtDate(best.invoice_date)}` : ''}
          {best.amount_diff_bhd ? ` · diff ${bhd(best.amount_diff_bhd, 3)}` : ''}
        </div>
        <div className="mt-0.5 text-muted-foreground">
          {lineDiffSummary(best.lines)} · invoice {bhd(best.invoice_total_bhd, 3)}
          {best.focus_customer ? <> to <b className="font-semibold text-foreground">{best.focus_customer}</b></> : null}
        </div>
        {others > 0 && (
          <div className="mt-0.5 font-medium text-amber-700">
            This invoice is also suggested for {others} other order{others === 1 ? '' : 's'}: check it is this shop's.
          </div>
        )}
      </div>
      {!readOnly && (
        <div className="flex gap-1.5 sm:w-[17rem]">
          <button type="button" onClick={() => decide(best, 'accept')} disabled={busy !== null}
            className="flex h-9 flex-1 items-center justify-center gap-1 rounded-lg bg-[#137a48] px-2.5 text-[12px] font-semibold text-white disabled:opacity-50">
            {busy === `accept:${best.invoice_key}` ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} {willDeliver ? 'Accept & mark delivered' : 'Link invoice'}
          </button>
          <button type="button" onClick={() => decide(best, 'reject')} disabled={busy !== null}
            className="flex h-9 flex-1 items-center justify-center gap-1 rounded-lg border border-[#E2DCEA] bg-white px-2.5 text-[12px] font-semibold text-[#1A1428] disabled:opacity-50">
            {busy === `reject:${best.invoice_key}` ? <Loader2 size={13} className="animate-spin" /> : <X size={13} />} Not this one
          </button>
        </div>
      )}
    </li>
  )
}

/**
 * Replaces the old "Focus check" banner, the Payment column/pills, the PaymentBox and the
 * free-text "Focus invoice no" inputs (R7a item 6) — one place that says what the ledger
 * suggests and lets the office accept it in a tap. Renders nothing while loading, on any error
 * (the route may not be deployed yet), or once the migration is applied but nothing needs a
 * look — same "say nothing until there's something to say" rule as AssignmentQueue.
 */
export function FocusExceptionsPanel({
  onOpenOrder, onChanged, readOnly,
}: {
  onOpenOrder: (id: number) => void
  onChanged: () => void
  readOnly?: boolean
}) {
  const { data, isLoading, isError } = useFocusCandidates()
  const [open, setOpen] = useState(true)
  if (isLoading || isError || !data?.orders?.length) return null
  return (
    <section className="mb-5 rounded-[18px] border border-[#f3c9d2] bg-[#fff7f8] p-4" aria-labelledby="focus-exceptions">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-center justify-between gap-2 text-left">
        <h2 id="focus-exceptions" className="flex items-center gap-2 font-display text-[15px] font-bold">
          <AlertTriangle size={16} className="text-amber-600" /> Focus exceptions
          <span className="text-[12.5px] font-medium text-muted-foreground">
            {data.count} order{data.count === 1 ? '' : 's'} · {data.candidates} suggestion{data.candidates === 1 ? '' : 's'}
          </span>
        </h2>
        <ChevronDown size={16} className={cn('shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="mt-3">
          <p className="mb-2 text-[12px] text-muted-foreground">
            Orders the uploaded Focus sales ledger suggests an invoice for
            {data.ledger_as_of ? <> (sales up to <b>{fmtDate(data.ledger_as_of)}</b>)</> : null} — one tap accepts the best match, or turn it down to see the next one.
          </p>
          <ul className="divide-y overflow-hidden rounded-xl border bg-white">
            {data.orders.map((o) => (
              <FocusOrderCandidates key={o.order_id} row={o} onOpenOrder={onOpenOrder} onChanged={onChanged} readOnly={readOnly} />
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
