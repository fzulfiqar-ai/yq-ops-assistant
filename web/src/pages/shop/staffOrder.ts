/**
 * The rep's order-entry rules (the salesman catalog and its checkout) — pure, no imports, so
 * web/scripts/rep_ui_test.mjs runs them in plain node.
 *
 *  • the checkout's idempotency key (client_order_id): one per order the rep means to place. A
 *    second tap after a timeout re-sends the SAME key, so a lost response returns the order the
 *    first tap created instead of making a second one. A key is never reused for a DIFFERENT order:
 *    a new one is minted when the shop changes, and when the lines or the shop's details change
 *    after a failed attempt — otherwise shop B's order could come back as shop A's "duplicate";
 *  • which cart the lines on screen belong to when the rep picks another shop;
 *  • what the other screens show again once an order is placed.
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
 * What an order placed from the catalog changes on the rep's other screens — the React Query keys
 * to drop (each a prefix: every list and filter under it). The Orders list and its badge, Today (to
 * confirm, due), the book (the shop's last order), the stock the catalog shows (and Today's price
 * drops, which read the same cache), and the usual basket of the shop it was for (a new shop typed
 * at checkout has none). Without them the rep came back to an Orders list without the order.
 */
export function afterOrderKeys(basketKey: string | null): (readonly string[])[] {
  return [
    ['shop-orders'],
    ['shop-orders-new-count'],
    ['shop-today'],
    ['shop-book'],
    ['staff-catalog'],
    ...(basketKey ? [['shop-basket', basketKey]] : []),
  ]
}

export interface Minimum {
  value: number
  remaining: number
  met: boolean
  /** how far along, 0-100 */
  pct: number
}

/**
 * The wholesale minimum for the order bar and the slip: the server's (quote.minimum) once it has
 * priced the cart, the list-price estimate until then; null when there is no minimum to draw.
 */
export function minimumOf(
  total: number,
  minBhd: number,
  quote?: { minimum?: { value_bhd: number; remaining_bhd: number; met: boolean } | null } | null,
): Minimum | null {
  const m = quote?.minimum
  const value = Number(m?.value_bhd ?? minBhd) || 0
  if (value <= 0) return null
  const remaining = m ? Number(m.remaining_bhd) || 0 : Math.max(0, value - total)
  const met = m ? Boolean(m.met) : remaining <= 0
  return { value, remaining, met, pct: met ? 100 : Math.max(0, Math.min(100, ((value - remaining) / value) * 100)) }
}

/**
 * The compact order bar's one line under the total: how far the order is from the minimum while it
 * is short of it (the thing to act on), otherwise whose order it is and its size.
 */
export function orderBarLine(o: { shop: string; lines: number; units: number; minimum: Minimum | null }): string {
  const lines = `${o.lines} ${o.lines === 1 ? 'line' : 'lines'}`
  if (o.minimum && !o.minimum.met) return `BHD ${o.minimum.remaining.toFixed(3)} to the minimum · ${lines}`
  return `${o.shop || 'No shop picked yet'} · ${lines} · ${o.units} pcs`
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
