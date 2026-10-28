import type { Quote } from '@/lib/shopApi'

/**
 * The checkout's money — what the totals show and when Place order may be pressed. Pure, so
 * web/scripts/rep_field_test.mjs runs it in plain node.
 *
 * The server is the only thing that prices a cart (useQuote). Before its first answer the drawer
 * used to read BHD 0.000 and "Free", and Place order was live: an absent quote passed
 * `quote?.can_submit !== false`, and so did a failed check with an older cart's price on screen.
 * The rule now:
 *
 *   empty     nothing in the cart, nothing to price: its total is zero (BHD 0.000, honestly);
 *   checking  no price for this cart yet: placeholders where the figures go, Place order off;
 *   updating  the last good price stays on screen (the totals never flash to zero) while this cart
 *             is priced again; Place order waits for it;
 *   failed    the price server did not answer: its words and a Retry, no figures, Place order off;
 *   blocked   the server priced this cart and says why it cannot go yet;
 *   ready     the server priced this very cart and it may go.
 */

/** What useQuote last heard: the request it answered (`req`), the cart its quote priced (`cart`). */
export interface QuoteAnswer {
  req: string
  cart: string
  quote: Quote | null
  error: string
}

export interface QuoteView {
  /** the last good price (it may be for an older cart: see `fresh`) */
  quote: Quote | null
  /** a request for the cart on screen is on its way */
  quoting: boolean
  /** the request for the cart on screen failed (an older failure is never shown) */
  error: string
  /** `quote` priced the cart on screen */
  fresh: boolean
}

export const NO_ANSWER: QuoteAnswer = { req: '', cart: '', quote: null, error: '' }

/** What the checkout knows, from the last answer, the cart on screen and the request for it. */
export function quoteView(a: QuoteAnswer, cart: string, req: string): QuoteView {
  const answered = a.req === req
  return { quote: a.quote, quoting: !answered, error: answered ? a.error : '', fresh: a.quote != null && a.cart === cart }
}

export type PriceState = 'empty' | 'checking' | 'updating' | 'failed' | 'blocked' | 'ready'

export interface PriceCheck {
  state: PriceState
  /** Place order may be pressed (the form's own checks aside) */
  canPlace: boolean
  /** the totals show the server's figures; otherwise placeholders (checking) or dashes (failed) */
  showTotals: boolean
  /** why the server will not take this order (blocked; or the price on screen while it is checked again) */
  reason: string
  /** the price server did not answer (failed) */
  error: string
}

export const BLOCKED_FALLBACK = 'This order cannot be submitted yet.'

const blockOf = (q: Quote | null) => (q?.can_submit === false ? q.block_reason || BLOCKED_FALLBACK : '')

export function priceCheck(v: QuoteView & { lines: number }): PriceCheck {
  const none = { canPlace: false, showTotals: false, reason: '', error: '' }
  // nothing to price, and its total is genuinely zero: shown as such, not as a placeholder
  if (v.lines <= 0) return { state: 'empty', ...none, showTotals: true }
  if (v.error && !v.quoting) return { state: 'failed', ...none, error: v.error }
  if (!v.quote) return { state: 'checking', ...none }
  if (v.quoting || !v.fresh) return { state: 'updating', ...none, showTotals: true, reason: blockOf(v.quote) }
  const reason = blockOf(v.quote)
  if (reason) return { state: 'blocked', ...none, showTotals: true, reason }
  return { state: 'ready', canPlace: true, showTotals: true, reason: '', error: '' }
}
