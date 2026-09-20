import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Clock, Hash, Search } from 'lucide-react'
import type { ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { recentSearches, rememberSearch } from '../lib/device'
import { track, trackSearch } from '../lib/events'
import { sortItems } from '../lib/facets'
import { categorySlug, niceCategory } from '../lib/format'
import { searchItems, suggest } from '../lib/search'
import { useShell } from '../shell/ShellContext'
import { S } from '../strings'
import { Sheet } from '../ui/Sheet'
import { groupResults } from '../lib/searchGroups'
import { MarketCard } from './MarketCard'
import { SearchGroups, SearchPreparing } from './SearchResults'

const POPULAR_MAX = 5
const SECTION_H = 'text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2'

/** Real best sellers / movers that are in stock — the shelf merchants reorder from most. */
function popularRestocks(items: ShopItem[]): ShopItem[] {
  const hot = items.filter((i) => i.stock_status !== 'out_of_stock' && (i.badges || []).some((b) => b === 'best_seller' || b === 'trending' || b === 'selling_fast'))
  return sortItems(hot, 'popular').slice(0, POPULAR_MAX)
}

/**
 * Desktop ⌘K / Ctrl+K / "/" palette: the same grouped results as the Search page in a dialog.
 * ↑↓ move · Enter opens the product · ⇧Enter adds the default quantity · Esc closes.
 * Enter on an exact code adds it and keeps the query (typing "uk15⏎ c18⏎" builds an order).
 * Empty query: Popular restocks (keyboard rows, same ↑↓ / Enter / ⇧Enter), recent searches,
 * categories — a restock can start without typing. Nothing is selected until ↑↓ is pressed, so a
 * stray Enter (or a scanner's second CR) after an exact-code add never opens or adds a best seller.
 */
export default function SearchPalette() {
  const m = useMarket()
  const { closePalette, paletteQuery, openProduct } = useShell()
  const navigate = useNavigate()
  const [q, setQ] = useState(paletteQuery)
  const [active, setActive] = useState(0)
  // ↑↓ has moved into the list: only then does Enter act on a Popular restocks row
  const [navigated, setNavigated] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    void m.ensureIndex()
  }, [m])

  const empty = !q.trim()
  const grouped = useMemo(() => groupResults(q, m.items, m.index, searchItems), [q, m.items, m.index])
  const popular = useMemo(() => popularRestocks(m.items), [m.items])
  const flat = useMemo(() => (empty ? popular : [...grouped.codes, ...grouped.products]), [empty, popular, grouped])
  const hints = useMemo(() => (m.index && q.trim() && !flat.length ? suggest(m.index, q) : []), [m.index, q, flat.length])
  const [qSeen, setQSeen] = useState(q)
  if (qSeen !== q) {
    setQSeen(q)
    setActive(0)
    setNavigated(false)
  }
  // with a query the first result is the Enter target; with none, nothing is selected until ↑↓
  const activeCode = empty && !navigated ? null : flat[active]?.item_code || null
  useEffect(() => {
    if (!q.trim() || !m.index) return
    trackSearch(q, flat.length)
  }, [q, flat.length, m.index])

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!flat.length) return
      setNavigated(true)
      // empty query: the first press picks the first Popular restocks row, it does not skip it
      if (empty && !navigated) {
        setActive(0)
        return
      }
      setActive((a) => Math.min(flat.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (!flat.length) return
      setNavigated(true)
      if (empty && !navigated) {
        setActive(flat.length - 1)
        return
      }
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const query = q.trim()
      const row = flat[active]
      if (!query) {
        // empty query: the Popular restocks rows, but only once ↑↓ has picked one
        if (!row || !navigated) return
        if (e.shiftKey) {
          m.add(row, undefined, 'palette')
          return
        }
        closePalette()
        openProduct(row.item_code, 'palette')
        return
      }
      if (e.shiftKey && row) {
        m.add(row, undefined, 'palette')
        track('search', { meta: { q: query.slice(0, 60), results: flat.length, code: row.item_code } })
        return
      }
      if (grouped.exact && grouped.exact.stock_status !== 'out_of_stock') {
        m.add(grouped.exact, undefined, 'palette')
        rememberSearch(query)
        setQ('')
        return
      }
      if (row) {
        rememberSearch(query)
        closePalette()
        openProduct(row.item_code, 'palette')
        return
      }
      rememberSearch(query)
      closePalette()
      navigate(`/search?q=${encodeURIComponent(query)}`)
    }
  }

  // keep the active row in view
  useEffect(() => {
    if (!activeCode) return
    document.querySelector(`[data-code="${CSS.escape(activeCode)}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [activeCode])

  return (
    <Sheet
      open
      onClose={closePalette}
      title={S.search.palette}
      variant="dialog"
      size="lg"
      bare
      className="sm:mt-[8vh] sm:max-h-[78dvh]"
    >
      <div className="sticky top-0 z-10 border-b border-line-2 bg-surface px-4 pb-3 pt-4 md:px-5">
        <div className="relative">
          <Search size={18} strokeWidth={2} className="pointer-events-none absolute start-3.5 top-1/2 -translate-y-1/2 text-plum" aria-hidden="true" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            autoFocus
            role="combobox"
            aria-expanded={flat.length > 0}
            aria-controls="palette-results"
            aria-activedescendant={activeCode ? `palette-${activeCode}` : undefined}
            placeholder={S.searchPlaceholder}
            aria-label={S.searchLabel}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            className="h-12 w-full rounded-md border border-line bg-canvas pe-24 ps-11 text-md text-ink outline-none placeholder:text-ink-3 focus:border-plum focus:ring-2 focus:ring-plum/15"
          />
          <span className="pointer-events-none absolute end-3 top-1/2 hidden -translate-y-1/2 text-2xs text-ink-3 md:block">{S.search.keys}</span>
        </div>
      </div>
      <div id="palette-results" className="px-4 py-4 md:px-5">
        {empty ? (
          <PaletteEmpty popular={popular} activeCode={activeCode} onPick={setQ} />
        ) : !m.index ? (
          <SearchPreparing />
        ) : (
          <SearchGroups q={q} grouped={grouped} hints={hints} onPick={setQ} activeCode={activeCode} from="palette" />
        )}
      </div>
    </Sheet>
  )
}

function PaletteEmpty({ popular, activeCode, onPick }: { popular: ShopItem[]; activeCode?: string | null; onPick: (q: string) => void }) {
  const { items, categories } = useMarket()
  const [recent] = useState<string[]>(() => recentSearches())
  const chip = 'inline-flex h-9 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm text-ink transition duration-1 ease-m hover:border-ink/25 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'
  return (
    <div className="space-y-6">
      <p className="flex items-center gap-1.5 text-xs text-ink-2">
        <Hash size={13} aria-hidden="true" /> {S.search.codeHint}
      </p>
      {recent.length > 0 && (
        <section aria-label={S.search.recent}>
          <h2 className={SECTION_H}>{S.search.recent}</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {recent.map((r) => (
              <button key={r} type="button" onClick={() => onPick(r)} className={chip}>
                <Clock size={13} className="text-ink-3" aria-hidden="true" /> {r}
              </button>
            ))}
          </div>
        </section>
      )}
      {popular.length > 0 && (
        <section aria-label={S.search.popularRestocks}>
          <h2 className={SECTION_H}>{S.search.popularRestocks}</h2>
          <div className="mt-1">
            {popular.map((it) => (
              <div key={it.item_code} id={`palette-${it.item_code}`} data-code={it.item_code} className={cn('rounded-sm transition-colors', activeCode === it.item_code && 'bg-plum-wash ring-1 ring-plum/20')}>
                <MarketCard item={it} variant="list" from="palette" />
              </div>
            ))}
          </div>
        </section>
      )}
      {categories.length > 0 && (
        <section aria-label={S.search.categories}>
          <h2 className={SECTION_H}>{S.search.categories}</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {categories.map((c) => (
              <Link key={c} to={`/t/${categorySlug(c)}`} className={cn(chip, 'font-semibold')}>
                {niceCategory(c)} <span className="text-xs font-normal tnum text-ink-3">{items.filter((i) => (i.category || 'OTHER') === c).length}</span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
