import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { LayoutGrid, List } from 'lucide-react'
import type { OrderStatusPayload } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { BrandTiles } from '../components/BrandTiles'
import { CtaBand } from '../components/CtaBand'
import { DealsSection } from '../components/DealsSection'
import { MarketCard } from '../components/MarketCard'
import { Rail } from '../components/Rail'
import { EmptyState, Footer } from '../components/States'
import { useReveal } from '../hooks/useReveal'
import { useMarket } from '../MarketContext'
import { applyQuickFilters, type QuickFilter } from '../lib/facets'
import { fmtDate } from '../lib/format'
import { dealSets, homeRails, pickedUpAgain, regularStock, type RegularLine } from '../lib/home'
import { PageTail } from '../shell/ShellContext'
import { useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'

/**
 * Home, below the fold (phase 2) — its own chunk, so the first screen (band, promises, slider,
 * category tiles, the next-step cards) paints without the card, rail, deals, grid, band and footer
 * code. Home starts downloading it right after its first commit and mounts it one frame after the
 * top zone painted, behind the same 60vh placeholder it always had.
 *
 * Order: regular stock (recognised) → Restock essentials → Stock-Up Deals → New arrivals → brands →
 * Moving fast (the one tinted block in the middle of the page) → Picked up again → the paste card
 * (first visits, phone) → All products → "Ready to restock?" → footer. Every product appears in at
 * most one rail (lib/home homeRails; Picked up again skips anything already shown) and rail
 * products sink to the end of the grid, which grows from 12 cards to one page on idle.
 *
 * That page is a column multiple, so the grid never ends on a ragged half-row above "Show more":
 * 24 on a phone (2 columns, and the home stays inside reach of the band and the footer — the
 * catalogue is what Browse is for), 60 on desktop (both 4 and 5 columns divide it).
 */

const FIRST = 12
const CHUNK = { phone: 24, desktop: 60 }
const DEALS: Set<QuickFilter> = new Set<QuickFilter>(['deals'])
/** section rhythm — the same as Home's top zone: 28px between phone sections, 56px on desktop */
const STACK = 'flex flex-col gap-7 lg:gap-14 [&>*]:!mt-0'

/**
 * One home section slot: collapses when its content renders nothing. `reveal` (desktop) lets the
 * whole section rise in — only for sections that do not already reveal their own cards (Rail and
 * DealsSection stagger their grid cards; a second rise on the wrapper would double the motion).
 */
function Sect({ reveal = false, children }: { reveal?: boolean; children: ReactNode }) {
  return <div className={cn('min-w-0 empty:hidden [&>*]:!mt-0', reveal && 'reveal')}>{children}</div>
}

export default function HomeBelow({ recent, lastLines, lastCodes, desktop, continueCard }: { recent: OrderStatusPayload[]; lastLines: RegularLine[]; lastCodes: Set<string>; desktop: boolean; /** the continue card when it is not already in the top zone (a first visit: the paste card, phone only) */ continueCard: ReactNode }) {
  const { data, items, itemsByCode, recognized, rep } = useMarket()
  const lines = useCartLines()
  const rootRef = useRef<HTMLDivElement>(null)

  /* ── rails: each product in at most one ── */
  const offerRules = useMemo(() => data?.offers || null, [data])
  const inCart = useMemo(() => new Set(lines.map((l) => l.item_code)), [lines])
  const regulars = useMemo(() => regularStock(recent, itemsByCode, lastCodes), [recent, itemsByCode, lastCodes])
  const showRegulars = recognized && lastLines.length > 0 && regulars.length >= 3
  const regularCodes = useMemo(() => new Set<string>(showRegulars ? regulars.map((r) => r.item.item_code) : []), [showRegulars, regulars])
  const rails = useMemo(() => homeRails(items, offerRules, regularCodes), [items, offerRules, regularCodes])
  const dealItems = useMemo(() => (regularCodes.size ? items.filter((i) => !regularCodes.has(i.item_code)) : items), [items, regularCodes])
  const dealCodes = useMemo(() => new Set(dealSets(dealItems, offerRules).all.map((i) => i.item_code)), [dealItems, offerRules])
  const viewed = useMemo(() => {
    if (!recognized) return []
    const taken = new Set<string>([...regularCodes, ...dealCodes, ...[...rails.essentials, ...rails.fresh, ...rails.moving].map((i) => i.item_code)])
    return pickedUpAgain(items, inCart).filter((i) => !taken.has(i.item_code))
  }, [recognized, regularCodes, dealCodes, rails, items, inCart])

  const railCodes = useMemo(() => {
    const s = new Set<string>([...lastCodes, ...regularCodes, ...dealCodes])
    for (const i of [...rails.essentials, ...rails.fresh, ...rails.moving]) s.add(i.item_code)
    if (viewed.length >= 3) for (const i of viewed) s.add(i.item_code)
    return s
  }, [lastCodes, regularCodes, dealCodes, rails, viewed])

  /* ── the grid ── */
  const [view, setView] = useState<'grid' | 'list'>(() => {
    try {
      return (localStorage.getItem('yq-view') as 'grid' | 'list') || 'grid'
    } catch {
      return 'grid'
    }
  })
  const setViewMode = (v: 'grid' | 'list') => {
    setView(v)
    try {
      localStorage.setItem('yq-view', v)
    } catch {
      /* ignore */
    }
  }
  const [inStockOnly, setInStockOnly] = useState(false)
  const [dealsOnly, setDealsOnly] = useState(false)
  const [visible, setVisible] = useState(FIRST)
  const hasDeals = useMemo(() => applyQuickFilters(items, DEALS).length > 0, [items])
  const filtering = inStockOnly || dealsOnly
  const grid = useMemo(() => {
    let r = items
    if (inStockOnly) r = r.filter((i) => i.stock_status !== 'out_of_stock')
    if (dealsOnly) r = applyQuickFilters(r, DEALS)
    if (!filtering && railCodes.size) r = [...r.filter((i) => !railCodes.has(i.item_code)), ...r.filter((i) => railCodes.has(i.item_code))]
    return r
  }, [items, inStockOnly, dealsOnly, filtering, railCodes])

  // the first grid page is on screen; the rest of the first chunk arrives on idle
  const chunk = desktop ? CHUNK.desktop : CHUNK.phone
  useEffect(() => {
    let idle = 0
    let t = 0
    const grow = () => setVisible((v) => (v < chunk ? chunk : v))
    if (typeof window.requestIdleCallback === 'function') idle = window.requestIdleCallback(grow, { timeout: 2500 })
    else t = window.setTimeout(grow, 1200)
    return () => {
      window.clearTimeout(t)
      if (idle) window.cancelIdleCallback(idle)
    }
  }, [chunk])
  const toggleFilter = (which: 'stock' | 'deals') => {
    if (which === 'stock') setInStockOnly((v) => !v)
    else setDealsOnly((v) => !v)
    setVisible(FIRST)
  }
  const shownCount = Math.min(visible, grid.length)

  // Everything that can add a section (or a card inside one) below the fold — and nothing else:
  // this page re-renders on every cart change, and each rescan forces layout.
  useReveal(rootRef, [desktop, view, shownCount, grid.length, rails, showRegulars, viewed.length >= 3, Boolean(continueCard)])

  return (
    <div ref={rootRef} className={cn(STACK, 'mt-7 lg:mt-14')}>
      <Sect>
        {showRegulars && (
          <Rail id="regular" title={S.home.regular} seeAllTo="/quick?load=regular">
            {regulars.map((r) => (
              <MarketCard key={r.item.item_code} item={r.item} variant="compact" from="regulars" presetQty={r.qty} />
            ))}
          </Rail>
        )}
      </Sect>
      <Sect>
        {rails.essentials.length >= 3 && (
          <Rail id="essentials" title={S.rails.essentials} seeAllTo="/shop?f=best">
            {rails.essentials.map((it) => (
              <MarketCard key={it.item_code} item={it} variant="compact" from="essentials" />
            ))}
          </Rail>
        )}
      </Sect>
      <Sect>
        <DealsSection items={dealItems} offers={offerRules} layout={desktop ? 'desktop' : 'phone'} />
      </Sect>
      <Sect>
        {rails.fresh.length >= 3 && (
          <Rail id="fresh" title={S.rails.fresh} seeAllTo="/shop?f=new">
            {rails.fresh.map((it) => (
              <MarketCard key={it.item_code} item={it} variant="compact" from="fresh" />
            ))}
          </Rail>
        )}
      </Sect>
      <Sect reveal={desktop}>
        <BrandTiles items={items} />
      </Sect>
      <Sect>
        {rails.moving.length >= 3 && (
          // the one tinted block in the middle of the page: after the cream deals panel the home
          // runs white-on-warm for thousands of pixels, and this is the section that earns a canvas
          <div className="bleed bg-fresh-soft/60 pb-6 pt-5 [&>*]:!mt-0 lg:mx-0 lg:rounded-xl lg:px-7 lg:pb-7 lg:pt-6 2xl:px-8">
            <Rail id="moving" title={S.rails.moving} seeAllTo="/shop?f=moving">
              {rails.moving.map((it) => (
                <MarketCard key={it.item_code} item={it} variant="compact" from="moving" />
              ))}
            </Rail>
          </div>
        )}
      </Sect>
      <Sect>
        {viewed.length >= 3 && (
          <Rail id="viewed" title={S.home.viewed}>
            {viewed.map((it) => (
              <MarketCard key={it.item_code} item={it} variant="compact" from="viewed" />
            ))}
          </Rail>
        )}
      </Sect>
      <Sect reveal={desktop}>{continueCard}</Sect>

      {/* ── the grid ── */}
      <Sect reveal={desktop}>
        <section aria-labelledby="home-all">
          {/* phone: heading and count on one line, then the filters flush left with the view
              toggle at the end — one row of pills instead of three styles fighting for the line */}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-2.5">
            <h2 id="home-all" className="font-display text-lg font-bold text-ink lg:text-xl">
              {S.home.all}
            </h2>
            <span aria-live="polite" className="text-sm tnum text-ink-2">
              {S.states.products(grid.length)}
            </span>
            <div className="flex basis-full items-center gap-1.5 lg:ms-auto lg:basis-auto">
              {[
                { on: inStockOnly, which: 'stock' as const, label: S.shop.inStock },
                { on: dealsOnly, which: 'deals' as const, label: S.nav.deals, hide: !hasDeals },
              ]
                .filter((t) => !t.hide)
                .map((t) => (
                  // 36 px pills, tapped with a thumb: `after` takes the hit area to 44 without changing the look
                  <button key={t.which} type="button" onClick={() => toggleFilter(t.which)} aria-pressed={t.on} className={cn('relative h-9 rounded-full border px-3.5 text-sm font-medium transition duration-1 ease-m after:absolute after:-inset-y-1 after:inset-x-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', t.on ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink-2 hover:bg-plum-wash')}>
                    {t.label}
                  </button>
                ))}
              <div className="ms-auto inline-flex rounded-sm border border-line bg-surface p-0.5 lg:ms-1" role="group" aria-label={S.home.view}>
                {(['grid', 'list'] as const).map((v) => (
                  // the hit area grows to 44 px tall (vertically only: the two sit edge to edge)
                  <button key={v} type="button" onClick={() => setViewMode(v)} aria-pressed={view === v} aria-label={v === 'grid' ? S.card.grid : S.card.list} className={cn('relative grid h-8 w-9 place-items-center rounded-xs transition duration-1 ease-m after:absolute after:-inset-y-1.5 after:inset-x-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', view === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-plum-wash')}>
                    {v === 'grid' ? <LayoutGrid size={15} aria-hidden="true" /> : <List size={15} aria-hidden="true" />}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {grid.length === 0 ? (
            <EmptyState
              className="mt-3"
              title={S.states.noMatch(dealsOnly ? S.nav.deals : S.shop.inStock)}
              action={
                <Button
                  variant="secondary"
                  onClick={() => {
                    setInStockOnly(false)
                    setDealsOnly(false)
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
            // The deals grid's ceiling, for the same reason: past four columns a wholesale name
            // clipped to "20W Charger + Type-C Cable (US…" — and the end of the name is the half
            // that tells two SKUs apart in this catalogue. The fifth column waits for 3xl.
            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-4 3xl:grid-cols-5">
              {grid.slice(0, shownCount).map((it) => (
                <MarketCard key={it.item_code} item={it} />
              ))}
            </div>
          )}
          {shownCount < grid.length && (
            <div className="mt-7 text-center">
              <Button variant="secondary" size="lg" onClick={() => setVisible((v) => v + chunk)}>
                {S.states.showMore(grid.length - shownCount)}
              </Button>
            </div>
          )}
        </section>
      </Sect>

      {/* the closing statement spans the container: on desktop the shell hosts it below the main
          column + mini-cart row (PageTail), elsewhere it stays right here */}
      <PageTail>
        <Sect reveal={desktop}>
          <CtaBand canReorder={lastLines.length > 0} />
        </Sect>
        <Footer prices={fmtDate(data?.prices_updated)} stock={fmtDate(data?.stock_as_of)} rep={rep} />
      </PageTail>
    </div>
  )
}
