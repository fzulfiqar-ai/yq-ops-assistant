import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { Clock, Search, ShoppingBag, X } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { useCart } from '@/lib/cart'
import { useSeo } from '@/lib/seo'
import {
  getCatalog,
  getStaffCatalog,
  pingEvent,
  pingVisit,
  postQuote,
  postStaffQuote,
  type CatalogPayload,
  type Offer,
  type OrderResponse,
  type ShopItem,
} from '@/lib/shopApi'
import { CartDrawer } from './CartDrawer'
import { OrderSuccess } from './OrderSuccess'
import { ProductCard } from './ProductCard'
import { ProductSheet } from './ProductSheet'
import { Select } from './Select'
import { bhd, fmtDate, hasBadge, minQtyOf, RING, sessionId, useCountdown } from './shared'
import { useQuote, type QuoteFetcher } from './useQuote'

/**
 * The shop — one page, two lives.
 *
 *  • mode="public"   /c/{token}. The customer-facing catalog behind every link a
 *    salesman sends on WhatsApp. Browse, price, fill a cart, send it to a human.
 *  • mode="salesman" /shop, inside the portal shell. The same catalog with the
 *    real stock numbers on it, so a salesman standing in a shop can answer "how
 *    many do you have?" and place the order there and then, on his phone.
 *
 * Nothing here is authoritative: the server prices the cart and re-checks stock
 * (docs/SHOP.md). This page's job is to be fast on a phone, honest about stock,
 * and to end in a conversation.
 */

const CHUNK = 48

/* The logged-in shell pins a 56px bar to the top and a tab bar to the bottom
   (--yq-tabbar: 64px on a phone, 0 from 768px up). Everything this page pins
   has to clear them, so both offsets are read from the shell's own variables
   with the shell's measurements as the fallback. */
const TOPBAR = 'var(--yq-topbar, 56px)'
const TABBAR = 'calc(var(--yq-tabbar, 64px) + env(safe-area-inset-bottom, 0px))'
const SAFE = 'env(safe-area-inset-bottom, 0px)'

type SortKey = 'featured' | 'best' | 'price_asc' | 'price_desc' | 'newest'

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'featured', label: 'Featured' },
  { value: 'best', label: 'Best selling' },
  { value: 'price_asc', label: 'Price: low to high' },
  { value: 'price_desc', label: 'Price: high to low' },
  { value: 'newest', label: 'Newest' },
]

function OfferCard({ offer }: { offer: Offer }) {
  const countdown = useCountdown(offer.ends_at)
  return (
    <div className="w-[15.5rem] shrink-0 rounded-[18px] border border-[#ece9f3] bg-white p-4 shadow-[0_1px_2px_rgba(24,16,48,.04)] sm:w-auto">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-display text-[13px] font-bold leading-tight text-[#1a1430]">{offer.name}</h3>
        {offer.coupon_code && <Badge tone="accent">{offer.coupon_code}</Badge>}
      </div>
      {offer.summary && <p className="mt-1 text-[11.5px] leading-snug text-[#6b6480]">{offer.summary}</p>}
      {countdown && (
        <p className="mt-2.5 inline-flex items-center gap-1 text-[11px] font-semibold tabular-nums text-[#6b6480]">
          <Clock size={12} aria-hidden="true" /> Ends in {countdown}
        </p>
      )}
    </div>
  )
}

function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={cn(
        'h-9 shrink-0 rounded-full border px-3.5 text-[12px] font-medium transition duration-150 ease-out',
        RING,
        on
          ? 'border-[#6d28d9] bg-[#f3eefc] text-[#6d28d9]'
          : 'border-[#e4e0ee] bg-white text-[#6b6480] hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
      )}
    >
      {children}
    </button>
  )
}

/** A card-shaped placeholder, so the first paint has the same rhythm as the last. */
function CardSkeleton() {
  return (
    <div className="overflow-hidden rounded-[20px] border border-[#ece9f3] bg-white">
      <div className="aspect-square w-full animate-pulse bg-[#f4f2f9]" />
      <div className="space-y-2 border-t border-[#f4f2f9] p-3">
        <div className="h-3 w-2/5 animate-pulse rounded bg-[#f0eef6]" />
        <div className="h-2.5 w-full animate-pulse rounded bg-[#f4f2f9]" />
        <div className="h-2.5 w-3/4 animate-pulse rounded bg-[#f4f2f9]" />
        <div className="h-5 w-20 animate-pulse rounded-full bg-[#f4f2f9]" />
        <div className="h-4 w-24 animate-pulse rounded bg-[#f0eef6]" />
        <div className="h-11 w-full animate-pulse rounded-xl bg-[#f4f2f9]" />
      </div>
    </div>
  )
}

export interface ShopPageProps {
  /** "salesman" renders inside the logged-in shell against the bearer endpoints. */
  mode?: 'public' | 'salesman'
}

export function ShopPage({ mode = 'public' }: ShopPageProps) {
  const staff = mode === 'salesman'
  const { token = '' } = useParams()
  const [params] = useSearchParams()
  const refParam = params.get('ref') || ''
  const srcParam = params.get('src') || 'direct'
  const itemParam = params.get('item') || ''

  const [data, setData] = useState<CatalogPayload | null>(null)
  const [err, setErr] = useState(false)
  const [cat, setCat] = useState('All')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('featured')
  const [inStockOnly, setInStockOnly] = useState(false)
  const [offersOnly, setOffersOnly] = useState(false)
  const [bestOnly, setBestOnly] = useState(false)
  const [paging, setPaging] = useState({ key: '', n: CHUNK })
  const [sheetCode, setSheetCode] = useState<string | null>(null)
  const [cartOpen, setCartOpen] = useState(false)
  const [coupon, setCoupon] = useState('')
  const [order, setOrder] = useState<{ res: OrderResponse; customer: { name: string; shop: string } } | null>(null)

  // One cart per link, and one for the salesman — his basket must never be the
  // basket some customer's share token left behind on the same device.
  const cart = useCart(staff ? 'staff' : token)
  const sid = useMemo(() => sessionId(), [])
  const deepLinked = useRef(false)

  const referralCode = data?.ref?.referral_code || (staff ? '' : refParam)

  /* ── load once ──────────────────────────────────────────── */
  useEffect(() => {
    if (!staff && !token) return
    let alive = true
    ;(staff ? getStaffCatalog() : getCatalog(token, refParam))
      .then((d) => {
        if (!alive) return
        setData(d)
        // ?item=CODE deep link (a shared product): open its sheet as soon as the
        // payload lands, so a WhatsApp link lands the customer on the product.
        if (!deepLinked.current && itemParam) {
          deepLinked.current = true
          const match = (d.items || []).find((i) => i.item_code.toLowerCase() === itemParam.toLowerCase())
          if (match) {
            setSheetCode(match.item_code)
            if (!staff) {
              pingEvent(token, {
                event: 'item',
                item_code: match.item_code,
                referral_code: d.ref?.referral_code || refParam,
                src: srcParam,
                session_id: sid,
              })
            }
          }
        }
      })
      .catch(() => {
        if (alive) setErr(true)
      })
    // Funnel pings belong to the public link only — a salesman browsing his own
    // price list is not a marketing session.
    if (!staff) {
      pingVisit(token, srcParam)
      pingEvent(token, { event: 'view', referral_code: refParam, src: srcParam, session_id: sid })
    }
    return () => {
      alive = false
    }
    // one fetch per link — filters and sort are all client-side
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, staff])

  // Memoised so the `|| []` fallback doesn't hand every downstream memo a brand
  // new array on each render.
  const items = useMemo(() => data?.items || [], [data])

  const itemsByCode = useMemo(() => {
    const m = new Map<string, ShopItem>()
    for (const it of items) m.set(it.item_code, it)
    return m
  }, [items])

  /* ── filter + sort ──────────────────────────────────────── */
  const filterKey = `${cat}|${q.trim().toLowerCase()}|${inStockOnly}|${offersOnly}|${bestOnly}|${sort}`

  const filtered = useMemo(() => {
    let r = items
    if (cat !== 'All') r = r.filter((i) => (i.category || 'OTHER') === cat)
    if (q.trim()) {
      const s = q.trim().toLowerCase()
      r = r.filter(
        (i) =>
          i.item_code.toLowerCase().includes(s) ||
          (i.spec || '').toLowerCase().includes(s) ||
          (i.display_name || '').toLowerCase().includes(s),
      )
    }
    if (inStockOnly) r = r.filter((i) => i.stock_status !== 'out_of_stock')
    if (offersOnly) r = r.filter((i) => hasBadge(i, 'on_offer') || i.compare_at_bhd != null || hasBadge(i, 'price_drop'))
    if (bestOnly) r = r.filter((i) => hasBadge(i, 'best_seller'))

    if (sort === 'featured') return r
    const idx = new Map(items.map((i, n) => [i.item_code, n]))
    // Featured order is the tie-break for every other sort, so equal items never
    // shuffle between renders.
    const order0 = (i: ShopItem) => idx.get(i.item_code) ?? 0
    // Items with no price sink to the bottom of BOTH price sorts.
    const SINK = Number.MAX_SAFE_INTEGER
    const asc = (i: ShopItem) => (i.price_bhd == null ? SINK : Number(i.price_bhd))
    const desc = (i: ShopItem) => (i.price_bhd == null ? -SINK : Number(i.price_bhd))
    const copy = r.slice()
    if (sort === 'price_asc') copy.sort((a, b) => asc(a) - asc(b) || order0(a) - order0(b))
    else if (sort === 'price_desc') copy.sort((a, b) => desc(b) - desc(a) || order0(a) - order0(b))
    else if (sort === 'best')
      copy.sort(
        (a, b) =>
          Number(hasBadge(b, 'best_seller')) - Number(hasBadge(a, 'best_seller')) ||
          Number(Boolean(b.social_proof)) - Number(Boolean(a.social_proof)) ||
          order0(a) - order0(b),
      )
    else if (sort === 'newest')
      copy.sort((a, b) => Number(hasBadge(b, 'new')) - Number(hasBadge(a, 'new')) || order0(a) - order0(b))
    return copy
  }, [items, cat, q, inStockOnly, offersOnly, bestOnly, sort])

  // Paging is keyed to the filter: changing a filter shows the first chunk again
  // without an effect that would render the long list twice.
  const visible = paging.key === filterKey ? paging.n : CHUNK
  const showMore = () => setPaging({ key: filterKey, n: visible + CHUNK })

  // No product rail above the grid: every product appears exactly once on this
  // page (owner, 15-Sep — a "Best sellers" row repeating the grid below read as
  // the page glitching). Best sellers are a filter instead, and a badge on the card.
  const offers = data?.offers || []
  const railsVisible = !q.trim() && cat === 'All' && !inStockOnly && !offersOnly && !bestOnly

  const settings = data?.settings || {}
  const allowBackorder = settings.allow_backorder !== false
  const showCompare = settings.show_retail_compare !== false

  /* ── cart actions ───────────────────────────────────────── */
  const addToCart = useCallback(
    (item: ShopItem) => {
      cart.set(item.item_code, minQtyOf(item))
      if (!staff) {
        pingEvent(token, {
          event: 'add',
          item_code: item.item_code,
          referral_code: referralCode,
          src: srcParam,
          session_id: sid,
        })
      }
    },
    [cart, staff, token, referralCode, srcParam, sid],
  )

  const openSheet = useCallback(
    (code: string) => {
      setSheetCode(code)
      if (!staff) {
        pingEvent(token, { event: 'item', item_code: code, referral_code: referralCode, src: srcParam, session_id: sid })
      }
    },
    [staff, token, referralCode, srcParam, sid],
  )

  const openCart = useCallback(() => {
    setCartOpen(true)
    if (!staff) pingEvent(token, { event: 'checkout', referral_code: referralCode, src: srcParam, session_id: sid })
  }, [staff, token, referralCode, srcParam, sid])

  /* ── live quote (drives the drawer AND the sticky bar total) ─────────────
     Hoisted out of the drawer so the bar can show a real, server-priced total
     before the customer ever opens it. Falls back to a list-price estimate
     while the first quote is in flight or the API is unreachable. */
  const fetchQuote = useCallback<QuoteFetcher>(
    (body, signal) => (staff ? postStaffQuote(body, signal) : postQuote(token, body, signal)),
    [staff, token],
  )
  const { quote, quoting, error: quoteError } = useQuote(staff || Boolean(token), cart.lines, coupon, referralCode, fetchQuote)
  const estimate = useMemo(
    () => cart.lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0),
    [cart.lines, itemsByCode],
  )
  const barTotal = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate

  const sheetItem = sheetCode ? itemsByCode.get(sheetCode) || null : null
  const sheetPairs = useMemo(() => {
    if (!sheetItem) return []
    const pair = (data?.pairs || []).find((p) => p.item_code === sheetItem.item_code)
    return (pair?.with || []).map((c) => itemsByCode.get(c)).filter((x): x is ShopItem => Boolean(x))
  }, [sheetItem, data, itemsByCode])

  useSeo({
    title: staff
      ? 'Catalog · YQ Bahrain'
      : sheetItem
        ? `${sheetItem.item_code} — ${bhd(sheetItem.price_bhd)} · YQ Bahrain`
        : `YQ Bahrain — ${data?.brand || 'VFAN'} Trade Catalog`,
    description: staff
      ? undefined
      : sheetItem?.spec
        ? `${sheetItem.item_code} — ${String(sheetItem.spec).replace(/\s+/g, ' ').trim().slice(0, 140)}`
        : `Trade prices on ${items.length || ''} ${data?.brand || 'VFAN'} mobile accessories. Order from your YQ Bahrain salesman — cables, chargers, audio and more, delivered in Bahrain.`,
    // No structured data behind a login — the salesman page is not indexable.
    items: staff ? null : items,
    allowBackorder,
  })

  /* ── screens ────────────────────────────────────────────── */
  if (err) {
    if (staff) {
      return (
        <div className="mx-auto max-w-md px-4 py-16 text-center">
          <h1 className="font-display text-[18px] font-bold text-[#1a1430]">The catalog did not load</h1>
          <p className="mt-1.5 text-[13px] leading-snug text-[#6b6480]">
            The price server did not answer. Check your connection and try again.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className={cn(
              'mt-5 inline-flex h-11 items-center rounded-xl bg-[#6d28d9] px-5 text-[13px] font-semibold text-white transition duration-150 ease-out hover:bg-[#5b21b6]',
              RING,
            )}
          >
            Try again
          </button>
        </div>
      )
    }
    return (
      <div className="grid min-h-screen place-items-center bg-[#140f24] px-4 text-center text-white/80">
        <div>
          <Logo className="mx-auto h-14 w-14 rounded-2xl" />
          <p className="mt-4 text-sm">
            This catalog link is no longer valid.
            <br />
            Please ask your YQ Bahrain contact for a new one.
          </p>
        </div>
      </div>
    )
  }

  if (order) {
    return (
      <div className={cn(staff && 'bg-[#faf9fc]')}>
        <OrderSuccess
          order={order.res}
          mode={mode}
          customer={order.customer}
          onContinue={() => {
            setOrder(null)
            window.scrollTo({ top: 0 })
          }}
        />
      </div>
    )
  }

  const updated = fmtDate(data?.prices_updated)
  const stockAsOf = fmtDate(data?.stock_as_of)
  const shown = filtered.slice(0, visible)

  const hasCart = cart.lines.length > 0
  // Reserve room for whatever is pinned to the bottom of the viewport. In the
  // shell that is the cart bar only — the shell's own <main> already pads for the
  // tab bar, so adding it again would leave a dead band under the last row.
  const pagePad = staff
    ? hasCart
      ? '6rem'
      : undefined
    : hasCart
      ? `calc(${SAFE} + 6.5rem)`
      : undefined

  return (
    <div className={cn('bg-[#faf9fc] text-[#1a1430]', !staff && 'min-h-screen')}>
      {/* ── masthead ──
             Public: identity plus the two dates the trade actually asks about.
             Salesman: the shell already owns the page title and the user, so all
             that is left worth saying is which prices and which stock these are.
             Either way it scrolls away — only the tools below stay pinned. ── */}
      {staff ? (
        <div className="mx-auto max-w-6xl px-4 pb-1 pt-3">
          <p className="text-[12px] leading-snug text-[#6b6480]">
            Trade prices · live stock
            {stockAsOf && <span className="text-[#a8a2bb]"> · as of {stockAsOf}</span>}
          </p>
        </div>
      ) : (
        <div className="mx-auto max-w-6xl px-4 pb-3 pt-4 sm:pt-6">
          <div className="flex items-start gap-3">
            <Logo className="h-11 w-11 shrink-0 rounded-[14px]" />
            <div className="min-w-0 flex-1">
              <h1 className="font-display text-[20px] font-bold leading-tight tracking-[-0.02em] text-[#1a1430] sm:text-[22px]">
                YQ Bahrain
              </h1>
              <p className="mt-0.5 text-[12px] leading-snug text-[#6b6480]">Mobile accessories · trade price list</p>
            </div>
            {updated && (
              <span className="mt-0.5 shrink-0 rounded-full bg-[#f3eefc] px-2.5 py-1 text-[10.5px] font-semibold text-[#6d28d9]">
                Prices {updated}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── the tools, pinned: search first, then the categories.
             In the shell this hangs under its 56px top bar and must stay below
             its z-30; standalone it owns the top of the window. ── */}
      <div
        className="sticky z-20 border-b border-[#ece9f3] bg-[#faf9fc]/92 backdrop-blur-md"
        style={{ top: staff ? TOPBAR : 0 }}
      >
        <div className="mx-auto max-w-6xl px-4 pb-2 pt-2.5">
          <div className="relative">
            <Search
              size={16}
              className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[#a8a2bb]"
              aria-hidden="true"
            />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search a code or a spec…"
              aria-label="Search the catalog"
              type="search"
              className={cn(
                'h-11 w-full rounded-xl border border-[#e4e0ee] bg-white pl-10 pr-11 text-[14px] text-[#1a1430] outline-none transition duration-150 ease-out placeholder:text-[#a8a2bb] hover:border-[#d9d2ee] focus:border-[#6d28d9] focus:ring-2 focus:ring-[#6d28d9]/15',
                '[&::-webkit-search-cancel-button]:hidden',
              )}
            />
            {q && (
              <button
                type="button"
                onClick={() => setQ('')}
                aria-label="Clear search"
                className={cn(
                  'absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-[#6b6480] transition duration-150 ease-out hover:bg-[#f4f2f9] hover:text-[#1a1430]',
                  RING,
                )}
              >
                <X size={15} />
              </button>
            )}
          </div>

          <div className="-mx-4 mt-2 flex gap-1.5 overflow-x-auto px-4 pb-1">
            {['All', ...(data?.categories || [])].map((c) => (
              <button
                key={c}
                onClick={() => setCat(c)}
                aria-pressed={cat === c}
                className={cn(
                  'h-8 shrink-0 rounded-full border px-3.5 text-[12px] font-medium capitalize transition duration-150 ease-out',
                  RING,
                  cat === c
                    ? 'border-[#6d28d9] bg-[#6d28d9] text-white'
                    : 'border-[#e4e0ee] bg-white text-[#6b6480] hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
                )}
              >
                {c.toLowerCase()}
              </button>
            ))}
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-6xl px-4 py-4" style={pagePad ? { paddingBottom: pagePad } : undefined}>
        {/* ── count + the two filters that matter + sort ── */}
        <div className="flex flex-wrap items-center gap-2">
          <p aria-live="polite" className="text-[12px] text-[#6b6480]">
            {data ? (
              <>
                <b className="font-semibold tabular-nums text-[#1a1430]">{filtered.length}</b>{' '}
                {filtered.length === 1 ? 'product' : 'products'}
                {filtered.length !== items.length ? ` of ${items.length}` : ''}
              </>
            ) : (
              'Loading the price list…'
            )}
          </p>
          <div className="ml-auto flex items-center gap-2 overflow-x-auto">
            <Toggle on={inStockOnly} onClick={() => setInStockOnly((v) => !v)}>
              In stock
            </Toggle>
            <Toggle on={bestOnly} onClick={() => setBestOnly((v) => !v)}>
              Best sellers
            </Toggle>
            <Toggle on={offersOnly} onClick={() => setOffersOnly((v) => !v)}>
              On offer
            </Toggle>
            <label htmlFor="yq-sort" className="sr-only">
              Sort products
            </label>
            <Select id="yq-sort" shape="pill" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {/* ── rails ── */}
        {railsVisible && offers.length > 0 && (
          <section className="mt-6" aria-labelledby="yq-offers">
            <h2 id="yq-offers" className="font-display text-[14px] font-bold tracking-[-0.01em] text-[#1a1430]">
              Offers
            </h2>
            <div className="-mx-4 mt-3 flex gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 lg:grid-cols-3">
              {offers.map((o) => (
                <OfferCard key={o.id} offer={o} />
              ))}
            </div>
          </section>
        )}

        {/* ── grid ── */}
        <section className="mt-6" aria-label="Products">
          {!data ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <CardSkeleton key={i} />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-[20px] border border-[#ece9f3] bg-white px-6 py-16 text-center">
              <p className="font-display text-[15px] font-bold text-[#1a1430]">Nothing matches that</p>
              <p className="mt-1 text-[12.5px] text-[#6b6480]">Try a different code, or clear the filters.</p>
              <button
                type="button"
                onClick={() => {
                  setQ('')
                  setCat('All')
                  setInStockOnly(false)
                  setOffersOnly(false)
                  setBestOnly(false)
                }}
                className={cn(
                  'mt-5 inline-flex h-11 items-center rounded-xl border border-[#e4e0ee] bg-white px-5 text-[13px] font-semibold text-[#1a1430] transition duration-150 ease-out hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
                  RING,
                )}
              >
                Clear filters
              </button>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4">
                {shown.map((it, i) => (
                  <ProductCard
                    key={it.item_code}
                    item={it}
                    qty={cart.qtyOf(it.item_code)}
                    allowBackorder={allowBackorder}
                    showCompare={showCompare}
                    eagerImage={i < 4}
                    onOpen={() => openSheet(it.item_code)}
                    onAdd={() => addToCart(it)}
                    onSetQty={(n) => cart.set(it.item_code, n)}
                    onRemove={() => cart.remove(it.item_code)}
                  />
                ))}
              </div>
              {visible < filtered.length && (
                <div className="mt-7 text-center">
                  <button
                    type="button"
                    onClick={showMore}
                    className={cn(
                      'h-11 rounded-xl border border-[#e4e0ee] bg-white px-6 text-[13px] font-semibold text-[#1a1430] transition duration-150 ease-out hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
                      RING,
                    )}
                  >
                    Show more ({filtered.length - visible} left)
                  </button>
                </div>
              )}
            </>
          )}
        </section>

        <footer className="py-10 text-center text-[11px] leading-relaxed text-[#6b6480]">
          <div>YQ Bahrain W.L.L · Trade prices — may change without notice.</div>
          {stockAsOf && <div className="mt-0.5">Stock as of {stockAsOf}</div>}
        </footer>
      </main>

      {/* ── sticky order bar, in the thumb zone ── */}
      {hasCart && (
        <div
          className="fixed inset-x-0 z-30 border-t border-[#ece9f3] bg-white/95 px-4 pt-2.5 backdrop-blur-md"
          style={
            staff
              ? { bottom: TABBAR, paddingBottom: '0.625rem' }
              : { bottom: 0, paddingBottom: `max(0.625rem, ${SAFE})` }
          }
        >
          <div className="mx-auto flex max-w-6xl items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-[11.5px] text-[#6b6480]">
                <span className="tabular-nums">{cart.items}</span> {cart.items === 1 ? 'item' : 'items'} ·{' '}
                <span className="tabular-nums">{cart.units}</span> units
              </div>
              <div className="font-display text-[17px] font-extrabold leading-tight tracking-[-0.015em] tabular-nums text-[#1a1430]">
                {bhd(barTotal)}
              </div>
            </div>
            <button
              type="button"
              onClick={openCart}
              className={cn(
                'flex h-12 shrink-0 items-center gap-2 rounded-xl bg-[#6d28d9] px-5 text-[14px] font-semibold text-white transition duration-150 ease-out hover:bg-[#5b21b6] active:scale-[.99]',
                RING,
              )}
            >
              <ShoppingBag size={17} aria-hidden="true" /> {staff ? 'Review' : 'Review order'}
            </button>
          </div>
        </div>
      )}

      <ProductSheet
        item={sheetItem}
        token={token}
        referralCode={referralCode}
        allowBackorder={allowBackorder}
        showCompare={showCompare}
        canShare={!staff}
        qty={sheetItem ? cart.qtyOf(sheetItem.item_code) : 0}
        pairs={sheetPairs}
        onAdd={addToCart}
        onSetQty={(it, n) => cart.set(it.item_code, n)}
        onRemove={(it) => cart.remove(it.item_code)}
        onOpenItem={openSheet}
        onClose={() => setSheetCode(null)}
      />

      {data && (
        <CartDrawer
          open={cartOpen}
          onClose={() => setCartOpen(false)}
          mode={mode}
          token={token}
          data={data}
          itemsByCode={itemsByCode}
          cart={cart}
          quote={quote}
          quoting={quoting}
          quoteError={quoteError}
          coupon={coupon}
          onCouponChange={setCoupon}
          src={srcParam}
          sessionId={sid}
          onSuccess={(o, who) => {
            setOrder({ res: o, customer: who })
            setCartOpen(false)
            cart.clear()
            setCoupon('')
            window.scrollTo({ top: 0 })
          }}
        />
      )}
    </div>
  )
}

export default ShopPage
