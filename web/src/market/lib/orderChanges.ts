import type { OrderStatusLine, OrderStatusPayload } from '@/lib/shopApi'
import type { Strings } from '../i18n/en'

/**
 * "What changed" on the tracking page (R7d, plan §5 / §12) — pure, so web/scripts/merchant_test.mjs
 * can check it on hand-made payloads. Everything comes from the order's own public lines (the R7c
 * payload): the three numbers per line (requested / confirmed / delivered), the disposition, the
 * public reason key and the substitute. The page turns each row into words (S.track.change*).
 *
 *   reduced / increased  "10 requested → 5 confirmed" (→ "4 delivered" when that differs)
 *   unavailable          "10 requested · not available this time" — never "10 → 0"
 *   substituted          "Replaced with UK20N × 10": the LIVE replacement (down a re-substitute chain)
 *                        is folded into this row — never a dead substitute "× 0"
 *   added                a line the rep added — "Added by your representative · 6 pcs"
 *   delivered            as ordered and confirmed, but a different quantity was handed over
 *
 * A rep line taken off again (confirmed 0) has no row: the shop never requested it.
 * A backorder line is not a change. A cancelled order has no "What changed" (it says Cancelled).
 */

export type ChangeKind = 'reduced' | 'increased' | 'unavailable' | 'substituted' | 'added' | 'delivered'

export interface ChangeRow {
  key: string
  kind: ChangeKind
  code: string
  name: string
  requested: number
  confirmed: number | null
  delivered: number | null
  /** the line that replaced this one (substituted rows) */
  replacement: { code: string; name: string; qty: number } | null
  /** the PUBLIC reason key (app/shop_heart.py PUBLIC_REASONS) — never the rep's note */
  reason: string | null
}

export interface OrderChanges {
  rows: ChangeRow[]
  /** the total as ordered */
  before: number | null
  /** the total as it stands now (confirmed ?? as ordered; after a delivery with changes, the delivered value) */
  after: number | null
  /** before and after differ, to the fils */
  totalChanged: boolean
}

/** line statuses that mean "confirmed 0" (removed is the pre-R7c word) */
export const LINE_OUT = new Set(['removed', 'unavailable', 'substituted'])

const n = (v: unknown): number | null => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v))
const fils = (v: number | null): number | null => (v === null ? null : Math.round(v * 1000))

/** The line's disposition — the payload's, else derived the way app/shop_heart.py does (older payloads). */
export function dispositionOf(l: OrderStatusLine): string {
  if (l.disposition) return String(l.disposition)
  const st = String(l.line_status || 'ok')
  if (st === 'substituted' || (LINE_OUT.has(st) && l.substitute_item_code)) return 'substituted'
  const qc = n(l.qty_confirmed)
  // an added line (or a substitute) taken out again is gone, not "added" (shop_heart.disposition)
  if (st === 'added' || l.added_at_stage) return LINE_OUT.has(st) || qc === 0 ? 'unavailable' : 'added'
  if (LINE_OUT.has(st) || qc === 0) return 'unavailable'
  if (qc !== null && qc < Number(l.qty)) return 'reduced'
  if (qc !== null && qc > Number(l.qty)) return 'increased'
  return st === 'backorder' ? 'backorder' : 'as_ordered'
}

/** A line that is not part of the order any more (confirmed 0). */
export function isOut(l: OrderStatusLine): boolean {
  const d = dispositionOf(l)
  return d === 'unavailable' || d === 'substituted'
}

/** The quantity this line stands at: delivered, else confirmed, else requested. */
export function qtyNow(l: OrderStatusLine): number {
  return n(l.qty_delivered) ?? n(l.qty_confirmed) ?? Number(l.qty || 0)
}

/** The line's money as it stands: delivered, else confirmed, else as ordered. */
export function lineMoneyNow(l: OrderStatusLine): number {
  const d = n(l.line_total_delivered)
  if (d !== null) return d
  const c = n(l.line_total_confirmed)
  if (c !== null) return c
  const t = n(l.line_total_bhd)
  if (t !== null) return t
  return (n(l.unit_price_bhd) ?? 0) * Number(l.qty || 0)
}

/** A line the rep put on the order (an added line or a substitute) — not one the shop requested. */
export function fromRep(l: OrderStatusLine): boolean {
  return Boolean(l.substitute_for || l.added_at_stage || String(l.line_status || '') === 'added')
}

/** A line still coming: not out, and a quantity above 0. */
const live = (l: OrderStatusLine): boolean => !isOut(l) && qtyNow(l) > 0

/**
 * The item actually coming in place of a substituted line — the way app/shop_heart.py
 * live_substitutes walks it: the substitute lines of `l` (substitute_for = its code), and where one
 * was itself taken out (re-substituted, restored, or substituted again) down the chain to the live
 * one. A dead substitute (confirmed 0) is never the answer. Prefers the line the order names
 * (`substitute_item_code`), else the newest live one. `taken` = replacements already folded into
 * another row. Older payloads without `substitute_for`: the rep's live line with that code.
 */
export function liveReplacement(lines: OrderStatusLine[], l: OrderStatusLine, taken: Set<OrderStatusLine> = new Set()): OrderStatusLine | null {
  const want = l.substitute_item_code || null
  const visited = new Set<OrderStatusLine>([l])
  let level: OrderStatusLine[] = [l]
  while (level.length) {
    const kids = lines.filter((x) => !visited.has(x) && !taken.has(x) && level.some((p) => x.substitute_for === p.item_code))
    kids.forEach((k) => visited.add(k))
    const alive = kids.filter(live)
    if (alive.length) return alive.find((x) => x.item_code === want) || alive[alive.length - 1]
    level = kids
  }
  const named = want ? lines.filter((x) => x !== l && !taken.has(x) && x.item_code === want && fromRep(x) && live(x)) : []
  return named.length ? named[named.length - 1] : null
}

export function orderChanges(d: OrderStatusPayload | null | undefined): OrderChanges {
  const empty: OrderChanges = { rows: [], before: null, after: null, totalChanged: false }
  if (!d || d.cancelled || d.status === 'cancelled') return empty
  const lines = d.lines || []
  const rows: ChangeRow[] = []
  // each substituted line the shop requested -> the item really coming in its place (folded into its row)
  const replacedBy = new Map<OrderStatusLine, OrderStatusLine>()
  const folded = new Set<OrderStatusLine>()
  lines.forEach((l) => {
    if (fromRep(l) || dispositionOf(l) !== 'substituted') return
    const rep = liveReplacement(lines, l, folded)
    if (rep) {
      replacedBy.set(l, rep)
      folded.add(rep)
    }
  })
  lines.forEach((l, i) => {
    const disp = dispositionOf(l)
    const base = {
      key: `${l.item_code}-${i}`,
      code: l.item_code,
      name: l.display_name || l.item_code,
      requested: Number(l.qty || 0),
      confirmed: n(l.qty_confirmed),
      delivered: n(l.qty_delivered),
      replacement: null,
      reason: l.reason_code || null,
    }
    if (disp === 'added') {
      // a replacement is shown on the row of the line it replaced; a live rep line nothing folded
      // (its line's chain went elsewhere) is still coming, so it is listed as added
      if (folded.has(l)) return
      rows.push({ ...base, kind: 'added', confirmed: base.confirmed ?? base.requested })
      return
    }
    // a line the rep put on and took off again (a substitute re-substituted or restored away, a
    // door-added line after a reopen): the shop never requested it — no "requested · not available"
    if (fromRep(l)) return
    if (disp === 'substituted') {
      const rep = replacedBy.get(l) || null
      if (rep) {
        rows.push({ ...base, kind: 'substituted', replacement: { code: rep.item_code, name: rep.display_name || rep.item_code, qty: qtyNow(rep) } })
      } else {
        // the replacement line is not on the payload: say only what is certain
        rows.push({ ...base, kind: 'unavailable', confirmed: 0 })
      }
      return
    }
    if (disp === 'unavailable') {
      rows.push({ ...base, kind: 'unavailable', confirmed: 0 })
      return
    }
    if (disp === 'reduced' || disp === 'increased') {
      rows.push({ ...base, kind: disp })
      return
    }
    const conf = base.confirmed ?? base.requested
    if (base.delivered !== null && base.delivered !== conf) rows.push({ ...base, kind: 'delivered', confirmed: conf })
  })
  const before = n(d.total_bhd)
  const after = n(d.total_effective_bhd) ?? n(d.total_confirmed_bhd) ?? before
  return { rows, before, after, totalChanged: rows.length > 0 && before !== null && after !== null && fils(before) !== fils(after) }
}

/** A delivered quantity that differs from the confirmed one — the "→ 4 delivered" tail. */
export function deliveredTail(r: ChangeRow): number | null {
  if (r.delivered === null) return null
  const conf = r.confirmed ?? r.requested
  return r.delivered !== conf ? r.delivered : null
}

/** One changed line in words (`t` = S.track of the page's language): requested → confirmed
 *  (→ delivered), "not available this time", or the replacement. */
export function changeText(t: Strings['track'], r: ChangeRow): string {
  const tail = deliveredTail(r)
  const withTail = (s: string) => (tail !== null ? `${s} ${t.changeArrow} ${t.changeDelivered(tail)}` : s)
  switch (r.kind) {
    case 'unavailable':
      return t.changeNone(r.requested)
    case 'substituted':
      return r.replacement ? t.changeReplaced(r.replacement.code, r.replacement.qty) : t.changeNone(r.requested)
    case 'added':
      return withTail(t.changeAdded(r.confirmed ?? r.requested))
    default:
      return withTail(t.changeQty(r.requested, r.confirmed ?? r.requested))
  }
}
