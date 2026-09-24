import { ApiError } from '@/lib/api'
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

export const PAYMENT_LABEL: Record<string, string> = { unpaid: 'Unpaid', partial: 'Partly paid', paid: 'Paid', refunded: 'Refunded' }
export const PAYMENT_TONE: Record<string, BadgeTone> = { unpaid: 'grey', partial: 'amber', paid: 'green', refunded: 'rose' }
export const PAYMENT_METHODS: { value: string; label: string }[] = [
  { value: '', label: 'Method…' }, { value: 'cash', label: 'Cash' }, { value: 'benefit', label: 'Benefit' },
  { value: 'bank_transfer', label: 'Bank transfer' }, { value: 'cheque', label: 'Cheque' }, { value: 'credit', label: 'On credit' },
  { value: 'other', label: 'Other' },
]

/** "BHD 7.150 short of the BHD 20.000 minimum" for a small order; null otherwise. */
export function minimumGapText(row: { order_kind?: string | null; minimum_gap_bhd?: number | null }, minOrder?: number | null): string | null {
  if (row.order_kind !== 'small') return null
  const gap = Number(row.minimum_gap_bhd || 0)
  if (!(gap > 0)) return null
  const min = Number(minOrder || 0)
  return min > 0 ? `${bhd(gap, 3)} short of the ${bhd(min, 3)} minimum` : `${bhd(gap, 3)} short of the wholesale minimum`
}
