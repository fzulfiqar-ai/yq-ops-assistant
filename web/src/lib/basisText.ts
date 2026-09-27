/** The words that say what a portal number is made of, shared so two pages never describe the same
 *  figure two ways. Pure: no React, no fetch (web/scripts/numbers_ui_test.mjs exercises it in node). */

/** Dead stock is valued at COST, and an item with no usable cost (no Focus average, no landed cost) is
 *  left out of that value — never valued at 0. The item count covers every dead item, so whenever some
 *  are uncosted the tile says how many, and the count and the value never silently describe different
 *  sets of items. Empty when every dead item is costed. */
export function deadUncostedNote(deadCount: number | null | undefined, uncosted: number | null | undefined): string {
  const n = Math.max(0, Math.round(Number(uncosted) || 0))
  if (n <= 0) return ''
  const total = Math.max(n, Math.round(Number(deadCount) || 0))
  return ` · ${n.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} without a usable cost, left out of the value`
}

/** Marketplace order money as the marketplace stores it: VAT-inclusive (the API's money_basis
 *  "confirmed_else_requested_incl_vat"). The Command Centre shows the same orders ex-VAT. */
export const VAT_LABEL = 'incl. VAT'
export const MONEY_NOTE = 'order money incl. VAT, as the marketplace stores it (the Command Centre shows it ex-VAT)'
