import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { LayoutGrid, List } from 'lucide-react'
import { cn } from '@/lib/utils'
import { HomeBlocks } from '../components/HomeBlocksIndex'
import { MarketCard } from '../components/MarketCard'
import { Rail } from '../components/Rail'
import { RepCard } from '../components/RepCard'
import { SearchField } from '../components/SearchField'
import { ClosedState, ConnectingState, EmptyState, Footer, HomeSkeleton, OfflineBanner } from '../components/States'
import { useRecentOrders } from '../hooks/useRecentOrders'
import { useMarket, useOrder } from '../MarketContext'
import { currentRef, forgetRef, isSlugShaped, rememberRef } from '../lib/device'
import { bestSellers, categoryTiles, clearance, heroProduct, justArrived, liveOffer, onOffer, orderLines, pickedUpAgain, priceDrops, regularStock } from '../lib/home'
import { fmtDate } from '../lib/format'
import { usePageTitle, useShell } from '../shell/ShellContext'
import { useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'

/**
 * The front door. One page, three entrances (/ · /{slug} · /p/{code} deep link) and three
 * states (first-time, recognised merchant, salesman storefront). The top zone answers "what can I
 * buy, what's new, where are the offers, how do I search, how fast can I order" in one phone
 * screen; the hero is a real product (or the merchant's last order); rails are capped and
 * de-duplicated against the grid; the grid paints 12 cards and grows on idle.
 */

const FIRST = 12
const CHUNK = 48

export default function Home() {
  const params = useParams<{ slug?: string }>()
  const navigate = useNavigate()
  const m = useMarket()
  const { myOrders, quote } = useOrder()
  const { viewport } = useShell()
  const { data, status, items, itemsByCode, categories, rep, recognized } = m
  const lines = useCartLines()
  usePageTitle(null, false, rep ? `${rep.first_name || rep.name} · ${S.brand}` : `${S.brand} · ${S.company}`)

  /* ── /{slug}: remember the rep, refetch with ?ref, bounce unknown slugs to / ── */
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
    if (data.ref) rememberRef(slug)
    else {
      const previous = currentRef()
      if (previous === slug) forgetRef()
      setRef(previous && previous !== slug ? previous : null)
      navigate('/', { replace: true })
    }
  }, [slug, status, data, currentRefValue, setRef, navigate])

  /* ── blocks ── */
  const [q, setQ] = useState('')
  const [view, setView] = useState<'grid' | 'list'>(() => {
    try {
      return (localStorage.getItem('yq-view') as 'grid' | 'list') || 'grid'
    } catch {
      return 'grid'
    }
  })
  const [inStockOnly, setInStockOnly] = useState(false)
  const [offersOnly, setOffersOnly] = useState(false)
  const [visible, setVisible] = useState(FIRST)
  // phase 2 (rails + grid) mounts after the first paint: the first commit is the top zone only
  const [phase2, setPhase2] = useState(false)
  const setViewMode = (v: 'grid' | 'list') => {
    setView(v)
    try {
      localStorage.setItem('yq-view', v)
    } catch {
      /* ignore */
    }
  }

  const recent = useRecentOrders(recognized, 3)
  const latest = recent[0] || null
  const lastLines = useMemo(() => orderLines(latest, itemsByCode), [latest, itemsByCode])
  const lastCodes = useMemo(() => new Set(lastLines.map((l) => l.item.item_code)), [lastLines])
  const regulars = useMemo(() => regularStock(recent, itemsByCode, lastCodes), [recent, itemsByCode, lastCodes])
  const openOrder = useMemo(() => myOrders.find((o) => o.status !== 'delivered' && o.status !== 'cancelled') || null, [myOrders])
  const inCart = useMemo(() => new Set(lines.map((l) => l.item_code)), [lines])
  const cartTotal = useMemo(() => (quote?.total_bhd != null ? Number(quote.total_bhd) : lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)), [quote, lines, itemsByCode])

  const tiles = useMemo(() => categoryTiles(items, categories), [items, categories])
  const offer = useMemo(() => liveOffer(data), [data])
  const hero = useMemo(() => heroProduct(items, categories), [items, categories])
  const best = useMemo(() => bestSellers(items), [items])
  const arrived = useMemo(() => justArrived(items), [items])
  const offers = useMemo(() => onOffer(items), [items])
  const aging = useMemo(() => clearance(items), [items])
  const drops = useMemo(() => priceDrops(items), [items])
  const viewed = useMemo(() => (recognized ? pickedUpAgain(items, inCart) : []), [items, inCart, recognized])

  const railCodes = useMemo(() => {
    const s = new Set<string>()
    for (const l of lastLines) s.add(l.item.item_code)
    for (const l of regulars) s.add(l.item.item_code)
    for (const i of best) s.add(i.item_code)
    for (const i of arrived) s.add(i.item_code)
    for (const i of offers) s.add(i.item_code)
    for (const i of drops) s.add(i.item_code)
    for (const i of aging) s.add(i.item_code)
    if (hero) s.add(hero.item.item_code)
    return s
  }, [lastLines, regulars, best, arrived, offers, drops, aging, hero])

  const filtering = inStockOnly || offersOnly
  const grid = useMemo(() => {
    let r = items
    if (inStockOnly) r = r.filter((i) => i.stock_status !== 'out_of_stock')
    if (offersOnly) r = r.filter((i) => (i.badges || []).includes('on_offer') || i.compare_at_bhd != null)
    if (!filtering && railCodes.size) r = [...r.filter((i) => !railCodes.has(i.item_code)), ...r.filter((i) => railCodes.has(i.item_code))]
    return r
  }, [items, inStockOnly, offersOnly, filtering, railCodes])

  useEffect(() => {
    if (!data) return
    let raf = 0
    let idle = 0
    let t = 0
    // one frame after the top zone painted → rails + first grid page; then, on idle, the rest
    raf = window.requestAnimationFrame(() => {
      t = window.setTimeout(() => setPhase2(true), 0)
    })
    const grow = () => setVisible((v) => (v < CHUNK ? CHUNK : v))
    if (typeof window.requestIdleCallback === 'function') idle = window.requestIdleCallback(grow, { timeout: 2500 })
    else t = window.setTimeout(grow, 1200)
    return () => {
      window.cancelAnimationFrame(raf)
      window.clearTimeout(t)
      if (idle) window.cancelIdleCallback(idle)
    }
  }, [data])
  const toggleFilter = (which: 'stock' | 'offers') => {
    if (which === 'stock') setInStockOnly((v) => !v)
    else setOffersOnly((v) => !v)
    setVisible(FIRST)
  }

  if (status === 'closed') return <ClosedState />
  if (!data) return status === 'error' ? <ConnectingState onRetry={m.reload} failed /> : <HomeSkeleton />

  const phone = viewport === 'phone'
  const shownCount = Math.min(visible, grid.length)
  const showOrderAgain = recognized && lastLines.length > 0
  const railCount = [showOrderAgain && regulars.length >= 3, best.length > 0, arrived.length >= 3, offers.length >= 3, viewed.length >= 3].filter(Boolean).length

  return (
    <div className="px-gutter lg:px-0">
      {status === 'offline' && <OfflineBanner onRetry={m.reload} />}

      {/* ── top zone ── */}
      {phone && (
        <div className="pt-1">
          <SearchField value={q} onChange={setQ} onFocus={() => navigate('/search')} onSubmit={(v) => navigate(v.trim() ? `/search?q=${encodeURIComponent(v.trim())}` : '/search')} readOnlyTap />
        </div>
      )}
      <HomeBlocks.MissionStrip lastCount={showOrderAgain ? lastLines.length : 0} newCount={arrived.length} offerCount={offers.length} clearanceCount={aging.length} dropCount={drops.length} />
      {lines.length > 0 && <HomeBlocks.ResumeOrderCard lines={lines} itemsByCode={itemsByCode} total={cartTotal} />}
      <HomeBlocks.CategoryTiles tiles={tiles} />
      {offer && <HomeBlocks.OfferStrip offer={offer} />}
      {recognized && openOrder && <HomeBlocks.TrackCard order={openOrder} />}

      {showOrderAgain ? (
        <HomeBlocks.OrderAgainHero lines={lastLines} placedAt={latest?.created_at} more={Math.max(0, lastLines.length - 4)} />
      ) : hero ? (
        <HomeBlocks.HeroProduct item={hero.item} rank={hero.rank} category={hero.category} also={best.filter((b) => b.item_code !== hero.item.item_code)} />
      ) : null}

      {rep && <RepCard rep={rep} compact className="mt-3" />}

      {!phase2 && <div aria-hidden="true" className="h-[60vh]" />}
      {phase2 && (
        <>
      {/* ── rails (max 4 on phones) ── */}
      {showOrderAgain && regulars.length >= 3 && (
        <Rail id="regular" title={S.home.regular} seeAllTo="/quick?load=regular">
          {regulars.map((r) => (
            <MarketCard key={r.item.item_code} item={r.item} variant="compact" from="regulars" presetQty={r.qty} />
          ))}
        </Rail>
      )}
      {best.length > 0 && (
        <Rail id="best" title={S.rails.best} seeAllTo="/shop?sort=popular">
          {best.map((it, i) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="best" priority={i < 2 && !hero} />
          ))}
        </Rail>
      )}
      {arrived.length >= 3 && (
        <Rail id="arrived" title={S.rails.arrived} seeAllTo="/shop?f=new">
          {arrived.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="arrived" />
          ))}
        </Rail>
      )}
      {drops.length >= 2 && (
        <Rail id="drops" title={S.rails.drops} seeAllTo="/shop?f=drops">
          {drops.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="drops" />
          ))}
        </Rail>
      )}
      {aging.length >= 3 && (
        <Rail id="clearance" title={S.rails.clearance} subtitle={S.rails.clearanceHint} seeAllTo="/shop?f=clearance">
          {aging.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="clearance" />
          ))}
        </Rail>
      )}
      {offers.length >= 3 && (!phone || railCount <= 4) && (
        <Rail id="offers" title={S.rails.offers} seeAllTo="/shop?f=offers">
          {offers.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="offers" />
          ))}
        </Rail>
      )}
      {viewed.length >= 3 && (!phone || railCount <= 4) && (
        <Rail id="viewed" title={S.home.viewed}>
          {viewed.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="viewed" />
          ))}
        </Rail>
      )}

      {/* ── the grid ── */}
      <section className="mt-8" aria-label={S.home.all}>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-display text-lg font-bold text-ink lg:text-xl">{S.home.all}</h2>
          <span aria-live="polite" className="text-sm tnum text-ink-2">
            {S.states.products(grid.length)}
          </span>
          <div className="ms-auto flex items-center gap-1.5">
            {[
              { on: inStockOnly, which: 'stock' as const, label: S.shop.inStock },
              { on: offersOnly, which: 'offers' as const, label: S.shop.offers, hide: !offers.length },
            ]
              .filter((t) => !t.hide)
              .map((t) => (
                <button key={t.label} type="button" onClick={() => toggleFilter(t.which)} aria-pressed={t.on} className={cn('h-9 rounded-full border px-3.5 text-sm font-medium transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', t.on ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink-2 hover:bg-plum-wash')}>
                  {t.label}
                </button>
              ))}
            <div className="ms-1 inline-flex rounded-sm border border-line bg-surface p-0.5" role="group" aria-label="View">
              {(['grid', 'list'] as const).map((v) => (
                <button key={v} type="button" onClick={() => setViewMode(v)} aria-pressed={view === v} aria-label={v === 'grid' ? S.card.grid : S.card.list} className={cn('grid h-8 w-9 place-items-center rounded-xs transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', view === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-plum-wash')}>
                  {v === 'grid' ? <LayoutGrid size={15} aria-hidden="true" /> : <List size={15} aria-hidden="true" />}
                </button>
              ))}
            </div>
          </div>
        </div>
        {grid.length === 0 ? (
          <EmptyState
            className="mt-3"
            title={S.states.noMatch(offersOnly ? S.shop.offers : S.shop.inStock)}
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setInStockOnly(false)
                  setOffersOnly(false)
                }}
              >
                {S.states.clear}
              </Button>
            }
          />
        ) : view === 'list' ? (
          <div className="mt-2">
            {grid.slice(0, shownCount).map((it) => (
              <MarketCard key={it.item_code} item={it} variant="list" />
            ))}
          </div>
        ) : (
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6">
            {grid.slice(0, shownCount).map((it, i) => (
              <MarketCard key={it.item_code} item={it} priority={i < 2 && !hero && !best.length} />
            ))}
          </div>
        )}
        {shownCount < grid.length && (
          <div className="mt-7 text-center">
            <Button variant="secondary" size="lg" onClick={() => setVisible((v) => v + CHUNK)}>
              {S.states.showMore(grid.length - shownCount)}
            </Button>
          </div>
        )}
      </section>

      <Footer prices={fmtDate(data.prices_updated)} stock={fmtDate(data.stock_as_of)} />
        </>
      )}
    </div>
  )
}
