import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { ContinueRestock } from '../components/ContinueRestock'
import { HomeBlocks } from '../components/HomeBlocksIndex'
import { PromiseStrip } from '../components/PromiseBar'
import { PromoSlider } from '../components/PromoSlider'
import { RepCard } from '../components/RepCard'
import { SlideCard } from '../components/SlideCard'
import { ClosedState, ConnectingState, HomeSkeleton, OfflineBanner } from '../components/States'
import { fetchOrderCached, useRecentOrders } from '../hooks/useRecentOrders'
import { useMarket, useOrder } from '../MarketContext'
import { currentRef, forgetRef, isSlugShaped, rememberedOrders, rememberRef } from '../lib/device'
import { categoryTiles, heroSplit, liveOffer, orderLines } from '../lib/home'
import { buildSlides, useClaimSlides } from '../lib/slides'
import { usePageTitle, useSearchBand, useShell } from '../shell/ShellContext'
import { isDesktopLike } from '../shell/useViewport'
import { useCartLines } from '../store/cart'
import { S } from '../strings'

/**
 * The front door. One page, three entrances (/ · /{slug} · /p/{code} deep link) and three states
 * (first visit, recognised merchant, salesman storefront).
 *
 * Phone: the shell's plum band with the pinned search → the promise strip → the promo slider →
 * round category tiles → what to do next (continue the restock / order again, track, offer, the
 * wholesale actions) → [below the fold, pages/HomeBelow] Restock essentials → Stock-Up Deals → New
 * arrivals → brands → Moving fast → all products → "Ready to restock?" → footer.
 * Desktop: the hero composition (slider stage + pastel tiles from the same slide list) → one row of
 * category tiles → the same sections on a wider rhythm, each rising in as it scrolls into view.
 *
 * The orders this phone remembers arrive after the first paint: while they do, the Order again
 * slot is held (skeleton) and the hero tile row is built without 'd:again', so a returning
 * merchant's top zone is laid out once instead of shifting when they land.
 *
 * A slide shows once (lib/slides, claimed so the aside Spotlight skips it). Two-phase render: the
 * first commit is this top zone only; the below-the-fold sections are their own chunk (HomeBelow —
 * cards, rails, deals, grid, band, footer), fetched right after the first commit and mounted one
 * frame after the top zone painted, behind a 60vh placeholder.
 */

const loadBelow = () => import('./HomeBelow')
const HomeBelow = lazy(loadBelow)

/** section rhythm: 28px between phone sections, 56px on desktop (children's own top margins are reset) */
const STACK = 'flex flex-col gap-7 lg:gap-14 [&>*]:!mt-0'
/** what phase 2 occupies until it has mounted: keeps the footer-less page from jumping */
const belowPlaceholder = <div aria-hidden="true" className="h-[60vh]" />
/** how many of this phone's orders Order again / Your regular stock read */
const RECENT = 3

/**
 * True while the orders this phone remembers are still on their way. They land after the first
 * paint and decide the top zone (Order again vs paste, the action strip, the desktop tile row), so
 * Home reserves their slot instead of laying the page out twice. Watches the same cached promises
 * useRecentOrders awaits — no second request.
 */
function usePendingOrders(enabled: boolean): boolean {
  const tokens = useMemo(() => rememberedOrders().slice(0, RECENT).map((o) => o.token), [])
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    if (!enabled || !tokens.length || settled) return
    let alive = true
    const done = () => {
      if (alive) setSettled(true)
    }
    Promise.all(tokens.map(fetchOrderCached)).then(done, done)
    return () => {
      alive = false
    }
  }, [enabled, tokens, settled])
  return enabled && tokens.length > 0 && !settled
}

export default function Home() {
  const params = useParams<{ slug?: string }>()
  const navigate = useNavigate()
  const m = useMarket()
  const { myOrders } = useOrder()
  const { viewport } = useShell()
  const { data, status, items, itemsByCode, categories, rep, recognized, campaigns } = m
  const lines = useCartLines()
  usePageTitle(null, false, rep ? `${rep.first_name || rep.name} · ${S.brand}` : `${S.brand} · ${S.company}`)
  useSearchBand()

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

  // phase 2 (the below-the-fold chunk) mounts after the first paint: the first commit is the top zone only
  const [phase2, setPhase2] = useState(false)

  /* ── the merchant ── */
  const recent = useRecentOrders(recognized, RECENT)
  const pendingOrders = usePendingOrders(recognized)
  const latest = recent[0] || null
  const lastLines = useMemo(() => orderLines(latest, itemsByCode), [latest, itemsByCode])
  const lastCodes = useMemo(() => new Set(lastLines.map((l) => l.item.item_code)), [lastLines])
  // Order again offers only what is on the shelf, so its count and ≈ total are what one tap adds —
  // the same lines the "Order again" slide is built from
  const againLines = useMemo(() => lastLines.filter((l) => l.item.stock_status !== 'out_of_stock'), [lastLines])
  const openOrder = useMemo(() => myOrders.find((o) => o.status !== 'delivered' && o.status !== 'cancelled') || null, [myOrders])
  const desktop = isDesktopLike(viewport)

  /* ── the top zone ── */
  const tiles = useMemo(() => categoryTiles(items, categories), [items, categories])
  const offer = useMemo(() => liveOffer(data), [data])
  const lastOrderItems = useMemo(() => againLines.map((l) => l.item), [againLines])
  const slides = useMemo(() => buildSlides({ items, campaigns, recognized, lastOrder: lastOrderItems }), [items, campaigns, recognized, lastOrderItems])
  const stage = useMemo(() => {
    if (!desktop) return { hero: slides, tiles: [] }
    // 'd:again' appears only once the last order has loaded, and it is never tileable: keep it out
    // of the split's arithmetic so the tile row under the stage does not change when it arrives
    const split = heroSplit(slides.filter((s) => s.id !== 'd:again'))
    const picked = new Set(split.tiles.map((s) => s.id))
    return { hero: slides.filter((s) => !picked.has(s.id)).slice(0, 3), tiles: split.tiles }
  }, [desktop, slides])
  const shownSlides = useMemo(() => [...stage.hero, ...stage.tiles].map((s) => s.id), [stage])
  useClaimSlides(shownSlides)

  useEffect(() => {
    if (!data) return
    // fetch the below-the-fold chunk now, so it is usually there by the time phase 2 mounts
    void loadBelow()
    let t = 0
    // one frame after the top zone painted → phase 2
    const raf = window.requestAnimationFrame(() => {
      t = window.setTimeout(() => setPhase2(true), 0)
    })
    return () => {
      window.cancelAnimationFrame(raf)
      window.clearTimeout(t)
    }
  }, [data])

  if (status === 'closed') return <ClosedState />
  if (!data) return status === 'error' ? <ConnectingState onRetry={m.reload} failed /> : <HomeSkeleton />

  // the Order again card (cart empty, last order known — or still loading) already offers the
  // reorder: no second button above it
  const againCard = lines.length === 0 && recognized && (againLines.length > 0 || pendingOrders)
  const continueTop = lines.length > 0 || recognized
  // …and the paste card, when that is what the top slot shows, already is the paste action
  const pasteCardTop = continueTop && lines.length === 0 && !againCard
  const continueCard = <ContinueRestock lastLines={againLines} placedAt={latest?.created_at} pending={pendingOrders} />

  return (
    <div className="px-gutter lg:px-0 lg:pt-5">
      {/* phone/tablet: a thin white strip tucked under the band's rounded bottom edge */}
      {!desktop && <PromiseStrip className="bleed -mt-[22px] border-b border-line-2 bg-surface pb-2.5 pt-[34px]" />}
      {status === 'offline' && <OfflineBanner onRetry={m.reload} />}

      {/* ── top zone (phase 1) ── */}
      <div className={cn(STACK, 'mt-3 lg:mt-0')}>
        {desktop ? (
          stage.hero.length > 0 && (
            <div>
              <PromoSlider slides={stage.hero} layout="desktop" />
              {stage.tiles.length >= 2 && (
                <ul className={cn('mt-4 grid gap-4 2xl:gap-5', stage.tiles.length === 3 ? 'grid-cols-3' : 'grid-cols-2')}>
                  {stage.tiles.map((s) => (
                    <li key={s.id} className="min-w-0">
                      <SlideCard slide={s} size="tile" />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        ) : (
          <PromoSlider slides={slides} layout="phone" />
        )}

        <HomeBlocks.CategoryTiles tiles={tiles} />

        <div className="flex flex-col gap-3 empty:hidden lg:gap-4">
          {continueTop && continueCard}
          {recognized && openOrder && <HomeBlocks.TrackCard order={openOrder} />}
          {offer && <HomeBlocks.OfferStrip offer={offer} />}
          <HomeBlocks.MissionStrip againCount={againCard ? 0 : againLines.length} rep={rep} paste={!pasteCardTop} className={cn(continueTop && 'mt-1')} />
          {/* a storefront rep without WhatsApp: who they are (the Ask-your-rep action covers the rest) */}
          {rep && !rep.whatsapp_url && <RepCard rep={rep} compact />}
        </div>
      </div>

      {phase2 ? (
        <Suspense fallback={belowPlaceholder}>
          <HomeBelow recent={recent} lastLines={lastLines} lastCodes={lastCodes} desktop={desktop} continueCard={continueTop ? null : continueCard} />
        </Suspense>
      ) : (
        belowPlaceholder
      )}
    </div>
  )
}
