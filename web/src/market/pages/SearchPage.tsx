import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MarketCard } from '../components/MarketCard'
import { Rail } from '../components/Rail'
import { SearchField } from '../components/SearchField'
import { SearchEmpty, SearchGroups, SearchPreparing } from '../components/SearchResults'
import { groupResults } from '../lib/searchGroups'
import { useMarket } from '../MarketContext'
import { rememberSearch } from '../lib/device'
import { track, trackSearch } from '../lib/events'
import { bestSellers } from '../lib/home'
import { usePageTitle } from '../shell/ShellContext'
import { S } from '../strings'

/**
 * /search — reached from the pinned search band. Instant, grouped, with inline Add. The query lives
 * in the URL (survives refresh, shareable). The bar stays pinned while results scroll; empty and
 * unfocused it rotates example queries. Enter on an exact code adds it at the default quantity and
 * keeps the field ready for the next code. Empty query: recent searches → Popular restocks (real
 * best sellers in stock, one-tap Add) → categories. Zero results is never a dead end: essentials.
 */
export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const m = useMarket()
  const [q, setQ] = useState(params.get('q') || '')
  const inputRef = useRef<HTMLInputElement>(null)
  usePageTitle(S.nav.search, true, q.trim() ? `${q.trim()} · ${S.brand}` : `${S.nav.search} · ${S.brand}`)

  useEffect(() => {
    void m.ensureIndex()
  }, [m])
  useEffect(() => {
    const cur = params.get('q') || ''
    if (cur !== q.trim()) setParams(q.trim() ? { q: q.trim() } : {}, { replace: true })
  }, [q, params, setParams])

  const [search, setSearch] = useState<null | { searchItems: typeof import('../lib/search').searchItems; suggest: typeof import('../lib/search').suggest }>(null)
  useEffect(() => {
    let alive = true
    import('../lib/search').then((mod) => alive && setSearch({ searchItems: mod.searchItems, suggest: mod.suggest }))
    return () => {
      alive = false
    }
  }, [])

  const grouped = useMemo(() => (search ? groupResults(q, m.items, m.index, search.searchItems) : { codes: [], products: [], categories: [], exact: null }), [q, m.items, m.index, search])
  const flat = grouped.codes.length + grouped.products.length
  const hints = useMemo(() => (search && m.index && q.trim() && !flat ? search.suggest(m.index, q) : []), [search, m.index, q, flat])
  const best = useMemo(() => bestSellers(m.items), [m.items])

  useEffect(() => {
    if (!q.trim() || !m.index) return
    trackSearch(q, flat)
    const id = window.setTimeout(() => rememberSearch(q), 1200)
    return () => window.clearTimeout(id)
  }, [q, flat, m.index])

  const submit = (v: string) => {
    const query = v.trim()
    if (!query) return
    rememberSearch(query)
    if (grouped.exact && grouped.exact.stock_status !== 'out_of_stock') {
      m.add(grouped.exact, undefined, 'search_enter')
      track('search', { meta: { q: query.slice(0, 60), results: flat, code: grouped.exact.item_code } })
      setQ('')
      inputRef.current?.focus()
    }
  }

  return (
    <div className="px-gutter lg:px-0">
      <div className="sticky top-0 z-header -mx-gutter border-b border-line-2 bg-canvas/95 px-gutter pb-3 pt-1 backdrop-blur lg:static lg:mx-0 lg:border-0 lg:bg-transparent lg:px-0 lg:pb-2 lg:pt-2 lg:backdrop-blur-none">
        <h1 className="hidden font-display text-2xl font-bold text-ink lg:mb-3 lg:block">{S.nav.search}</h1>
        <SearchField ref={inputRef} value={q} onChange={setQ} onSubmit={submit} hints={S.search.hints} autoFocus />
      </div>
      <div className="pt-4">
        {!q.trim() ? (
          <SearchEmpty onPick={setQ} />
        ) : !m.index || !search ? (
          <SearchPreparing />
        ) : (
          <>
            <SearchGroups q={q} grouped={grouped} hints={hints} onPick={setQ} />
            {flat === 0 && best.length > 0 && (
              <Rail id="best" title={S.rails.essentials} seeAllTo="/shop?f=best">
                {best.map((it) => (
                  <MarketCard key={it.item_code} item={it} variant="compact" from="search_zero" />
                ))}
              </Rail>
            )}
          </>
        )}
      </div>
    </div>
  )
}
