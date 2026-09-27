import { useEffect, useMemo, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { LayoutGrid } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ComingSoonCard } from '../components/ComingSoonCard'
import { ComingSoonCircles, ComingSoonStage, ComingSoonTile } from '../components/ComingSoonHero'
import { jumpTo, sectionId, sectionLabel, upcomingCopy } from '../components/ComingSoonShared'
import { MarketCard } from '../components/MarketCard'
import { Rail } from '../components/Rail'
import { ConnectingState, EmptyState } from '../components/States'
import { useMarket } from '../MarketContext'
import type { UpcomingItem, UpcomingPayload } from '../lib/marketApi'
import { cachedUpcoming, fetchUpcoming } from '../lib/upcoming'
import { usePageTitle, useSearchBand, useShell } from '../shell/ShellContext'
import { isDesktopLike } from '../shell/useViewport'
import { locale, S } from '../strings'
import { LinkButton } from '../ui/Button'
import { CardSkeleton } from '../ui/Skeleton'

/**
 * /brands/{brand} — the brand's page. Before the range lands it is the "Coming soon" page, in the
 * home page's language (ComingSoonHero): on desktop-like widths the night stage beside the lilac
 * tile (Home's one-tile grid), on phones and tablets the stage alone (2:1 / 21:9); then the
 * colourful category circles; then the cards grouped by category, each section headed by its label
 * only — no counts anywhere. The month comes from the payload. A card retires the moment the
 * office links it to a live catalog item, so the same URL later shows the live range; once nothing
 * is announced the page hands over to Browse filtered by brand. /wekome and /coming-soon redirect
 * here, and a rep link /brands/wekome?ref=<slug> keeps its ref: every jump on this page is a
 * button that scrolls, never a #hash link.
 *
 * One h1 per page: the phone/tablet header already carries "WEKOME" as its h1, so the stage's
 * headline is an h2 there and the h1 on desktop-like widths.
 */
export default function BrandPage() {
  const { brand: raw = '' } = useParams()
  const brand = decodeURIComponent(raw).trim().toUpperCase()
  const t = upcomingCopy()
  const { items, status, reload } = useMarket()
  const { viewport } = useShell()
  const desktop = isDesktopLike(viewport)
  const phone = viewport === 'phone'
  // { tick, data } — the effect records which attempt it answered; "loading" is derived (the
  // useAuthedBlob pattern), so a retry never sets state synchronously inside the effect. The payload
  // is the one lib/upcoming shares with the home slide, rail and aside: arriving from Home it is
  // already here (first render, no skeleton); a failed attempt is never cached, so Retry asks again.
  const [tick, setTick] = useState(0)
  const [got, setGot] = useState<{ tick: number; data: UpcomingPayload | 'error' } | null>(() => {
    const known = cachedUpcoming()
    return known ? { tick: 0, data: known } : null
  })
  useSearchBand()
  usePageTitle(brand, true, `${brand} · ${S.brand}`)

  useEffect(() => {
    let alive = true
    fetchUpcoming()
      .then((d) => {
        if (alive) setGot({ tick, data: d })
      })
      .catch(() => {
        if (alive) setGot({ tick, data: 'error' })
      })
    return () => {
      alive = false
    }
  }, [tick])
  const data = got && got.tick === tick ? got.data : null

  const live = useMemo(() => items.filter((i) => (i.brand || '').trim().toUpperCase() === brand), [items, brand])
  const upcoming = useMemo<UpcomingItem[]>(() => (data && data !== 'error' && data.enabled ? data.items.filter((i) => i.brand.toUpperCase() === brand) : []), [data, brand])
  const groups = useMemo(() => {
    const by = new Map<string, UpcomingItem[]>()
    for (const it of upcoming) {
      const key = it.category || ''
      const list = by.get(key)
      if (list) list.push(it)
      else by.set(key, [it])
    }
    return [...by.entries()]
  }, [upcoming])
  const circles = useMemo(() => groups.map(([category, list]) => ({ id: sectionId(category), category, first: list[0] })), [groups])

  if (!brand) return <Navigate to="/shop" replace />
  // nothing announced but the brand is on the shelf: Browse, filtered, is the brand page
  if (data && data !== 'error' && upcoming.length === 0 && live.length > 0) return <Navigate to={`/shop?brand=${encodeURIComponent(brand)}`} replace />

  const when = data && data !== 'error' ? (locale.lang === 'ar' ? data.expected_label_ar : data.expected_label_en) : ''
  const browse = `/shop?brand=${encodeURIComponent(brand)}`
  /** "See the range" and the tile's notify: the first section, by a scroll — the URL never changes */
  const toRange = () => {
    if (circles[0]) jumpTo(circles[0].id)
  }
  const stage = (size: 'hero' | 'phone', className?: string) => (
    <ComingSoonStage brand={brand} when={when} items={upcoming} size={size} heading={desktop ? 'h1' : 'h2'} onSeeRange={toRange} className={className} />
  )

  return (
    // pt-3 below lg: the stage sits 12 px under the phone / tablet header, as /shop's content does —
    // flush against it, the two rounded panels read as one (the loading frame shares this wrapper)
    <div className="px-gutter pt-3 lg:px-0 lg:pt-5">
      {upcoming.length > 0 &&
        (desktop ? (
          // Home's one-tile hero grid: the stage keeps 1.6 of the row, the tile stretches to its height
          <div className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-4 2xl:gap-5">
            {stage('hero', 'min-w-0')}
            <ComingSoonTile brand={brand} items={upcoming} onNotifyAny={toRange} className="aspect-auto h-full min-h-[7.5rem] min-w-0" />
          </div>
        ) : (
          stage(phone ? 'phone' : 'hero')
        ))}
      {upcoming.length > 0 && <ComingSoonCircles sections={circles} onJump={jumpTo} className="mt-4 lg:mt-6" />}

      {data === null && (
        <div aria-busy="true">
          {/* the stage's (and on desktop the tile's) own frame on its own canvas, so nothing moves when the payload lands */}
          {desktop ? (
            <div className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-4 2xl:gap-5">
              <div className="canvas-night aspect-[21/9] rounded-xl" />
              <div className="canvas-lilac h-full min-h-[7.5rem] rounded-lg" />
            </div>
          ) : (
            <div className={cn('canvas-night', phone ? 'aspect-[2/1] rounded-lg' : 'aspect-[21/9] rounded-xl')} />
          )}
          <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-4 3xl:grid-cols-5">
            {Array.from({ length: 8 }, (_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        </div>
      )}
      {data === 'error' && (status === 'error' ? <ConnectingState onRetry={reload} failed /> : <ConnectingState onRetry={() => setTick((n) => n + 1)} failed />)}
      {data && data !== 'error' && upcoming.length === 0 && live.length === 0 && (
        <EmptyState
          className="mt-4"
          title={t.empty}
          hint={t.emptyHint}
          action={
            <LinkButton to="/shop" variant="primary" icon={<LayoutGrid size={15} aria-hidden="true" />}>
              {S.nav.browse}
            </LinkButton>
          }
        />
      )}

      {groups.map(([category, list]) => {
        const id = sectionId(category)
        return (
          // scroll-margin: a circle's jump lands the heading clear of the pinned header (phones get
          // the pinned search band from html's scroll-padding; desktop's sticky header is added here)
          <section key={id} id={id} aria-labelledby={`${id}-h`} className="mt-7 scroll-mt-4 lg:mt-10 lg:scroll-mt-[calc(var(--m-sticky-h,var(--m-header-h))+1rem)]">
            <h2 id={`${id}-h`} tabIndex={-1} data-jump-focus className="font-display text-lg font-bold text-ink outline-none lg:text-xl">
              {sectionLabel(category) || brand}
            </h2>
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-4 3xl:grid-cols-5">
              {list.map((it) => (
                <ComingSoonCard key={it.id} item={it} />
              ))}
            </div>
          </section>
        )
      })}

      {live.length > 0 && (
        <div className="mt-8 lg:mt-12">
          <Rail id="brand-live" title={t.liveNow(brand)} subtitle={t.browseBrand(brand)} seeAllTo={browse}>
            {live.slice(0, 8).map((it) => (
              <MarketCard key={it.item_code} item={it} variant="compact" from="brand" />
            ))}
          </Rail>
        </div>
      )}
    </div>
  )
}
