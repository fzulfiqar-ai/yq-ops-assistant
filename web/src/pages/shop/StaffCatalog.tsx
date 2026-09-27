import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { LayoutGrid, List, MessageCircle, RotateCcw, Search, ShoppingBag, SlidersHorizontal, X } from 'lucide-react'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { peekCart, useCart, writeCart } from '@/lib/cart'
import { useSeo } from '@/lib/seo'
import { getStaffCatalog, postStaffQuote, type OrderResponse, type ShopItem, type StaffCustomer } from '@/lib/shopApi'
import { buildIndex, searchItems } from '@/market/lib/search'
import { parseToken, resolveQuick, splitList } from '@/market/lib/quickParse'
import { codeKey } from '@/market/lib/format'
import {
  basketQuery,
  restockText,
  setSelectedCustomer,
  shopKeyOf,
  stockAge,
  useSelectedCustomer,
  useShopBasket,
  useShopBook,
  waLink,
  type ShopBasket,
} from '@/pages/sales/lib'
import { CartDrawer } from './CartDrawer'
import { FiltersSheet } from './FiltersSheet'
import { isQuickList } from './quickAdd'
import { NO_FILTERS, type StaffFilters, type StaffSort } from './staffFilters'
import { MinimumRuler, OrderSlip } from './OrderSlip'
import { OrderSuccess } from './OrderSuccess'
import { ProductCard } from './ProductCard'
import { ProductRow } from './ProductRow'
import { ProductSheet } from './ProductSheet'
import { QtySheet } from './QtySheet'
import { ShopPickerSheet, ShopPill } from './ShopPicker'
import { ShopSheet } from './ShopSheet'
import { SoldOutDivider } from './SoldOut'
import { bhd, hasBadge, isSoldOut, minQtyOf, RING, stepOf } from './shared'
import { useQuote, type QuoteFetcher } from './useQuote'

/**
 * The salesman catalog (Sprint 5 "rep speed", plan §10-11). Built for a repeat order in a handful
 * of taps: pick the shop (one picker over the merged book), "Order again" fills its usual basket,
 * Review, Place. Everything else is there to find a line fast:
 *
 *  • one chip row — the categories that have matches, then "Filters" (In stock, Clearance, Best
 *    sellers, Sort) — and search through the marketplace's index (codes with spaces work);
 *  • a quick-add in the same field: "C18 3, UK15 6" + Enter adds the exact codes (a sold-out line is
 *    never added on its own; anything that is not a code stays in the field to pick from the list);
 *  • list mode by default (72 px rows, stock bands, a keypad quantity and "+"), a grid to show a
 *    customer; sold-out lines last, under "Sold Out · N lines";
 *  • from 1280 px a dense order-entry table ("/" to search, arrows to move, Enter to add) beside a
 *    docked order slip with the shop, the lines, the BHD minimum ruler and Place order.
 *
 * Carts are kept per shop (`staff:<book key>`): switching shops never mixes two orders, and a cart
 * started before the shop was picked moves into that shop's cart when it is picked.
 */

const TOPBAR = 'var(--yq-topbar, 56px)'
const TABBAR = 'calc(var(--yq-tabbar, 64px) + env(safe-area-inset-bottom, 0px))'
const GRID_CHUNK = 48
const VIEW_KEY = 'yq-staff-view'

type View = 'list' | 'grid'

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'grid' ? 'grid' : 'list'
  } catch {
    return 'list'
  }
}

interface QuickRow {
  raw: string
  item: ShopItem | null
  qty: number | null
  /** the typed code IS this sold-out line — never added on its own */
  soldOut: ShopItem | null
}

interface Notice {
  tone: 'ok' | 'warn'
  text: string
  soldOut?: string[]
}

const byPrice = (dir: 1 | -1) => (a: ShopItem, b: ShopItem) => {
  const pa = a.price_bhd == null ? Number.MAX_SAFE_INTEGER * dir : Number(a.price_bhd)
  const pb = b.price_bhd == null ? Number.MAX_SAFE_INTEGER * dir : Number(b.price_bhd)
  return dir * (pa - pb)
}

export function StaffCatalog() {
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()

  // Kept in React Query's cache for five minutes: Today → Catalog → Orders → Catalog paints at once.
  const catalogQ = useQuery({ queryKey: ['staff-catalog'], queryFn: getStaffCatalog, staleTime: 5 * 60_000, retry: 1 })
  const data = catalogQ.data || null
  const items = useMemo(() => data?.items || [], [data])
  const itemsByCode = useMemo(() => new Map(items.map((i) => [i.item_code, i])), [items])
  const index = useMemo(() => (items.length ? buildIndex(items) : null), [items])
  const settings = data?.settings || {}
  const allowBackorder = settings.allow_backorder !== false
  const minBhd = Number(settings.min_order_bhd) || 0

  /* ── the shop, and its own cart ── */
  const sel = useSelectedCustomer()
  const shopKey = shopKeyOf(sel)
  const cartKey = shopKey ? `staff:${shopKey}` : 'staff'
  const cart = useCart(cartKey)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [shopSheetOpen, setShopSheetOpen] = useState(false)
  const [returnToCart, setReturnToCart] = useState(false)
  const book = useShopBook(pickerOpen || Boolean(sel))
  const row = useMemo(() => (shopKey ? book.data?.shops.find((s) => s.key === shopKey) || null : null), [book.data, shopKey])
  const basketKey = shopKey && !shopKey.startsWith('n:') ? shopKey : null
  const basketQ = useShopBasket(basketKey)

  /* ── browse state ── */
  const [cat, setCat] = useState('All')
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState<StaffFilters>(NO_FILTERS)
  const [sort, setSort] = useState<StaffSort>('featured')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [view, setViewState] = useState<View>(readView)
  const [gridN, setGridN] = useState({ key: '', n: GRID_CHUNK })
  const [highlight, setHighlight] = useState(0)
  const [sheetCode, setSheetCode] = useState<string | null>(null)
  const [qtyCode, setQtyCode] = useState<string | null>(null)
  const [cartOpen, setCartOpen] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [order, setOrder] = useState<{ res: OrderResponse; customer: { name: string; shop: string } } | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const setView = (v: View) => {
    setViewState(v)
    try {
      localStorage.setItem(VIEW_KEY, v)
    } catch {
      /* private mode — the list stays the default */
    }
  }

  /* ── pick a shop: carry a cart started before the pick into that shop's own cart ── */
  const pickShop = useCallback(
    (c: StaffCustomer | null) => {
      const next = c ? `staff:${shopKeyOf(c)}` : 'staff'
      if (cartKey === 'staff' && next !== 'staff' && cart.lines.length && !peekCart(next).length) {
        writeCart(next, cart.lines)
        cart.clear()
      }
      setSelectedCustomer(c)
      setNotice(null)
      if (returnToCart) {
        setReturnToCart(false)
        setCartOpen(true)
      }
    },
    [cart, cartKey, returnToCart],
  )

  /* ── Order again: the shop's suggested repeat into its cart; sold-out lines left out and named ── */
  const preload = useCallback(
    (b: ShopBasket) => {
      let added = 0
      const soldOut: string[] = []
      for (const ln of b.suggested) {
        const it = itemsByCode.get(ln.item_code)
        if (!it) continue
        if (isSoldOut(it)) {
          soldOut.push(ln.item_code)
          continue
        }
        cart.set(it.item_code, Math.max(minQtyOf(it), ln.qty))
        added += 1
      }
      setNotice(
        added
          ? { tone: soldOut.length ? 'warn' : 'ok', text: `Added ${added} ${added === 1 ? 'line' : 'lines'} from ${b.suggested_from === 'last_order' ? 'their last order' : 'their usual order'} — check the quantities, then Review.`, soldOut }
          : { tone: 'warn', text: 'Nothing to add — every line of their usual order is sold out right now.', soldOut },
      )
    },
    [cart, itemsByCode],
  )

  // "Order again" from Today or Customers lands here with the shop picked and { again: key } in the state
  const againKey = (location.state as { again?: string } | null)?.again || ''
  const againDone = useRef('')
  useEffect(() => {
    if (!againKey) {
      againDone.current = ''
      return
    }
    if (!data || againKey !== shopKey || againDone.current === againKey) return
    againDone.current = againKey
    qc.fetchQuery(basketQuery(againKey))
      .then(preload)
      .catch(() => setNotice({ tone: 'warn', text: 'Their usual order did not load — add the lines by hand, or try Order again from the shop.' }))
      .finally(() => navigate(location.pathname, { replace: true, state: null }))
  }, [againKey, data, shopKey, qc, preload, navigate, location.pathname])

  /* ── search → category → filters → sort (sold-out last, always) ── */
  const quick = isQuickList(q)
  const quickRows = useMemo<QuickRow[]>(() => {
    if (!quick || !index) return []
    return splitList(q).map((raw) => {
      const { query, qty } = parseToken(raw)
      const r = resolveQuick(query, items, index)
      return { raw, qty, item: r.lock, soldOut: r.exactOut }
    })
  }, [quick, q, items, index])

  const searched = useMemo(() => {
    const s = q.trim()
    if (!s) return items
    if (quick) {
      // the exact codes of the list, then — for a token that is not a code ("tws 4") — its best
      // matches, so the rep picks one with "+" instead of retyping it
      const out = quickRows.map((r) => r.item || r.soldOut).filter((x): x is ShopItem => Boolean(x))
      const seen = new Set(out.map((i) => i.item_code))
      if (index) {
        for (const r of quickRows) {
          if (r.item || r.soldOut) continue
          for (const hit of searchItems(index, items, parseToken(r.raw).query, 6)) {
            if (!seen.has(hit.item_code)) {
              seen.add(hit.item_code)
              out.push(hit)
            }
          }
        }
      }
      return out
    }
    if (!index) return items
    // an exact code (or its start) first — "x05 uc" and "X05UC" both find X05 UC-1Mtr — then the index
    const key = codeKey(s)
    const exact = key.length >= 2 ? items.filter((i) => codeKey(i.item_code).startsWith(key)) : []
    const hits = searchItems(index, items, s, 500)
    const seen = new Set(exact.map((i) => i.item_code))
    return [...exact, ...hits.filter((i) => !seen.has(i.item_code))]
  }, [items, index, q, quick, quickRows])

  const passFilters = useCallback(
    (i: ShopItem) => (!filters.inStock || !isSoldOut(i)) && (!filters.clearance || hasBadge(i, 'clearance')) && (!filters.best || hasBadge(i, 'best_seller')),
    [filters],
  )

  const catCounts = useMemo(() => {
    const m = new Map<string, number>()
    for (const i of searched) if (passFilters(i)) m.set(i.category || 'OTHER', (m.get(i.category || 'OTHER') || 0) + 1)
    return m
  }, [searched, passFilters])

  const filterCounts = useMemo(() => {
    const inCat = searched.filter((i) => cat === 'All' || (i.category || 'OTHER') === cat)
    return {
      inStock: inCat.filter((i) => !isSoldOut(i)).length,
      clearance: inCat.filter((i) => hasBadge(i, 'clearance')).length,
      best: inCat.filter((i) => hasBadge(i, 'best_seller')).length,
    }
  }, [searched, cat])

  const shown = useMemo(() => {
    let r = searched.filter((i) => passFilters(i) && (cat === 'All' || (i.category || 'OTHER') === cat))
    const order = new Map(items.map((i, n) => [i.item_code, n]))
    const shelf = (a: ShopItem, b: ShopItem) => (order.get(a.item_code) ?? 0) - (order.get(b.item_code) ?? 0)
    if (sort === 'price_asc') r = r.slice().sort((a, b) => byPrice(1)(a, b) || shelf(a, b))
    else if (sort === 'price_desc') r = r.slice().sort((a, b) => byPrice(-1)(a, b) || shelf(a, b))
    else if (sort === 'best') r = r.slice().sort((a, b) => Number(hasBadge(b, 'best_seller')) - Number(hasBadge(a, 'best_seller')) || shelf(a, b))
    else if (sort === 'newest') r = r.slice().sort((a, b) => Number(hasBadge(b, 'new')) - Number(hasBadge(a, 'new')) || shelf(a, b))
    // stable partition: what can be sold today, then the sold-out lines, each in the order above
    return [...r.filter((i) => !isSoldOut(i)), ...r.filter((i) => isSoldOut(i))]
  }, [searched, passFilters, cat, sort, items])

  const firstOut = shown.findIndex((i) => isSoldOut(i))
  const soldCount = firstOut < 0 ? 0 : shown.length - firstOut
  const filterKey = `${cat}|${q}|${JSON.stringify(filters)}|${sort}`
  const gridLimit = gridN.key === filterKey ? gridN.n : GRID_CHUNK
  // the highlighted row follows the search: a new query starts on its best line
  const [hlKey, setHlKey] = useState(filterKey)
  if (hlKey !== filterKey) {
    setHlKey(filterKey)
    setHighlight(0)
  }
  const activeFilters = Number(filters.inStock) + Number(filters.clearance) + Number(filters.best) + Number(sort !== 'featured')

  /* ── the live quote (the bar, the slip and the checkout all show the server's total) ── */
  const [coupon, setCoupon] = useState('')
  const fetchQuote = useCallback<QuoteFetcher>((body, signal) => postStaffQuote(body, signal), [])
  const { quote, quoting, error: quoteError } = useQuote(true, cart.lines, coupon, '', fetchQuote)
  const estimate = useMemo(() => cart.lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0), [cart.lines, itemsByCode])
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate

  /* ── actions ── */
  const addLine = useCallback((it: ShopItem, qty?: number | null) => cart.set(it.item_code, Math.max(minQtyOf(it), qty || 0)), [cart])

  const runQuickAdd = () => {
    const added: string[] = []
    const soldOut: string[] = []
    const left: string[] = []
    for (const r of quickRows) {
      if (r.item) {
        addLine(r.item, r.qty)
        added.push(r.item.item_code)
      } else if (r.soldOut) soldOut.push(r.soldOut.item_code)
      else left.push(r.raw)
    }
    setQ(left.join(', '))
    setNotice({
      tone: soldOut.length || left.length ? 'warn' : 'ok',
      text: [
        added.length ? `Added ${added.length} ${added.length === 1 ? 'line' : 'lines'}` : 'Nothing added',
        left.length ? `not a code: ${left.join(', ')} — pick it from the list` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      soldOut,
    })
  }

  const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const max = view === 'list' ? shown.length - 1 : -1
      if (max < 0) return
      const next = Math.max(0, Math.min(max, highlight + (e.key === 'ArrowDown' ? 1 : -1)))
      setHighlight(next)
      document.querySelector(`[data-code="${CSS.escape(shown[next].item_code)}"]`)?.scrollIntoView({ block: 'nearest' })
      return
    }
    if (e.key === 'Escape') {
      setQ('')
      return
    }
    if (e.key !== 'Enter') return
    e.preventDefault()
    if (quick) {
      runQuickAdd()
      return
    }
    if (!q.trim()) return
    // Enter adds the highlighted line only at the desk (the table, where the highlight is drawn). On a
    // phone the keyboard's Go / Search key is how the keyboard is put away — it must not add anything.
    if (!window.matchMedia('(min-width: 1280px)').matches) {
      e.currentTarget.blur()
      return
    }
    const it = shown[highlight]
    if (!it) return
    if (isSoldOut(it)) {
      setNotice({ tone: 'warn', text: `${it.item_code} is sold out — not added. Open it to take a backorder.` })
      return
    }
    // not in the order yet: its minimum; already there: one more step, like "+"
    const cur = cart.qtyOf(it.item_code)
    const next = cur > 0 ? Math.min(9999, cur + stepOf(it)) : minQtyOf(it)
    cart.set(it.item_code, next)
    setNotice({ tone: 'ok', text: `${cur > 0 ? 'Now' : 'Added'} ${it.item_code} × ${next}` })
    setQ('')
  }

  // "/" anywhere on the page (not while typing) jumps to the search — the desk's first key
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (document.querySelector('[role="dialog"]')) return
      e.preventDefault()
      searchRef.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const sheetItem = sheetCode ? itemsByCode.get(sheetCode) || null : null
  const qtyItem = qtyCode ? itemsByCode.get(qtyCode) || null : null
  const sheetPairs = useMemo(() => {
    if (!sheetItem) return []
    const pair = (data?.pairs || []).find((p) => p.item_code === sheetItem.item_code)
    return (pair?.with || []).map((c) => itemsByCode.get(c)).filter((x): x is ShopItem => Boolean(x))
  }, [sheetItem, data, itemsByCode])

  useSeo({ title: 'Catalog · YQ Bahrain', items: null, allowBackorder })

  /* ── screens ── */
  if (catalogQ.isError && !data) {
    return (
      <div className="mx-auto max-w-md px-4 py-16 text-center">
        <h1 className="font-display text-[18px] font-bold text-[#1A1428]">The catalog did not load</h1>
        <p className="mt-1.5 text-[13px] leading-snug text-[#6b6480]">The price server did not answer. Check your connection and try again.</p>
        <button type="button" onClick={() => void catalogQ.refetch()} className={cn('mt-5 inline-flex h-11 items-center rounded-xl bg-[#6D4091] px-5 text-[13px] font-semibold text-white hover:bg-[#5A3478]', RING)}>
          Try again
        </button>
      </div>
    )
  }

  if (order) {
    return (
      <div className="bg-[#F9F7F3]">
        <OrderSuccess
          order={order.res}
          mode="salesman"
          customer={order.customer}
          onContinue={() => {
            setOrder(null)
            window.scrollTo({ top: 0 })
          }}
        />
      </div>
    )
  }

  const age = stockAge(data?.stock_as_of, data?.stock_fresh)
  const basket = basketQ.data
  const available = basket?.available || []
  const restockWa = basket?.restock_link ? waLink(basket.shop.phone, restockText(basket.restock_link, basket.shop.contact_name)) || `https://wa.me/?text=${encodeURIComponent(restockText(basket.restock_link, basket.shop.contact_name))}` : null
  const hasCart = cart.lines.length > 0
  const cats = ['All', ...(data?.categories || []).filter((c) => (catCounts.get(c) || 0) > 0 || c === cat)]

  return (
    <div className="bg-[#F9F7F3] text-[#1A1428]" style={hasCart ? { paddingBottom: '8.5rem' } : undefined}>
      <div className="mx-auto max-w-[1600px] px-4 lg:px-6 xl:grid xl:grid-cols-[minmax(0,1fr)_360px] xl:gap-6 xl:pb-10">
        <div className="min-w-0">
          {/* ── who this order is for (the slip carries it from 1280 px) ── */}
          <div className="pt-3 xl:hidden">
            <ShopPill selected={sel} row={row} onOpen={() => (sel ? setShopSheetOpen(true) : setPickerOpen(true))} />
          </div>

          {sel && basketKey && (
            <div className="mt-2 flex gap-2 xl:mt-4">
              <button
                type="button"
                disabled={!available.length}
                onClick={() => basket && preload(basket)}
                className={cn(
                  'inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-xl px-3 text-[13px] font-semibold transition duration-150 ease-out sm:flex-none sm:px-4',
                  RING,
                  available.length ? 'bg-[#6D4091] text-white hover:bg-[#5A3478]' : 'cursor-not-allowed bg-[#f0eef6] text-[#a8a2bb]',
                )}
              >
                <RotateCcw size={15} aria-hidden="true" />
                {basketQ.isLoading ? 'Order again…' : available.length ? `Order again · ${available.length} ${available.length === 1 ? 'line' : 'lines'}` : 'No usual order yet'}
              </button>
              {restockWa && (
                <a href={restockWa} target="_blank" rel="noreferrer" className={cn('inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-[#bfe8cf] bg-white px-3 text-[13px] font-semibold text-[#137a48] hover:bg-[#f2fbf5] sm:px-4', RING)}>
                  <MessageCircle size={15} aria-hidden="true" />
                  <span className="sm:hidden">Restock link</span>
                  <span className="hidden sm:inline">Send restock link</span>
                </a>
              )}
              <button type="button" onClick={() => setShopSheetOpen(true)} className={cn('hidden h-11 items-center rounded-xl border border-[#E2DCEA] bg-white px-3.5 text-[13px] font-semibold text-[#1A1428] hover:bg-[#f7f5fb] xl:inline-flex', RING)}>
                Their usual order
              </button>
            </div>
          )}

          {notice && (
            <div role="status" className={cn('mt-2 flex items-start gap-2 rounded-xl px-3 py-2 text-[12px] leading-snug', notice.tone === 'ok' ? 'bg-[#e8f7ee] text-[#137a48]' : 'bg-[#fdf3e3] text-[#96600d]')}>
              <span className="min-w-0 flex-1">
                {notice.text}
                {notice.soldOut?.length ? <> · <b className="font-semibold">Sold out, not added: {notice.soldOut.join(', ')}</b></> : null}
              </span>
              <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" className="-my-1 -mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-lg hover:bg-black/5">
                <X size={13} aria-hidden="true" />
              </button>
            </div>
          )}

          {age && <p className={cn('mt-2 text-[11.5px]', age.stale ? 'font-semibold text-amber-700' : 'text-[#6b6480]')}>{age.label}{age.stale ? ' — check before promising a quantity' : ''}</p>}

          {/* ── the tools, pinned: one field for search and quick add, one chip row ── */}
          <div className="sticky z-20 -mx-4 mt-2 border-b border-[#E9E4EF] bg-[#F9F7F3]/95 px-4 pb-2 pt-2 backdrop-blur-md lg:-mx-6 lg:px-6 xl:mx-0 xl:rounded-b-2xl xl:px-0" style={{ top: TOPBAR }}>
            <div className="relative">
              <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[#a8a2bb]" aria-hidden="true" />
              <input
                ref={searchRef}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={onSearchKey}
                placeholder="Search, or add a list: C18 3, UK15 6"
                aria-label="Search the catalog, or type codes and quantities to add them"
                aria-describedby="yq-staff-search-hint"
                type="search"
                enterKeyHint={quick ? 'done' : 'search'}
                autoComplete="off"
                className="h-11 w-full rounded-xl border border-[#E2DCEA] bg-white pl-10 pr-11 text-[15px] text-[#1A1428] outline-none transition duration-150 ease-out placeholder:text-[#a8a2bb] hover:border-[#CFC3DE] focus:border-[#6D4091] focus:ring-2 focus:ring-[#6D4091]/15 [&::-webkit-search-cancel-button]:hidden"
              />
              {q ? (
                <button type="button" onClick={() => setQ('')} aria-label="Clear search" className={cn('absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-[#6b6480] hover:bg-[#F3F0F6]', RING)}>
                  <X size={15} />
                </button>
              ) : (
                <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded-md border border-[#E2DCEA] bg-[#F9F7F3] px-1.5 py-0.5 font-sans text-[11px] text-[#6b6480] xl:block">/</kbd>
              )}
            </div>
            <p id="yq-staff-search-hint" className="sr-only">
              Enter adds the highlighted line; a list like C18 3, UK15 6 adds every exact code. Arrow keys move the highlight.
            </p>
            {quick && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5" aria-live="polite">
                {quickRows.map((r, n) => (
                  <span
                    key={`${r.raw}-${n}`}
                    className={cn(
                      'rounded-lg px-2 py-1 text-[11.5px] font-semibold tabular-nums',
                      r.item ? 'bg-[#EEE8F4] text-[#5A3478]' : r.soldOut ? 'bg-[#fdecef] text-[#9f1239]' : 'bg-[#f4f3f8] text-[#6b6480]',
                    )}
                  >
                    {r.item ? `${r.item.item_code} × ${Math.max(minQtyOf(r.item), r.qty || 0)}` : r.soldOut ? `${r.soldOut.item_code} sold out` : `${r.raw} — not a code`}
                  </span>
                ))}
                <span className="text-[11.5px] text-[#6b6480]">
                  {quickRows.some((r) => r.item) ? 'Enter adds the exact codes' : 'No exact code yet — pick from the list below'}
                </span>
              </div>
            )}

            <div className="-mx-4 mt-2 flex items-center gap-1.5 overflow-x-auto px-4 pb-0.5 lg:-mx-6 lg:px-6 xl:mx-0 xl:px-0">
              {cats.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setCat(c)}
                  aria-pressed={cat === c}
                  className={cn(
                    'inline-flex h-9 shrink-0 items-center gap-1 rounded-full border px-3.5 text-[12px] font-medium capitalize transition duration-150 ease-out',
                    RING,
                    cat === c ? 'border-[#6D4091] bg-[#6D4091] text-white' : 'border-[#E2DCEA] bg-white text-[#6b6480] hover:border-[#CFC3DE] hover:bg-[#f7f5fb]',
                  )}
                >
                  {c.toLowerCase()}
                  {c !== 'All' && <span className={cn('text-[10.5px] tabular-nums', cat === c ? 'text-white/75' : 'text-[#a8a2bb]')}>{catCounts.get(c) || 0}</span>}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setFiltersOpen(true)}
                aria-haspopup="dialog"
                className={cn(
                  'inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border px-3.5 text-[12px] font-semibold transition duration-150 ease-out',
                  RING,
                  activeFilters ? 'border-[#6D4091] bg-[#EEE8F4] text-[#5A3478]' : 'border-[#E2DCEA] bg-white text-[#1A1428] hover:bg-[#f7f5fb]',
                )}
              >
                <SlidersHorizontal size={13} aria-hidden="true" /> Filters{activeFilters ? ` · ${activeFilters}` : ''}
              </button>
              <span className="ml-auto flex shrink-0 rounded-full border border-[#E2DCEA] bg-white p-0.5" role="group" aria-label="Show as">
                {(['list', 'grid'] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setView(v)}
                    aria-pressed={view === v}
                    aria-label={v === 'list' ? 'List' : 'Grid'}
                    title={v === 'list' ? 'List — for ordering' : 'Grid — for showing a customer'}
                    className={cn('grid h-8 w-9 place-items-center rounded-full transition duration-150 ease-out', RING, view === v ? 'bg-[#6D4091] text-white' : 'text-[#6b6480] hover:text-[#1A1428]')}
                  >
                    {v === 'list' ? <List size={15} aria-hidden="true" /> : <LayoutGrid size={15} aria-hidden="true" />}
                  </button>
                ))}
              </span>
            </div>
          </div>

          {/* ── results ── */}
          <main className="pb-6 pt-3">
            <p aria-live="polite" className="mb-2 text-[12px] text-[#6b6480]">
              {data ? (
                <>
                  <b className="font-semibold tabular-nums text-[#1A1428]">{shown.length}</b> {shown.length === 1 ? 'product' : 'products'}
                  {shown.length !== items.length ? ` of ${items.length}` : ''}
                </>
              ) : (
                'Loading the price list…'
              )}
            </p>

            {!data ? (
              <div className="overflow-hidden rounded-2xl border border-[#E9E4EF] bg-white">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="flex h-[72px] items-center gap-3 border-b border-[#F3F0F6] px-3 last:border-0">
                    <div className="h-14 w-14 animate-pulse rounded-xl bg-[#F3F0F6]" />
                    <div className="flex-1 space-y-2">
                      <div className="h-3 w-1/3 animate-pulse rounded bg-[#f0eef6]" />
                      <div className="h-2.5 w-2/3 animate-pulse rounded bg-[#F3F0F6]" />
                    </div>
                  </div>
                ))}
              </div>
            ) : shown.length === 0 ? (
              <div className="rounded-2xl border border-[#E9E4EF] bg-white px-6 py-14 text-center">
                <p className="font-display text-[15px] font-bold text-[#1A1428]">{quick ? 'No exact code in that list' : 'Nothing matches that'}</p>
                <p className="mt-1 text-[12.5px] text-[#6b6480]">{quick ? 'Type the codes as they are printed, or search one at a time.' : 'Try another code or word, or clear the filters.'}</p>
                <button
                  type="button"
                  onClick={() => {
                    setQ('')
                    setCat('All')
                    setFilters(NO_FILTERS)
                    setSort('featured')
                  }}
                  className={cn('mt-5 inline-flex h-11 items-center rounded-xl border border-[#E2DCEA] bg-white px-5 text-[13px] font-semibold text-[#1A1428] hover:bg-[#f7f5fb]', RING)}
                >
                  Clear search and filters
                </button>
              </div>
            ) : view === 'list' ? (
              <div className="overflow-hidden rounded-2xl border border-[#E9E4EF] bg-white">
                <div className="hidden grid-cols-[40px_minmax(0,1fr)_104px_112px_80px_44px] gap-4 border-b border-[#F3F0F6] bg-[#FBFAF7] px-4 py-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6b6480] xl:grid">
                  <span />
                  <span>Product</span>
                  <span>Stock</span>
                  <span className="text-right">Price</span>
                  <span className="text-center">Qty</span>
                  <span />
                </div>
                <div className="divide-y divide-[#F3F0F6]">
                  {shown.map((it, i) => (
                    <div key={it.item_code}>
                      {i === firstOut && <SoldOutDivider count={soldCount} className="bg-[#FBFAF7] px-4" />}
                      <ProductRow
                        item={it}
                        qty={cart.qtyOf(it.item_code)}
                        allowBackorder={allowBackorder}
                        highlighted={Boolean(q.trim()) && !quick && i === highlight}
                        eagerImage={i < 8}
                        onOpen={() => setSheetCode(it.item_code)}
                        onSetQty={(n) => (n > 0 ? cart.set(it.item_code, n) : cart.remove(it.item_code))}
                        onQtyClick={() => setQtyCode(it.item_code)}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-5 min-[1800px]:grid-cols-6">
                  {shown.slice(0, gridLimit).flatMap((it, i) => {
                    const card = (
                      <ProductCard
                        key={it.item_code}
                        item={it}
                        qty={cart.qtyOf(it.item_code)}
                        allowBackorder={allowBackorder}
                        showCompare={settings.show_retail_compare !== false}
                        eagerImage={i < 4}
                        onOpen={() => setSheetCode(it.item_code)}
                        onAdd={() => addLine(it)}
                        onSetQty={(n) => cart.set(it.item_code, n)}
                        onRemove={() => cart.remove(it.item_code)}
                        onQtyClick={() => setQtyCode(it.item_code)}
                      />
                    )
                    return i === firstOut ? [<SoldOutDivider key="sold-out" count={soldCount} grid />, card] : [card]
                  })}
                </div>
                {gridLimit < shown.length && (
                  <div className="mt-6 text-center">
                    <button type="button" onClick={() => setGridN({ key: filterKey, n: gridLimit + GRID_CHUNK })} className={cn('h-11 rounded-xl border border-[#E2DCEA] bg-white px-6 text-[13px] font-semibold text-[#1A1428] hover:bg-[#f7f5fb]', RING)}>
                      Show more ({shown.length - gridLimit} left)
                    </button>
                  </div>
                )}
              </>
            )}
            <p className="py-8 text-center text-[11px] text-[#6b6480]">Trade prices · the exact free-to-sell number is in each product&apos;s sheet</p>
          </main>
        </div>

        {/* ── the docked order slip (1280 px and up) ── */}
        <div className="hidden pt-4 xl:block">
          <OrderSlip
            selected={sel}
            row={row}
            cart={cart}
            itemsByCode={itemsByCode}
            quote={quote}
            quoting={quoting}
            total={total}
            minBhd={minBhd}
            onPickShop={() => (sel ? setShopSheetOpen(true) : setPickerOpen(true))}
            onPlace={() => setCartOpen(true)}
          />
        </div>
      </div>

      {/* ── the order bar, in the thumb zone (under 1280 px) ── */}
      {hasCart && (
        <div className="fixed right-0 z-30 border-t border-[#E9E4EF] bg-white/95 px-4 pb-2.5 pt-2 backdrop-blur-md xl:hidden" style={{ bottom: TABBAR, left: 'var(--yq-sidebar, 0px)' }}>
          <div className="mx-auto max-w-[1600px]">
            <MinimumRuler total={total} minBhd={minBhd} quote={quote} className="mb-2" />
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[11.5px] text-[#6b6480]">
                  {sel ? <b className="font-semibold text-[#1A1428]">{sel.shop || sel.name}</b> : 'No shop picked yet'} · <span className="tabular-nums">{cart.items}</span> {cart.items === 1 ? 'line' : 'lines'} · <span className="tabular-nums">{cart.units}</span> pcs
                </div>
                <div className="font-display text-[17px] font-extrabold leading-tight tracking-[-0.015em] tabular-nums text-[#1A1428]">{bhd(total)}</div>
              </div>
              <button type="button" onClick={() => setCartOpen(true)} className={cn('flex h-12 shrink-0 items-center gap-2 rounded-xl bg-[#6D4091] px-5 text-[14px] font-semibold text-white hover:bg-[#5A3478] active:scale-[.99]', RING)}>
                <ShoppingBag size={17} aria-hidden="true" /> Review
              </button>
            </div>
          </div>
        </div>
      )}

      <ShopPickerSheet open={pickerOpen} onClose={() => { setPickerOpen(false); setReturnToCart(false) }} onPick={(c) => pickShop(c)} selectedKey={shopKey} />
      <ShopSheet
        shop={sel ? (row || { key: shopKey, name: sel.shop || sel.name, phone: sel.phone || null, contact_name: sel.shop ? sel.name : null, area: sel.area || null, focus_name: sel.focus_name || null }) : null}
        open={shopSheetOpen && Boolean(sel)}
        onClose={() => setShopSheetOpen(false)}
        onChangeShop={() => {
          setShopSheetOpen(false)
          setPickerOpen(true)
        }}
        onOrderAgain={preload}
      />
      <FiltersSheet open={filtersOpen} onClose={() => setFiltersOpen(false)} filters={filters} onFilters={setFilters} sort={sort} onSort={setSort} counts={filterCounts} />

      <ProductSheet
        item={sheetItem}
        token=""
        allowBackorder={allowBackorder}
        showCompare={settings.show_retail_compare !== false}
        canShare={false}
        qty={sheetItem ? cart.qtyOf(sheetItem.item_code) : 0}
        pairs={sheetPairs}
        onAdd={(it) => addLine(it)}
        onSetQty={(it, n) => cart.set(it.item_code, n)}
        onRemove={(it) => cart.remove(it.item_code)}
        onOpenItem={setSheetCode}
        onClose={() => setSheetCode(null)}
        onQtyClick={(it) => setQtyCode(it.item_code)}
      />
      {qtyItem && (
        <QtySheet
          item={qtyItem}
          value={cart.qtyOf(qtyItem.item_code)}
          onApply={(n) => cart.set(qtyItem.item_code, n)}
          onRemove={() => cart.remove(qtyItem.item_code)}
          onClose={() => setQtyCode(null)}
        />
      )}

      {data && (
        <CartDrawer
          open={cartOpen}
          onClose={() => setCartOpen(false)}
          mode="salesman"
          token=""
          data={data}
          itemsByCode={itemsByCode}
          cart={cart}
          quote={quote}
          quoting={quoting}
          quoteError={quoteError}
          coupon={coupon}
          onCouponChange={setCoupon}
          src="salesman"
          sessionId=""
          onChangeShop={() => {
            setCartOpen(false)
            setReturnToCart(true)
            setPickerOpen(true)
          }}
          onSuccess={(o, who) => {
            setOrder({ res: o, customer: who })
            setCartOpen(false)
            cart.clear()
            setCoupon('')
            setNotice(null)
            void qc.invalidateQueries({ queryKey: ['shop-today'] })
            void qc.invalidateQueries({ queryKey: ['shop-book'] })
            void qc.invalidateQueries({ queryKey: ['shop-orders-new-count'] })
            if (o.duplicate) toast('This order had already been placed — here it is.', 'success')
            window.scrollTo({ top: 0 })
          }}
        />
      )}
    </div>
  )
}

export default StaffCatalog
