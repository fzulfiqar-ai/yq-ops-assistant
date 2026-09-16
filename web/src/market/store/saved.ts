import { useSyncExternalStore } from 'react'

/**
 * Saved items — a merchant's shortlist, kept on the device like the cart (no account). Codes only;
 * the catalog payload supplies the rest, so a saved product that leaves the shelf simply stops
 * rendering.
 */
const KEY = 'yq-saved'
const listeners = new Set<() => void>()
let codes: string[] = read()

function read(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string').slice(0, 200) : []
  } catch {
    return []
  }
}

function commit(next: string[]) {
  codes = next
  try {
    if (next.length) localStorage.setItem(KEY, JSON.stringify(next))
    else localStorage.removeItem(KEY)
  } catch {
    /* private mode */
  }
  listeners.forEach((fn) => fn())
}

export const savedStore = {
  get: () => codes,
  has: (code: string) => codes.includes(code),
  toggle(code: string): boolean {
    const on = !codes.includes(code)
    commit(on ? [code, ...codes.filter((c) => c !== code)] : codes.filter((c) => c !== code))
    return on
  },
  remove: (code: string) => commit(codes.filter((c) => c !== code)),
  clear: () => commit([]),
  subscribe(fn: () => void) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === KEY) commit(read())
  })
}

export function useSaved(): string[] {
  return useSyncExternalStore(savedStore.subscribe, savedStore.get, () => [])
}

export function useIsSaved(code: string): boolean {
  return useSyncExternalStore(savedStore.subscribe, () => codes.includes(code), () => false)
}
