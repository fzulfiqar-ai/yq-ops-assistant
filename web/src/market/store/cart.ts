import { useSyncExternalStore } from 'react'
import type { CartLine } from '@/lib/shopApi'

/**
 * The merchant's order-in-progress as an external store.
 *
 * Same storage key and shape as lib/cart.ts (`yq-shop-cart:market`, [{item_code, qty}]) — a cart
 * left by the previous build is picked up untouched. Kept outside React state so that one add
 * re-renders the card that changed and the cart surfaces, not the whole grid: components
 * subscribe to exactly the slice they read (`useCartQty(code)`, `useCartLines()`).
 *
 * Only {item_code, qty} is stored — prices and totals are the server's job.
 */

const KEY = 'yq-shop-cart:market'

let lines: CartLine[] = read()
let version = 0
/** bumps on every ADD (not on set/remove) so the badge can pulse once per add */
let addTick = 0
const listeners = new Set<() => void>()

function read(): CartLine[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.map((l) => ({ item_code: String(l?.item_code || ''), qty: Number(l?.qty) || 0 })).filter((l) => l.item_code && l.qty > 0)
  } catch {
    return []
  }
}

function write(next: CartLine[]) {
  try {
    if (next.length) localStorage.setItem(KEY, JSON.stringify(next))
    else localStorage.removeItem(KEY)
  } catch {
    /* private mode / quota — the cart just will not survive a reload */
  }
}

function commit(next: CartLine[], isAdd = false) {
  lines = next
  version++
  if (isAdd) addTick++
  write(next)
  listeners.forEach((fn) => fn())
}

// another tab changed the cart
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === KEY) commit(read())
  })
}

export const cartStore = {
  getLines: () => lines,
  getVersion: () => version,
  getAddTick: () => addTick,
  qtyOf: (code: string) => lines.find((l) => l.item_code === code)?.qty || 0,
  /** set an absolute quantity (0 removes). `isAdd` marks a first add for the badge pulse. */
  set(code: string, qty: number) {
    const exists = lines.some((l) => l.item_code === code)
    if (qty <= 0) {
      if (exists) commit(lines.filter((l) => l.item_code !== code))
      return
    }
    commit(exists ? lines.map((l) => (l.item_code === code ? { ...l, qty } : l)) : [...lines, { item_code: code, qty }], !exists)
  },
  add(code: string, qty: number) {
    const found = lines.find((l) => l.item_code === code)
    if (!found) {
      if (qty > 0) commit([...lines, { item_code: code, qty }], true)
      return
    }
    const next = found.qty + qty
    commit(next > 0 ? lines.map((l) => (l.item_code === code ? { ...l, qty: next } : l)) : lines.filter((l) => l.item_code !== code), true)
  },
  /** set many at once (Order again / Quick order): one notification, one badge pulse */
  setMany(entries: { item_code: string; qty: number }[]) {
    const map = new Map(lines.map((l) => [l.item_code, l.qty]))
    for (const e of entries) {
      if (e.qty > 0) map.set(e.item_code, e.qty)
      else map.delete(e.item_code)
    }
    commit(Array.from(map, ([item_code, qty]) => ({ item_code, qty })), true)
  },
  remove(code: string) {
    if (lines.some((l) => l.item_code === code)) commit(lines.filter((l) => l.item_code !== code))
  },
  clear() {
    if (lines.length) commit([])
  },
  subscribe(fn: () => void) {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  },
}

/* ───────────────────────── hooks ───────────────────────── */

export function useCartLines(): CartLine[] {
  return useSyncExternalStore(cartStore.subscribe, cartStore.getLines, cartStore.getLines)
}

export function useCartQty(code: string): number {
  return useSyncExternalStore(cartStore.subscribe, () => cartStore.qtyOf(code), () => 0)
}

export interface CartCounts {
  items: number
  units: number
}

let countsCache: { v: number; c: CartCounts } = { v: -1, c: { items: 0, units: 0 } }
function getCounts(): CartCounts {
  if (countsCache.v !== version) {
    countsCache = { v: version, c: { items: lines.length, units: lines.reduce((s, l) => s + l.qty, 0) } }
  }
  return countsCache.c
}

export function useCartCounts(): CartCounts {
  return useSyncExternalStore(cartStore.subscribe, getCounts, getCounts)
}

export function useCartAddTick(): number {
  return useSyncExternalStore(cartStore.subscribe, cartStore.getAddTick, () => 0)
}
