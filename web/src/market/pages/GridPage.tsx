import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowDownUp, ChevronDown, ClipboardList, Flame, Heart, Hourglass, LayoutGrid, List, Percent, Sparkles, Star, Tag, TrendingDown, X, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { CategoryBanner } from '../components/CampaignStrip'
import { MarketCard } from '../components/MarketCard'
import { useMarket } from '../MarketContext'
import { ClosedState, ConnectingState, EmptyState } from '../components/States'
import { revealActiveChip, useEdgeFade } from '../hooks/useEdgeFade'
import { useReveal } from '../hooks/useReveal'
import { track } from '../lib/events'
import { applyQuickFilters, facetsFor, isDeal, isEssential, isMoving, isOffer, isRealDrop, matchesFacets, parseFilters, readFacets, shelfOrder, sortItems, type QuickFilter, type SortMode } from '../lib/facets'
import { hasBadge } from '../lib/format'
import { useShell } from '../shell/ShellContext'
import { isDesktopLike } from '../shell/useViewport'
import { useSaved } from '../store/saved'
import { S } from '../strings'
import { Button, LinkButton } from '../ui/Button'
import { Select } from '../ui/Field'
import { CardSkeleton } from '../ui/Skeleton'

/**
 * A shelf: every product of a category, a destination (Deals, Restock essentials…) or the whole
 * catalog, with quick filters, inferred facets, sort and grid/list density. All state is in the URL
 * so a filtered shelf can be shared, refreshed and linked from rails and slides ("See all").
 *
 * Phones/tablets (under the plum search band, useSearchBand on the page): the chips row — Sort ·
 * In stock · Deals · Price drops · Last chance · Saved — pins at `--m-search-h`, Keeta-style, and a
 * category's own facets ride a second row under it (they are a shelf's real navigation, not a
 * postscript to the quick filters; on desktop both rows wrap statically in the toolbar).
 * It gains a hairline once it is stuck (a 1px sentinel sits exactly `--m-search-h` above the row,
 * so it leaves the viewport the moment the row sticks — no scroll handler), and fills the band's
 * rounded corners with the canvas. Changing a filter while stuck brings the results back to the top
 * under the row. Desktop keeps the toolbar (title · count · view · sort) and the same chips, static;
 * its grid cards fade and rise in (hooks/useReveal), stagger by column.
 */
const FIRST = 24
const CHUNK = 48

type Sort = SortMode

const SORT_LABEL: Record<Sort, string> = {
  shelf: S.shop.sort,
  popular: S.shop.popular,
  price_asc: S.shop.lowest,
  price_desc: S.shop.highest,
}

interface ChipDef {
  key: QuickFilter
  label: string
  icon?: LucideIcon
  /** the amber disc the header's Deals link uses */
  deal?: boolean
  /** a live-stock dot instead of an icon */
  dot?: boolean
  /** shown only when it matches something here (or is on) */
  test?: (i: ShopItem) => boolean
  /** extra chips: on a category shelf only while on (the facets matter more there) */
  extra?: boolean
}

const CHIPS: ChipDef[] = [
  { key: 'instock', label: S.shop.inStock, dot: true },
  { key: 'deals', label: S.shop.deals, icon: Tag, deal: true, test: isDeal },
  { key: 'drops', label: S.shop.drops, icon: TrendingDown, test: isRealDrop },
  { key: 'clearance', label: S.shop.clearance, icon: Hourglass, test: (i) => hasBadge(i, 'clearance') },
  { key: 'saved', label: S.shop.saved, icon: Heart },
  { key: 'best', label: S.shop.essentials, icon: Star, test: isEssential, extra: true },
  { key: 'moving', label: S.shop.moving, icon: Flame, test: isMoving, extra: true },
  { key: 'new', label: S.shop.new, icon: Sparkles, test: (i) => hasBadge(i, 'new'), extra: true },
  { key: 'offers', label: S.shop.offers, icon: Percent, test: isOffer, extra: true },
]

export function GridPage({ title, line, items, category, breadcrumb, lead }: { title: string; /** one muted line under the title (destination shelves) */ line?: string | null; items: ShopItem[]; category?: string; breadcrumb?: { label: string; to: string }; /** content between the title and the chips (Browse: the category tiles) */ lead?: ReactNode }) {
  const [params, setParams] = useSearchParams()
  const { viewport } = useShell()
  const desktop = isDesktopLike(viewport)
  const { campaigns, data, status, reload } = useMarket()
  const savedCodes = useSaved()
  const sort = (params.get('sort') as Sort) || 'shelf'
  const filters = useMemo(() => parseFilters(params.get('f')), [params])
  // ?brand= (the home brand tiles): one brand, matched case-insensitively
  const brand = (params.get('brand') || '').trim()
  const groups = useMemo(() => (category ? facetsFor(category, items) : []), [category, items])
  const sel = useMemo(() => readFacets(params, groups), [params, groups])
  const [visible, setVisible] = useState(FIRST)
  const [view, setView] = useState<'grid' | 'list'>(() => {
    try {
      return (localStorage.getItem('yq-view') as 'grid' | 'list') || 'grid'
    } catch {
      return 'grid'
    }
  })
  const setViewMode = (v: 'grid' | 'list') => {
    setView(v)
    track('view', { meta: { rail: 'view_mode', code: v } })
    try {
      localStorage.setItem('yq-view', v)
    } catch {
      /* ignore */
    }
  }

  const update = (fn: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params)
    fn(next)
    setParams(next, { replace: true })
  }
  const toggleFilter = (f: QuickFilter) => {
    const set = new Set(filters)
    if (set.has(f)) set.delete(f)
    else set.add(f)
    update((p) => (set.size ? p.set('f', Array.from(set).join(',')) : p.delete('f')))
    if (!filters.has(f)) track('search', { meta: { rail: 'quick_filter', q: f } })
  }
  const setFacet = (key: string, value: string | null) => {
    update((p) => (value ? p.set(key, value) : p.delete(key)))
    if (value && category) track('search', { meta: { rail: 'facet', q: `${category}:${key}=${value}` } })
  }
  const setSort = (v: string) => update((p) => (v === 'shelf' ? p.delete('sort') : p.set('sort', v)))

  const savedKey = filters.has('saved') ? savedCodes.join('|') : ''
  const result = useMemo(() => {
    void savedKey // the Saved shelf follows the heart
    let r = applyQuickFilters(items, filters)
    if (brand) r = r.filter((i) => (i.brand || '').trim().toUpperCase() === brand.toUpperCase())
    if (groups.length) r = r.filter((i) => matchesFacets(i, groups, sel))
    return sort === 'shelf' ? shelfOrder(r, filters) : sortItems(r, sort)
  }, [items, filters, brand, groups, sel, sort, savedKey])
  // a new filter/sort/list starts the page again (derived during render)
  const signature = `${params.toString()}|${items.length}`
  const [sigSeen, setSigSeen] = useState(signature)
  if (sigSeen !== signature) {
    setSigSeen(signature)
    setVisible(FIRST)
  }
  const shown = Math.min(visible, result.length)
  const active = filters.size + Object.keys(sel).length + (brand ? 1 : 0)

  /* ── stuck chips row (phones/tablets) ── */
  const anchorRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLSpanElement>(null)
  const [stuck, setStuck] = useState(false)
  const stuckRef = useRef(false)
  useEffect(() => {
    const el = sentinelRef.current
    if (desktop || !el || typeof IntersectionObserver !== 'function') return
    const io = new IntersectionObserver(([entry]) => {
      const next = !entry.isIntersecting && entry.boundingClientRect.top < 0
      stuckRef.current = next
      setStuck(next)
    })
    io.observe(el)
    return () => {
      io.disconnect()
      stuckRef.current = false
      setStuck(false)
    }
  }, [desktop])

  // a filter changed while the row was pinned: show the new shelf from its first line
  const paramsKey = params.toString()
  const keySeen = useRef(paramsKey)
  useLayoutEffect(() => {
    if (keySeen.current === paramsKey) return
    keySeen.current = paramsKey
    const anchor = anchorRef.current
    if (desktop || !stuckRef.current || !anchor) return
    const band = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--m-search-h')) || 0
    // +1: just past the sticking point, so the row stays marked stuck
    const y = Math.max(0, anchor.getBoundingClientRect().bottom + window.scrollY - band + 1)
    if (window.scrollY > y) window.scrollTo({ top: y })
  }, [paramsKey, desktop])

  /* ── nothing lands under the pinned rows (WCAG 2.4.11) ──
   * market.css only clears the phone band; this shelf pins the chips row under it, and the desktop
   * header carries its category strip. Measure whichever is over the grid and raise <html>'s
   * scroll-padding-top for as long as this page is mounted (Shift+Tab, #anchors, scrollIntoView). */
  const chipsRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const root = document.documentElement
    const clear = () => root.style.removeProperty('scroll-padding-top')
    const el = desktop ? document.querySelector('header') : chipsRef.current
    if (!el) return clear
    const apply = () => {
      const h = Math.round(el.getBoundingClientRect().height) + 8
      root.style.scrollPaddingTop = desktop ? `${h}px` : `calc(var(--m-search-h, 0px) + ${h}px)`
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return clear
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => {
      ro.disconnect()
      clear()
    }
  }, [desktop])

  /* ── the chips row scrolls: the active chip must be on screen, and the row must look scrollable ──
   * A filtered shelf that looks exactly like the whole shelf is the bug: on a phone "Essentials" or
   * "Last chance" can sit 600px into the row. Bring the first pressed chip into view inside the
   * scroller (never the page), and fade whichever edge has more chips behind it. */
  const scrollerRef = useRef<HTMLDivElement>(null)
  const facetsRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (desktop) return
    revealActiveChip(scrollerRef.current)
    revealActiveChip(facetsRef.current)
  }, [paramsKey, desktop, items])
  const mask = useEdgeFade(scrollerRef, !desktop, [paramsKey, items, groups])
  const facetMask = useEdgeFade(facetsRef, !desktop && groups.length > 0, [paramsKey, items, groups])

  /* ── desktop reveal: stagger by column ── */
  const gridRef = useRef<HTMLDivElement>(null)
  const [cols, setCols] = useState(4)
  const revealGrid = desktop && view === 'grid' && result.length > 0
  useLayoutEffect(() => {
    const el = gridRef.current
    if (!revealGrid || !el) return
    const measure = () => {
      const n = getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length || 4
      setCols((c) => (c === n ? c : n))
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [revealGrid])
  useReveal(gridRef, [revealGrid, result, shown, cols])

  /* ── chips ── */
  const chipCls = (on: boolean, extra?: string) =>
    cn(
      'relative inline-flex h-10 shrink-0 select-none items-center gap-1.5 whitespace-nowrap rounded-full px-3.5 text-sm font-semibold transition duration-1 ease-m after:absolute after:inset-x-0 after:-inset-y-[3px] active:scale-[.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
      on ? 'bg-ink text-white shadow-1' : 'bg-surface text-ink shadow-1 ring-1 ring-inset ring-line hover:bg-plum-wash hover:ring-ink/15',
      extra,
    )
  const chips = CHIPS.filter((c) => filters.has(c.key) || ((!c.extra || !category) && (!c.test || items.some(c.test))))

  const viewToggle = (
    <div className="inline-flex shrink-0 rounded-sm border border-line bg-surface p-0.5" role="group" aria-label={S.shop.view}>
      {(['grid', 'list'] as const).map((v) => (
        <button key={v} type="button" onClick={() => setViewMode(v)} aria-pressed={view === v} aria-label={v === 'grid' ? S.card.grid : S.card.list} className={cn('relative grid h-9 w-10 place-items-center rounded-xs transition duration-1 ease-m after:absolute after:-inset-y-1 after:inset-x-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', view === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-plum-wash')}>
          {v === 'grid' ? <LayoutGrid size={15} aria-hidden="true" /> : <List size={15} aria-hidden="true" />}
        </button>
      ))}
    </div>
  )

  const clearAll = () => update((p) => [...p.keys()].forEach((k) => k !== 'sort' && p.delete(k)))

  return (
    <div className="px-gutter lg:px-0">
      {category && <CategoryBanner campaigns={campaigns} category={category} items={items} className="mt-3 lg:mb-4 lg:mt-1" />}

      {/* desktop toolbar — the phone band carries the title */}
      <div className="hidden items-end gap-3 lg:flex lg:pt-2">
        <div className="min-w-0 flex-1">
          {breadcrumb && (
            <Link to={breadcrumb.to} className="-ms-1 inline-flex h-8 items-center rounded-xs px-1 text-xs font-semibold text-plum hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              {breadcrumb.label}
            </Link>
          )}
          <h1 className="truncate font-display text-2xl font-bold text-ink">
            {title} {data && <span className="font-sans text-sm font-normal tnum tracking-normal text-ink-2">· {S.shop.products(result.length)}</span>}
          </h1>
          {line && <p className="mt-1 text-sm text-ink-2">{line}</p>}
        </div>
        {viewToggle}
        <div className="w-[11rem] shrink-0">
          <Select aria-label={S.shop.sort} value={sort} onChange={(e) => setSort(e.target.value)} className="!h-10 !rounded-sm">
            <option value="shelf">{S.shop.shelf}</option>
            <option value="popular">{S.shop.popular}</option>
            <option value="price_asc">{S.shop.priceUp}</option>
            <option value="price_desc">{S.shop.priceDown}</option>
          </Select>
        </div>
      </div>
      {line && <p className="mt-3 text-sm leading-snug text-ink-2 lg:hidden">{line}</p>}

      {lead}

      {/* the spacer above the row; its sentinel ends --m-search-h above the row's place, so it leaves the viewport exactly when the row sticks */}
      <div ref={anchorRef} aria-hidden="true" className="pointer-events-none relative pt-2 lg:pt-3">
        <span ref={sentinelRef} className="absolute inset-x-0 h-px" style={{ top: 'calc(100% - var(--m-search-h, 0px) - 1px)' }} />
      </div>
      <div
        ref={chipsRef}
        data-stuck={stuck ? '' : undefined}
        className="sticky z-[29] -mx-gutter border-b border-transparent bg-canvas transition-[border-color,box-shadow] duration-2 ease-m before:pointer-events-none before:absolute before:inset-x-0 before:bottom-full before:hidden before:h-[22px] before:bg-canvas data-[stuck]:border-line data-[stuck]:shadow-[0_10px_18px_-16px_hsl(268_30%_10%/0.45)] data-[stuck]:before:block lg:static lg:mx-0 lg:border-0 lg:bg-transparent lg:shadow-none"
        style={{ top: 'var(--m-search-h, 0px)' }}
      >
        <div
          ref={scrollerRef}
          role="group"
          aria-label={S.shop.filters}
          style={mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined}
          className="no-scrollbar flex items-center gap-2 overflow-x-auto px-gutter py-2 lg:flex-wrap lg:overflow-visible lg:px-0 lg:py-0"
        >
          <label className={chipCls(sort !== 'shelf', 'cursor-pointer pe-2.5 focus-within:ring-2 focus-within:ring-focus/70 lg:hidden')}>
            <ArrowDownUp size={15} strokeWidth={2} aria-hidden="true" />
            <span aria-hidden="true">{SORT_LABEL[sort] || S.shop.sort}</span>
            <ChevronDown size={14} strokeWidth={2} aria-hidden="true" className="opacity-70" />
            <select aria-label={S.shop.sort} value={sort} onChange={(e) => setSort(e.target.value)} className="absolute inset-0 z-[1] h-full w-full cursor-pointer appearance-none rounded-full text-[16px] opacity-0">
              <option value="shelf">{S.shop.shelf}</option>
              <option value="popular">{S.shop.popular}</option>
              <option value="price_asc">{S.shop.priceUp}</option>
              <option value="price_desc">{S.shop.priceDown}</option>
            </select>
          </label>
          <span aria-hidden="true" className="h-6 w-px shrink-0 bg-line lg:hidden" />
          {brand && (
            <button type="button" onClick={() => update((p) => p.delete('brand'))} aria-pressed="true" aria-label={S.shop.removeFilter(brand)} className={chipCls(true, 'pe-3')}>
              {brand} <X size={14} strokeWidth={2.2} aria-hidden="true" className="opacity-70" />
            </button>
          )}
          {chips.map((c) => {
            const on = filters.has(c.key)
            const Icon = c.icon
            return (
              <button key={c.key} type="button" onClick={() => toggleFilter(c.key)} aria-pressed={on} className={chipCls(on, c.deal || c.dot ? 'ps-2.5' : undefined)}>
                {c.dot ? (
                  <span aria-hidden="true" className="grid h-5 w-5 place-items-center">
                    <span className={cn('h-2 w-2 rounded-full ring-[3px]', on ? 'bg-fresh ring-fresh/30' : 'bg-fresh ring-fresh-soft')} />
                  </span>
                ) : c.deal && Icon ? (
                  <span aria-hidden="true" className="grid h-5 w-5 place-items-center rounded-full bg-deal-soft text-deal-ink">
                    <Icon size={12} strokeWidth={2.2} />
                  </span>
                ) : Icon ? (
                  <Icon size={15} strokeWidth={2} aria-hidden="true" className={cn(!on && 'text-ink-2', on && c.key === 'saved' && 'fill-current')} />
                ) : null}
                {c.label}
              </button>
            )
          })}
          {active > 0 && (
            <button type="button" onClick={clearAll} className="relative inline-flex h-10 shrink-0 items-center gap-1 rounded-full px-3 text-sm font-semibold text-plum transition duration-1 ease-m after:absolute after:inset-x-0 after:-inset-y-[3px] hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              <X size={14} aria-hidden="true" /> {S.shop.clear}
            </button>
          )}
          <span aria-hidden="true" className="w-px shrink-0 lg:hidden" />
        </div>
      </div>

      {/* ── the facets get their own row ──
       * On a 78-line cable shelf the inferred facets (Type-C 61 · Lightning 32 · 1 m 42 · 25–65W 18…)
       * are how a merchant actually finds a line, and behind five quick filters they started 1.4
       * screens to the right and ran 5.5 screens long. They scroll on their own row from the gutter,
       * under the pinned quick filters rather than inside them, and wrap beside them on desktop. */}
      {groups.length > 0 && (
        <div
          ref={facetsRef}
          role="group"
          aria-label={S.shop.refine}
          style={facetMask ? { maskImage: facetMask, WebkitMaskImage: facetMask } : undefined}
          className="no-scrollbar -mx-gutter flex items-center gap-2 overflow-x-auto px-gutter pb-2 pt-0.5 lg:mx-0 lg:mt-2 lg:flex-wrap lg:overflow-visible lg:px-0 lg:pb-0 lg:pt-0"
        >
          {groups.map((g, gi) => (
            <span key={g.key} role="group" aria-label={g.label} className="flex shrink-0 items-center gap-2 lg:flex-wrap">
              {gi > 0 && <span aria-hidden="true" className="h-6 w-px shrink-0 bg-line" />}
              {g.values.map((v) => {
                const on = sel[g.key] === v.key
                return (
                  <button key={v.key} type="button" onClick={() => setFacet(g.key, on ? null : v.key)} aria-pressed={on} className={chipCls(on)}>
                    {v.label} <span className={cn('text-xs font-medium tnum', on ? 'text-white/70' : 'text-ink-3')}>{v.count}</span>
                  </button>
                )
              })}
            </span>
          ))}
          <span aria-hidden="true" className="w-px shrink-0 lg:hidden" />
        </div>
      )}

      {/* phone meta row: live count · paste a list · density */}
      <div className="mt-2 flex items-center gap-1 lg:hidden">
        <p aria-live="polite" className="min-w-0 flex-1 truncate text-sm tnum text-ink-2">
          {data ? S.shop.products(result.length) : ''}
        </p>
        <Link to="/quick" onClick={() => track('rail_click', { meta: { rail: 'browse_paste' } })} className="relative inline-flex h-10 shrink-0 items-center gap-1.5 rounded-sm px-2.5 text-sm font-semibold text-plum transition duration-1 ease-m after:absolute after:inset-x-0 after:-inset-y-0.5 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          <ClipboardList size={15} aria-hidden="true" /> {S.shop.paste}
        </Link>
        {viewToggle}
      </div>

      {!data ? (
        status === 'closed' ? (
          <ClosedState />
        ) : status === 'error' ? (
          <ConnectingState onRetry={reload} failed />
        ) : (
          <div aria-busy="true" aria-label={S.states.loading} className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:mt-4 lg:grid-cols-4 3xl:grid-cols-5">
            {Array.from({ length: 8 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        )
      ) : result.length === 0 ? (
        // nothing saved is not a filter miss: the shelf is empty because the heart was never tapped
        filters.has('saved') && !savedCodes.length ? (
          <EmptyState
            className="mt-4 lg:mt-5"
            title={S.shop.savedNoneTitle}
            hint={S.shop.savedNone}
            action={
              <LinkButton to="/shop" variant="secondary">
                {S.restock.browse}
              </LinkButton>
            }
          />
        ) : (
          <EmptyState className="mt-4 lg:mt-5" title={S.shop.none} hint={S.shop.noneHint} action={active > 0 ? <Button variant="secondary" onClick={clearAll}>{S.shop.clear}</Button> : undefined} />
        )
      ) : view === 'list' ? (
        <div className="mt-1 lg:mt-3">
          {result.slice(0, shown).map((it) => (
            <MarketCard key={it.item_code} item={it} variant="list" from="grid" />
          ))}
        </div>
      ) : (
        // the one desktop shelf ladder (components/Rail.tsx, components/DealsSection.tsx): 4 up to
        // 1800, 5 beyond it. A fifth column at 1440 left ~175px of card, too narrow for the price
        // row to carry the old price beside today's — and a 5-up grid under a 4-up rail on the
        // same page read as two pages stitched together.
        <div ref={gridRef} className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:mt-4 lg:grid-cols-4 3xl:grid-cols-5">
          {result.slice(0, shown).map((it, i) =>
            desktop ? (
              // .reveal on a plain wrapper (the card keeps its hover transition and .cv-card); className never changes
              <div key={it.item_code} className="reveal grid grid-cols-1" style={{ ['--i' as string]: i % cols }}>
                <MarketCard item={it} from="grid" priority={i < 2} />
              </div>
            ) : (
              <MarketCard key={it.item_code} item={it} from="grid" priority={i < 2} />
            ),
          )}
        </div>
      )}
      {shown < result.length && (
        <div className="mt-7 text-center">
          <Button variant="secondary" size="lg" onClick={() => setVisible((v) => v + CHUNK)}>
            {S.states.showMore(result.length - shown)}
          </Button>
        </div>
      )}
    </div>
  )
}
