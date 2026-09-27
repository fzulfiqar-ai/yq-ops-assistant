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
 *   substituted          "Replaced with UK20N × 10": the replacement line is folded into this row
 *   added                a line the rep added — "Added by your representative · 6 pcs"
 *   delivered            as ordered and confirmed, but a different quantity was handed over
 *
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
  if (st === 'added' || l.added_at_stage) return 'added'
  const qc = n(l.qty_confirmed)
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

export function orderChanges(d: OrderStatusPayload | null | undefined): OrderChanges {
  const empty: OrderChanges = { rows: [], before: null, after: null, totalChanged: false }
  if (!d || d.cancelled || d.status === 'cancelled') return empty
  const lines = d.lines || []
  const rows: ChangeRow[] = []
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
      // a replacement is shown on the row of the line it replaced
      if (l.substitute_for) return
      rows.push({ ...base, kind: 'added', confirmed: base.confirmed ?? base.requested })
      return
    }
    if (disp === 'substituted') {
      const rep = lines.find((x) => x.substitute_for === l.item_code && x !== l) || null
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
