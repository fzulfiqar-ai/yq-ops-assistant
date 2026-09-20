import { useMemo } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { ClosedState, ConnectingState } from '../components/States'
import { useMarket } from '../MarketContext'
import { categoryFromSlug, niceCategory } from '../lib/format'
import { usePageTitle, useSearchBand } from '../shell/ShellContext'
import { S } from '../strings'
import { GridPage } from './GridPage'

/**
 * /t/{category} — one category's shelf: its banner (a campaign aimed at the category, else a
 * creative composed from its own lines), the pinned chips row with its facets, and the grid.
 */
export default function CategoryPage() {
  const { category: slug = '' } = useParams()
  const { items, categories, status, reload } = useMarket()
  useSearchBand()
  const cat = categoryFromSlug(slug, categories)
  const title = cat ? niceCategory(cat) : ''
  usePageTitle(title || S.shop.title, true, title ? `${title} · ${S.brand}` : undefined)
  const inCat = useMemo(() => items.filter((i) => (i.category || 'OTHER') === cat), [items, cat])
  if (!cat && status === 'ready') return <Navigate to="/shop" replace />
  // no catalog yet: a closed shop or a failed load must say so, not sit blank forever
  if (!cat && status === 'closed') return <ClosedState />
  if (!cat && status === 'error') return <ConnectingState onRetry={reload} failed />
  if (!cat) return null
  return <GridPage title={title} items={inCat} category={cat} breadcrumb={{ label: S.shop.title, to: '/shop' }} />
}
