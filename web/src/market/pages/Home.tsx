import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ChevronRight, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { OrderStatusPayload, ShopItem } from '@/lib/shopApi'
import { ProductSheet } from '@/pages/shop/ProductSheet'
import { fmtDate, hasBadge, RING } from '@/pages/shop/shared'
import { useMarket } from '../MarketContext'
import { BTN_SECONDARY, CardSkeleton, CategoryChips, ClosedState, ConnectingState, EmptyState, OfferHero, OfflineBanner, QtySheet, Rail, RepBanner, SearchBox } from '../components/Bits'
import { BottomNav, CartBar, Page, TopBar } from '../components/Chrome'
import { MarketCard } from '../components/MarketCard'
import { currentRef, forgetRef, isSlugShaped, rememberedOrders, rememberRef, rememberViewed } from '../lib/device'
import { track } from '../lib/events'
import { getOrder } from '../lib/marketApi'
import { S } from '../strings'

/**
 * The marketplace home (plan §G). One page, three entrances:
 *   /            the root
 *   /{slug}      a salesman's storefront — same page, his card pinned, attribution remembered
 *   /p/{code}    a product deep link — the sheet opens over the page
 *   /t/{cat}     a category
 *
 * Unknown visitors get: search, chips, the weekly deal, Best sellers, Just arrived, the grid.
 * Recognized merchants get: their rep, their open order, "Order again" (last order, last
 * quantities), then the discovery rails. Rails are capped (3 / 4) and de-duplicated against the
 * first page of the grid: every product appears once before you scroll past it.
 */

const CHUNK = 48
const RAIL_MAX = 10

function useLatestOrderLines(enabled: boolean): OrderStatusPayload | null {
  const [latest, setLatest] = useState<OrderStatusPayload | null>(null)
  useEffect(() => {
    if (!enabled) return
    const newest = rememberedOrders()[0]
    if (!newest) return
    let alive = true
    getOrder(newest.token)
      .then((o) => alive && setLatest(o))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [enabled])
  return latest
}

export default function Home() {
  const params = useParams<{ slug?: string; code?: string; category?: string }>()
  const navigate = useNavigate()
  const m = useMarket()
  const { data, status, items, itemsByCode, categories, rep, cart, recognized, myOrders } = m

  const [cat, setCat] = useState<string>(() => (params.category ? decodeURIComponent(params.category).toUpperCase() : S.categories.all))
  const [q, setQ] = useState('')
  const [offersOnly, setOffersOnly] = useState(false)
  const [inStockOnly, setInStockOnly] = useState(false)
  const [visible, setVisible] = useState(12)
  const [keypad, setKeypad] = useState<ShopItem | null>(null)

  /* ── /{slug}: remember the rep, refetch with ?ref, bounce unknown slugs to /.
        `attempted` makes this once per slug: the context value changes on every state update,
        so without the guard an unknown slug would be re-applied each time the validation
        effect cleared it — an endless fetch loop. ── */
  const slug = params.slug ? params.slug.toLowerCase() : null
  const attempted = useRef<string | null>(null)
  const { ref: currentRefValue, setRef } = m
  useEffect(() => {
    if (!slug) return
    if (!isSlugShaped(slug)) {
      navigate('/', { replace: true })
      return
    }
    if (attempted.current === slug) return
    attempted.current = slug
    if (currentRefValue !== slug) setRef(slug)
  }, [slug, currentRefValue, setRef, navigate])
  useEffect(() => {
    if (!slug || status !== 'ready' || !data || currentRefValue !== slug) return
    if (data.ref) {
      rememberRef(slug)   // confirmed by the server: this phone now belongs to this rep's storefront
    } else {
      // unknown slug: fall back to whatever this phone remembered before (or nothing) and go home
      const previous = currentRef()
      if (previous === slug) forgetRef()
      setRef(previous && previous !== slug ? previous : null)
      navigate('/', { replace: true })
    }
  }, [slug, status, data, currentRefValue, setRef, navigate])

  useEffect(() => {
    document.title = rep ? `${rep.first_name || rep.name} · ${S.brand}` : `${S.brand} · ${S.company}`
  }, [rep])

  /* ── product deep link ── */
  const sheetItem = useMemo(() => {
    if (!params.code) return null
    const want = decodeURIComponent(params.code).toLowerCase()
    return items.find((i) => i.item_code.toLowerCase() === want) || null
  }, [params.code, items])
  useEffect(() => {
    if (!sheetItem) return
    rememberViewed(sheetItem.item_code)
    track('item', { item_code: sheetItem.item_code })
  }, [sheetItem])
  const closeSheet = useCallback(() => {
    if (window.history.length > 1) navigate(-1)
    else navigate(rep ? `/${rep.slug}` : '/', { replace: true })
  }, [navigate, rep])
  const openItem = useCallback((code: string) => navigate(`/p/${encodeURIComponent(code)}`), [navigate])

  /* ── recognized merchant: latest order → "Order again" ── */
  const latest = useLatestOrderLines(recognized)
  const regulars = useMemo(() => {
    const out: { item: ShopItem; qty: number }[] = []
    for (const ln of latest?.lines || []) {
      if ((ln.line_status || 'ok') === 'removed') continue
      const it = itemsByCode.get(ln.item_code)
      if (it) out.push({ item: it, qty: Number(ln.qty_confirmed ?? ln.qty) || 1 })
    }
    return out
  }, [latest, itemsByCode])
  const openOrder = useMemo(() => myOrders.find((o) => o.status !== 'delivered' && o.status !== 'cancelled') || null, [myOrders])

  const reviewRegular = () => {
    for (const r of regulars) m.add(r.item, r.qty, 'regulars')
    track('reorder', { meta: { count: regulars.length, rail: 'regulars' } })
    navigate('/cart')
  }

  /* ── rails ── */
  const best = useMemo(() => items.filter((i) => hasBadge(i, 'best_seller') && i.stock_status !== 'out_of_stock').slice(0, RAIL_MAX), [items])
  const arrived = useMemo(() => items.filter((i) => hasBadge(i, 'new') && i.stock_status !== 'out_of_stock').slice(0, RAIL_MAX), [items])
  const offers = useMemo(() => items.filter((i) => hasBadge(i, 'on_offer') || i.compare_at_bhd != null).slice(0, RAIL_MAX), [items])
  const hero = (data?.offers || [])[0] || null
  const railCodes = useMemo(() => {
    const s = new Set<string>()
    for (const r of regulars) s.add(r.item.item_code)
    for (const i of best) s.add(i.item_code)
    for (const i of arrived) s.add(i.item_code)
    if (!recognized) for (const i of offers) s.add(i.item_code)
    return s
  }, [regulars, best, arrived, offers, recognized])

  /* ── grid ── */
  const filtering = cat !== S.categories.all || offersOnly || inStockOnly || q.trim().length > 0
  const filtered = useMemo(() => {
    let r = items
    if (cat !== S.categories.all) r = r.filter((i) => (i.category || 'OTHER') === cat)
    if (offersOnly) r = r.filter((i) => hasBadge(i, 'on_offer') || i.compare_at_bhd != null)
    if (inStockOnly) r = r.filter((i) => i.stock_status !== 'out_of_stock')
    if (q.trim()) {
      const s = q.trim().toLowerCase()
      r = r.filter((i) => i.item_code.toLowerCase().includes(s) || (i.spec || '').toLowerCase().includes(s) || (i.display_name || '').toLowerCase().includes(s))
    }
    // rails first, grid after: products a rail already showed drop to the end of the grid
    if (!filtering && railCodes.size) {
      const seen = r.filter((i) => !railCodes.has(i.item_code))
      const dup = r.filter((i) => railCodes.has(i.item_code))
      r = [...seen, ...dup]
    }
    return r
  }, [items, cat, offersOnly, inStockOnly, q, filtering, railCodes])

  useEffect(() => {
    if (!data) return
    const id = window.setTimeout(() => setVisible((v) => (v < CHUNK ? CHUNK : v)), 80)
    return () => window.clearTimeout(id)
  }, [data])
  useEffect(() => setVisible(12), [cat, offersOnly, inStockOnly, q])

  const railsVisible = !filtering
  const showRails = railsVisible && status !== 'loading'
  const cardProps = (it: ShopItem, from?: string, compact?: boolean, eager?: boolean) => ({
    item: it,
    qty: cart.qtyOf(it.item_code),
    defaultQty: m.defaultQty(it),
    allowBackorder: m.allowBackorder,
    showCompare: m.showCompare,
    publicTiers: m.publicTiers,
    rep,
    compact,
    eagerImage: eager,
    onOpen: () => {
      if (from) track('rail_click', { item_code: it.item_code, meta: { rail: from } })
      openItem(it.item_code)
    },
    onAdd: () => m.add(it, undefined, from),
    onSetQty: (n: number) => m.setQty(it, n),
    onRemove: () => m.remove(it.item_code),
    onKeypad: () => setKeypad(it),
  })

  if (status === 'closed') {
    return (
      <Page>
        <TopBar />
        <ClosedState />
        <BottomNav />
      </Page>
    )
  }
  if (!data) {
    return (
      <Page>
        <TopBar />
        <main className="mx-auto max-w-6xl px-4">
          {status === 'error' ? (
            <ConnectingState onRetry={m.reload} failed />
          ) : (
            <>
              <div className="h-12 animate-pulse rounded-2xl bg-[#f0eef6]" />
              <div className="mt-3 flex gap-2">{[80, 96, 72, 110].map((w, i) => <div key={i} className="h-9 animate-pulse rounded-full bg-[#f0eef6]" style={{ width: w }} />)}</div>
              <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <CardSkeleton key={i} />)}</div>
              <p className="mt-6 text-center text-[12px] text-[#6b6480]">{S.states.connecting}</p>
            </>
          )}
        </main>
        <BottomNav />
      </Page>
    )
  }

  const updated = fmtDate(data.prices_updated)
  const shownCount = Math.min(visible, filtered.length)

  return (
    <Page withCartBar>
      <TopBar right={updated ? <span className="hidden shrink-0 rounded-full bg-[#f3eefc] px-2.5 py-1 text-[10.5px] font-semibold text-[#6d28d9] sm:inline">{S.states.pricesAsOf(updated)}</span> : undefined} />

      {/* search + chips, pinned */}
      <div className="sticky top-0 z-20 border-b border-[#ece9f3] bg-[#faf9fc]/92 backdrop-blur-md">
        <div className="mx-auto max-w-6xl px-4 pb-2 pt-2">
          <SearchBox value={q} onChange={setQ} onSubmit={(v) => v.trim() && navigate(`/search?q=${encodeURIComponent(v.trim())}`)} />
          <div className="mt-2">
            <CategoryChips categories={categories} active={cat} onPick={(c) => { setCat(c); setOffersOnly(false) }} />
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-6xl px-4">
        {status === 'offline' && <OfflineBanner kind="offline" onRetry={m.reload} />}

        {showRails && rep && <div className="mt-4"><RepBanner rep={rep} /></div>}

        {showRails && recognized && openOrder && (
          <Link to={`/o/${openOrder.token}`} className={cn('mt-3 flex items-center gap-3 rounded-[18px] border border-[#ece9f3] bg-white px-4 py-3 shadow-[0_1px_2px_rgba(24,16,48,.04)] hover:border-[#e2ddef]', RING)}>
            <div className="min-w-0 flex-1">
              <div className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">{openOrder.order_no}</div>
              <div className="font-display text-[14px] font-bold text-[#1a1430]">{openOrder.status_label || openOrder.status}{openOrder.expected_delivery ? ` · ${openOrder.expected_delivery}` : ''}</div>
            </div>
            <span className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-[#6d28d9]">{S.placed.track} <ChevronRight size={15} /></span>
          </Link>
        )}

        {showRails && !recognized && hero && <OfferHero offer={hero} onCta={() => { setOffersOnly(true); setCat(S.categories.all) }} />}

        {showRails && recognized && regulars.length > 0 && (
          <Rail id="regulars" title={S.rails.regulars} action={<button type="button" onClick={reviewRegular} className={cn('inline-flex h-9 items-center gap-1.5 rounded-xl bg-[#6d28d9] px-3 text-[12.5px] font-semibold text-white hover:bg-[#5b21b6]', RING)}><RotateCcw size={14} aria-hidden="true" /> {S.rails.regularsCta}</button>}>
            {regulars.map((r) => <MarketCard key={r.item.item_code} {...cardProps(r.item, 'regulars', true)} defaultQty={r.qty} />)}
          </Rail>
        )}

        {showRails && best.length > 0 && (
          <Rail id="best" title={S.rails.best}>
            {best.map((it, i) => <MarketCard key={it.item_code} {...cardProps(it, 'best', true, i < 3)} />)}
          </Rail>
        )}
        {showRails && arrived.length > 0 && (
          <Rail id="arrived" title={S.rails.arrived}>
            {arrived.map((it) => <MarketCard key={it.item_code} {...cardProps(it, 'arrived', true)} />)}
          </Rail>
        )}
        {showRails && !recognized && offers.length > 0 && (
          <Rail id="offers" title={S.rails.offers}>
            {offers.map((it) => <MarketCard key={it.item_code} {...cardProps(it, 'offers', true)} />)}
          </Rail>
        )}

        {/* grid */}
        <section className="mt-6" aria-label="Products">
          <div className="flex flex-wrap items-center gap-2">
            <p aria-live="polite" className="text-[12px] text-[#6b6480]">
              <b className="font-semibold tabular-nums text-[#1a1430]">{filtered.length}</b> {filtered.length === 1 ? 'product' : 'products'}
              {cat !== S.categories.all && <span className="capitalize"> · {cat.toLowerCase()}</span>}
            </p>
            <div className="ml-auto flex gap-1.5">
              {[{ on: inStockOnly, set: setInStockOnly, label: 'In stock' }, { on: offersOnly, set: setOffersOnly, label: S.rails.offers }].map((t) => (
                <button key={t.label} type="button" onClick={() => t.set(!t.on)} aria-pressed={t.on} className={cn('h-9 rounded-full border px-3.5 text-[12px] font-medium transition', RING, t.on ? 'border-[#6d28d9] bg-[#f3eefc] text-[#6d28d9]' : 'border-[#e4e0ee] bg-white text-[#6b6480] hover:bg-[#f7f5fb]')}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          {filtered.length === 0 ? (
            <div className="mt-3">
              <EmptyState title={S.states.noMatch(q || cat)} hint={S.states.noMatchHint} action={<button type="button" onClick={() => { setQ(''); setCat(S.categories.all); setOffersOnly(false); setInStockOnly(false) }} className={BTN_SECONDARY}>{S.states.clear}</button>} />
            </div>
          ) : (
            <>
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4 xl:grid-cols-5">
                {filtered.slice(0, shownCount).map((it, i) => <MarketCard key={it.item_code} {...cardProps(it, undefined, false, i < 4 && !showRails)} />)}
              </div>
              {shownCount < filtered.length && (
                <div className="mt-7 text-center">
                  <button type="button" onClick={() => setVisible((v) => v + CHUNK)} className={BTN_SECONDARY}>{S.states.showMore(filtered.length - shownCount)}</button>
                </div>
              )}
            </>
          )}
        </section>

        <footer className="py-10 text-center text-[11px] leading-relaxed text-[#6b6480]">
          <div>{S.states.footer}</div>
          {fmtDate(data.stock_as_of) && <div className="mt-0.5">{S.states.stockAsOf(fmtDate(data.stock_as_of)!)}</div>}
        </footer>
      </main>

      <CartBar />
      <BottomNav />

      {sheetItem && (
        <ProductSheet
          item={sheetItem}
          token=""
          referralCode={rep?.slug}
          allowBackorder={m.allowBackorder}
          showCompare={m.showCompare}
          qty={cart.qtyOf(sheetItem.item_code)}
          pairs={m.pairsFor(sheetItem.item_code)}
          shareUrl={`${window.location.origin}/p/${encodeURIComponent(sheetItem.item_code)}${rep ? `?ref=${encodeURIComponent(rep.slug)}` : ''}`}
          onShared={() => track('share', { item_code: sheetItem.item_code })}
          onAdd={(it) => m.add(it)}
          onSetQty={(it, n) => m.setQty(it, n)}
          onRemove={(it) => m.remove(it.item_code)}
          onOpenItem={(code) => { track('reco_click', { item_code: code, meta: { rail: 'together' } }); openItem(code) }}
          onClose={closeSheet}
        />
      )}
      {keypad && (
        <QtySheet item={keypad} value={cart.qtyOf(keypad.item_code)} onApply={(n) => m.setQty(keypad, n)} onRemove={() => m.remove(keypad.item_code)} onClose={() => setKeypad(null)} />
      )}
    </Page>
  )
}
