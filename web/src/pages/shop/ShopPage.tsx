import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { Clock, MessageCircle, Search, ShoppingBag, X } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { useCart } from '@/lib/cart'
import { useSeo } from '@/lib/seo'
import {
  getCatalog,
  pingEvent,
  pingVisit,
  type CatalogPayload,
  type Offer,
  type OrderResponse,
  type ShopItem,
} from '@/lib/shopApi'
import { CartDrawer } from './CartDrawer'
import { OrderSuccess } from './OrderSuccess'
import { ProductCard } from './ProductCard'
import { ProductSheet } from './ProductSheet'
import { bhd, fmtDate, hasBadge, minQtyOf, sessionId, useCountdown } from './shared'
import { useQuote } from './useQuote'

/**
 * The customer-facing shop — the page behind every /c/{token} link a salesman
 * sends on WhatsApp. Browse, price, fill a cart, send it to a named human.
 *
 * Nothing here is authoritative: the server prices the cart and re-checks stock
 * (docs/SHOP.md). This page's job is to be fast on a phone, honest about stock,
 * and to end in a conversation with a salesman.
 */

const CHUNK = 48

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
    <div className="w-[15rem] shrink-0 rounded-2xl border border-[#ece9f3] bg-white p-3.5 shadow-[0_1px_2px_rgba(24,16,48,.04)] sm:w-auto">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-display text-[13px] font-bold leading-tight text-[#1a1430]">{offer.name}</h3>
        {offer.coupon_code && <Badge tone="accent">{offer.coupon_code}</Badge>}
      </div>
      {offer.summary && <p className="mt-1 text-[11.5px] leading-snug text-[#6b6480]">{offer.summary}</p>}
      {countdown && (
        <p className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold tabular-nums text-[#6d28d9]">
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
        'h-9 shrink-0 rounded-full border px-3.5 text-[12px] font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] focus-visible:ring-offset-1',
        on ? 'border-[#6d28d9] bg-[#f1ecfb] text-[#6d28d9]' : 'border-[#e4e0ee] bg-white text-[#6b6480] hover:bg-[#f7f5fb]',
      )}
    >
      {children}
    </button>
  )
}

export function ShopPage() {
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
  const [paging, setPaging] = useState({ key: '', n: CHUNK })
  const [sheetCode, setSheetCode] = useState<string | null>(null)
  const [cartOpen, setCartOpen] = useState(false)
  const [coupon, setCoupon] = useState('')
  const [order, setOrder] = useState<OrderResponse | null>(null)

  const cart = useCart(token)
  const sid = useMemo(() => sessionId(), [])
  const deepLinked = useRef(false)

  const referralCode = data?.ref?.referral_code || refParam

  /* ── load once ──────────────────────────────────────────── */
  useEffect(() => {
    if (!token) return
    let alive = true
    getCatalog(token, refParam)
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
            pingEvent(token, {
              event: 'item',
              item_code: match.item_code,
              referral_code: d.ref?.referral_code || refParam,
              src: srcParam,
              session_id: sid,
            })
          }
        }
      })
      .catch(() => {
        if (alive) setErr(true)
      })
    pingVisit(token, srcParam)
    pingEvent(token, { event: 'view', referral_code: refParam, src: srcParam, session_id: sid })
    return () => {
      alive = false
    }
    // one fetch per link — filters and sort are all client-side
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  // Memoised so the `|| []` fallback doesn't hand every downstream memo a brand
  // new array on each render.
  const items = useMemo(() => data?.items || [], [data])

  const itemsByCode = useMemo(() => {
    const m = new Map<string, ShopItem>()
    for (const it of items) m.set(it.item_code, it)
    return m
  }, [items])

  /* ── filter + sort ──────────────────────────────────────── */
  const filterKey = `${cat}|${q.trim().toLowerCase()}|${inStockOnly}|${offersOnly}|${sort}`

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
  }, [items, cat, q, inStockOnly, offersOnly, sort])

  // Paging is keyed to the filter: changing a filter shows the first chunk again
  // without an effect that would render the long list twice.
  const visible = paging.key === filterKey ? paging.n : CHUNK
  const showMore = () => setPaging({ key: filterKey, n: visible + CHUNK })

  const bestSellers = useMemo(() => items.filter((i) => hasBadge(i, 'best_seller')).slice(0, 12), [items])
  const offers = data?.offers || []
  const railsVisible = !q.trim() && cat === 'All' && !inStockOnly && !offersOnly

  const settings = data?.settings || {}
  const allowBackorder = settings.allow_backorder !== false
  const showCompare = settings.show_retail_compare !== false

  /* ── cart actions ───────────────────────────────────────── */
  const addToCart = useCallback(
    (item: ShopItem) => {
      cart.set(item.item_code, minQtyOf(item))
      pingEvent(token, { event: 'add', item_code: item.item_code, referral_code: referralCode, src: srcParam, session_id: sid })
    },
    [cart, token, referralCode, srcParam, sid],
  )

  const openSheet = useCallback(
    (code: string) => {
      setSheetCode(code)
      pingEvent(token, { event: 'item', item_code: code, referral_code: referralCode, src: srcParam, session_id: sid })
    },
    [token, referralCode, srcParam, sid],
  )

  const openCart = useCallback(() => {
    setCartOpen(true)
    pingEvent(token, { event: 'checkout', referral_code: referralCode, src: srcParam, session_id: sid })
  }, [token, referralCode, srcParam, sid])

  /* ── live quote (drives the drawer AND the sticky bar total) ─────────────
     Hoisted out of the drawer so the bar can show a real, server-priced total
     before the customer ever opens it. Falls back to a list-price estimate
     while the first quote is in flight or the API is unreachable. */
  const { quote, quoting, error: quoteError } = useQuote(token, cart.lines, coupon, referralCode)
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
    title: sheetItem
      ? `${sheetItem.item_code} — ${bhd(sheetItem.price_bhd)} · YQ Bahrain`
      : `YQ Bahrain — ${data?.brand || 'VFAN'} Trade Catalog`,
    description: sheetItem?.spec
      ? `${sheetItem.item_code} — ${String(sheetItem.spec).replace(/\s+/g, ' ').trim().slice(0, 140)}`
      : `Trade prices on ${items.length || ''} ${data?.brand || 'VFAN'} mobile accessories. Order from your YQ Bahrain salesman — cables, chargers, audio and more, delivered in Bahrain.`,
    items,
    allowBackorder,
  })

  /* ── screens ────────────────────────────────────────────── */
  if (err) {
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
      <OrderSuccess
        order={order}
        onContinue={() => {
          setOrder(null)
          window.scrollTo({ top: 0 })
        }}
      />
    )
  }

  const updated = fmtDate(data?.prices_updated)
  const shown = filtered.slice(0, visible)
  const whatsapp = data?.whatsapp

  return (
    <div className="min-h-screen bg-[#faf9fc] text-[#1a1430]">
      <header className="sticky top-0 z-20 border-b border-[#ece9f3] bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
          <Logo className="h-10 w-10 rounded-xl" />
          <div className="min-w-0">
            <div className="font-display text-base font-bold leading-tight text-[#1a1430]">YQ Bahrain — Catalog</div>
            <div className="text-[11px] text-[#6b6480]">
              Mobile accessories · trade price list
              {updated && (
                <span className="ml-1.5 inline-block rounded-full bg-[#f1ecfb] px-2 py-0.5 font-medium text-[#6d28d9]">
                  Prices updated {updated}
                </span>
              )}
            </div>
          </div>
          <div className="ml-auto hidden items-center gap-2 rounded-lg border border-[#e4e0ee] bg-white px-3 sm:flex">
            <Search size={14} className="text-[#6b6480]" aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search…"
              aria-label="Search the catalog"
              className="h-9 w-44 bg-transparent text-sm outline-none"
            />
          </div>
        </div>
        <div className="mx-auto flex max-w-6xl gap-1.5 overflow-x-auto px-4 pb-2">
          {['All', ...(data?.categories || [])].map((c) => (
            <button
              key={c}
              onClick={() => setCat(c)}
              aria-pressed={cat === c}
              className={cn(
                'shrink-0 rounded-full border px-3.5 py-1 text-[12px] font-medium capitalize transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] focus-visible:ring-offset-1',
                cat === c ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : 'border-[#e4e0ee] bg-white text-[#6b6480]',
              )}
            >
              {c.toLowerCase()}
            </button>
          ))}
        </div>
      </header>

      <main className={cn('mx-auto max-w-6xl px-4 py-5', cart.lines.length > 0 && 'pb-28')}>
        {/* ── search (mobile) + controls ── */}
        <div className="space-y-2.5">
          <div className="relative sm:hidden">
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#6b6480]" aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search code or spec…"
              aria-label="Search the catalog"
              className="h-11 w-full rounded-xl border border-[#e4e0ee] bg-white pl-9 pr-9 text-[14px] outline-none transition focus:border-[#6d28d9] focus:ring-2 focus:ring-[#6d28d9]/20"
            />
            {q && (
              <button
                type="button"
                onClick={() => setQ('')}
                aria-label="Clear search"
                className="absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-[#6b6480] hover:bg-[#f4f2f9]"
              >
                <X size={15} />
              </button>
            )}
          </div>

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
                In stock only
              </Toggle>
              <Toggle on={offersOnly} onClick={() => setOffersOnly((v) => !v)}>
                On offer
              </Toggle>
              <label htmlFor="yq-sort" className="sr-only">
                Sort products
              </label>
              <select
                id="yq-sort"
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
                className="h-9 shrink-0 rounded-full border border-[#e4e0ee] bg-white px-3 text-[12px] font-medium text-[#1a1430] outline-none transition focus:border-[#6d28d9] focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
              >
                {SORTS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* ── rails ── */}
        {railsVisible && offers.length > 0 && (
          <section className="mt-5" aria-labelledby="yq-offers">
            <h2 id="yq-offers" className="font-display text-[14px] font-bold text-[#1a1430]">
              Offers
            </h2>
            <div className="-mx-4 mt-2.5 flex gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 lg:grid-cols-3">
              {offers.map((o) => (
                <OfferCard key={o.id} offer={o} />
              ))}
            </div>
          </section>
        )}

        {railsVisible && bestSellers.length > 0 && (
          <section className="mt-5" aria-labelledby="yq-best">
            <h2 id="yq-best" className="font-display text-[14px] font-bold text-[#1a1430]">
              Best sellers
            </h2>
            <div className="-mx-4 mt-2.5 flex gap-3 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0">
              {bestSellers.map((it) => (
                <ProductCard
                  key={it.item_code}
                  item={it}
                  qty={cart.qtyOf(it.item_code)}
                  allowBackorder={allowBackorder}
                  showCompare={showCompare}
                  onOpen={() => openSheet(it.item_code)}
                  onAdd={() => addToCart(it)}
                  onSetQty={(n) => cart.set(it.item_code, n)}
                  onRemove={() => cart.remove(it.item_code)}
                  className="w-[10.5rem] shrink-0 sm:w-[12rem]"
                />
              ))}
            </div>
          </section>
        )}

        {/* ── grid ── */}
        <section className="mt-5" aria-label="Products">
          {!data ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="aspect-[3/4] animate-pulse rounded-2xl bg-[#f0eef6]" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-2xl border border-[#ece9f3] bg-white px-6 py-14 text-center">
              <p className="font-display text-[15px] font-bold text-[#1a1430]">Nothing matches that</p>
              <p className="mt-1 text-[12.5px] text-[#6b6480]">Try a different code, or clear the filters.</p>
              <button
                type="button"
                onClick={() => {
                  setQ('')
                  setCat('All')
                  setInStockOnly(false)
                  setOffersOnly(false)
                }}
                className="mt-4 h-10 rounded-xl border border-[#e4e0ee] bg-white px-4 text-[13px] font-semibold text-[#1a1430] transition hover:bg-[#f7f5fb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
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
                <div className="mt-6 text-center">
                  <button
                    type="button"
                    onClick={showMore}
                    className="h-11 rounded-xl border border-[#e4e0ee] bg-white px-6 text-[13px] font-semibold text-[#1a1430] transition hover:bg-[#f7f5fb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
                  >
                    Show more ({filtered.length - visible} left)
                  </button>
                </div>
              )}
            </>
          )}
        </section>

        <footer className="py-10 text-center text-[11px] leading-relaxed text-[#6b6480]">
          {cart.lines.length === 0 && whatsapp && (
            <a
              href={`https://wa.me/${whatsapp}?text=${encodeURIComponent('Hello YQ Bahrain! I have a question about your catalog.')}`}
              target="_blank"
              rel="noreferrer"
              className="mb-4 inline-flex h-10 items-center gap-1.5 rounded-xl border border-[#25D366]/30 bg-[#25D366]/8 px-4 text-[12.5px] font-semibold text-[#128C7E] transition hover:bg-[#25D366]/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#128C7E]"
            >
              <MessageCircle size={14} aria-hidden="true" /> Chat on WhatsApp
            </a>
          )}
          <div>YQ Bahrain W.L.L · Trade prices — may change without notice.</div>
          {data?.stock_as_of && <div className="mt-0.5">Stock as of {fmtDate(data.stock_as_of)}</div>}
        </footer>
      </main>

      {/* ── sticky order bar ── */}
      {cart.lines.length > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-[#ece9f3] bg-white/95 px-4 pb-[max(0.625rem,env(safe-area-inset-bottom))] pt-2.5 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-[11.5px] text-[#6b6480]">
                <span className="tabular-nums">{cart.items}</span> {cart.items === 1 ? 'item' : 'items'} ·{' '}
                <span className="tabular-nums">{cart.units}</span> units
              </div>
              <div className="font-display text-[17px] font-extrabold leading-tight tabular-nums text-[#1a1430]">
                {bhd(barTotal)}
              </div>
            </div>
            <button
              type="button"
              onClick={openCart}
              className="flex h-12 shrink-0 items-center gap-2 rounded-xl bg-[#6d28d9] px-5 text-[14px] font-semibold text-white transition hover:bg-[#5b21b6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] focus-visible:ring-offset-2"
            >
              <ShoppingBag size={17} aria-hidden="true" /> Review order
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
          onSuccess={(o) => {
            setOrder(o)
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
