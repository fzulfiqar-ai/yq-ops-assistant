import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { LayoutGrid, List, SlidersHorizontal, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { CategoryBanner } from '../components/CampaignStrip'
import { MarketCard } from '../components/MarketCard'
import { useMarket } from '../MarketContext'
import { EmptyState } from '../components/States'
import { track } from '../lib/events'
import { applyQuickFilters, facetsFor, matchesFacets, parseFilters, readFacets, sortItems, type QuickFilter, type SortMode } from '../lib/facets'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Select } from '../ui/Field'

/**
 * A browsable list of products with a sticky header (title · count · view · sort), facet chips
 * inferred from the product text, quick filters and grid/list density. All state is in the URL
 * so a filtered shelf can be shared, refreshed and linked from rails ("See all").
 */
const FIRST = 24
const CHUNK = 48

export function GridPage({ title, items, category, breadcrumb }: { title: string; items: ShopItem[]; category?: string; breadcrumb?: { label: string; to: string } }) {
  const [params, setParams] = useSearchParams()
  const sort = (params.get('sort') as SortMode) || 'shelf'
  const filters = useMemo(() => parseFilters(params.get('f')), [params])
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
  }
  const setFacet = (key: string, value: string | null) => {
    update((p) => (value ? p.set(key, value) : p.delete(key)))
    if (value && category) track('search', { meta: { rail: 'facet', q: `${category}:${key}=${value}` } })
  }

  const result = useMemo(() => {
    let r = applyQuickFilters(items, filters)
    if (groups.length) r = r.filter((i) => matchesFacets(i, groups, sel))
    return sortItems(r, sort)
  }, [items, filters, groups, sel, sort])
  // a new filter/sort/list starts the page again (derived during render)
  const signature = `${params.toString()}|${items.length}`
  const [sigSeen, setSigSeen] = useState(signature)
  if (sigSeen !== signature) {
    setSigSeen(signature)
    setVisible(FIRST)
  }
  const shown = Math.min(visible, result.length)
  const active = filters.size + Object.keys(sel).length

  const chip = (on: boolean) =>
    cn('inline-flex h-10 shrink-0 items-center gap-1 rounded-full border px-3.5 text-sm font-medium transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', on ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink-2 hover:bg-plum-wash hover:text-ink')

  const { campaigns } = useMarket()
  return (
    <div className="px-gutter lg:px-0">
      {category && <CategoryBanner campaigns={campaigns} category={category} />}
      <div className="sticky top-0 z-header -mx-gutter bg-canvas/95 px-gutter pb-2 pt-1 backdrop-blur lg:static lg:mx-0 lg:bg-transparent lg:px-0 lg:backdrop-blur-none">
        <div className="flex items-center gap-2">
          {/* the phone header already shows the title; from lg the page carries it */}
          <div className="min-w-0 flex-1">
            {breadcrumb && (
              <Link to={breadcrumb.to} className="hidden h-8 items-center text-xs font-semibold text-plum hover:underline lg:inline-flex">
                {breadcrumb.label}
              </Link>
            )}
            <h1 className="hidden truncate font-display text-2xl font-bold text-ink lg:block">
              {title} <span className="font-sans text-sm font-normal tnum text-ink-2">· {S.shop.products(result.length)}</span>
            </h1>
            <p aria-live="polite" className="text-sm tnum text-ink-2 lg:hidden">
              {S.shop.products(result.length)}
            </p>
          </div>
          <div className="inline-flex rounded-sm border border-line bg-surface p-0.5" role="group" aria-label="View">
            {(['grid', 'list'] as const).map((v) => (
              <button key={v} type="button" onClick={() => setViewMode(v)} aria-pressed={view === v} aria-label={v === 'grid' ? S.card.grid : S.card.list} className={cn('grid h-9 w-10 place-items-center rounded-xs transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', view === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-plum-wash')}>
                {v === 'grid' ? <LayoutGrid size={15} aria-hidden="true" /> : <List size={15} aria-hidden="true" />}
              </button>
            ))}
          </div>
          <div className="w-[10.5rem] shrink-0">
            <Select aria-label={S.shop.sort} value={sort} onChange={(e) => update((p) => (e.target.value === 'shelf' ? p.delete('sort') : p.set('sort', e.target.value)))} className="!h-10 !rounded-sm">
              <option value="shelf">{S.shop.shelf}</option>
              <option value="popular">{S.shop.popular}</option>
              <option value="price_asc">{S.shop.priceUp}</option>
              <option value="price_desc">{S.shop.priceDown}</option>
            </Select>
          </div>
        </div>
        <div className="no-scrollbar -mx-gutter mt-2 flex items-center gap-1.5 overflow-x-auto px-gutter lg:mx-0 lg:flex-wrap lg:px-0">
          <SlidersHorizontal size={15} className="shrink-0 text-ink-3" aria-hidden="true" />
          <button type="button" onClick={() => toggleFilter('instock')} aria-pressed={filters.has('instock')} className={chip(filters.has('instock'))}>
            {S.shop.inStock}
          </button>
          {items.some((i) => (i.badges || []).includes('on_offer') || i.compare_at_bhd != null) && (
            <button type="button" onClick={() => toggleFilter('offers')} aria-pressed={filters.has('offers')} className={chip(filters.has('offers'))}>
              {S.shop.offers}
            </button>
          )}
          {items.some((i) => (i.badges || []).includes('new')) && (
            <button type="button" onClick={() => toggleFilter('new')} aria-pressed={filters.has('new')} className={chip(filters.has('new'))}>
              {S.shop.new}
            </button>
          )}
          {items.some((i) => (i.badges || []).includes('clearance')) && (
            <button type="button" onClick={() => toggleFilter('clearance')} aria-pressed={filters.has('clearance')} className={chip(filters.has('clearance'))}>
              {S.shop.clearance}
            </button>
          )}
          {items.some((i) => (i.badges || []).includes('price_drop') || i.was_bhd != null) && (
            <button type="button" onClick={() => toggleFilter('drops')} aria-pressed={filters.has('drops')} className={chip(filters.has('drops'))}>
              {S.shop.drops}
            </button>
          )}
          {groups.map((g) => (
            <span key={g.key} className="flex items-center gap-1.5 border-s border-line ps-2">
              {g.values.map((v) => {
                const on = sel[g.key] === v.key
                return (
                  <button key={v.key} type="button" onClick={() => setFacet(g.key, on ? null : v.key)} aria-pressed={on} className={chip(on)}>
                    {v.label} <span className={cn('text-xs tnum', on ? 'text-plum-ink/70' : 'text-ink-3')}>{v.count}</span>
                  </button>
                )
              })}
            </span>
          ))}
          {active > 0 && (
            <button type="button" onClick={() => update((p) => [...p.keys()].forEach((k) => k !== 'sort' && p.delete(k)))} className="inline-flex h-10 shrink-0 items-center gap-1 rounded-full px-3 text-sm font-semibold text-plum hover:bg-plum-wash">
              <X size={14} aria-hidden="true" /> {S.shop.clear}
            </button>
          )}
        </div>
      </div>

      {result.length === 0 ? (
        <EmptyState className="mt-4" title={S.states.noMatch(title)} hint={S.states.noMatchHint} action={<Button variant="secondary" onClick={() => update((p) => [...p.keys()].forEach((k) => p.delete(k)))}>{S.shop.clear}</Button>} />
      ) : view === 'list' ? (
        <div className="mt-1">
          {result.slice(0, shown).map((it) => (
            <MarketCard key={it.item_code} item={it} variant="list" from="grid" />
          ))}
        </div>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6">
          {result.slice(0, shown).map((it, i) => (
            <MarketCard key={it.item_code} item={it} from="grid" priority={i < 2} />
          ))}
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
