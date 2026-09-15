import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CartLine } from '@/lib/shopApi'

/**
 * The customer's basket for one catalog link.
 *
 * Persisted in localStorage keyed by the share token, so a customer who closes
 * WhatsApp mid-browse comes back to the same cart — and two different salesmen's
 * links never bleed into each other. Only {item_code, qty} is stored: prices and
 * totals are the server's job (docs/SHOP.md — client totals are ignored).
 */

const PREFIX = 'yq-shop-cart:'

function storageKey(token?: string) {
  return `${PREFIX}${token || 'anon'}`
}

function read(key: string): CartLine[] {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .map((l) => ({ item_code: String(l?.item_code || ''), qty: Number(l?.qty) || 0 }))
      .filter((l) => l.item_code && l.qty > 0)
  } catch {
    return []
  }
}

function write(key: string, lines: CartLine[]) {
  try {
    if (lines.length) localStorage.setItem(key, JSON.stringify(lines))
    else localStorage.removeItem(key)
  } catch {
    /* private mode / quota — the cart just won't survive a reload */
  }
}

export interface Cart {
  lines: CartLine[]
  /** distinct products in the cart */
  items: number
  /** total pieces across all products */
  units: number
  qtyOf: (code: string) => number
  add: (code: string, qty: number) => void
  set: (code: string, qty: number) => void
  remove: (code: string) => void
  clear: () => void
}

export function useCart(token?: string): Cart {
  const key = storageKey(token)
  // Keyed state: when the token changes we read THAT link's cart instead of
  // carrying the previous one over (the classic "wrong cart on a shared device"
  // bug). The stale entry is simply ignored — no re-render needed to swap it.
  const [state, setState] = useState<{ key: string; lines: CartLine[] }>(() => ({ key, lines: read(key) }))
  const lines = useMemo(() => (state.key === key ? state.lines : read(key)), [state, key])

  useEffect(() => {
    write(state.key, state.lines)
  }, [state])

  const update = useCallback(
    (fn: (prev: CartLine[]) => CartLine[]) => {
      setState((s) => ({ key, lines: fn(s.key === key ? s.lines : read(key)) }))
    },
    [key],
  )

  const set = useCallback(
    (code: string, qty: number) => {
      update((prev) => {
        if (qty <= 0) return prev.filter((l) => l.item_code !== code)
        return prev.some((l) => l.item_code === code)
          ? prev.map((l) => (l.item_code === code ? { ...l, qty } : l))
          : [...prev, { item_code: code, qty }]
      })
    },
    [update],
  )

  const add = useCallback(
    (code: string, qty: number) => {
      update((prev) => {
        const found = prev.find((l) => l.item_code === code)
        if (!found) return qty > 0 ? [...prev, { item_code: code, qty }] : prev
        const next = found.qty + qty
        return next > 0 ? prev.map((l) => (l.item_code === code ? { ...l, qty: next } : l)) : prev.filter((l) => l.item_code !== code)
      })
    },
    [update],
  )

  const remove = useCallback((code: string) => update((prev) => prev.filter((l) => l.item_code !== code)), [update])
  const clear = useCallback(() => update(() => []), [update])

  const qtyMap = useMemo(() => {
    const m = new Map<string, number>()
    for (const l of lines) m.set(l.item_code, l.qty)
    return m
  }, [lines])

  const qtyOf = useCallback((code: string) => qtyMap.get(code) || 0, [qtyMap])
  const units = useMemo(() => lines.reduce((s, l) => s + l.qty, 0), [lines])

  return { lines, items: lines.length, units, qtyOf, add, set, remove, clear }
}
