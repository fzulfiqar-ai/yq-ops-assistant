import { useMemo, useState } from 'react'
import { Navigate, useLocation, useNavigationType, useSearchParams } from 'react-router-dom'
import { CategoryTiles } from '../components/HomeBlocks'
import { useMarket } from '../MarketContext'
import { isOffer, parseFilters, type QuickFilter } from '../lib/facets'
import { categoryTiles, dealSets, dealsLine, type DealSets } from '../lib/home'
import { usePageTitle, useSearchBand } from '../shell/ShellContext'
import { S } from '../strings'
import { GridPage } from './GridPage'

/**
 * /shop — Browse. The root is the whole shelf: round category tiles, then the pinned chips row and
 * every product; its chips filter in place (the page stays "Browse" and keeps its tiles, so nothing
 * jumps under the finger). A link with ?f= makes it a destination shelf with its own title and line — Deals
 * (/shop?f=deals, "Stock-Up Deals" only when a real price drop or offer exists, else "Last-Chance
 * Stock"), Last chance (f=clearance), Price drops, Restock essentials (f=best), Moving fast
 * (f=moving), New arrivals, Saved. The legacy f=offers link (it used to mean "deals") goes to Deals
 * while no live offer exists, so an old campaign never lands on an empty shelf.
 */

/** Title priority when several destination filters are on. */
const ORDER: QuickFilter[] = ['saved', 'deals', 'clearance', 'drops', 'offers', 'best', 'moving', 'new']

function destination(filters: ReadonlySet<QuickFilter>, sets: DealSets, brand: string): { title: string; line: string | null } | null {
  const f = ORDER.find((k) => filters.has(k))
  switch (f) {
    case 'saved':
      return { title: S.me.saved, line: null }
    case 'deals':
      // the same honest line as the home Deals section: name only the kinds this shelf really has
      return sets.hasRealDeals ? { title: S.deals.title, line: dealsLine(sets) } : { title: S.deals.lastChance, line: S.deals.line }
    case 'clearance':
      return { title: S.deals.lastChance, line: S.deals.line }
    case 'drops':
      return { title: S.deals.drops, line: S.slides.dropsLine }
    case 'offers':
      return { title: S.deals.offers, line: null }
    case 'best':
      return { title: S.rails.essentials, line: S.slides.essentialsLine }
    case 'moving':
      return { title: S.rails.moving, line: S.slides.movingLine }
    case 'new':
      return { title: S.rails.arrived, line: null }
    default:
      return brand ? { title: brand, line: null } : null
  }
}

export default function ShopPage() {
  const { items, categories, data, status } = useMarket()
  useSearchBand()
  const [params] = useSearchParams()
  const filters = useMemo(() => parseFilters(params.get('f')), [params])
  const sets = useMemo(() => dealSets(items, data?.offers), [items, data])
  // Root or destination is decided by how the query was reached: a link or Back decides again; the
  // chips row (a replace) keeps it; the product panel (same query, new history entry) leaves it alone.
  const { search } = useLocation()
  const navType = useNavigationType()
  const current = destination(filters, sets, (params.get('brand') || '').trim())
  const [entry, setEntry] = useState(() => ({ search, root: current == null }))
  if (entry.search !== search) setEntry({ search, root: navType === 'REPLACE' ? entry.root : current == null })
  const dest = entry.root ? null : current
  const title = dest?.title ?? S.shop.title
  usePageTitle(title, Boolean(dest), `${title} · ${S.brand}`)
  const tiles = useMemo(() => categoryTiles(items, categories), [items, categories])
  const root = entry.root

  if (status === 'ready' && params.get('f') === 'offers' && !items.some(isOffer)) return <Navigate to="/shop?f=deals" replace />

  return (
    <GridPage
      title={title}
      line={dest?.line}
      items={items}
      breadcrumb={dest ? { label: S.shop.title, to: '/shop' } : undefined}
      lead={root ? <CategoryTiles tiles={tiles} className="mt-4 lg:mt-5" /> : null}
    />
  )
}
