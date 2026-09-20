import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Tag } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Offer, ShopItem } from '@/lib/shopApi'
import { useReveal } from '../hooks/useReveal'
import { track } from '../lib/events'
import { dealSets, dealsLine } from '../lib/home'
import { S } from '../strings'
import { SectionHeader } from '../ui/SectionHeader'
import { MarketCard } from './MarketCard'

/**
 * Stock-Up Deals on the home page — or, while the price book has no real drop, offer or bundle,
 * simply "Last-Chance Stock". Everything in it is real: price-book drops that carry the old price,
 * live offer rules, bundles, and the lines we are clearing at their normal trade price (the card
 * shows the shop's margin against retail — never a markdown). The section sits on a pale amber
 * panel so a merchant scanning the page finds the opportunities at a glance; chips appear only
 * when there is more than one kind to choose from, and only for kinds that have lines.
 * Phone: a swipe rail with a See-all tile at the end. Desktop: two rows of grid cards that rise in.
 */

type SetKey = 'all' | 'drops' | 'offers' | 'bundles' | 'lastChance'

const PHONE_MAX = 12
/** two full rows at every desktop width, never a part row: 8 in four columns, 10 in five (3xl) */
const DESKTOP_MAX = 10

export function DealsSection({ items, offers, layout }: { items: ShopItem[]; offers?: Offer[] | null; layout: 'phone' | 'desktop' }) {
  const sets = useMemo(() => dealSets(items, offers), [items, offers])
  const [pick, setPick] = useState<SetKey>('all')
  const gridRef = useRef<HTMLDivElement>(null)
  const desktop = layout === 'desktop'

  const tabs = [
    { key: 'drops' as const, label: S.deals.drops, n: sets.drops.length },
    { key: 'offers' as const, label: S.deals.offers, n: sets.offers.length },
    { key: 'bundles' as const, label: S.deals.bundles, n: sets.bundles.length },
    { key: 'lastChance' as const, label: S.deals.last, n: sets.lastChance.length },
  ].filter((t) => t.n > 0)
  // the amber pill above already counts the whole shelf, so the All chip carries no number: "28
  // LINES" and "All 28" said the same thing 200 px apart. Every other chip counts its own subset.
  const chips = tabs.length >= 2 ? [{ key: 'all' as const, label: S.deals.all, n: null }, ...tabs] : []
  const active: SetKey = chips.some((c) => c.key === pick) ? pick : 'all'
  useReveal(gridRef, [active, desktop, sets])

  if (!sets.all.length) return null

  const list = active === 'all' ? sets.all : sets[active]
  const title = sets.hasRealDeals ? S.deals.title : S.deals.lastChance
  // the line only names the kinds that are actually in the section (lib/home dealsLine)
  const line = dealsLine(sets)
  const seeAllTo = active === 'drops' ? '/shop?f=drops' : active === 'lastChance' ? '/shop?f=clearance' : '/shop?f=deals'
  const seeAllLabel = sets.hasRealDeals ? S.deals.seeAll : S.home.seeAll
  const phoneList = list.slice(0, PHONE_MAX)

  return (
    <section aria-labelledby="home-deals" className="bleed bg-deal-soft/60 pb-6 pt-5 lg:mx-0 lg:rounded-xl lg:px-7 lg:pb-7 lg:pt-6 2xl:px-8">
      <span className="inline-flex h-6 items-center gap-1.5 rounded-full bg-deal px-2.5 text-2xs font-bold uppercase tracking-[0.08em] text-deal-ink">
        <Tag size={12} strokeWidth={2.2} aria-hidden="true" />
        <span className="tnum">{S.deals.count(sets.all.length)}</span>
      </span>
      <SectionHeader id="home-deals" className="mt-2" title={title} line={line} seeAllTo={seeAllTo} seeAllLabel={seeAllLabel} />

      {chips.length > 0 && (
        <div role="group" aria-label={title} className="no-scrollbar bleed mt-4 flex gap-2 overflow-x-auto lg:mx-0 lg:px-0">
          {chips.map((c) => {
            const on = active === c.key
            return (
              <button
                key={c.key}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  setPick(c.key)
                  track('rail_click', { meta: { rail: 'deals', code: c.key } })
                }}
                className={cn(
                  'inline-flex h-11 shrink-0 items-center gap-1.5 rounded-full px-4 text-sm font-semibold lg:h-10 ring-1 ring-inset transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 active:scale-[.97]',
                  on ? 'bg-ink text-white ring-ink' : 'bg-surface text-ink ring-deal/70 hover:bg-deal-soft',
                )}
              >
                {c.label}
                {c.n != null && <span className={cn('text-xs font-medium tnum', on ? 'text-white/70' : 'text-ink-3')}>{c.n}</span>}
              </button>
            )
          })}
        </div>
      )}

      {desktop ? (
        // Four columns until 1800. A deal card has to carry the struck old price beside today's
        // price — the honesty this section is named for — and the fifth column at 1440 left ~175 px
        // of content, which cut "2.000" to "2.0…", the sixth at 1920 to "2…". Columns stop where
        // the card stops being able to say the thing the section promises.
        <div key={active} ref={gridRef} className="anim-fade-in mt-5 grid grid-cols-4 gap-3 xl:gap-4 3xl:grid-cols-5">
          {list.slice(0, DESKTOP_MAX).map((it, i) => (
            <div key={it.item_code} className={cn('reveal', i >= 8 && 'hidden 3xl:block')} style={{ ['--i' as string]: i }}>
              <MarketCard item={it} from="deals" className="h-full" />
            </div>
          ))}
        </div>
      ) : (
        <div key={active} className="rail bleed anim-fade-in mt-4 pb-1.5">
          {phoneList.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="deals" />
          ))}
          {list.length > phoneList.length && (
            <Link
              to={seeAllTo}
              onClick={() => track('rail_click', { meta: { rail: 'deals', code: 'see_all' } })}
              className="flex w-[clamp(10rem,46vw,13rem)] shrink-0 flex-col items-center justify-center gap-2 rounded-lg bg-surface/70 px-3 text-center ring-1 ring-inset ring-deal/70 transition duration-1 ease-m active:scale-[.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
            >
              <span className="grid h-11 w-11 place-items-center rounded-full bg-deal text-deal-ink" aria-hidden="true">
                <ArrowRight size={18} strokeWidth={2.2} className="rtl:-scale-x-100" />
              </span>
              <span className="text-sm font-semibold text-ink">{seeAllLabel}</span>
              <span className="text-xs tnum text-ink-2">{S.deals.count(list.length)}</span>
            </Link>
          )}
          <span aria-hidden="true" className="w-px shrink-0" />
        </div>
      )}
    </section>
  )
}
