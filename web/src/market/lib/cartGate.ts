import type { Quote, QuoteMinimum, ShopSettings } from '@/lib/shopApi'

/**
 * What the cart may offer, from the price check and the payload (28-Oct-2026, finding L9).
 *
 * The cart read the wholesale minimum only from the server's quote. When the price check was still
 * out or had failed (a slow or lost connection), a BHD 5 basket showed no "BHD 15 away" and offered
 * "Place wholesale order" on an unchecked total. Now:
 *   - the minimum comes from the quote when there is one, else from the payload's settings
 *     (`min_order_bhd`, `small_order_mode`) against the shelf-price estimate — marked `estimated`;
 *   - nothing can be placed without a priced quote: `checking` while it is out, `failed` (with a
 *     Retry) when it did not come back.
 * A quote is this cart's price only when its check answered without an error (`priced`). After a
 * failed re-price the shared useQuote keeps the LAST good quote on screen, which belongs to an older
 * cart: it is treated as no quote at all (final review, 28-Oct-2026), never as "ready".
 * The server re-prices and enforces the minimum on submit anyway; this keeps the page honest.
 */
export interface CartGate {
  minimum: QuoteMinimum | null
  /** the minimum was worked out here from the payload, not by the server */
  estimated: boolean
  checking: boolean
  failed: boolean
  /** the quote on screen is this cart's, freshly checked */
  priced: boolean
  canCheckout: boolean
}

export function cartGate({
  lines,
  quote,
  quoting,
  quoteError,
  settings,
  estimate,
}: {
  lines: number
  quote: Quote | null
  quoting: boolean
  quoteError: string
  settings: Pick<ShopSettings, 'min_order_bhd' | 'small_order_mode'>
  estimate: number
}): CartGate {
  const failed = !quoting && Boolean(quoteError)
  const priced = Boolean(quote) && !quoting && !quoteError
  // a quote left over from a failed check is an older cart's: as good as none
  const shown = failed ? null : quote
  const checking = !shown && quoting
  let minimum: QuoteMinimum | null = shown?.minimum ?? null
  let estimated = false
  const floor = Number(settings.min_order_bhd) || 0
  if (!shown && floor > 0) {
    const remaining = Math.max(0, Math.round((floor - estimate) * 1000) / 1000)
    minimum = { value_bhd: floor, remaining_bhd: remaining, met: remaining <= 0, mode: settings.small_order_mode || 'request', kind: remaining <= 0 ? 'standard' : 'small' }
    estimated = true
  }
  const canCheckout = lines > 0 && priced && quote?.can_submit !== false
  return { minimum, estimated, checking, failed, priced, canCheckout }
}
