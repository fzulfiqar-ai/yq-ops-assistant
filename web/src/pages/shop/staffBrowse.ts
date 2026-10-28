import type { ShopItem } from '@/lib/shopApi'
import { codeKey } from '@/market/lib/format'

/**
 * The salesman catalog before its search index is built — pure, so web/scripts/rep_field_test.mjs
 * runs it in plain node.
 *
 * The index (MiniSearch) used to be built in the first render after the catalog arrived, holding up
 * the first paint. It is now built when the phone is idle (whenIdle); a search typed before it is
 * ready is a plain filter (plainSearch), so the list never ignores what was typed.
 */

type IdleWindow = typeof globalThis & {
  requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
  cancelIdleCallback?: (id: number) => void
}

/**
 * Run `fn` when the phone is idle (requestIdleCallback, at the latest after `timeout` ms), or on the
 * next timer turn where there is none (Safari); the function returned cancels it.
 */
export function whenIdle(fn: () => void, timeout = 600): () => void {
  const w = globalThis as IdleWindow
  if (typeof w.requestIdleCallback === 'function') {
    const id = w.requestIdleCallback(fn, { timeout })
    return () => w.cancelIdleCallback?.(id)
  }
  const t = setTimeout(fn, 1)
  return () => clearTimeout(t)
}

/** Every word of the text in the code (spaces, dashes and case ignored), the name, the spec or the category. */
export function plainSearch(items: readonly ShopItem[], text: string): ShopItem[] {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return items.slice()
  return items.filter((i) => {
    const code = codeKey(i.item_code)
    const hay = `${i.item_code} ${i.display_name || ''} ${i.spec || ''} ${i.category || ''}`.toLowerCase()
    return words.every((w) => hay.includes(w) || (codeKey(w) !== '' && code.includes(codeKey(w))))
  })
}
