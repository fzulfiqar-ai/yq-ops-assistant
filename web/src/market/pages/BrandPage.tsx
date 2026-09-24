import { useEffect, useMemo, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { LayoutGrid } from 'lucide-react'
import { ComingSoonCard } from '../components/ComingSoonCard'
import { categoryPhrase, upcomingCopy } from '../components/ComingSoonShared'
import { MarketCard } from '../components/MarketCard'
import { Rail } from '../components/Rail'
import { ConnectingState, EmptyState } from '../components/States'
import { useMarket } from '../MarketContext'
import { getUpcoming, type UpcomingItem, type UpcomingPayload } from '../lib/marketApi'
import { usePageTitle, useSearchBand } from '../shell/ShellContext'
import { locale, S } from '../strings'
import { LinkButton } from '../ui/Button'
import { CardSkeleton } from '../ui/Skeleton'

/**
 * /brands/{brand} — the brand's page. Before the range lands it is the "Coming soon" page: the
 * plum announcement (headline, the count and the month from the payload, the tagline, "Price on
 * arrival") and the cards grouped by category. A card retires the moment the office links it to
 * a live catalog item, so the same URL later shows the live range; once nothing is announced
 * the page hands over to Browse filtered by brand. /wekome and /coming-soon redirect here.
 */
export default function BrandPage() {
  const { brand: raw = '' } = useParams()
  const brand = decodeURIComponent(raw).trim().toUpperCase()
  const t = upcomingCopy()
  const { items, status, reload } = useMarket()
  // { tick, data } — the effect records which attempt it answered; "loading" is derived (the
  // useAuthedBlob pattern), so a retry never sets state synchronously inside the effect
  const [tick, setTick] = useState(0)
  const [got, setGot] = useState<{ tick: number; data: UpcomingPayload | 'error' } | null>(null)
  useSearchBand()
  usePageTitle(brand, true, `${brand} · ${S.brand}`)

  useEffect(() => {
    let alive = true
    getUpcoming()
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

  if (!brand) return <Navigate to="/shop" replace />
  // nothing announced but the brand is on the shelf: Browse, filtered, is the brand page
  if (data && data !== 'error' && upcoming.length === 0 && live.length > 0) return <Navigate to={`/shop?brand=${encodeURIComponent(brand)}`} replace />

  const when = data && data !== 'error' ? (locale.lang === 'ar' ? data.expected_label_ar : data.expected_label_en) : ''
  const browse = `/shop?brand=${encodeURIComponent(brand)}`

  return (
    <div className="px-gutter lg:px-0 lg:pt-5">
      {upcoming.length > 0 && (
        <section aria-labelledby="brand-hero" className="band-sash sash-corner overflow-hidden rounded-xl bg-plum p-5 text-white lg:p-7">
          <p className="text-2xs font-semibold uppercase tracking-[0.14em] text-white/75">
            {t.kicker} · {when}
          </p>
          <h1 id="brand-hero" className="mt-1 text-balance font-display text-2xl font-bold leading-tight text-white lg:text-3xl">
            {t.headline(brand)}
          </h1>
          {/* count, month AND the category words are data from the cards on this page */}
          <p className="mt-3 max-w-prose text-[15px] leading-relaxed text-white/95">{t.subline(upcoming.length, when, categoryPhrase(upcoming))}</p>
          <p className="mt-2 font-display text-base font-bold text-white/90">{t.tagline}</p>
          <p className="mt-3 inline-flex rounded-full bg-white/15 px-3 py-1 text-xs font-semibold">{t.price}</p>
        </section>
      )}

      {data === null && (
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-4 3xl:grid-cols-5" aria-busy="true">
          {Array.from({ length: 8 }, (_, i) => (
            <CardSkeleton key={i} />
          ))}
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

      {groups.map(([category, list]) => (
        <section key={category || 'all'} className="mt-7 lg:mt-10" aria-labelledby={`brand-cat-${category.replace(/\W+/g, '-') || 'all'}`}>
          <div className="flex items-baseline gap-2">
            <h2 id={`brand-cat-${category.replace(/\W+/g, '-') || 'all'}`} className="font-display text-lg font-bold text-ink lg:text-xl">
              {category || brand}
            </h2>
            <span className="text-sm tnum text-ink-2">{t.designs(list.length)}</span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-4 3xl:grid-cols-5">
            {list.map((it) => (
              <ComingSoonCard key={it.id} item={it} />
            ))}
          </div>
        </section>
      ))}

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
