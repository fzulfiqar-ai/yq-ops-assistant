import type { ShopItem } from '@/lib/shopApi'
import { isSoldOut, minQtyOf } from './shared'

/**
 * Edits to the rep's cart that can be taken back (the salesman catalog's Undo) — pure, so
 * web/scripts/rep_field_test.mjs runs them in plain node.
 *
 *  • "Order again" MERGES the shop's usual order into the cart. It used to set each usual line's
 *    quantity, so a quantity the rep had already typed for that product was overwritten. Now every
 *    line already in the cart keeps its quantity, and only the usual lines that are missing are
 *    added (at least their minimum); sold-out lines stay out and are named.
 *  • Undo puts the cart back: the whole cart before an "Order again", or one removed line at the
 *    place it had, with its quantity.
 */

export interface LineLike {
  item_code: string
  qty: number
}

export interface UsualMerge {
  lines: LineLike[]
  /** usual lines added to the cart */
  added: string[]
  /** usual lines already in the cart: their typed quantity kept */
  kept: string[]
  /** usual lines sold out today: not added */
  soldOut: string[]
}

export function mergeUsual(cart: readonly LineLike[], usual: readonly LineLike[], itemOf: (code: string) => ShopItem | undefined): UsualMerge {
  const lines = cart.map((l) => ({ ...l }))
  const have = new Set(lines.map((l) => l.item_code))
  const added: string[] = []
  const kept: string[] = []
  const soldOut: string[] = []
  for (const ln of usual) {
    const it = itemOf(ln.item_code)
    if (!it) continue // no longer in the catalog
    if (isSoldOut(it)) soldOut.push(it.item_code)
    else if (have.has(it.item_code)) kept.push(it.item_code)
    else {
      lines.push({ item_code: it.item_code, qty: Math.max(minQtyOf(it), Number(ln.qty) || 0) })
      have.add(it.item_code)
      added.push(it.item_code)
    }
  }
  return { lines, added, kept, soldOut }
}

const linesWord = (n: number, usual: boolean) => `${n} ${usual ? 'usual ' : ''}${n === 1 ? 'line' : 'lines'}`

/** The words of the toast that offers Undo after an "Order again" ('' when nothing was added: nothing to undo). */
export function usualText(m: Pick<UsualMerge, 'added' | 'kept'>, from?: string | null): string {
  if (!m.added.length) return ''
  const what = from === 'last_order' ? `${linesWord(m.added.length, false)} from their last order` : linesWord(m.added.length, true)
  return `Added ${what}${m.kept.length ? ' · kept your quantities' : ''}`
}

/** The lines with a removed one put back at the place it had (or last), with its quantity. */
export function restoreLine(lines: readonly LineLike[], line: LineLike, at: number): LineLike[] {
  const rest = lines.filter((l) => l.item_code !== line.item_code)
  const i = Math.max(0, Math.min(at, rest.length))
  return [...rest.slice(0, i), { ...line }, ...rest.slice(i)]
}

/** Make a cart exactly these lines, in this order, with the cart's own calls (lib/cart has no "replace"). */
export function replaceLines(cart: { clear: () => void; set: (code: string, qty: number) => void }, lines: readonly LineLike[]): void {
  cart.clear()
  for (const l of lines) cart.set(l.item_code, l.qty)
}
