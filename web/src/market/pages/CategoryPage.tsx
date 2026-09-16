import { useMemo } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { useMarket } from '../MarketContext'
import { categoryFromSlug, niceCategory } from '../lib/format'
import { usePageTitle } from '../shell/ShellContext'
import { S } from '../strings'
import { GridPage } from './GridPage'

/** /t/{category} — one category's shelf with its facets. */
export default function CategoryPage() {
  const { category: slug = '' } = useParams()
  const { items, categories, status } = useMarket()
  const cat = categoryFromSlug(slug, categories)
  const title = cat ? niceCategory(cat) : ''
  usePageTitle(title || S.shop.title, true, title ? `${title} · ${S.brand}` : undefined)
  const inCat = useMemo(() => items.filter((i) => (i.category || 'OTHER') === cat), [items, cat])
  if (!cat && status === 'ready') return <Navigate to="/shop" replace />
  if (!cat) return null
  return <GridPage title={title} items={inCat} category={cat} breadcrumb={{ label: S.shop.title, to: '/shop' }} />
}
