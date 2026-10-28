import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Search, ArrowUp, ArrowDown, ChevronsUpDown, Download } from 'lucide-react'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'
import { FINE_POINTER_QUERY, WIDE_QUERY, useMediaQuery } from '@/lib/media'
import {
  CARD_PAGE, SENTINEL_LEAD_PX, TABLE_PAGE, edgeFade, grownLimit, hasMore, limitFor, windowKey,
  type SortState, type WindowState,
} from '@/lib/tableWindow'

// Render rows incrementally: mount a page, grow by a page as the end of the list comes near. A sentinel over
// the list's last rows is watched by an IntersectionObserver on the VIEWPORT (root = null): it fires the same
// whether the page scrolls in the shell's <main>, in the window, or in this table's own scroller, and it only
// fires once per page (the old onScroll handler mounted 150 rows on every scroll event near the end). Keeps
// natural table layout (no fixed row heights) while avoiding thousand-row DOM mounts. When it starts again
// from the first page is lib/tableWindow.ts's rule: the search, the sort or the caller's `resetKey` — never a
// re-read of the same rows.

export interface Column<T> {
  key: keyof T & string
  label: string
  money?: boolean
  align?: 'left' | 'right'
  render?: (value: unknown, row: T) => ReactNode
}

function toNum(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isNaN(n) ? null : n
}

/** A sideways scroller's live state: which edges fade, and how much room its own scrollbars take. */
function useScrollEdges(el: HTMLElement | null) {
  const [edges, setEdges] = useState({ left: false, right: false, vbar: 0, hbar: 0 })
  useEffect(() => {
    if (!el) return
    const update = () => {
      const f = edgeFade(el)
      const next = { ...f, vbar: el.offsetWidth - el.clientWidth, hbar: el.offsetHeight - el.clientHeight }
      setEdges((p) => (p.left === next.left && p.right === next.right && p.vbar === next.vbar && p.hbar === next.hbar ? p : next))
    }
    el.addEventListener('scroll', update, { passive: true })
    // the first measurement comes with the observer's first callback; the content is watched too, since rows
    // mounting or a column widening change the scroll width without resizing the scroller
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    ro?.observe(el)
    if (el.firstElementChild) ro?.observe(el.firstElementChild)
    return () => {
      el.removeEventListener('scroll', update)
      ro?.disconnect()
    }
  }, [el])
  return edges
}

/** The soft "more this way" fades over a scroller's sides (never over its scrollbars). Opacity only, 200 ms. */
function EdgeFades({ left, right, vbar, hbar, skipRight = false }: {
  left: boolean; right: boolean; vbar: number; hbar: number
  /** a pinned last column already marks the right edge (and must stay crisp) */
  skipRight?: boolean
}) {
  const base = 'pointer-events-none absolute top-0 z-30 w-8 transition-opacity duration-200 ease-[cubic-bezier(.22,1,.36,1)] motion-reduce:transition-none'
  return (
    <>
      <div aria-hidden="true" style={{ bottom: hbar }}
        className={cn(base, 'left-0 bg-gradient-to-r from-card to-transparent', left ? 'opacity-100' : 'opacity-0')} />
      {!skipRight && (
        <div aria-hidden="true" style={{ bottom: hbar, right: vbar }}
          className={cn(base, 'bg-gradient-to-l from-card to-transparent', right ? 'opacity-100' : 'opacity-0')} />
      )}
    </>
  )
}

/**
 * A wide table inside a card scrolls sideways within the card (a phone used to cut it off: the cards hide
 * their overflow), with a soft fade on the side that has more to show. Put the table straight inside.
 */
export function ScrollX({ children, className }: { children: ReactNode; className?: string }) {
  const [el, setEl] = useState<HTMLDivElement | null>(null)
  const edges = useScrollEdges(el)
  return (
    <div className={cn('relative isolate', className)}>
      <div ref={setEl} className="overflow-x-auto overscroll-x-contain">{children}</div>
      <EdgeFades {...edges} />
    </div>
  )
}

export function DataTable<T extends object>({
  rows,
  cols,
  rowClass,
  empty = 'No data.',
  maxHeight = '62vh',
  searchable = true,
  exportName,
  initialQuery,
  pinLast = false,
  resetKey,
}: {
  rows: T[]
  cols: Column<T>[]
  rowClass?: (row: T) => string | undefined
  empty?: string
  /** the table's own scroller height, with a mouse only — on a touch screen the table is part of the page */
  maxHeight?: string
  searchable?: boolean
  exportName?: string
  initialQuery?: string
  /** Keep the last column (row actions) pinned to the right edge while the table scrolls sideways,
   *  so a wide table never pushes its buttons off-screen (R7d, audit UX-10). */
  pinLast?: boolean
  /** The page's own filter (a status chip, a period): a new value starts the rows again from the first page
   *  and the table's scroller from the top. A re-read of the same filter keeps both where they were. */
  resetKey?: string | number
}) {
  const lastKey = cols[cols.length - 1]?.key
  const pinTh = 'sticky right-0 z-20 bg-secondary shadow-[-10px_0_12px_-12px_rgba(0,0,0,0.35)]'
  const pinTd = 'sticky right-0 z-[1] bg-card shadow-[-10px_0_12px_-12px_rgba(0,0,0,0.35)]'
  const [query, setQuery] = useState(initialQuery || '')
  const [sort, setSort] = useState<SortState | null>(null)
  // eslint-disable-next-line react-hooks/set-state-in-effect -- pre-existing reset/sync-on-change effect; behaviour kept, refactor later
  useEffect(() => { if (initialQuery !== undefined) setQuery(initialQuery) }, [initialQuery])

  // ONE of the two layouts is mounted (a phone never builds the hidden table, a desktop the hidden cards)
  const wide = useMediaQuery(WIDE_QUERY, true)
  const mouse = useMediaQuery(FINE_POINTER_QUERY, true)
  const page = wide ? TABLE_PAGE : CARD_PAGE
  const key = windowKey(query, sort, `${resetKey ?? ''}|${wide ? 'table' : 'cards'}`)
  const [win, setWin] = useState<WindowState>({ key, limit: page })
  const limit = limitFor(win, key, page)

  const view = useMemo(() => {
    let r = rows
    if (query.trim()) {
      const q = query.toLowerCase()
      r = r.filter((row) => cols.some((c) => String((row as Record<string, unknown>)[c.key] ?? '').toLowerCase().includes(q)))
    }
    if (sort) {
      r = [...r].sort((a, b) => {
        const av = (a as Record<string, unknown>)[sort.key]
        const bv = (b as Record<string, unknown>)[sort.key]
        const an = toNum(av), bn = toNum(bv)
        const cmp = an !== null && bn !== null ? an - bn : String(av ?? '').localeCompare(String(bv ?? ''))
        return sort.dir === 'asc' ? cmp : -cmp
      })
    }
    return r
  }, [rows, cols, query, sort])

  const more = hasMore(limit, view.length)
  const grow = () => setWin({ key, limit: grownLimit(limit, view.length, page) })

  // The next page mounts when the sentinel (over the list's last SENTINEL_LEAD_PX) comes on screen. A new
  // observer per page: its first callback says whether the sentinel is STILL in view — a short page keeps
  // growing, one page per frame, until it is not.
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el || !more || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setWin({ key, limit: grownLimit(limit, view.length, page) })
    }, { root: null })
    io.observe(el)
    return () => io.disconnect()
  }, [key, limit, more, page, view.length])

  // the table's own scroller back to the top when its search, sort or filter changes (never the page)
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  useEffect(() => { scroller?.scrollTo({ top: 0 }) }, [key, scroller])
  const edges = useScrollEdges(scroller)

  function toggleSort(k: string) {
    setSort((s) => (s?.key !== k ? { key: k, dir: 'desc' } : s.dir === 'desc' ? { key: k, dir: 'asc' } : null))
  }

  function exportCSV() {
    const esc = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s)
    const header = cols.map((c) => esc(c.label)).join(',')
    const lines = view.map((row) =>
      cols.map((c) => {
        const v = (row as Record<string, unknown>)[c.key]
        return esc(v == null ? '' : String(v))
      }).join(','),
    )
    const blob = new Blob([[header, ...lines].join('\n')], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${exportName || 'yq-export'}-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const cell = (c: Column<T>, row: T) => {
    const v = (row as Record<string, unknown>)[c.key]
    return c.render ? c.render(v, row) : c.money ? bhd(Number(v ?? 0)) : v == null ? '—' : String(v)
  }
  // Invisible, over the LAST SENTINEL_LEAD_PX of the list (a negative top margin: it takes no room). Any view of
  // the list's end meets it — scrolling near it mounts the next page before it is needed, and so does a jump
  // straight to the bottom (a dragged scrollbar), which would skip a one-pixel marker in a scroller shorter
  // than the lead.
  const sentinelEl = more && (
    <div ref={sentinel} aria-hidden="true" className="pointer-events-none"
      style={{ marginTop: -SENTINEL_LEAD_PX, height: SENTINEL_LEAD_PX }} />
  )
  const moreLine = more && (
    <div className="flex flex-wrap items-center justify-center gap-x-2 border-t py-2 text-center text-xs text-muted-foreground">
      Showing {limit} of {view.length} — keep scrolling, or
      <button type="button" onClick={grow} className="font-semibold text-primary hover:underline">show {Math.min(page, view.length - limit)} more</button>
    </div>
  )

  return (
    <div>
      {/* Toolbar */}
      {(searchable || rows.length > 0) && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {searchable && (
            <div className="flex items-center gap-2 rounded-lg border bg-card px-3 shadow-sm transition focus-within:border-primary/40 focus-within:ring-4 focus-within:ring-primary/10">
              <Search size={15} className="text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search…"
                aria-label="Search the table"
                className="h-9 w-44 bg-transparent text-sm outline-none placeholder:text-muted-foreground sm:w-56"
              />
            </div>
          )}
          <span className="text-xs text-muted-foreground">
            {view.length}{view.length !== rows.length ? ` of ${rows.length}` : ''} rows
          </span>
          <button
            type="button"
            onClick={exportCSV}
            className="ml-auto flex items-center gap-1.5 rounded-lg border bg-card px-3 py-2 text-[13px] font-medium text-muted-foreground shadow-sm transition hover:border-primary/40 hover:text-foreground"
            title="Export to CSV"
          >
            <Download size={14} /> Export CSV
          </button>
        </div>
      )}

      {!view.length ? (
        <div className="rounded-xl border py-12 text-center text-sm text-muted-foreground">
          {rows.length ? `No rows match “${query}”.` : empty}
        </div>
      ) : !wide ? (
        /* Phones: stacked cards, part of the page — no scroller of their own, more as the page scrolls */
        <div>
          <div className="space-y-2">
            {view.slice(0, limit).map((row, i) => {
              const [first, ...rest] = cols
              return (
                <div key={i} className={cn('rounded-xl border bg-card p-3 shadow-soft', rowClass?.(row))}>
                  <div className="mb-1.5 text-sm font-semibold">{cell(first, row)}</div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                    {rest.map((c) => (
                      <div key={c.key} className="flex items-baseline justify-between gap-2">
                        <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{c.label}</dt>
                        <dd className="text-[13px] font-medium tabular-nums">{cell(c, row)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              )
            })}
          </div>
          {sentinelEl}
          {moreLine}
        </div>
      ) : (
        /* Tablets & up: the real table. With a mouse a long one is its own scroller (its header stays in view);
           on a touch screen it is part of the page and only scrolls sideways. */
        <div className="relative isolate overflow-hidden rounded-xl border bg-card">
          <div ref={setScroller} className="overflow-auto overscroll-x-contain"
            style={mouse ? ({ maxHeight } as CSSProperties) : undefined}>
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-secondary">
                <tr>
                  {cols.map((c) => {
                    const sorted = sort?.key === c.key
                    return (
                      <th
                        key={c.key}
                        aria-sort={sorted ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                        className={cn(
                          'whitespace-nowrap px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground',
                          c.align === 'right' ? 'text-right' : 'text-left',
                          pinLast && c.key === lastKey && pinTh,
                        )}
                      >
                        <button type="button" onClick={() => toggleSort(c.key)}
                          className={cn('inline-flex select-none items-center gap-1 uppercase tracking-wide transition-colors hover:text-foreground',
                            c.align === 'right' && 'flex-row-reverse')}>
                          {c.label}
                          {sorted ? (sort!.dir === 'asc' ? <ArrowUp size={12} className="text-primary" /> : <ArrowDown size={12} className="text-primary" />)
                            : <ChevronsUpDown size={12} className="opacity-30" />}
                        </button>
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {view.slice(0, limit).map((row, i) => (
                  <tr key={i} className={cn('border-t transition-colors hover:bg-accent/40', rowClass?.(row))}>
                    {cols.map((c) => (
                      <td key={c.key} className={cn('whitespace-nowrap px-4 py-2.5', c.align === 'right' ? 'text-right tabular-nums' : 'text-left', pinLast && c.key === lastKey && pinTd)}>
                        {cell(c, row)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {sentinelEl}
            {moreLine}
          </div>
          <EdgeFades {...edges} skipRight={pinLast} />
        </div>
      )}
    </div>
  )
}

export function Stat({
  label,
  value,
  tone,
  foot,
}: {
  label: string
  value: ReactNode
  tone?: 'amber' | 'rose' | 'violet' | 'emerald' | 'blue' | 'slate'
  foot?: ReactNode
}) {
  const TONES: Record<string, string> = {
    amber: 'text-amber-600',
    rose: 'text-rose-600',
    violet: 'text-primary',
    emerald: 'text-emerald-600',
    blue: 'text-blue-600',
    slate: 'text-slate-600 dark:text-slate-300',
  }
  return (
    <div className="rounded-xl border bg-card px-4 py-3 shadow-soft transition-shadow hover:shadow-lift">
      <div className={cn('font-display text-xl font-extrabold tracking-tight tabular-nums', tone && TONES[tone])}>
        {value}
      </div>
      <div className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      {foot && <div className="mt-1 text-[12px] text-muted-foreground">{foot}</div>}
    </div>
  )
}
