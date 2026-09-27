/**
 * The order heart (R7c, Sprint 3) — the pure rules behind the order desk and the rep's field sheet,
 * as the owner simplified them on 27-Sep-2026: "as simple as possible; most of the time the shop
 * will not press OK, so the salesman must be able to".
 *
 *   What a rep, the desk or a shop ever sees or taps:  Received → Confirmed → Delivered  (or Cancelled)
 *
 * app/shop_heart.py is the authority. This file mirrors just enough of it for the page to be honest
 * BEFORE the round trip — the three visible stages, the confirmed-else-original money, which change
 * needs a reason, and when the "Shop agreed" tick is required — so a rep is not refused after he taps
 * Save. The server still decides: its ADVERSE_MSG reveals the tick even where this estimate did not.
 *
 * No imports (types only would be fine too): web/scripts/order_heart_ui_test.mjs loads this file in
 * plain node and checks it against hand-worked orders. Money is counted in integer fils here, so a
 * sum never drifts; every figure the page shows as final comes from the server.
 */

/* ───────────────────────── the three visible stages ───────────────────────── */

export type VisibleStatus = 'new' | 'confirmed' | 'delivered' | 'cancelled'
/** Badge tones (components/ui/badge BadgeTone) — kept as a local union so this file has no imports. */
export type Tone = 'accent' | 'green' | 'amber' | 'grey' | 'rose' | 'ink'

export const VISIBLE_STATUSES: readonly VisibleStatus[] = ['new', 'confirmed', 'delivered', 'cancelled']

export const VISIBLE_LABEL: Record<VisibleStatus, string> = {
  new: 'Received',
  confirmed: 'Confirmed',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
}

/** The DB enum keeps packed / out_for_delivery (the storekeeper's pick-list stamps); every rep and
 *  desk reader shows them as Confirmed (shop_heart.VISIBLE_STATUS). */
const TO_VISIBLE: Record<string, VisibleStatus> = {
  new: 'new', confirmed: 'confirmed', packed: 'confirmed', out_for_delivery: 'confirmed',
  delivered: 'delivered', cancelled: 'cancelled',
}

export function visibleStatus(status?: string | null): VisibleStatus {
  return TO_VISIBLE[String(status || '')] ?? 'new'
}

/** Every stored status → the word a rep or the desk reads (Preparing / On the way read "Confirmed"). */
export const STATUS_LABEL: Record<string, string> = {
  new: 'Received',
  confirmed: 'Confirmed',
  packed: 'Confirmed',
  out_for_delivery: 'Confirmed',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
}

export const STATUS_TONE: Record<string, Tone> = {
  new: 'ink',
  confirmed: 'accent',
  packed: 'accent',
  out_for_delivery: 'accent',
  delivered: 'green',
  cancelled: 'grey',
}

/** The storekeeper's own stamp words — shown only as a quiet "Pick list: …" note on the desk. */
export const STAMP_LABEL: Record<string, string> = { packed: 'Preparing', out_for_delivery: 'On the way' }

/** One status filter = the stored statuses behind it (the list API takes a comma list). */
export const FILTER_PARAM: Record<VisibleStatus, string> = {
  new: 'new',
  confirmed: 'confirmed,packed,out_for_delivery',
  delivered: 'delivered',
  cancelled: 'cancelled',
}

export function visibleCount(counts: Partial<Record<string, number>>, v: VisibleStatus): number {
  return FILTER_PARAM[v].split(',').reduce((s, k) => s + (counts[k] ?? 0), 0)
}

/** Old deep links (?bucket=new|progress|done from Today and Customers) → the visible stage. */
export function bucketFromParam(raw?: string | null): VisibleStatus {
  const s = String(raw || '').trim().toLowerCase()
  if (s === 'progress' || s === 'in_progress') return 'confirmed'
  if (s === 'done') return 'delivered'
  if (s === 'received') return 'new'
  return (VISIBLE_STATUSES as readonly string[]).includes(s) ? (s as VisibleStatus) : 'new'
}

export interface StageStep { status: string; label: string; done: boolean; current: boolean; at?: string | null }

/** The server's 3 steps, or the same computed from the row (an older API sent five). */
export function stagesOf(o: {
  status?: string | null; steps?: StageStep[] | null
  created_at?: string | null; confirmed_at?: string | null; delivered_at?: string | null
}): StageStep[] {
  if (Array.isArray(o.steps) && o.steps.length === 3) return o.steps
  const vis = visibleStatus(o.status)
  const order: VisibleStatus[] = ['new', 'confirmed', 'delivered']
  const reached = order.indexOf(vis)
  const at: Record<string, string | null | undefined> = { new: o.created_at, confirmed: o.confirmed_at, delivered: o.delivered_at }
  return order.map((s, i) => ({
    status: s, label: VISIBLE_LABEL[s],
    done: reached > i || (reached === 2 && i === 2),
    current: reached === i,
    at: reached >= i ? at[s] ?? null : null,
  }))
}

/* ───────────────────────── money (integer fils) ───────────────────────── */

export const fils = (x: number | null | undefined): number => Math.round(Number(x ?? 0) * 1000)
export const fromFils = (f: number): number => f / 1000

interface MoneyRow {
  total_bhd?: number | null
  total_confirmed_bhd?: number | null
  total_effective_bhd?: number | null
}

/** confirmed ?? original — the ONE rule every total on an order is read with (shop_heart.effective_money). */
export function effectiveTotal(o: MoneyRow): number {
  if (o.total_effective_bhd != null) return Number(o.total_effective_bhd)
  return Number(o.total_confirmed_bhd ?? o.total_bhd ?? 0)
}

/** True when the confirmed total is not the as-ordered one (the as-ordered figure is then struck through). */
export function totalChanged(o: MoneyRow): boolean {
  return o.total_confirmed_bhd != null && fils(o.total_confirmed_bhd) !== fils(o.total_bhd)
}

/* ───────────────────────── lines: three numbers ───────────────────────── */

export type Disposition = 'as_ordered' | 'reduced' | 'increased' | 'unavailable' | 'substituted' | 'added' | 'backorder'

export interface HeartLine {
  id: number
  item_code: string
  display_name?: string | null
  /** requested — never changed */
  qty: number
  /** confirmed (null while Received; 0 = unavailable / substituted) */
  qty_confirmed?: number | null
  /** handed over (null until delivered) */
  qty_delivered?: number | null
  qty_unavailable?: number | null
  line_status?: string | null
  unit_price_bhd?: number | null
  unit_price_confirmed?: number | null
  list_price_bhd?: number | null
  discount_bhd?: number | null
  line_total_bhd?: number | null
  line_total_confirmed?: number | null
  line_total_delivered?: number | null
  disposition?: Disposition | string | null
  reason_label?: string | null
  change_reason?: string | null
  substitute_item_code?: string | null
  substitute_for?: string | null
  substitute_for_line?: number | null
  added_at_stage?: string | null
  note?: string | null
  stock_status?: string | null
  backorder?: boolean | null
}

const LINE_OUT = new Set(['removed', 'unavailable', 'substituted'])

/** A line the rep added (or a substitute) — not one the shop requested. */
export function isAddedLine(l: HeartLine): boolean {
  return Boolean(l.added_at_stage) || l.line_status === 'added'
}

/** Confirmed quantity as it stands: the stored one, else the requested one (0 for a line that is out). */
export function confirmedQty(l: HeartLine): number {
  if (l.qty_confirmed == null) return LINE_OUT.has(l.line_status || 'ok') ? 0 : Math.max(0, Number(l.qty) || 0)
  return Math.max(0, Number(l.qty_confirmed) || 0)
}

export function isBackorderLine(l: HeartLine): boolean {
  return Boolean(l.backorder) || l.line_status === 'backorder'
}

/** The server's disposition, else the same rule (shop_heart.disposition) on an older payload. */
export function dispositionOf(l: HeartLine): Disposition {
  const d = l.disposition
  if (d === 'as_ordered' || d === 'reduced' || d === 'increased' || d === 'unavailable' || d === 'substituted' || d === 'added' || d === 'backorder') return d
  const st = l.line_status || 'ok'
  if (st === 'substituted' || (LINE_OUT.has(st) && l.substitute_item_code)) return 'substituted'
  if (isAddedLine(l)) return 'added'
  const qc = confirmedQty(l)
  if (qc <= 0) return 'unavailable'
  if (qc < l.qty) return 'reduced'
  if (qc > l.qty) return 'increased'
  return st === 'backorder' ? 'backorder' : 'as_ordered'
}

/** The line's chip, or null when it is exactly as the shop asked. */
export function dispositionChip(l: HeartLine): { label: string; tone: Tone } | null {
  const d = dispositionOf(l)
  switch (d) {
    case 'reduced': return { label: 'Reduced', tone: 'amber' }
    case 'increased': return { label: 'Increased', tone: 'accent' }
    case 'unavailable': return { label: 'Unavailable', tone: 'rose' }
    case 'substituted': return { label: l.substitute_item_code ? `Substituted → ${l.substitute_item_code}` : 'Substituted', tone: 'accent' }
    case 'added': return { label: l.substitute_for ? `Replaces ${l.substitute_for}` : 'Added', tone: 'green' }
    case 'backorder': return { label: 'Comes later', tone: 'amber' }
    default: return null
  }
}

/** The price lock: the unit price the shop was told (confirmed), else the one it ordered at. */
export function lockUnit(l: HeartLine): number | null {
  const v = l.unit_price_confirmed ?? l.unit_price_bhd ?? l.list_price_bhd
  return v == null ? null : Number(v)
}

/** A line's money now (delivered, else confirmed, else as ordered) and the as-ordered figure when it
 *  differs — the second is shown struck through. */
export function lineMoney(l: HeartLine, status?: string | null): { now: number; was: number | null } {
  const ordered = Number(l.line_total_bhd ?? 0)
  let now = ordered
  if (status === 'delivered' && l.line_total_delivered != null) now = Number(l.line_total_delivered)
  else if (l.line_total_confirmed != null) now = Number(l.line_total_confirmed)
  else if (l.qty_confirmed != null && lockUnit(l) != null) now = fromFils(fils(lockUnit(l)) * confirmedQty(l))
  const was = !isAddedLine(l) && fils(now) !== fils(ordered) ? ordered : null
  return { now, was }
}

/* ───────────────────────── live stock (the staff catalog) ───────────────────────── */

export interface StockItem {
  item_code: string
  display_name?: string | null
  category?: string | null
  price_bhd?: number | null
  stock_qty?: number | null
  stock_status?: string | null
  moq?: number | null
  tiers?: { min_qty: number; unit_price_bhd: number }[] | null
}

export function indexItems(items?: StockItem[] | null): Map<string, StockItem> {
  const m = new Map<string, StockItem>()
  for (const it of items || []) if (it?.item_code) m.set(String(it.item_code).toUpperCase(), it)
  return m
}

export function itemOf(catalog: Map<string, StockItem> | null | undefined, code?: string | null): StockItem | undefined {
  return code && catalog ? catalog.get(String(code).toUpperCase()) : undefined
}

/** "In stock 25" / "Only 3" / "Sold out" against the quantity wanted; the order-time wording when
 *  the live catalog is not in hand. `available` = units on hand (null = unknown). */
export function stockView(item: StockItem | undefined, need: number, fallback?: string | null): { label: string; tone: Tone; available: number | null } {
  if (item && typeof item.stock_qty === 'number') {
    const q = Math.max(0, Math.floor(item.stock_qty))
    if (q <= 0) return { label: 'Sold out', tone: 'rose', available: 0 }
    if (q < need) return { label: `Only ${q}`, tone: 'amber', available: q }
    return { label: `In stock ${q}`, tone: 'green', available: q }
  }
  const st = item?.stock_status || fallback
  if (st === 'out_of_stock') return { label: 'Sold out', tone: 'rose', available: null }
  if (st === 'low_stock') return { label: 'Only a few left', tone: 'amber', available: null }
  if (st === 'in_stock') return { label: 'In stock', tone: 'green', available: null }
  return { label: '', tone: 'grey', available: null }
}

/** Today's book price for `qty` (the best volume tier reached) — an estimate: the server prices it. */
export function unitAt(item: StockItem | undefined, qty: number): number | null {
  if (!item || item.price_bhd == null) return null
  let best = Number(item.price_bhd)
  for (const t of item.tiers || []) if (qty >= t.min_qty && t.unit_price_bhd < best) best = t.unit_price_bhd
  return best
}

export interface ItemOption { item: StockItem; unit: number | null; diff: number | null }

function inStock(it: StockItem): boolean {
  return typeof it.stock_qty === 'number' ? it.stock_qty > 0 : it.stock_status !== 'out_of_stock'
}

/** Substitutes for a line: the same category, in stock, priced, not the item itself — closest price first. */
export function substituteOptions(
  catalog: Map<string, StockItem>, lineCode: string, lock: number | null, qty: number, limit = 8,
): ItemOption[] {
  const self = itemOf(catalog, lineCode)
  const cat = self?.category || null
  const out: ItemOption[] = []
  for (const it of catalog.values()) {
    if (String(it.item_code).toUpperCase() === lineCode.toUpperCase()) continue
    if (!cat || it.category !== cat || !inStock(it)) continue
    const unit = unitAt(it, qty)
    if (unit == null) continue
    out.push({ item: it, unit, diff: lock == null ? null : fromFils(fils(unit) - fils(lock)) })
  }
  out.sort((a, b) => Math.abs(a.diff ?? 0) - Math.abs(b.diff ?? 0) || (b.item.stock_qty ?? 0) - (a.item.stock_qty ?? 0))
  return out.slice(0, limit)
}

/** Free search over the catalog (code or name), priced items only, in stock first. */
export function searchItems(
  catalog: Map<string, StockItem>, query: string, qty: number, lock: number | null, exclude: Set<string>, limit = 8,
): ItemOption[] {
  const q = query.trim().toLowerCase()
  if (q.length < 2) return []
  const out: ItemOption[] = []
  for (const it of catalog.values()) {
    const code = String(it.item_code)
    if (exclude.has(code.toUpperCase())) continue
    if (!code.toLowerCase().includes(q) && !String(it.display_name || '').toLowerCase().includes(q)) continue
    const unit = unitAt(it, qty)
    if (unit == null) continue
    out.push({ item: it, unit, diff: lock == null ? null : fromFils(fils(unit) - fils(lock)) })
  }
  out.sort((a, b) => Number(inStock(b.item)) - Number(inStock(a.item)) || String(a.item.item_code).localeCompare(String(b.item.item_code)))
  return out.slice(0, limit)
}

/* ───────────────────────── reasons, the tick, the messages ───────────────────────── */

/** shop_heart.CHANGE_REASONS (the detail also sends `change_reasons`; that one wins when present). */
export const CHANGE_REASONS: { code: string; label: string }[] = [
  { code: 'out_of_stock', label: 'Out of stock' },
  { code: 'discontinued', label: 'Discontinued' },
  { code: 'price', label: 'Price' },
  { code: 'customer_changed', label: 'Shop changed it' },
  { code: 'substituted', label: 'Substituted' },
  { code: 'damaged', label: 'Damaged' },
  { code: 'other', label: 'Other' },
]

export function reasonOptions(server?: Record<string, string> | null): { code: string; label: string }[] {
  if (server && typeof server === 'object' && Object.keys(server).length) {
    return Object.entries(server).map(([code, label]) => ({ code, label: String(label) }))
  }
  return CHANGE_REASONS
}

export const REASON_NOTE_MIN = 3

/** A reason the server accepts: picked, and 'other' carries a note of 3+ characters. */
export function reasonReady(code: string, note: string): boolean {
  if (!code) return false
  return code !== 'other' || note.trim().length >= REASON_NOTE_MIN
}

export const AGREED_VIA: { code: string; label: string }[] = [
  { code: 'whatsapp', label: 'WhatsApp' },
  { code: 'phone', label: 'Phone' },
  { code: 'visit', label: 'Visit' },
]

/** The server's exact 400 details (app/shop_heart.py) — the page reacts to these two. */
export const ADVERSE_MSG = "Tick 'Shop agreed' — this change costs the shop more or goes below the minimum"
export const CAS_MSG = 'This order changed a moment ago — refresh and try again'

/** What a salesman actually says in the shop — one tap instead of typing on the road. */
export const ETA_CHIPS = ['Today', 'Tomorrow', 'Day after tomorrow', 'With my next visit'] as const

export type AdverseKind = 'substitute_price' | 'price_increase' | 'backorder' | 'below_minimum'
export interface Adverse { kind: AdverseKind; item_code?: string; substitute?: string }

export function adverseText(a: Adverse, minOrder?: number | null): string {
  switch (a.kind) {
    case 'substitute_price': return `${a.item_code} replaced by ${a.substitute} at a different price`
    case 'price_increase': return `a higher price on ${a.item_code}`
    case 'backorder': return `${a.item_code} comes later`
    case 'below_minimum': return `the order drops below the BHD ${Number(minOrder ?? 0).toFixed(3)} minimum`
  }
}

/* ───────────────────────── the editor: Confirm and Amend ───────────────────────── */

export interface LineDraft {
  qty: number
  reason: string
  note: string
  /** a substitute replaces the whole line (the original goes to confirmed 0) */
  sub: { code: string; qty: number } | null
  /** keep the quantity but it comes later (a backorder) */
  backorder: boolean
}

export interface AddDraft { key: string; code: string; qty: number }

export interface EditOrder {
  status: string
  total_bhd?: number | null
  total_confirmed_bhd?: number | null
  delivery_bhd?: number | null
  discount_bhd?: number | null
  min_order_bhd?: number | null
  lines: HeartLine[]
}

export function initialDraft(lines: HeartLine[]): Record<number, LineDraft> {
  const out: Record<number, LineDraft> = {}
  for (const l of lines) out[l.id] = { qty: confirmedQty(l), reason: '', note: '', sub: null, backorder: false }
  return out
}

/** Did the rep change this line (so it needs a reason and goes in the request)? */
export function lineChanged(l: HeartLine, d?: LineDraft | null): boolean {
  if (!d) return false
  if (d.sub) return true
  if (d.qty !== confirmedQty(l)) return true
  return d.backorder && d.qty > 0 && !isBackorderLine(l)
}

export interface ConfirmLineBody {
  line_id: number
  qty_confirmed?: number
  line_status?: string
  backorder?: boolean
  reason?: string
  note?: string
  substitute_item_code?: string
  substitute_qty?: number
}
export interface AddedLineBody { item_code: string; qty: number; reason: string }

export interface EditPlan {
  lines: ConfirmLineBody[]
  added_lines: AddedLineBody[]
  changed: number
  /** item codes whose change still needs a reason (or the 'other' note) */
  needReason: string[]
  adverse: Adverse[]
  beforeTotal: number
  estTotal: number
  allOut: boolean
}

/** `subOf` = the line a NEW substitute (not saved yet) stands in for. */
interface Priced { line: HeartLine | null; qtyF: number; unitF: number; original: boolean; subOf?: HeartLine }

/** compute_totals in fils: every line at its locked unit; the ORIGINAL lines — and a substitute in
 *  the place of one (shop_heart.carries_share) — share the order's own cart discount pro rata, never
 *  more than the discount as placed nor more than their value; other added lines carry none;
 *  delivery as ordered. */
function totalFils(order: EditOrder, priced: Priced[]): number {
  const orig = order.lines.filter((l) => !isAddedLine(l))
  const byId = new Map(order.lines.map((l) => [l.id, l] as const))
  const carries = (l: HeartLine | undefined, hops = 0): boolean => {
    if (!l) return false
    if (!isAddedLine(l)) return true
    if (l.substitute_for_line == null || hops > 20) return false
    return carries(byId.get(l.substitute_for_line), hops + 1)
  }
  const s0 = orig.reduce((s, l) => s + fils(l.line_total_bhd), 0)
  const lineDisc0 = orig.reduce((s, l) => s + fils(l.discount_bhd), 0)
  const cart0 = Math.max(0, fils(order.discount_bhd) - lineDisc0)
  let items = 0
  let s1 = 0
  for (const p of priced) {
    const lt = p.unitF * p.qtyF
    items += lt
    if (p.line ? carries(p.line) : carries(p.subOf)) s1 += lt
  }
  const cart = cart0 > 0 && s0 > 0 ? Math.min(Math.round((cart0 * s1) / s0), cart0, s1) : 0
  return items - cart + fils(order.delivery_bhd)
}

function belowMinimum(order: EditOrder, beforeF: number, afterF: number): boolean {
  const minF = fils(order.min_order_bhd)
  const dF = fils(order.delivery_bhd)
  return minF > 0 && beforeF - dF >= minF && minF > afterF - dF
}

/**
 * Turn the editor's draft into the request body and say, before the round trip, what the server
 * will ask for: a reason on every changed line, the "Shop agreed" tick when a change is adverse
 * (a substitute at another price, goods that come later, a cut that crosses the minimum). The
 * estimate total is for the summary line only — the server's figure is the one stored.
 * `legacy` = an API from before R7c: a line out goes as 'removed' (the one word it knows).
 */
export function planEdit(
  order: EditOrder,
  draft: Record<number, LineDraft>,
  added: AddDraft[],
  catalog: Map<string, StockItem> | null,
  stage: 'confirm' | 'amend',
  legacy = false,
): EditPlan {
  const lines: ConfirmLineBody[] = []
  const needReason: string[] = []
  const adverse: Adverse[] = []
  const priced: Priced[] = []
  let changed = 0
  for (const l of order.lines) {
    const d = draft[l.id]
    const unitF = fils(lockUnit(l))
    if (!d || !lineChanged(l, d)) {
      priced.push({ line: l, qtyF: confirmedQty(l), unitF, original: !isAddedLine(l) })
      continue
    }
    changed += 1
    const note = d.note.trim()
    if (d.sub) {
      const reason = d.reason || 'substituted'
      if (!reasonReady(reason, note)) needReason.push(l.item_code)
      lines.push({ line_id: l.id, substitute_item_code: d.sub.code, substitute_qty: d.sub.qty, reason, ...(note ? { note } : {}) })
      priced.push({ line: l, qtyF: 0, unitF, original: !isAddedLine(l) })
      const it = itemOf(catalog, d.sub.code)
      const u = unitAt(it, d.sub.qty)
      priced.push({ line: null, qtyF: d.sub.qty, unitF: fils(u), original: false, subOf: l })
      if (u != null && lockUnit(l) != null && fils(u) !== fils(lockUnit(l))) {
        adverse.push({ kind: 'substitute_price', item_code: l.item_code, substitute: d.sub.code })
      }
      if (it && typeof it.stock_qty === 'number' && it.stock_qty <= 0) adverse.push({ kind: 'backorder', item_code: d.sub.code })
      continue
    }
    if (!reasonReady(d.reason, note)) needReason.push(l.item_code)
    const base = { line_id: l.id, ...(d.reason ? { reason: d.reason } : {}), ...(note ? { note } : {}) }
    if (d.qty <= 0) {
      lines.push({ ...base, qty_confirmed: 0, line_status: legacy ? 'removed' : 'unavailable' })
    } else if (d.backorder && !isBackorderLine(l)) {
      lines.push({ ...base, qty_confirmed: d.qty, line_status: 'backorder', backorder: true })
      adverse.push({ kind: 'backorder', item_code: l.item_code })
    } else {
      lines.push({ ...base, qty_confirmed: d.qty })
    }
    priced.push({ line: l, qtyF: Math.max(0, d.qty), unitF, original: !isAddedLine(l) })
  }
  const added_lines: AddedLineBody[] = []
  for (const a of added) {
    if (!a.code || a.qty <= 0) continue
    added_lines.push({ item_code: a.code, qty: a.qty, reason: 'customer_changed' })
    const it = itemOf(catalog, a.code)
    priced.push({ line: null, qtyF: a.qty, unitF: fils(unitAt(it, a.qty)), original: false })
    if (it && typeof it.stock_qty === 'number' && it.stock_qty <= 0) adverse.push({ kind: 'backorder', item_code: a.code })
  }
  const beforeF = fils(stage === 'confirm' || order.total_confirmed_bhd == null ? order.total_bhd : order.total_confirmed_bhd)
  const afterF = totalFils(order, priced)
  if (belowMinimum(order, beforeF, afterF)) adverse.push({ kind: 'below_minimum' })
  const allOut = !priced.some((p) => p.qtyF > 0)
  return {
    lines, added_lines, changed: changed + added_lines.length, needReason, adverse,
    beforeTotal: fromFils(beforeF), estTotal: fromFils(afterF), allOut,
  }
}

/* ───────────────────────── Deliver with changes ───────────────────────── */

export interface DeliverDraft { qty: number; reason: string; note: string }
export interface DeliverLineBody { line_id: number; qty_delivered: number; reason?: string; note?: string }

export interface DeliverPlan {
  lines: DeliverLineBody[]
  added: AddedLineBody[]
  needReason: string[]
  adverse: Adverse[]
  beforeTotal: number
  estTotal: number
  nothing: boolean
  withChanges: boolean
}

/** Lines the rep hands over: the ones with something confirmed. */
export function deliverableLines(lines: HeartLine[]): HeartLine[] {
  return lines.filter((l) => confirmedQty(l) > 0)
}

export function initialDeliverDraft(lines: HeartLine[]): Record<number, DeliverDraft> {
  const out: Record<number, DeliverDraft> = {}
  for (const l of lines) out[l.id] = { qty: confirmedQty(l), reason: '', note: '' }
  return out
}

/** What was really handed over. A line equal to its confirmed quantity is not sent; the delivered
 *  value becomes the order's agreed total, and crossing the minimum needs the tick. */
export function planDeliver(
  order: EditOrder,
  draft: Record<number, DeliverDraft>,
  added: AddDraft[],
  catalog: Map<string, StockItem> | null,
): DeliverPlan {
  const lines: DeliverLineBody[] = []
  const needReason: string[] = []
  const priced: Priced[] = []
  for (const l of order.lines) {
    const qc = confirmedQty(l)
    const d = draft[l.id]
    const q = d ? Math.max(0, d.qty) : qc
    priced.push({ line: l, qtyF: q, unitF: fils(lockUnit(l)), original: !isAddedLine(l) })
    if (!d || q === qc) continue
    const note = d.note.trim()
    if (!reasonReady(d.reason, note)) needReason.push(l.item_code)
    lines.push({ line_id: l.id, qty_delivered: q, ...(d.reason ? { reason: d.reason } : {}), ...(note ? { note } : {}) })
  }
  const addedOut: AddedLineBody[] = []
  for (const a of added) {
    if (!a.code || a.qty <= 0) continue
    addedOut.push({ item_code: a.code, qty: a.qty, reason: 'customer_changed' })
    priced.push({ line: null, qtyF: a.qty, unitF: fils(unitAt(itemOf(catalog, a.code), a.qty)), original: false })
  }
  const beforeF = fils(order.total_confirmed_bhd ?? order.total_bhd)
  const afterF = totalFils(order, priced)
  const adverse: Adverse[] = belowMinimum(order, beforeF, afterF) ? [{ kind: 'below_minimum' }] : []
  return {
    lines, added: addedOut, needReason, adverse,
    beforeTotal: fromFils(beforeF), estTotal: fromFils(afterF),
    nothing: !priced.some((p) => p.qtyF > 0),
    withChanges: lines.length > 0 || addedOut.length > 0,
  }
}

/* ───────────────────────── what the drawer offers ───────────────────────── */

export type OrderAction = 'confirm' | 'amend' | 'deliver' | 'cancel' | 'reopen' | 'tell_shop'
const ACTIONS: readonly OrderAction[] = ['confirm', 'amend', 'deliver', 'cancel', 'reopen', 'tell_shop']

/**
 * The server's `actions` (R7c), else — an API from before it — the old forward steps only:
 * Received → Confirm, Confirmed → Delivered, and Cancel; no amend, reopen or logged "tell the
 * shop" (those routes do not exist there). `legacy` says which one the page is talking to.
 */
export function actionsOf(
  o: { status?: string | null; actions?: string[] | null; whatsapp_url?: string | null } | null | undefined,
  readOnly: boolean,
): { list: OrderAction[]; legacy: boolean } {
  if (!o) return { list: [], legacy: false }
  const legacy = !Array.isArray(o.actions)
  if (readOnly) return { list: [], legacy }
  if (!legacy) return { list: (o.actions as string[]).filter((a): a is OrderAction => (ACTIONS as readonly string[]).includes(a)), legacy }
  const vis = visibleStatus(o.status)
  const list: OrderAction[] = vis === 'new' ? ['confirm', 'cancel'] : vis === 'confirmed' ? ['deliver', 'cancel'] : []
  if (o.whatsapp_url) list.push('tell_shop')
  return { list, legacy }
}

/** Reopen goes back one stage: Delivered → Confirmed, Cancelled → Received. */
export function reopenTarget(status?: string | null): VisibleStatus | null {
  return status === 'delivered' ? 'confirmed' : status === 'cancelled' ? 'new' : null
}

/** The "Shop not told yet" chip: a step was taken (anything past Received) and no tap logged since. */
export function shopNotTold(o: { status?: string | null; shop_told?: boolean | null }): boolean {
  return o.shop_told === false && visibleStatus(o.status) !== 'new'
}
