/**
 * The rep's order-entry rules (the salesman catalog and its checkout) — pure, no imports, so
 * web/scripts/rep_ui_test.mjs runs them in plain node.
 *
 *  • the checkout's idempotency key (client_order_id): one per order the rep means to place. A
 *    second tap after a timeout re-sends the SAME key, so a lost response returns the order the
 *    first tap created instead of making a second one. A key is never reused for a DIFFERENT order:
 *    a new one is minted when the shop changes, and when the lines or the shop's details change
 *    after a failed attempt — otherwise shop B's order could come back as shop A's "duplicate";
 *  • which cart the lines on screen belong to when the rep picks another shop.
 */

export interface CartLineLike { item_code: string; qty: number }
export interface OrderWho { name?: string | null; phone?: string | null; shop?: string | null }

/** A fresh idempotency key for the staff checkout. */
export function newClientOrderId(): string {
  try {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  } catch {
    /* an old browser: fall through */
  }
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

const norm = (s?: string | null) => String(s || '').trim().toLowerCase().replace(/\s+/g, ' ')

/** What one attempt is FOR: the lines (order-free) and who the order is for. Two attempts with the
 *  same key are the same order; anything else is a different one and needs its own id. */
export function attemptKey(lines: readonly CartLineLike[], who: OrderWho): string {
  const ls = lines
    .filter((l) => l && l.item_code && Number(l.qty) > 0)
    .map((l) => `${String(l.item_code).toUpperCase()}:${Math.floor(Number(l.qty))}`)
    .sort()
    .join(',')
  const phone = String(who.phone || '').replace(/\D/g, '')
  return `${ls}|${phone}|${norm(who.name)}|${norm(who.shop)}`
}

/**
 * The client_order_id to send now. `current` is the checkout's id (made when it opened or the shop
 * changed); `tried` maps each attempt that failed — a network error, a timeout, a refusal — to the
 * id it went with. The same order again goes with ITS id (the safe retry: a lost response comes
 * back as that order); a different order after a failure gets a new id, so an id only ever names
 * one order.
 */
export function clientIdFor(
  current: string,
  tried: Readonly<Record<string, string>>,
  key: string,
  mint: () => string = newClientOrderId,
): string {
  if (tried[key]) return tried[key]
  if (!current || Object.keys(tried).length > 0) return mint()
  return current
}

/**
 * Picking a shop: do the lines on screen move into that shop's cart? Only into an EMPTY cart, and
 * only from the cart of "no shop picked yet" ('staff') or from the checkout's "Change" (the rep is
 * correcting whose order this is). Switching shops anywhere else keeps each shop's own cart, so two
 * shops' orders never mix.
 */
export function carryCart(fromKey: string, toKey: string, lines: number, targetLines: number, fromCheckout: boolean): boolean {
  return toKey !== fromKey && toKey !== 'staff' && lines > 0 && targetLines === 0 && (fromKey === 'staff' || fromCheckout)
}
