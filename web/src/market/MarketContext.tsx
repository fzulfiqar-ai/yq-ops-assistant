import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useCart, type Cart } from '@/lib/cart'
import {
  ShopApiError,
  type CatalogPayload,
  type MyOrderSummary,
  type Quote,
  type RepCard,
  type ShopItem,
  type ShopSettings,
} from '@/lib/shopApi'
import { minQtyOf, stepOf } from '@/pages/shop/shared'
import { useQuote, type QuoteFetcher } from '@/pages/shop/useQuote'
import {
  currentRef,
  forgetRef,
  isRecognized,
  lastQty,
  readNote,
  rememberQty,
  rememberedOrders,
  writeNote,
} from './lib/device'
import { track } from './lib/events'
import { getMarket, postMarketQuote, postMyOrders } from './lib/marketApi'
import { buildIndex, type SearchIndex } from './lib/search'

/**
 * Everything a marketplace screen needs, loaded once per visit:
 *
 *  • the catalog — painted from the copy this phone saved last time, replaced by the live
 *    payload as soon as it lands (a sleeping API never leaves the merchant staring at nothing);
 *  • who the merchant is attributed to (the /{slug} or ?ref remembered on this device);
 *  • the cart (device-scoped), the last quantity ordered per SKU, the server-priced quote;
 *  • the orders this device placed (tokens → summaries) — "recognized" merchants get a
 *    different home page;
 *  • the search index.
 *
 * Nothing here is authoritative: the server prices the cart and owns attribution.
 */

export type MarketStatus = 'loading' | 'ready' | 'offline' | 'closed' | 'error'

export interface MarketValue {
  data: CatalogPayload | null
  status: MarketStatus
  items: ShopItem[]
  itemsByCode: Map<string, ShopItem>
  categories: string[]
  settings: ShopSettings
  allowBackorder: boolean
  showCompare: boolean
  publicTiers: boolean
  rep: RepCard | null
  ref: string | null
  setRef: (slug: string | null) => void
  cart: Cart
  quote: Quote | null
  quoting: boolean
  quoteError: string
  coupon: string
  setCoupon: (c: string) => void
  note: string
  setNote: (n: string) => void
  recognized: boolean
  myOrders: MyOrderSummary[]
  refreshMyOrders: () => void
  index: SearchIndex | null
  add: (item: ShopItem, qty?: number, from?: string) => void
  setQty: (item: ShopItem, qty: number) => void
  remove: (code: string) => void
  defaultQty: (item: ShopItem) => number
  pairsFor: (code: string) => ShopItem[]
  reload: () => void
}

const Ctx = createContext<MarketValue | null>(null)

const cacheKey = (ref: string | null) => `yq-market-catalog:${(ref || '').toLowerCase()}`

function readCache(ref: string | null): CatalogPayload | null {
  try {
    const raw = localStorage.getItem(cacheKey(ref))
    const d = raw ? (JSON.parse(raw) as CatalogPayload) : null
    return d?.items?.length ? d : null
  } catch {
    return null
  }
}

function writeCache(ref: string | null, d: CatalogPayload | null): void {
  try {
    if (d) localStorage.setItem(cacheKey(ref), JSON.stringify(d))
    else localStorage.removeItem(cacheKey(ref))
  } catch {
    /* quota / private mode */
  }
}

export function MarketProvider({ children, initialRef }: { children: ReactNode; initialRef?: string | null }) {
  const [ref, setRefState] = useState<string | null>(() => initialRef || currentRef())
  const [data, setData] = useState<CatalogPayload | null>(() => readCache(initialRef || currentRef()))
  const [status, setStatus] = useState<MarketStatus>(() => (data ? 'ready' : 'loading'))
  const [coupon, setCoupon] = useState('')
  const [note, setNoteState] = useState(() => readNote())
  const [myOrders, setMyOrders] = useState<MyOrderSummary[]>([])
  const [reloadTick, setReloadTick] = useState(0)
  const cart = useCart('market')
  const viewedRef = useRef(false)

  // State only: the Home page persists a slug AFTER the server confirms it resolves, so an
  // unknown /{slug} can never overwrite the rep this phone already remembers.
  const setRef = useCallback((slug: string | null) => {
    setRefState(slug ? slug.toLowerCase() : null)
  }, [])

  /* ── catalog ─────────────────────────────────────────────── */
  useEffect(() => {
    let alive = true
    const cached = readCache(ref)
    if (cached) {
      setData(cached)
      setStatus('ready')
    } else {
      setStatus((s) => (s === 'ready' ? 'loading' : s))
    }
    getMarket(ref)
      .then((d) => {
        if (!alive) return
        setData(d)
        writeCache(ref, d)
        setStatus('ready')
        // A remembered slug that no longer resolves (rep left, code changed) is forgotten, so the
        // merchant is not attributed to a ghost. The URL slug case is handled by the Home page.
        if (ref && !d.ref && currentRef() === ref) forgetRef()
        if (!viewedRef.current) {
          viewedRef.current = true
          track('view', { referral_code: d.rep?.slug || ref || undefined })
        }
      })
      .catch((e: unknown) => {
        if (!alive) return
        if (e instanceof ShopApiError && e.status === 404) {
          writeCache(ref, null)
          setStatus('closed')
          return
        }
        setStatus(cached ? 'offline' : 'error')
      })
    return () => {
      alive = false
    }
  }, [ref, reloadTick])

  const reload = useCallback(() => setReloadTick((n) => n + 1), [])

  /* ── derived catalog views ─────────────────────────────────── */
  const items = useMemo(() => data?.items || [], [data])
  const itemsByCode = useMemo(() => {
    const m = new Map<string, ShopItem>()
    for (const it of items) m.set(it.item_code, it)
    return m
  }, [items])
  const categories = useMemo(() => data?.categories || [], [data])
  const settings = useMemo<ShopSettings>(() => data?.settings || {}, [data])
  // The search index is built off the critical path: Home never needs it, and the Search tab is at
  // most a few milliseconds behind. Building it inside the first render cost main-thread time
  // exactly when the largest photo and the first tap were waiting.
  const [index, setIndex] = useState<SearchIndex | null>(null)
  useEffect(() => {
    let cancelled = false
    const build = () => {
      if (!cancelled) setIndex(items.length ? buildIndex(items) : null)
    }
    if (typeof window.requestIdleCallback === 'function') {
      const id = window.requestIdleCallback(build, { timeout: 2000 })
      return () => {
        cancelled = true
        window.cancelIdleCallback(id)
      }
    }
    const id = window.setTimeout(build, 300) // Safari has no requestIdleCallback
    return () => {
      cancelled = true
      window.clearTimeout(id)
    }
  }, [items])
  const pairsMap = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const p of data?.pairs || []) m.set(p.item_code, p.with || [])
    return m
  }, [data])

  const pairsFor = useCallback(
    (code: string) =>
      (pairsMap.get(code) || []).map((c) => itemsByCode.get(c)).filter((x): x is ShopItem => Boolean(x)),
    [pairsMap, itemsByCode],
  )

  /* ── cart with quantity memory ─────────────────────────────── */
  const defaultQty = useCallback((item: ShopItem) => {
    const min = minQtyOf(item)
    const step = stepOf(item)
    const last = lastQty(item.item_code)
    if (!last) return min
    return Math.max(min, Math.ceil(last / step) * step)
  }, [])

  const add = useCallback(
    (item: ShopItem, qty?: number, from?: string) => {
      const q = qty && qty > 0 ? qty : defaultQty(item)
      cart.set(item.item_code, q)
      rememberQty(item.item_code, q)
      track('add', { item_code: item.item_code, meta: from ? { rail: from, count: q } : { count: q } })
    },
    [cart, defaultQty],
  )

  const setQty = useCallback(
    (item: ShopItem, qty: number) => {
      cart.set(item.item_code, qty)
      if (qty > 0) rememberQty(item.item_code, qty)
      track('qty', { item_code: item.item_code, meta: { count: qty } })
    },
    [cart],
  )

  const remove = useCallback(
    (code: string) => {
      cart.remove(code)
      track('remove', { item_code: code })
    },
    [cart],
  )

  const setNote = useCallback((n: string) => {
    setNoteState(n)
    writeNote(n)
  }, [])

  /* ── quote ───────────────────────────────────────────────── */
  const fetchQuote = useCallback<QuoteFetcher>((body, signal) => postMarketQuote(body, signal), [])
  const { quote, quoting, error: quoteError } = useQuote(Boolean(data), cart.lines, coupon, ref || '', fetchQuote)

  /* ── my orders (tokens this device holds) ──────────────────── */
  const refreshMyOrders = useCallback(() => {
    const tokens = rememberedOrders().map((o) => o.token)
    if (!tokens.length) {
      setMyOrders([])
      return
    }
    postMyOrders(tokens)
      .then(setMyOrders)
      .catch(() => {
        /* keep whatever we had */
      })
  }, [])

  useEffect(() => {
    refreshMyOrders()
  }, [refreshMyOrders])

  const recognized = isRecognized()

  const value = useMemo<MarketValue>(
    () => ({
      data,
      status,
      items,
      itemsByCode,
      categories,
      settings,
      allowBackorder: settings.allow_backorder !== false,
      showCompare: settings.show_retail_compare !== false,
      publicTiers: settings.public_tiers !== false,
      rep: data?.rep || null,
      ref,
      setRef,
      cart,
      quote,
      quoting,
      quoteError,
      coupon,
      setCoupon,
      note,
      setNote,
      recognized,
      myOrders,
      refreshMyOrders,
      index,
      add,
      setQty,
      remove,
      defaultQty,
      pairsFor,
      reload,
    }),
    [
      data, status, items, itemsByCode, categories, settings, ref, setRef, cart, quote, quoting, quoteError,
      coupon, note, setNote, recognized, myOrders, refreshMyOrders, index, add, setQty, remove, defaultQty,
      pairsFor, reload,
    ],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useMarket(): MarketValue {
  const v = useContext(Ctx)
  if (!v) throw new Error('useMarket must be used inside MarketProvider')
  return v
}
