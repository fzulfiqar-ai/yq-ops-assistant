import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { useMarket } from '../MarketContext'
import { rememberSearch } from '../lib/device'
import { track, trackSearch } from '../lib/events'
import { searchItems, suggest } from '../lib/search'
import { useShell } from '../shell/ShellContext'
import { S } from '../strings'
import { Sheet } from '../ui/Sheet'
import { groupResults } from '../lib/searchGroups'
import { SearchEmpty, SearchGroups, SearchPreparing } from './SearchResults'

/**
 * Desktop ⌘K / Ctrl+K / "/" palette: the same grouped results as the Search tab in a dialog.
 * ↑↓ move · Enter opens the product · ⇧Enter adds the default quantity · Esc closes.
 * Enter on an exact code adds it and keeps the query (typing "uk15⏎ c18⏎" builds an order).
 */
export default function SearchPalette() {
  const m = useMarket()
  const { closePalette, paletteQuery, openProduct } = useShell()
  const navigate = useNavigate()
  const [q, setQ] = useState(paletteQuery)
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    void m.ensureIndex()
  }, [m])

  const grouped = useMemo(() => groupResults(q, m.items, m.index, searchItems), [q, m.items, m.index])
  const flat = useMemo(() => [...grouped.codes, ...grouped.products], [grouped])
  const hints = useMemo(() => (m.index && q.trim() && !flat.length ? suggest(m.index, q) : []), [m.index, q, flat.length])
  const [qSeen, setQSeen] = useState(q)
  if (qSeen !== q) {
    setQSeen(q)
    setActive(0)
  }
  useEffect(() => {
    if (!q.trim() || !m.index) return
    trackSearch(q, flat.length)
  }, [q, flat.length, m.index])

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(flat.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const query = q.trim()
      if (!query) return
      if (e.shiftKey && flat[active]) {
        m.add(flat[active], undefined, 'palette')
        track('search', { meta: { q: query.slice(0, 60), results: flat.length, code: flat[active].item_code } })
        return
      }
      if (grouped.exact && grouped.exact.stock_status !== 'out_of_stock') {
        m.add(grouped.exact, undefined, 'palette')
        rememberSearch(query)
        setQ('')
        return
      }
      if (flat[active]) {
        rememberSearch(query)
        closePalette()
        openProduct(flat[active].item_code, 'palette')
        return
      }
      rememberSearch(query)
      closePalette()
      navigate(`/search?q=${encodeURIComponent(query)}`)
    }
  }

  // keep the active row in view
  useEffect(() => {
    const code = flat[active]?.item_code
    if (!code) return
    document.querySelector(`[data-code="${CSS.escape(code)}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active, flat])

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
          <Search size={18} className="pointer-events-none absolute start-3.5 top-1/2 -translate-y-1/2 text-ink-3" aria-hidden="true" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            autoFocus
            role="combobox"
            aria-expanded={flat.length > 0}
            aria-controls="palette-results"
            aria-activedescendant={flat[active] ? `palette-${flat[active].item_code}` : undefined}
            placeholder={S.searchPlaceholder}
            aria-label={S.searchLabel}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            className="h-12 w-full rounded-md border border-line bg-canvas ps-11 pe-24 text-md text-ink outline-none placeholder:text-ink-3 focus:border-plum focus:ring-2 focus:ring-plum/15"
          />
          <span className="pointer-events-none absolute end-3 top-1/2 hidden -translate-y-1/2 text-2xs text-ink-3 md:block">{S.search.keys}</span>
        </div>
      </div>
      <div id="palette-results" className="px-4 py-4 md:px-5">
        {!q.trim() ? <SearchEmpty onPick={setQ} /> : !m.index ? <SearchPreparing /> : <SearchGroups q={q} grouped={grouped} hints={hints} onPick={setQ} activeCode={flat[active]?.item_code} from="palette" />}
      </div>
    </Sheet>
  )
}
