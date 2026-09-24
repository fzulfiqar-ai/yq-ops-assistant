import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ShopApiError, type Campaign, type CatalogPayload, type MyOrderSummary, type Quote, type RepCard, type ShopItem, type ShopSettings } from '@/lib/shopApi'
import { useQuote, type QuoteFetcher } from '@/pages/shop/useQuote'
import { currentRef, forgetRef, isRecognized, lastQty, readNote, rememberQty, rememberedOrders, writeNote } from './lib/device'
import { track } from './lib/events'
import { fmtDateShort, isOut, minQtyOf, normalizeQty } from './lib/format'
import { getMarket, postMarketQuote, postMyOrders } from './lib/marketApi'
import type { SearchIndex } from './lib/search'
import { cartStore, useCartLines } from './store/cart'
import { S } from './strings'
import { useToast } from './ui/Toast'

/**
 * Two contexts, one provider:
 *
 *  • `useMarket()` — the catalog (painted from the copy this phone saved last time, replaced by
 *    the live payload), who the merchant is attributed to, the search index (loaded lazily, off
 *    the critical path), and the cart ACTIONS (add / setQty / remove with quantity memory and
 *    analytics). This value changes rarely, so product cards stay cheap.
 *  • `useOrder()` — the server-priced quote, coupon, note, and the orders this device placed.
 *    Changes on every cart edit; only cart surfaces subscribe.
 *
 * Cart LINES live in store/cart.ts; components read their own slice with useCartQty / useCartLines.
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
  /** live campaigns for this visitor (audience filter applied) */
  campaigns: Campaign[]
  allowBackorder: boolean
  /** the stock snapshot every status is read from (ISO date), and whether the API called it stale */
  stockAsOf: string | null
  stockStale: boolean
  /** "Sold out" — or, once the snapshot is stale, "Sold out · stock as of 21 Sep": the one label every surface uses */
  soldOutLabel: string
  showCompare: boolean
  publicTiers: boolean
  rep: RepCard | null
  ref: string | null
  setRef: (slug: string | null) => void
  recognized: boolean
  index: SearchIndex | null
  /** ask for the search index now (search intent) — resolves when built */
  ensureIndex: () => Promise<SearchIndex | null>
  /** remembered quantity for this SKU, rounded to its rules — what one tap of Add sets */
  defaultQty: (item: ShopItem) => number
  add: (item: ShopItem, qty?: number, from?: string) => number
  setQty: (item: ShopItem, qty: number) => void
  remove: (code: string) => void
  /** put many lines in at once (Order again, Quick order) */
  addMany: (entries: { item: ShopItem; qty: number }[], from: string) => number
  pairsFor: (code: string) => ShopItem[]
  reload: () => void
}

export interface OrderValue {
  quote: Quote | null
  quoting: boolean
  quoteError: string
  coupon: string
  setCoupon: (c: string) => void
  note: string
  setNote: (n: string) => void
  myOrders: MyOrderSummary[]
  refreshMyOrders: () => void
}

const MarketCtx = createContext<MarketValue | null>(null)
const OrderCtx = createContext<OrderValue | null>(null)

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
  const viewedRef = useRef(false)

  // State only: the Home page persists a slug AFTER the server confirms it resolves, so an
  // unknown /{slug} can never overwrite the rep this phone already remembers.
  const setRef = useCallback((slug: string | null) => setRefState(slug ? slug.toLowerCase() : null), [])

  /* ── catalog ─────────────────────────────────────────────── */
  // When the ref changes (a /{slug} entrance), paint that storefront's cached copy at once —
  // derived during render, so the effect below only ever fetches.
  const [refSeen, setRefSeen] = useState(ref)
  if (refSeen !== ref) {
    setRefSeen(ref)
    const c = readCache(ref)
    setData(c)
    setStatus(c ? 'ready' : 'loading')
  }
  useEffect(() => {
    let alive = true
    const cached = readCache(ref)
    getMarket(ref)
      .then((d) => {
        if (!alive) return
        setData(d)
        writeCache(ref, d)
        setStatus('ready')
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

  /* ── search index: MiniSearch is its own chunk, loaded after the first paint or on intent ── */
  const [index, setIndex] = useState<SearchIndex | null>(null)
  const buildRef = useRef<Promise<SearchIndex | null> | null>(null)
  const itemsRef = useRef(items)
  useEffect(() => {
    itemsRef.current = items
  }, [items])
  const ensureIndex = useCallback(() => {
    if (!buildRef.current) {
      buildRef.current = import('./lib/search')
        .then(({ buildIndex }) => (itemsRef.current.length ? buildIndex(itemsRef.current) : null))
        .then((ix) => {
          setIndex(ix)
          return ix
        })
        .catch(() => null)
    }
    return buildRef.current
  }, [])
  useEffect(() => {
    // items changed (live payload replaced the cache): rebuild lazily on next intent / idle
    buildRef.current = null
    if (!items.length) return
    const go = () => void ensureIndex()
    if (typeof window.requestIdleCallback === 'function') {
      const id = window.requestIdleCallback(go, { timeout: 3000 })
      return () => window.cancelIdleCallback(id)
    }
    const id = window.setTimeout(go, 1200)
    return () => window.clearTimeout(id)
  }, [items, ensureIndex])

  const pairsMap = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const p of data?.pairs || []) m.set(p.item_code, p.with || [])
    return m
  }, [data])
  const pairsFor = useCallback(
    (code: string) => (pairsMap.get(code) || []).map((c) => itemsByCode.get(c)).filter((x): x is ShopItem => Boolean(x)),
    [pairsMap, itemsByCode],
  )

  /* ── cart actions with quantity memory ─────────────────────── */
  const defaultQty = useCallback((item: ShopItem) => {
    const last = lastQty(item.item_code)
    return last ? normalizeQty(item, last) : minQtyOf(item)
  }, [])

  /*
   * The sold-out rule at the cart's door (R1): once the shop takes no backorder, nothing sold out
   * reaches the cart from ANY path — the card, the panel, the palette's ⇧Enter, "Order again", a
   * loaded list — the line is left out and the merchant is told how many were. The server would
   * only refuse it at the quote, with a reason that reads as if it sold out after the add.
   */
  const toast = useToast()
  const allowBackorder = settings.allow_backorder !== false

  const add = useCallback(
    (item: ShopItem, qty?: number, from?: string) => {
      if (isOut(item) && !allowBackorder) {
        toast(S.card.leftOut(1), 'info')
        return 0
      }
      const q = qty && qty > 0 ? normalizeQty(item, qty) : defaultQty(item)
      cartStore.set(item.item_code, q)
      rememberQty(item.item_code, q)
      track('add', { item_code: item.item_code, meta: { count: q, ...(from ? { rail: from } : {}) } })
      return q
    },
    [defaultQty, allowBackorder, toast],
  )

  const setQty = useCallback((item: ShopItem, qty: number) => {
    cartStore.set(item.item_code, qty)
    if (qty > 0) rememberQty(item.item_code, qty)
    track('qty', { item_code: item.item_code, meta: { count: qty } })
  }, [])

  const remove = useCallback((code: string) => {
    cartStore.remove(code)
    track('remove', { item_code: code })
  }, [])

  const addMany = useCallback(
    (entries: { item: ShopItem; qty: number }[], from: string) => {
      const wanted = entries.filter((e) => e.qty > 0)
      const kept = allowBackorder ? wanted : wanted.filter((e) => !isOut(e.item))
      const rows = kept.map((e) => ({ item_code: e.item.item_code, qty: normalizeQty(e.item, e.qty) }))
      cartStore.setMany(rows)
      for (const r of rows) rememberQty(r.item_code, r.qty)
      track('reorder', { meta: { count: rows.length, rail: from } })
      if (wanted.length > kept.length) toast(S.card.leftOut(wanted.length - kept.length), 'info')
      return rows.length
    },
    [allowBackorder, toast],
  )

  const setNote = useCallback((n: string) => {
    setNoteState(n)
    writeNote(n)
  }, [])

  /* ── quote (subscribes to the lines here, not in every card) ── */
  const lines = useCartLines()
  const fetchQuote = useCallback<QuoteFetcher>((body, signal) => postMarketQuote(body, signal), [])
  const { quote, quoting, error: quoteError } = useQuote(Boolean(data), lines, coupon, ref || '', fetchQuote)

  /* ── my orders (tokens this device holds) ──────────────────── */
  const refreshMyOrders = useCallback(() => {
    const tokens = rememberedOrders().map((o) => o.token)
    ;(tokens.length ? postMyOrders(tokens) : Promise.resolve([] as MyOrderSummary[]))
      .then(setMyOrders)
      .catch(() => {})
  }, [])
  useEffect(() => {
    refreshMyOrders()
  }, [refreshMyOrders])

  const recognized = isRecognized()
  const campaigns = useMemo<Campaign[]>(
    () => (data?.campaigns || []).filter((c) => c.audience === 'all' || (c.audience === 'recognized') === recognized),
    [data, recognized],
  )
  // a stale snapshot (older than shop_stock_fresh_days, the API decides) keeps every status and
  // puts the snapshot date beside "Sold out"; an older API that sends no flag counts as fresh
  const stockAsOf = data?.stock_as_of || null
  const stockStale = data?.stock_fresh === false
  const soldOutLabel = stockStale && fmtDateShort(stockAsOf) ? S.card.soldOutAsOf(fmtDateShort(stockAsOf) as string) : S.card.stockOut

  const market = useMemo<MarketValue>(
    () => ({
      data,
      status,
      items,
      itemsByCode,
      categories,
      settings,
      campaigns,
      allowBackorder,
      stockAsOf,
      stockStale,
      soldOutLabel,
      showCompare: settings.show_retail_compare !== false,
      publicTiers: settings.public_tiers !== false,
      rep: data?.rep || null,
      ref,
      setRef,
      recognized,
      index,
      ensureIndex,
      defaultQty,
      add,
      setQty,
      remove,
      addMany,
      pairsFor,
      reload,
    }),
    [data, status, items, itemsByCode, categories, settings, campaigns, allowBackorder, stockAsOf, stockStale, soldOutLabel, ref, setRef, recognized, index, ensureIndex, defaultQty, add, setQty, remove, addMany, pairsFor, reload],
  )

  const order = useMemo<OrderValue>(
    () => ({ quote, quoting, quoteError, coupon, setCoupon, note, setNote, myOrders, refreshMyOrders }),
    [quote, quoting, quoteError, coupon, note, setNote, myOrders, refreshMyOrders],
  )

  return (
    <MarketCtx.Provider value={market}>
      <OrderCtx.Provider value={order}>{children}</OrderCtx.Provider>
    </MarketCtx.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useMarket(): MarketValue {
  const v = useContext(MarketCtx)
  if (!v) throw new Error('useMarket must be used inside MarketProvider')
  return v
}

// eslint-disable-next-line react-refresh/only-export-components
export function useOrder(): OrderValue {
  const v = useContext(OrderCtx)
  if (!v) throw new Error('useOrder must be used inside MarketProvider')
  return v
}
