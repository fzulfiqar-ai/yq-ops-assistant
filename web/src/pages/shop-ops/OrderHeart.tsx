import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, MessageCircle, Plus, Repeat, RotateCcw, Search, Undo2, X } from 'lucide-react'
import { apiPost, ApiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Stepper } from '@/components/ui/stepper'
import { apiDetail, useStaffCatalog } from './pipeline'
import {
  ADVERSE_MSG, AGREED_VIA, ETA_CHIPS, VISIBLE_LABEL,
  adverseText, confirmedQty, deliverableLines, dispositionChip, indexItems, initialDeliverDraft,
  initialDraft, isAddedLine, isBackorderLine, itemOf, lineChanged, lockUnit, planDeliver, planEdit, reasonOptions,
  reopenTarget, searchItems, stagesOf, stockView, substituteOptions, unitAt,
  type AddDraft, type Adverse, type DeliverDraft, type EditOrder, type HeartLine, type ItemOption,
  type LineDraft, type StageStep, type StockItem,
} from './heart'

/**
 * The order heart (R7c, Sprint 3) — the pieces the order desk drawer and the rep's field sheet
 * share, coded to app/shop_heart.py:
 *
 *   Received → Confirmed → Delivered   (or Cancelled)
 *
 *   • OrderEditor   — ONE editor for Confirm (from Received) and Amend (after): live stock per line
 *                     with a one-tap "set to available", a reason chip on every changed line,
 *                     replace with a similar item, add an item. An adverse change shows the
 *                     "Shop agreed" tick (WhatsApp / Phone / Visit) and cannot be saved without it.
 *   • DeliverEditor — "Deliver with changes": what was really handed over, a reason for every
 *                     difference, items added at the shop. (Plain Delivered is one tap, no editor.)
 *   • ReopenBox     — admins, within 7 days, with a reason.
 *   • TellShopButton — the prefilled WhatsApp; the tap is logged (POST …/customer-notified).
 *   • StageTrack, LineChips, EffectiveTotal — the three stages and the confirmed-else-original money.
 *
 * The pure rules live in ./heart.ts; this file exports components only (react-refresh).
 */

const INK = 'text-[#1A1428]'
const MUTED = 'text-[#6b6480]'
/** 44 px tall on a phone, 36 px from sm up (the desk). */
const CHIP = 'h-11 rounded-full border px-3.5 text-[12.5px] font-semibold transition-colors duration-150 motion-reduce:transition-none sm:h-9 sm:px-3'
const CHIP_ON = 'border-[#6D4091] bg-[#6D4091] text-white'
const CHIP_OFF = 'border-[#E2DCEA] bg-white text-[#1A1428] hover:border-[#6D4091]'
const SMALL_BTN = 'inline-flex h-11 items-center gap-1.5 rounded-xl border border-[#E2DCEA] bg-white px-3 text-[12.5px] font-semibold text-[#1A1428] transition-colors duration-150 hover:border-[#6D4091] motion-reduce:transition-none sm:h-9 sm:rounded-lg'
const INPUT = 'h-11 w-full rounded-xl border border-[#E2DCEA] bg-white px-3 text-[13.5px] outline-none focus:border-[#6D4091] sm:h-10 sm:rounded-lg sm:text-[13px]'

/** What every write answers with (StaffOrder under `order`, the step's WhatsApp text). */
export interface HeartResponse {
  ok?: boolean
  order?: { status?: string; whatsapp_url?: string | null } | null
  whatsapp_url?: string | null
  next_action?: string | null
  changed?: { item_code: string; from: number; to: number }[] | null
  removed?: string[] | null
  adverse?: { kind: string }[] | null
}

/** The order the editors read (a StaffOrder from GET /shop/orders/{id}). */
export interface HeartOrder extends EditOrder {
  id: number
  order_no?: string | null
  expected_delivery?: string | null
  change_reasons?: Record<string, string> | null
}

function priceDiff(d: number | null): { text: string; cls: string } | null {
  if (d == null) return null
  if (Math.round(d * 1000) === 0) return { text: 'Same price', cls: 'text-[#137a48]' }
  return d > 0
    ? { text: `+${d.toFixed(3)} each`, cls: 'text-[#96600d]' }
    : { text: `−${Math.abs(d).toFixed(3)} each`, cls: 'text-[#137a48]' }
}

function shortTime(iso?: string | null): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  try {
    return new Date(t).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

/* ───────────────────────── read-only pieces ───────────────────────── */

/** The confirmed-else-original money, with the as-ordered figure struck through when it differs. */
export function EffectiveTotal({ now, was, className }: { now: number; was?: number | null; className?: string }) {
  return (
    <span className={cn('inline-flex flex-wrap items-baseline justify-end gap-x-1.5 tabular-nums', className)}>
      {was != null && (
        <s className="whitespace-nowrap text-[0.82em] font-normal text-[#9a93ad]">
          <span className="sr-only">As ordered </span>{bhd(was, 3)}
        </s>
      )}
      <span className="whitespace-nowrap">{bhd(now, 3)}</span>
    </span>
  )
}

/** Received → Confirmed → Delivered, with the time each was reached. A cancelled order greys out. */
export function StageTrack({ order }: { order: { status?: string | null; steps?: StageStep[] | null; created_at?: string | null; confirmed_at?: string | null; delivered_at?: string | null } }) {
  const steps = stagesOf(order)
  const cancelled = order.status === 'cancelled'
  return (
    <ol className="flex items-start gap-1.5" aria-label={cancelled ? 'Order stages (cancelled)' : 'Order stages'}>
      {steps.map((s) => (
        <li key={s.status} className="min-w-0 flex-1" aria-current={s.current && !cancelled ? 'step' : undefined}>
          <div className={cn('h-1.5 rounded-full', !cancelled && (s.done || s.current) ? 'bg-[#6D4091]' : 'bg-[#E9E4EF]')} />
          <div className={cn('mt-1 truncate text-[11px] font-semibold',
            cancelled ? 'text-[#9a93ad]' : s.current ? INK : s.done ? 'text-[#6D4091]' : 'text-[#9a93ad]')}>
            {s.label}
          </div>
          {s.at && <div className="truncate text-[10.5px] tabular-nums text-[#9a93ad]">{shortTime(s.at)}</div>}
        </li>
      ))}
    </ol>
  )
}

/** Reduced / Unavailable / Substituted → CODE / Added / Comes later, and the public reason. */
export function LineChips({ line, showNote }: { line: HeartLine; showNote?: boolean }) {
  const chip = dispositionChip(line)
  if (!chip && !line.reason_label && !(showNote && line.note)) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {chip && <Badge tone={chip.tone}>{chip.label}</Badge>}
      {line.reason_label && <span className={cn('text-[11px]', MUTED)}>{line.reason_label}</span>}
      {showNote && line.note && <span className="text-[11px] italic text-[#9a93ad]" title="Office note — the shop never sees it">“{line.note}”</span>}
    </span>
  )
}

/* ───────────────────────── Tell the shop ───────────────────────── */

/**
 * The rep's one-tap WhatsApp to the shop, prefilled for the step just taken. The tap is logged
 * (POST …/customer-notified → a 'customer_notified' event) so the "Shop not told yet" chip clears;
 * the link opens whatever happens to the log. `primary` = the next action after a step.
 */
export function TellShopButton({
  orderId, url, variant = 'secondary', legacy, onTapped,
}: {
  orderId: number
  url?: string | null
  /** primary = the next action after a step (big, green, with "told them another way");
   *  secondary = a quiet full-width link; pill = the small button in the customer block */
  variant?: 'primary' | 'secondary' | 'pill'
  /** an API from before R7c has no customer-notified route: open WhatsApp, log nothing */
  legacy?: boolean
  onTapped?: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [told, setTold] = useState<string | null>(null)
  if (!url) return null
  const primary = variant === 'primary'

  function log(channel: 'whatsapp' | 'phone' | 'visit') {
    onTapped?.()
    if (legacy) return
    apiPost(`/shop/orders/${orderId}/customer-notified`, { channel })
      .then(() => {
        qc.invalidateQueries({ queryKey: ['shop-order', orderId] })
        if (channel !== 'whatsapp') {
          setTold(channel)
          toast(channel === 'phone' ? 'Noted: you told the shop by phone.' : 'Noted: you told the shop at a visit.', 'success')
        }
      })
      .catch(() => { /* best effort: WhatsApp opened anyway, and the chip simply stays */ })
  }

  if (variant === 'pill') {
    return (
      <a href={url} target="_blank" rel="noreferrer" onClick={() => log('whatsapp')}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-[12px] font-semibold text-white transition hover:bg-emerald-700 sm:min-h-0">
        <MessageCircle size={12} aria-hidden="true" /> WhatsApp customer
      </a>
    )
  }

  return (
    <div className="space-y-1.5">
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        onClick={() => log('whatsapp')}
        className={cn(
          'flex w-full items-center justify-center gap-2 rounded-xl font-semibold transition-opacity duration-150 hover:opacity-90 motion-reduce:transition-none',
          primary ? 'h-12 bg-[#25d366] text-[15px] text-[#08331b]' : 'h-11 border border-[#bfe9cf] bg-[#effaf3] text-[13.5px] text-[#0c5a2c]',
        )}
      >
        <MessageCircle size={primary ? 18 : 16} aria-hidden="true" /> {primary ? 'Tell the shop on WhatsApp' : 'WhatsApp the shop'}
      </a>
      {primary && !legacy && !told && (
        <p className={cn('text-center text-[11.5px]', MUTED)}>
          Already told them?{' '}
          <button type="button" onClick={() => log('phone')} className="inline-flex min-h-11 items-center px-1 font-semibold text-[#6D4091] hover:underline sm:min-h-0">By phone</button>
          ·
          <button type="button" onClick={() => log('visit')} className="inline-flex min-h-11 items-center px-1 font-semibold text-[#6D4091] hover:underline sm:min-h-0">At a visit</button>
        </p>
      )}
    </div>
  )
}

/* ───────────────────────── editor pieces ───────────────────────── */

function ReasonChips({
  label, options, value, note, onChange,
}: {
  label: string
  options: { code: string; label: string }[]
  value: string
  note: string
  onChange: (reason: string, note: string) => void
}) {
  return (
    <div className="space-y-1.5">
      <div className={cn('text-[11.5px] font-semibold', value ? MUTED : 'text-[#9f1239]')}>{label}</div>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={label}>
        {options.map((r) => (
          <button
            key={r.code}
            type="button"
            role="radio"
            aria-checked={value === r.code}
            onClick={() => onChange(value === r.code ? '' : r.code, note)}
            className={cn(CHIP, value === r.code ? CHIP_ON : CHIP_OFF)}
          >
            {r.label}
          </button>
        ))}
      </div>
      {value === 'other' && (
        <input
          value={note}
          onChange={(e) => onChange(value, e.target.value)}
          maxLength={200}
          placeholder="Say why (office only — the shop never sees this)"
          aria-label="Why — office note"
          aria-required
          className={INPUT}
        />
      )}
    </div>
  )
}

/** Shown only when a change is adverse to the shop — the rep ticks it and says how they agreed. */
function ShopAgreedBox({
  adverse, minOrder, agreed, onChange, server,
}: {
  adverse: Adverse[]
  minOrder?: number | null
  agreed: { on: boolean; via: string }
  onChange: (v: { on: boolean; via: string }) => void
  /** the server asked for the tick where the estimate did not see it (its price, its minimum) */
  server?: boolean
}) {
  const joined = adverse.map((a) => adverseText(a, minOrder)).join('; ')
  const what = joined ? `${joined.charAt(0).toUpperCase()}${joined.slice(1)}.` : ''
  return (
    <div className="space-y-2 rounded-xl border border-[#f0d9a8] bg-[#fdf6e7] p-3" role="group" aria-label="Shop agreement">
      <p className="text-[12.5px] leading-snug text-[#5c3d06]">
        <b>The shop has to agree to this.</b>{' '}
        {what || (server ? 'This change costs the shop more or goes below the minimum.' : '')}
        {' '}Save it only if they said yes.
      </p>
      <label className={cn('flex min-h-11 cursor-pointer items-center gap-2.5 text-[13.5px] font-semibold', INK)}>
        <input
          type="checkbox"
          checked={agreed.on}
          onChange={(e) => onChange({ on: e.target.checked, via: e.target.checked ? agreed.via : '' })}
          className="h-5 w-5 accent-[#6D4091]"
        />
        Shop agreed
      </label>
      {agreed.on && (
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="How the shop agreed">
          {AGREED_VIA.map((v) => (
            <button
              key={v.code}
              type="button"
              role="radio"
              aria-checked={agreed.via === v.code}
              onClick={() => onChange({ on: true, via: v.code })}
              className={cn(CHIP, agreed.via === v.code ? CHIP_ON : CHIP_OFF)}
            >
              {v.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

type CatalogStatus = 'loading' | 'ready' | 'error'

/** Pick an item: a substitute (same category, in stock, closest price first) or one to add (search). */
function ItemPicker({
  catalog, status, mode, lineCode, lock, qty, exclude, onPick, onClose,
}: {
  catalog: Map<string, StockItem> | null
  status: CatalogStatus
  mode: 'substitute' | 'add'
  lineCode?: string
  lock?: number | null
  qty: number
  exclude: Set<string>
  onPick: (it: StockItem) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const searching = q.trim().length >= 2
  let opts: ItemOption[] = []
  if (catalog) {
    opts = searching
      ? searchItems(catalog, q, qty, mode === 'substitute' ? lock ?? null : null, exclude)
      : mode === 'substitute' && lineCode ? substituteOptions(catalog, lineCode, lock ?? null, qty) : []
  }
  return (
    <div className="space-y-2 rounded-xl border border-[#E3DAEC] bg-white p-2.5">
      <div className="flex items-center gap-2">
        <div className="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-xl border border-[#E2DCEA] px-3 focus-within:border-[#6D4091] sm:h-10">
          <Search size={15} className={MUTED} aria-hidden="true" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={mode === 'substitute' ? 'Or search any item…' : 'Item code or name…'}
            aria-label={mode === 'substitute' ? 'Search a replacement item' : 'Search an item to add'}
            className="h-full w-full bg-transparent text-[13.5px] outline-none"
          />
        </div>
        <button type="button" onClick={onClose} aria-label="Close the picker" className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-[#E2DCEA] sm:h-10 sm:w-10">
          <X size={16} />
        </button>
      </div>
      {status === 'loading' && <p className={cn('px-1 text-[12px]', MUTED)}>Loading live stock…</p>}
      {status === 'error' && <p className="px-1 text-[12px] text-[#9f1239]">Couldn't load the catalog — try again in a moment.</p>}
      {status === 'ready' && mode === 'substitute' && !searching && opts.length > 0 && (
        <p className={cn('px-1 text-[11.5px]', MUTED)}>Same category, in stock — closest price first.</p>
      )}
      {opts.length > 0 && (
        <ul className="divide-y divide-[#F0ECF4] overflow-hidden rounded-lg border border-[#F0ECF4]">
          {opts.map((o) => {
            const sv = stockView(o.item, qty)
            const diff = mode === 'substitute' ? priceDiff(o.diff) : null
            return (
              <li key={o.item.item_code}>
                <button type="button" onClick={() => onPick(o.item)}
                  className="flex min-h-12 w-full items-center gap-3 px-2.5 py-2 text-left transition-colors duration-150 hover:bg-[#F6F3F8] motion-reduce:transition-none">
                  <div className="min-w-0 flex-1">
                    <div className={cn('truncate text-[13px] font-semibold', INK)}>{o.item.display_name || o.item.item_code}</div>
                    <div className={cn('flex flex-wrap items-center gap-1.5 text-[11px]', MUTED)}>
                      <span>{o.item.item_code}</span>
                      {sv.label && <Badge tone={sv.tone} dot>{sv.label}</Badge>}
                    </div>
                  </div>
                  <div className="shrink-0 text-right tabular-nums">
                    <div className={cn('text-[12.5px] font-semibold', INK)}>{bhd(o.unit, 3)}</div>
                    {diff && <div className={cn('text-[11px] font-semibold', diff.cls)}>{diff.text}</div>}
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {status === 'ready' && !opts.length && (
        <p className={cn('px-1 text-[12px]', MUTED)}>
          {searching ? 'Nothing priced matches that.' : mode === 'substitute' ? 'Nothing similar in stock — search for another item.' : 'Type at least two letters of the code or name.'}
        </p>
      )}
    </div>
  )
}

function useCatalogIndex() {
  const q = useStaffCatalog()
  const catalog = useMemo(() => (q.data ? indexItems(q.data.items) : null), [q.data])
  const status: CatalogStatus = q.isError ? 'error' : q.data ? 'ready' : 'loading'
  return { catalog, status }
}

function EditLineRow({
  line, d, onChange, catalog, status, reasons, extend,
}: {
  line: HeartLine
  d: LineDraft
  onChange: (next: LineDraft) => void
  catalog: Map<string, StockItem> | null
  status: CatalogStatus
  reasons: { code: string; label: string }[]
  /** substitutes, backorders and added items (an API from before R7c has none of them) */
  extend: boolean
}) {
  const [picking, setPicking] = useState(false)
  const item = itemOf(catalog, line.item_code)
  const current = confirmedQty(line)
  const sv = stockView(item, line.qty, line.stock_status)
  const changed = lineChanged(line, d)
  const lock = lockUnit(line)
  const set = (patch: Partial<LineDraft>) => onChange({ ...d, ...patch })
  const reset = () => onChange({ qty: current, reason: '', note: '', sub: null, backorder: false })
  const avail = sv.available
  const short = avail != null && avail < d.qty
  const subItem = d.sub ? itemOf(catalog, d.sub.code) : undefined
  const subUnit = d.sub ? unitAt(subItem, d.sub.qty) : null
  const subDiff = d.sub && subUnit != null && lock != null ? priceDiff(subUnit - lock) : null

  return (
    <li className="space-y-2.5 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className={cn('truncate text-[13.5px] font-semibold', INK, (d.qty === 0 || d.sub) && 'line-through decoration-[#9a93ad]')}>
            {line.display_name || line.item_code}
          </div>
          <div className={cn('text-[11.5px] tabular-nums', MUTED)}>
            {line.item_code} · {isAddedLine(line) ? `added ${current}` : `requested ${line.qty}${current !== line.qty ? ` · confirmed ${current}` : ''}`}
            {lock != null ? ` · ${bhd(lock, 3)} each` : ''}
          </div>
        </div>
        {sv.label && <Badge tone={sv.tone} dot className="shrink-0">{sv.label}</Badge>}
      </div>

      {d.sub ? (
        <div className="space-y-2 rounded-xl bg-[#F6F3F8] p-2.5">
          <div className="flex items-start gap-2 text-[12.5px]">
            <Repeat size={14} className="mt-0.5 shrink-0 text-[#6D4091]" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <div className={INK}>Replaced by <b>{subItem?.display_name || d.sub.code}</b></div>
              <div className={cn('text-[11.5px] tabular-nums', MUTED)}>
                {d.sub.code} · {subUnit != null ? `about ${bhd(subUnit, 3)} each` : 'priced when you save'}
                {subDiff && <span className={cn('ml-1 font-semibold', subDiff.cls)}>{subDiff.text}</span>}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Stepper size="lg" value={d.sub.qty} min={1} max={9999} label={`${d.sub.code} quantity`}
              onChange={(v) => d.sub && set({ sub: { ...d.sub, qty: v } })} />
            <button type="button" onClick={reset} className={SMALL_BTN}><Undo2 size={14} /> Undo</button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Stepper
            size="lg"
            value={d.qty}
            min={0}
            max={9999}
            label={line.item_code}
            onChange={(v) => set({ qty: v, backorder: v === 0 ? false : d.backorder })}
            className={cn(d.qty !== current && 'border-[#6D4091] [&_span]:text-[#6D4091]')}
          />
          {d.qty === 0 && <Badge tone="rose">Unavailable</Badge>}
          {short && avail != null && avail > 0 && (
            <button type="button" onClick={() => set({ qty: avail, reason: d.reason || 'out_of_stock', backorder: false })}
              className={cn(SMALL_BTN, 'border-[#6D4091] text-[#6D4091]')}>
              Set to {avail} available
            </button>
          )}
          {short && avail === 0 && d.qty > 0 && (
            <button type="button" onClick={() => set({ qty: 0, reason: d.reason || 'out_of_stock', backorder: false })}
              className={cn(SMALL_BTN, 'border-[#f3c9d2] text-[#9f1239]')}>
              Mark unavailable
            </button>
          )}
          {extend && short && d.qty > 0 && !isBackorderLine(line) && (
            <button type="button" aria-pressed={d.backorder}
              onClick={() => set({ backorder: !d.backorder, reason: d.reason || 'out_of_stock' })}
              className={cn(SMALL_BTN, d.backorder && 'border-[#96600d] bg-[#fdf3e3] text-[#96600d]')}>
              {d.backorder ? <Check size={14} /> : null} Comes later
            </button>
          )}
          {extend && (
            <button type="button" onClick={() => setPicking((v) => !v)} aria-expanded={picking} className={SMALL_BTN}>
              <Repeat size={14} /> Replace
            </button>
          )}
          {changed && <button type="button" onClick={reset} className={SMALL_BTN}><Undo2 size={14} /> Undo</button>}
        </div>
      )}

      {picking && !d.sub && (
        <ItemPicker
          catalog={catalog}
          status={status}
          mode="substitute"
          lineCode={line.item_code}
          lock={lock}
          qty={d.qty > 0 ? d.qty : line.qty}
          exclude={new Set([line.item_code.toUpperCase()])}
          onPick={(it) => {
            set({ sub: { code: it.item_code, qty: d.qty > 0 ? d.qty : line.qty }, reason: 'substituted', backorder: false })
            setPicking(false)
          }}
          onClose={() => setPicking(false)}
        />
      )}

      {changed && !d.sub && (
        <ReasonChips
          label={d.reason ? 'Reason' : `Why? Pick a reason for ${line.item_code}`}
          options={reasons.filter((r) => r.code !== 'substituted')}
          value={d.reason}
          note={d.note}
          onChange={(reason, note) => set({ reason, note })}
        />
      )}
    </li>
  )
}

function AddedRow({ a, catalog, onChange, onRemove }: {
  a: AddDraft
  catalog: Map<string, StockItem> | null
  onChange: (next: AddDraft) => void
  onRemove: () => void
}) {
  const it = itemOf(catalog, a.code)
  const unit = unitAt(it, a.qty)
  const sv = stockView(it, a.qty)
  return (
    <li className="space-y-2 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className={cn('truncate text-[13.5px] font-semibold', INK)}>{it?.display_name || a.code}</div>
          <div className={cn('text-[11.5px] tabular-nums', MUTED)}>
            {a.code}{unit != null ? ` · about ${bhd(unit, 3)} each` : ''}
          </div>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1.5">
          <Badge tone="green">Added</Badge>
          {sv.label && <Badge tone={sv.tone} dot>{sv.label}</Badge>}
        </span>
      </div>
      <Stepper size="lg" value={a.qty} min={1} max={9999} label={a.code} onChange={(v) => onChange({ ...a, qty: v })} onRemove={onRemove} />
    </li>
  )
}

function AddItem({ catalog, status, exclude, onAdd, label }: {
  catalog: Map<string, StockItem> | null
  status: CatalogStatus
  exclude: Set<string>
  onAdd: (it: StockItem) => void
  label: string
}) {
  const [open, setOpen] = useState(false)
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={cn(SMALL_BTN, 'w-full justify-center border-dashed')}>
        <Plus size={15} /> {label}
      </button>
    )
  }
  return (
    <ItemPicker catalog={catalog} status={status} mode="add" qty={1} exclude={exclude}
      onPick={(it) => { onAdd(it); setOpen(false) }} onClose={() => setOpen(false)} />
  )
}

function Blockers({ items }: { items: (string | false | null | undefined)[] }) {
  const list = items.filter(Boolean) as string[]
  if (!list.length) return null
  return (
    <ul className="space-y-0.5 text-[12px] font-medium text-[#9f1239]" aria-live="polite">
      {list.map((t) => <li key={t}>{t}</li>)}
    </ul>
  )
}

function newKey(code: string): string {
  return `${code}-${Math.random().toString(36).slice(2, 8)}`
}

/* ───────────────────────── Confirm / Amend ───────────────────────── */

/**
 * One editor behind Confirm (from Received) and Amend (after confirming). Lines not touched are
 * confirmed as ordered; every changed line needs a reason; the price lock is the server's (existing
 * lines keep the price the shop ordered at). An adverse change needs "Shop agreed" + how.
 */
export function OrderEditor({
  order, mode, legacy, onDone, onCancel, onConflict,
}: {
  order: HeartOrder
  mode: 'confirm' | 'amend'
  legacy?: boolean
  onDone: (res: HeartResponse) => void
  onCancel: () => void
  onConflict: () => void
}) {
  const toast = useToast()
  const extend = !legacy
  const { catalog, status } = useCatalogIndex()
  const [draft, setDraft] = useState<Record<number, LineDraft>>(() => initialDraft(order.lines))
  const [added, setAdded] = useState<AddDraft[]>([])
  const [eta, setEta] = useState('')
  const [note, setNote] = useState('')
  const [agreed, setAgreed] = useState({ on: false, via: '' })
  const [serverAdverse, setServerAdverse] = useState(false)
  const [busy, setBusy] = useState(false)

  const reasons = reasonOptions(order.change_reasons)
  const plan = planEdit(order, draft, added, catalog, mode, legacy)
  const needsTick = plan.adverse.length > 0 || serverAdverse
  const tickOk = !needsTick || (agreed.on && Boolean(agreed.via))
  const nothing = mode === 'amend' && plan.changed === 0 && !eta.trim() && !note.trim()
  const ready = !busy && !plan.allOut && plan.needReason.length === 0 && tickOk && !nothing
  const exclude = new Set([...order.lines.map((l) => l.item_code.toUpperCase()), ...added.map((a) => a.code.toUpperCase())])

  async function submit() {
    if (!ready) return
    setBusy(true)
    try {
      const body: Record<string, unknown> = { lines: plan.lines }
      if (plan.added_lines.length) body.added_lines = plan.added_lines
      if (eta.trim()) body.expected_delivery = eta.trim()
      if (note.trim()) body.note = note.trim()
      if (needsTick && agreed.on && agreed.via) body.shop_agreed = { via: agreed.via }
      const res = await apiPost<HeartResponse>(`/shop/orders/${order.id}/${mode === 'confirm' ? 'confirm' : 'amend'}`, body)
      onDone(res)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        onConflict()
        return
      }
      const msg = apiDetail(e, mode === 'confirm' ? 'Could not confirm the order.' : 'Could not save the changes.')
      if (msg === ADVERSE_MSG) setServerAdverse(true)
      toast(msg, 'error')
    } finally {
      setBusy(false)
    }
  }

  const changedTotal = Math.round(plan.estTotal * 1000) !== Math.round(plan.beforeTotal * 1000)

  return (
    <section className="space-y-3 rounded-2xl border border-[#E3DAEC] bg-[#F6F3F8] p-3" aria-label={mode === 'confirm' ? 'Confirm the order' : 'Amend the order'}>
      <div>
        <div className={cn('font-display text-[14px] font-bold', INK)}>{mode === 'confirm' ? 'Confirm the order' : 'Amend the confirmed order'}</div>
        <p className={cn('text-[12px] leading-snug', MUTED)}>
          Lines you don't touch are confirmed as ordered. The shop keeps the price it ordered at.
        </p>
      </div>

      <ul className="divide-y divide-[#E9E4EF] overflow-hidden rounded-xl border border-[#E9E4EF] bg-white">
        {order.lines.map((l) => (
          <EditLineRow
            key={l.id}
            line={l}
            d={draft[l.id] || { qty: confirmedQty(l), reason: '', note: '', sub: null, backorder: false }}
            onChange={(next) => setDraft((s) => ({ ...s, [l.id]: next }))}
            catalog={catalog}
            status={status}
            reasons={reasons}
            extend={extend}
          />
        ))}
        {added.map((a) => (
          <AddedRow key={a.key} a={a} catalog={catalog}
            onChange={(next) => setAdded((s) => s.map((x) => (x.key === a.key ? next : x)))}
            onRemove={() => setAdded((s) => s.filter((x) => x.key !== a.key))} />
        ))}
      </ul>

      {extend && (
        <AddItem catalog={catalog} status={status} exclude={exclude} label="Add an item"
          onAdd={(it) => setAdded((s) => [...s, { key: newKey(it.item_code), code: it.item_code, qty: Math.max(1, Number(it.moq) || 1) }])} />
      )}

      <div className="space-y-1.5">
        <div className={cn('text-[11.5px] font-semibold', MUTED)}>
          {mode === 'confirm' ? 'When will it reach the shop?' : `New delivery day${order.expected_delivery ? ` (now: ${order.expected_delivery})` : ''} — optional`}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {ETA_CHIPS.map((c) => (
            <button key={c} type="button" onClick={() => setEta(eta === c ? '' : c)} aria-pressed={eta === c}
              className={cn(CHIP, eta === c ? CHIP_ON : CHIP_OFF)}>
              {c}
            </button>
          ))}
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <input value={eta} onChange={(e) => setEta(e.target.value)} maxLength={120} placeholder="Or type it (e.g. Thursday morning)" aria-label="Expected delivery" className={INPUT} />
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Note for the shop (they see it)" aria-label="Note for the shop" className={INPUT} />
        </div>
      </div>

      {needsTick && (
        <ShopAgreedBox adverse={plan.adverse} minOrder={order.min_order_bhd} agreed={agreed} onChange={setAgreed} server={serverAdverse} />
      )}

      <div className={cn('flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-[12.5px]', MUTED)}>
        <span>{plan.changed ? `${plan.changed} change${plan.changed === 1 ? '' : 's'}` : 'As ordered'}</span>
        <span className="inline-flex items-baseline gap-1.5">
          Total about <EffectiveTotal now={plan.estTotal} was={changedTotal ? plan.beforeTotal : null} className={cn('font-semibold', INK)} />
        </span>
      </div>

      <Blockers items={[
        plan.allOut && 'Every line is out — cancel the order instead.',
        plan.needReason.length > 0 && `Pick a reason for ${plan.needReason.join(', ')}.`,
        needsTick && !tickOk && "Tick 'Shop agreed' and say how they agreed.",
      ]} />

      <div className="flex gap-2">
        <button type="button" onClick={onCancel} className={cn('h-12 flex-1 rounded-xl border border-[#E2DCEA] bg-white text-[13.5px] font-semibold', INK)}>Back</button>
        <button type="button" onClick={submit} disabled={!ready}
          className="flex h-12 flex-[1.4] items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[14.5px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 disabled:opacity-50 motion-reduce:transition-none">
          {busy ? <Loader2 size={17} className="animate-spin" /> : <Check size={17} />}
          {mode === 'amend' ? 'Save changes' : plan.changed ? 'Confirm with changes' : 'Confirm order'}
        </button>
      </div>
    </section>
  )
}

/* ───────────────────────── Deliver with changes ───────────────────────── */

/** What was really handed over: a delivered quantity per line (a reason when it differs from the
 *  confirmed one) and items added at the shop. The delivered value becomes the agreed total. */
export function DeliverEditor({
  order, onDone, onCancel, onConflict,
}: {
  order: HeartOrder
  onDone: (res: HeartResponse) => void
  onCancel: () => void
  onConflict: () => void
}) {
  const toast = useToast()
  const { catalog, status } = useCatalogIndex()
  const lines = deliverableLines(order.lines)
  const [draft, setDraft] = useState<Record<number, DeliverDraft>>(() => initialDeliverDraft(order.lines))
  const [added, setAdded] = useState<AddDraft[]>([])
  const [note, setNote] = useState('')
  const [agreed, setAgreed] = useState({ on: false, via: '' })
  const [serverAdverse, setServerAdverse] = useState(false)
  const [busy, setBusy] = useState(false)

  const reasons = reasonOptions(order.change_reasons).filter((r) => r.code !== 'substituted')
  const plan = planDeliver(order, draft, added, catalog)
  const needsTick = plan.adverse.length > 0 || serverAdverse
  const tickOk = !needsTick || (agreed.on && Boolean(agreed.via))
  const ready = !busy && !plan.nothing && plan.needReason.length === 0 && tickOk
  const exclude = new Set([...order.lines.map((l) => l.item_code.toUpperCase()), ...added.map((a) => a.code.toUpperCase())])
  const changedTotal = Math.round(plan.estTotal * 1000) !== Math.round(plan.beforeTotal * 1000)

  async function submit() {
    if (!ready) return
    setBusy(true)
    try {
      const body: Record<string, unknown> = {}
      if (plan.lines.length) body.lines = plan.lines
      if (plan.added.length) body.added = plan.added
      if (note.trim()) body.note = note.trim()
      if (needsTick && agreed.on && agreed.via) body.shop_agreed = { via: agreed.via }
      const res = await apiPost<HeartResponse>(`/shop/orders/${order.id}/deliver`, body)
      onDone(res)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        onConflict()
        return
      }
      const msg = apiDetail(e, 'Could not record the delivery.')
      if (msg === ADVERSE_MSG) setServerAdverse(true)
      toast(msg, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3 rounded-2xl border border-[#E3DAEC] bg-[#F6F3F8] p-3" aria-label="Deliver with changes">
      <div>
        <div className={cn('font-display text-[14px] font-bold', INK)}>Deliver with changes</div>
        <p className={cn('text-[12px] leading-snug', MUTED)}>Set what was really handed over. The shop pays for what it got.</p>
      </div>
      <ul className="divide-y divide-[#E9E4EF] overflow-hidden rounded-xl border border-[#E9E4EF] bg-white">
        {lines.map((l) => {
          const qc = confirmedQty(l)
          const d = draft[l.id] || { qty: qc, reason: '', note: '' }
          const differs = d.qty !== qc
          const set = (patch: Partial<DeliverDraft>) => setDraft((s) => ({ ...s, [l.id]: { ...d, ...patch } }))
          return (
            <li key={l.id} className="space-y-2.5 p-3">
              <div className="min-w-0">
                <div className={cn('truncate text-[13.5px] font-semibold', INK)}>{l.display_name || l.item_code}</div>
                <div className={cn('text-[11.5px] tabular-nums', MUTED)}>{l.item_code} · confirmed {qc}</div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className={cn('text-[12px] font-semibold', MUTED)}>Handed over</span>
                <Stepper size="lg" value={d.qty} min={0} max={9999} label={`${l.item_code} handed over`}
                  onChange={(v) => set({ qty: v })}
                  className={cn(differs && 'border-[#6D4091] [&_span]:text-[#6D4091]')} />
                {differs && (
                  <button type="button" onClick={() => set({ qty: qc, reason: '', note: '' })} className={SMALL_BTN}>
                    <Undo2 size={14} /> Undo
                  </button>
                )}
              </div>
              {differs && (
                <ReasonChips label={d.reason ? 'Reason' : `Why? Pick a reason for ${l.item_code}`} options={reasons}
                  value={d.reason} note={d.note} onChange={(reason, n) => set({ reason, note: n })} />
              )}
            </li>
          )
        })}
        {added.map((a) => (
          <AddedRow key={a.key} a={a} catalog={catalog}
            onChange={(next) => setAdded((s) => s.map((x) => (x.key === a.key ? next : x)))}
            onRemove={() => setAdded((s) => s.filter((x) => x.key !== a.key))} />
        ))}
      </ul>
      <AddItem catalog={catalog} status={status} exclude={exclude} label="Add an item handed over at the shop"
        onAdd={(it) => setAdded((s) => [...s, { key: newKey(it.item_code), code: it.item_code, qty: Math.max(1, Number(it.moq) || 1) }])} />
      <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Note for the shop (they see it)" aria-label="Note for the shop" className={INPUT} />

      {needsTick && (
        <ShopAgreedBox adverse={plan.adverse} minOrder={order.min_order_bhd} agreed={agreed} onChange={setAgreed} server={serverAdverse} />
      )}

      <div className={cn('flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-[12.5px]', MUTED)}>
        <span>{plan.withChanges ? 'With changes' : 'As confirmed'}</span>
        <span className="inline-flex items-baseline gap-1.5">
          Delivered value about <EffectiveTotal now={plan.estTotal} was={changedTotal ? plan.beforeTotal : null} className={cn('font-semibold', INK)} />
        </span>
      </div>

      <Blockers items={[
        plan.nothing && 'Nothing was handed over — cancel the order instead.',
        plan.needReason.length > 0 && `Pick a reason for ${plan.needReason.join(', ')}.`,
        needsTick && !tickOk && "Tick 'Shop agreed' and say how they agreed.",
      ]} />

      <div className="flex gap-2">
        <button type="button" onClick={onCancel} className={cn('h-12 flex-1 rounded-xl border border-[#E2DCEA] bg-white text-[13.5px] font-semibold', INK)}>Back</button>
        <button type="button" onClick={submit} disabled={!ready}
          className="flex h-12 flex-[1.4] items-center justify-center gap-2 rounded-xl bg-[#137a48] text-[14.5px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 disabled:opacity-50 motion-reduce:transition-none">
          {busy ? <Loader2 size={17} className="animate-spin" /> : <Check size={17} />} Mark delivered
        </button>
      </div>
    </section>
  )
}

/* ───────────────────────── Reopen (admin, within 7 days) ───────────────────────── */

export function ReopenBox({
  orderId, status, until, onDone, onCancel,
}: {
  orderId: number
  status: string
  until?: string | null
  onDone: () => void
  onCancel: () => void
}) {
  const toast = useToast()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const to = reopenTarget(status)
  const ready = !busy && reason.trim().length >= 3 && to != null

  async function submit() {
    if (!ready || !to) return
    setBusy(true)
    try {
      await apiPost(`/shop/orders/${orderId}/reopen`, { reason: reason.trim() })
      toast(`Reopened — the order is ${VISIBLE_LABEL[to]} again.`, 'success')
      onDone()
    } catch (e) {
      toast(apiDetail(e, 'Could not reopen the order.'), 'error')
      if (e instanceof ApiError && e.status === 409) onDone()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2.5 rounded-xl border border-[#E3DAEC] bg-[#F6F3F8] p-3">
      <p className={cn('text-[12.5px] leading-snug', INK)}>
        <b>Reopen to undo a mistake.</b> It goes back to <b>{to ? VISIBLE_LABEL[to] : '—'}</b>
        {status === 'delivered' ? ' and the delivered quantities are cleared' : ''}. The reason stays on the record.
        {until ? <span className={MUTED}> Possible until {shortTime(until)}.</span> : null}
      </p>
      <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} placeholder="Why is it reopened? (required)"
        aria-label="Why the order is reopened" aria-required className={INPUT} />
      <div className="flex gap-2">
        <button type="button" onClick={onCancel} className={cn('h-11 flex-1 rounded-xl border border-[#E2DCEA] bg-white text-[13px] font-semibold', INK)}>Keep it</button>
        <button type="button" onClick={submit} disabled={!ready}
          className="flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl bg-[#6D4091] text-[13px] font-semibold text-white disabled:opacity-50">
          {busy ? <Loader2 size={15} className="animate-spin" /> : <RotateCcw size={15} />} Reopen
        </button>
      </div>
    </div>
  )
}
