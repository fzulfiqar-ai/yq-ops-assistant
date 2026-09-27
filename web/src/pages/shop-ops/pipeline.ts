import { useQuery } from '@tanstack/react-query'
import { apiGet, ApiError } from '@/lib/api'
import { bhd } from '@/lib/format'
import type { BadgeTone } from '@/components/ui/badge'

/**
 * R3 order pipeline — the constants and pure helpers behind the cancel reasons, the payment
 * pill and the small-order gap line (app/shop_pipeline.py is the authority; this mirrors it so
 * a rep is not refused after the round trip). Kept out of OrderActions.tsx so that file exports
 * components only (react-refresh).
 */

/** The server's `detail` line for a failed call (FastAPI 400s carry the reason there). */
export function apiDetail(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    try {
      const j = JSON.parse(e.body) as { detail?: unknown }
      if (typeof j.detail === 'string' && j.detail) return j.detail
    } catch { /* not json */ }
    return e.body.slice(0, 160) || fallback
  }
  return fallback
}

/** app/shop_pipeline.py CANCEL_REASONS — the server refuses a staff cancel without one of these. */
export const CANCEL_REASONS: { code: string; label: string }[] = [
  { code: 'out_of_stock', label: 'Out of stock' },
  { code: 'customer_request', label: 'Customer asked' },
  { code: 'duplicate', label: 'Duplicate' },
  { code: 'test', label: 'Test order' },
  { code: 'price_issue', label: 'Price issue' },
  { code: 'other', label: 'Other' },
]

export const cancelReasonLabel = (code?: string | null): string =>
  CANCEL_REASONS.find((r) => r.code === code)?.label || (code ? code.replace(/_/g, ' ') : '')

/** True when the picker holds what the server will accept ('other' needs a note of 3+ chars). */
export function cancelReady(reason_code: string, note: string): boolean {
  if (!reason_code) return false
  return reason_code !== 'other' || note.trim().length >= 3
}

/** What the office RECORDS (the radios, the toast, the timeline line for an explicit record). */
export const PAYMENT_LABEL: Record<string, string> = { unpaid: 'Unpaid', partial: 'Partly paid', paid: 'Paid', refunded: 'Refunded' }
/** What a row's state MEANS: 'unpaid' is also the column default, so on a pill it says only what
 *  is true — nothing has been recorded here (payments live in Focus until the office records one). */
export const PAYMENT_PILL_LABEL: Record<string, string> = { ...PAYMENT_LABEL, unpaid: 'Payment not recorded' }
export const PAYMENT_TONE: Record<string, BadgeTone> = { unpaid: 'grey', partial: 'amber', paid: 'green', refunded: 'rose' }
export const PAYMENT_METHODS: { value: string; label: string }[] = [
  { value: '', label: 'Method…' }, { value: 'cash', label: 'Cash' }, { value: 'benefit', label: 'Benefit' },
  { value: 'bank_transfer', label: 'Bank transfer' }, { value: 'cheque', label: 'Cheque' }, { value: 'credit', label: 'On credit' },
  { value: 'other', label: 'Other' },
]

/**
 * "BHD 7.150 short of the wholesale minimum when placed" for a small order that is still
 * Received; null otherwise. The gap was stored when the order was created, against the minimum
 * of that moment — so it is never paired with today's setting (which may have moved since), and
 * it is not shown once the rep has confirmed, delivered or cancelled the order, where it is
 * history rather than something to act on.
 */
export function minimumGapText(row: { status?: string | null; order_kind?: string | null; minimum_gap_bhd?: number | null }): string | null {
  if (row.order_kind !== 'small' || row.status !== 'new') return null
  const gap = Number(row.minimum_gap_bhd || 0)
  if (!(gap > 0)) return null
  return `${bhd(gap, 3)} short of the wholesale minimum when placed`
}

/* ───────────────────────── R7a: plain-English attribution (item 3) ───────────────────────── */

/** app/shop.py ATTRIBUTION — how an order landed with its rep, in words a shop owner (or the
 *  office reading it over their shoulder) would actually say. Anything not in this map
 *  (focus_map, staff, default, legacy…) falls back to the old "snake_case -> words" reading, so
 *  an attribution source added later never renders blank. */
const ATTRIBUTION_LABEL: Record<string, (rep: string) => string> = {
  session_ref: (rep) => `Came via ${rep}'s link`,
  sticky: (rep) => `${rep}'s shop`,
  customer_admin: () => 'Assigned by office',
  checkout_pick: () => 'Picked at checkout',
}

export function attributionLabel(source: string | null | undefined, repName?: string | null): string {
  if (!source) return ''
  const fn = ATTRIBUTION_LABEL[source]
  return fn ? fn(repName || 'the rep') : source.replace(/_/g, ' ')
}

/** The shop's settled-rep line in the order drawer. Replaces the old "not settled yet · first
 *  link /x" (a state, not an action) with what actually happens next. */
export function shopRepLine(
  shopRep: { salesman_name?: string | null; sticky_name?: string | null },
  currentRepName?: string | null,
): string {
  if (shopRep.salesman_name) return `Assigned by office — ${shopRep.salesman_name}`
  if (shopRep.sticky_name) return `${shopRep.sticky_name}'s shop`
  return `New shop — becomes ${currentRepName || 'the rep'}'s when they confirm`
}

/* ───────────────────────── R7a: Focus exceptions (item 6) ─────────────────────────
 * app/shop_focus_links, live once scripts/r7_focus_links_migration.sql is applied. Types +
 * pure formatting live here (not OrderActions.tsx) so that file stays components-only. */

export interface FocusCandidateLines {
  order: number
  invoice: number
  matched: number
  qty_diff: number
  price_diff: number
  missing_on_invoice: number
  extra_on_invoice: number
}

export interface FocusCandidate {
  invoice_key: string
  invoice_date: string | null
  focus_salesman: string | null
  focus_customer: string | null
  invoice_total_bhd: number
  invoice_open_bhd: number
  invoice_linked_n: number
  invoice_orders_n: number
  amount_diff_bhd: number
  method: 'narration_ref' | 'sio_ref' | 'auto_items'
  method_label: string
  confidence: number
  overlap_share: number | null
  sio_key: string | null
  rank: number
  exact: boolean
  lines: FocusCandidateLines
}

export interface FocusOrderRow {
  order_id: number
  order_no: string
  status: string
  status_label: string
  created_at: string
  customer_shop: string | null
  salesman_id: number | null
  salesman_name: string | null
  order_total_bhd: number
  typed_invoice_key: string | null
  candidates: FocusCandidate[]
}

export interface FocusCandidatesResp {
  orders: FocusOrderRow[]
  count: number
  candidates: number
  ledger_as_of: string | null
  hint?: string
}

export interface FocusLinkResp {
  ok: true
  order_id: number
  order_no: string
  invoice_key: string
  action: 'accept' | 'reject'
  state: 'confirmed' | 'rejected'
  method: string
  method_label: string
  confidence: number | null
  sio_key: string | null
  status: string
  status_label: string
  advanced: boolean
  focus_invoice_no: string | null
}

/** "12 of 13 lines match · 1 not invoiced" — only the parts that differ from a clean match, so a
 *  perfect candidate reads as just "13 of 13 lines match". */
export function lineDiffSummary(l: FocusCandidateLines): string {
  const parts = [`${l.matched} of ${l.order} lines match`]
  if (l.missing_on_invoice) parts.push(`${l.missing_on_invoice} not invoiced`)
  if (l.extra_on_invoice) parts.push(`${l.extra_on_invoice} extra on invoice`)
  if (l.qty_diff) parts.push(`${l.qty_diff} qty ${l.qty_diff === 1 ? 'differs' : 'differ'}`)
  if (l.price_diff) parts.push(`${l.price_diff} price ${l.price_diff === 1 ? 'differs' : 'differ'}`)
  return parts.join(' · ')
}

/** The API sends 1.0 | 0.95 | at most 0.9 — "100%" / "95%" / "90%". */
export function confidencePct(c: number): string {
  return `${Math.round(c * 100)}%`
}

/** Not a component, so it lives here rather than OrderActions.tsx (react-refresh wants that file
 *  components-only) — shared between the panel there and the order desk's own "Needs action"
 *  count (ShopOrders.tsx), same query key either way, so mounting both never double-fetches. */
export function useFocusCandidates() {
  return useQuery({
    queryKey: ['shop-focus-candidates'],
    queryFn: () => apiGet<FocusCandidatesResp>('/shop/focus/candidates?limit=300&per_order=5'),
    staleTime: 60_000,
  })
}
