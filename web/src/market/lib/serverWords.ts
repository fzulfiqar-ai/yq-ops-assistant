import type { Quote, QuoteCoupon, QuoteLine, QuoteProgress } from '@/lib/shopApi'
import type { Strings } from '../i18n/en'

/**
 * The server's sentences in the page's language (R7d). The quote and the order endpoint write
 * English (app/shop.py price_cart / create_order); next to each sentence the quote sends a stable
 * key — a line's `blocked_code`, the cart's `block_code` + `block_items`, `warning_codes`,
 * `coupon.reason`, `progress.rule_name` — and this module words those keys through S. The order
 * endpoint's refusals are fixed sentences (tests/test_r7d_merchant.py pins each one against the
 * server), so they are looked up by their exact text.
 *
 *   * An English page shows the server's own sentence, exactly as before.
 *   * Any other language words the key; an older API that sends no key is read from its known
 *     English sentences (the sold-out line, the block, the backorder warning, the offer labels).
 *   * Anything still unknown: the server's English rather than an invented sentence (a refusal
 *     the merchant cannot read is better than one that says something the server did not).
 *
 * Pure — the page passes its strings, its language, its money format and the stock date — so
 * web/scripts/merchant_test.mjs checks it in plain node.
 */
export interface Words {
  t: Strings
  lang: string
  /** an amount with its currency, in the page's language (lib/format.ts bhd) */
  bhd: (n: number) => string
  /** the stock snapshot's date when it is stale (a sold-out line says what it is measured against) */
  asOf?: string | null
}

const num = (v: unknown): number => (Number.isFinite(Number(v)) ? Number(v) : 0)

/** The line's key: the quote's, else read from an older API's English sentence. */
export function blockedCode(q: QuoteLine | null | undefined): string | null {
  if (!q) return null
  if (q.blocked_code) return String(q.blocked_code)
  const r = String(q.blocked_reason || '')
  if (/^Sold Out/i.test(r)) return 'sold_out'
  if (/^Minimum order is \d+\.?$/.test(r)) return 'moq'
  if (r === 'No longer in the catalog.') return 'not_in_catalog'
  if (/^Price on request/.test(r)) return 'price_on_request'
  return null
}

/** Why a line cannot be ordered, under the line ("Sold Out — can't be ordered right now…"). */
export function lineBlockedText(w: Words, q: QuoteLine | null | undefined): string | null {
  if (!q) return null
  const english = q.blocked_reason || null
  if (w.lang === 'en' && english) return english
  switch (blockedCode(q)) {
    case 'sold_out':
      return w.t.cart.lineSoldOut(w.asOf || null)
    case 'moq':
      return w.t.cart.lineMin(Math.max(1, num(q.moq) || num(String(english || '').replace(/\D+/g, '')) || 1))
    case 'not_in_catalog':
      return w.t.cart.lineGone
    case 'price_on_request':
      return w.t.cart.linePriceAsk
    default:
      return english
  }
}

/** The same reason in the short form the cart's block sentence carries ("Sold Out"). */
function lineShort(w: Words, q: QuoteLine | null | undefined): string {
  if (blockedCode(q) === 'sold_out') return w.asOf ? w.t.card.soldOutAsOf(w.asOf) : w.t.card.soldOut
  return String(lineBlockedText(w, q) || w.t.card.soldOut).replace(/[.。]\s*$/, '')
}

/** The one thing to do before the order can go ("Remove UK15 to send this order — Sold Out."). */
export function blockText(w: Words, quote: Quote | null | undefined): string {
  if (!quote) return w.t.cart.blocked
  const english = quote.block_reason || null
  if (w.lang === 'en') return english || w.t.cart.blocked
  const dead = (quote.lines || []).filter((l) => l.unavailable)
  let code = quote.block_code || null
  if (!code && english) {
    if (dead.length) code = 'dead_lines'
    else if (english === 'Your order is empty.') code = 'empty'
    else if (/^Minimum order is BHD/.test(english)) code = 'minimum'
  }
  if (code === 'dead_lines') {
    const items = quote.block_items && quote.block_items.length ? quote.block_items : dead.map((l) => l.item_code)
    if (!items.length) return w.t.cart.blocked
    const names = items.slice(0, 3).join(', ')
    if (items.length === 1) return w.t.cart.removeOne(names, lineShort(w, dead.find((l) => l.item_code === items[0]) || dead[0]))
    return w.t.cart.removeMany(names, Math.max(0, items.length - 3))
  }
  if (code === 'empty') return w.t.cart.orderEmpty
  if (code === 'minimum') {
    const min = num(quote.min_order_bhd) || num(quote.minimum?.value_bhd)
    const gap = num(quote.minimum?.remaining_bhd)
    if (min > 0 && gap > 0) return w.t.cart.minimumBlock(w.bhd(min), w.bhd(gap))
  }
  // a block this page cannot word: say that the order cannot go yet, in the page's language
  return w.t.cart.blocked
}

/** The cart's warnings ("UK15 is Sold Out — it will be backordered…"), one per server warning. */
export function warningTexts(w: Words, quote: Quote | null | undefined): string[] {
  const english = quote?.warnings || []
  if (w.lang === 'en') return english
  const codes = quote?.warning_codes || []
  return english.map((text, i) => {
    const c = codes.length === english.length ? codes[i] : null
    if (c && c.code === 'backorder' && c.item_code) return w.t.cart.backorderWarn(c.item_code)
    const m = /^(.+?) is Sold Out — it will be backordered/.exec(text)
    return m ? w.t.cart.backorderWarn(m[1]) : text
  })
}

/** The coupon's answer ("This code is not valid or has expired.", "… applied"). */
export function couponText(w: Words, c: QuoteCoupon | null | undefined): string | null {
  if (!c || !c.message) return null
  if (w.lang === 'en') return c.message
  const amount = num(c.amount_bhd)
  switch (c.reason) {
    case 'invalid':
      return w.t.cart.couponInvalid
    case 'other_rep':
      return w.t.cart.couponOtherRep
    case 'add_more':
      return amount > 0 ? w.t.cart.couponAddMore(w.bhd(amount)) : c.message
    case 'not_these_items':
      return w.t.cart.couponNotThese
    case 'better_offer':
      return c.rule_name ? w.t.cart.couponBetter(c.rule_name) : c.message
    case 'at_floor':
      return w.t.cart.couponAtFloor
    case 'capped':
      return amount > 0 ? w.t.cart.couponCapped(w.bhd(amount)) : c.message
    case 'applied':
      return w.t.cart.couponApplied(String(c.code || ''))
    default:
      return c.message
  }
}

/** The progress line under the cart ("Add BHD 3.000 more for free delivery"). */
export function progressText(w: Words, p: QuoteProgress | null | undefined): string {
  if (!p) return w.t.cart.delivery
  if (p.unlocked && p.kind === 'free_delivery') return w.t.cart.unlocked
  const english = p.label || null
  if (w.lang === 'en') return english || w.t.cart.delivery
  const gap = num(p.remaining_bhd)
  if (p.kind === 'free_delivery' && gap > 0) return w.t.cart.freeDeliveryGap(w.bhd(gap))
  if (p.kind === 'cart_value') {
    const name = p.rule_name || (english ? (/ more to unlock (.+)$/.exec(english)?.[1] ?? /^(.+) applied$/.exec(english)?.[1] ?? null) : null)
    if (name && !p.unlocked && gap > 0) return w.t.cart.unlockGap(w.bhd(gap), name)
    if (name && p.unlocked) return w.t.cart.offerApplied(name)
  }
  return english || w.t.cart.delivery
}

/**
 * The order endpoint's refusals: app/shop.py create_order / normalize_lines and app/offers.py raise
 * these exact sentences (tests/test_r7d_merchant.py pins every key against the server source).
 */
const FIXED: Record<string, (t: Strings) => string> = {
  "Please enter your phone number — the one from your last order can't be used on this phone.": (t) => t.checkout.reuseFailed,
  'Please enter your name.': (t) => t.checkout.nameBad,
  'Please enter a valid phone number (8-digit Bahrain or international).': (t) => t.checkout.phoneBad,
  'Please enter a valid email or leave it blank.': (t) => t.checkout.emailBad,
  'Your order is still being placed — tap Place order again in a minute': (t) => t.checkout.inFlight,
  'This phone number has placed many orders today — please contact your salesman.': (t) => t.checkout.phoneCap,
  'Too many orders from this device today — please contact your salesman.': (t) => t.checkout.deviceCap,
  'Ordering is temporarily unavailable — please try again in a minute.': (t) => t.checkout.unavailableNow,
  'This code is no longer available — it has run out or ended. Remove it to place your order.': (t) => t.checkout.couponGone,
  'Your cart is empty.': (t) => t.cart.orderEmpty,
  'Your order is empty.': (t) => t.cart.orderEmpty,
  'Order cannot be submitted.': (t) => t.cart.blocked,
  'The marketplace is not open.': (t) => t.checkout.closed,
}

/** The English sentences FIXED knows — for the test that pins them against the server. */
export const FIXED_SENTENCES = Object.keys(FIXED)

/** An error the server sent (ShopApiError.detail) in the page's language. A refusal that repeats
 *  the quote's block sentence is worded the way the cart words that block. */
export function errorText(w: Words, detail: string | null | undefined, quote?: Quote | null): string {
  const d = String(detail || '')
  if (w.lang === 'en' || !d) return d
  const fixed = FIXED[d]
  if (fixed) return fixed(w.t)
  if (quote && quote.block_reason && d === quote.block_reason) return blockText(w, quote)
  return d
}
