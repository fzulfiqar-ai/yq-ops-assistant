import { useEffect, useMemo, useRef, useState, type ReactNode, type UIEvent } from 'react'
import { Search, ArrowUp, ArrowDown, ChevronsUpDown, Download } from 'lucide-react'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'

// Render rows incrementally: mount a page, grow as the user nears the bottom. Keeps
// natural table layout (no fixed row heights) while avoiding thousand-row DOM mounts.
const PAGE = 150

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

export function DataTable<T extends object>({
  rows,
  cols,
  rowClass,
  empty = 'No data.',
  maxHeight = '62vh',
  searchable = true,
  exportName,
  initialQuery,
}: {
  rows: T[]
  cols: Column<T>[]
  rowClass?: (row: T) => string | undefined
  empty?: string
  maxHeight?: string
  searchable?: boolean
  exportName?: string
  initialQuery?: string
}) {
  const [query, setQuery] = useState(initialQuery || '')
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null)
  const [limit, setLimit] = useState(PAGE)
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (initialQuery !== undefined) setQuery(initialQuery) }, [initialQuery])
  useEffect(() => { setLimit(PAGE); scrollRef.current?.scrollTo({ top: 0 }) }, [query, sort, rows])

  function onScroll(e: UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight > el.scrollHeight - 600) {
      setLimit((l) => l + PAGE)
    }
  }

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

  function toggleSort(key: string) {
    setSort((s) => (s?.key !== key ? { key, dir: 'desc' } : s.dir === 'desc' ? { key, dir: 'asc' } : null))
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
                className="h-9 w-44 bg-transparent text-sm outline-none placeholder:text-muted-foreground sm:w-56"
              />
            </div>
          )}
          <span className="text-xs text-muted-foreground">
            {view.length}{view.length !== rows.length ? ` of ${rows.length}` : ''} rows
          </span>
          <button
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
      ) : (
        <>
          {/* Phones: stacked cards (no sideways scrolling) */}
          <div className="space-y-2 md:hidden">
            {view.slice(0, Math.min(limit, 60)).map((row, i) => {
              const [first, ...rest] = cols
              const fv = (row as Record<string, unknown>)[first.key]
              return (
                <div key={i} className={cn('rounded-xl border bg-card p-3 shadow-soft', rowClass?.(row))}>
                  <div className="mb-1.5 text-sm font-semibold">
                    {first.render ? first.render(fv, row) : fv == null ? '—' : String(fv)}
                  </div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                    {rest.map((c) => {
                      const v = (row as Record<string, unknown>)[c.key]
                      return (
                        <div key={c.key} className="flex items-baseline justify-between gap-2">
                          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{c.label}</dt>
                          <dd className="text-[13px] font-medium tabular-nums">
                            {c.render ? c.render(v, row) : c.money ? bhd(Number(v ?? 0)) : v == null ? '—' : String(v)}
                          </dd>
                        </div>
                      )
                    })}
                  </dl>
                </div>
              )
            })}
            {view.length > 60 && (
              <div className="py-2 text-center text-xs text-muted-foreground">
                Showing 60 of {view.length} — search to narrow, or use a bigger screen for the full table.
              </div>
            )}
          </div>

          {/* Tablets & up: the real table, incrementally rendered */}
          <div ref={scrollRef} onScroll={onScroll} className="hidden overflow-auto rounded-xl border md:block" style={{ maxHeight }}>
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-secondary/90 backdrop-blur">
                <tr>
                  {cols.map((c) => {
                    const sorted = sort?.key === c.key
                    return (
                      <th
                        key={c.key}
                        onClick={() => toggleSort(c.key)}
                        className={cn(
                          'cursor-pointer select-none whitespace-nowrap px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground',
                          c.align === 'right' ? 'text-right' : 'text-left',
                        )}
                      >
                        <span className={cn('inline-flex items-center gap-1', c.align === 'right' && 'flex-row-reverse')}>
                          {c.label}
                          {sorted ? (sort!.dir === 'asc' ? <ArrowUp size={12} className="text-primary" /> : <ArrowDown size={12} className="text-primary" />)
                            : <ChevronsUpDown size={12} className="opacity-30" />}
                        </span>
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {view.slice(0, limit).map((row, i) => (
                  <tr key={i} className={cn('border-t transition-colors hover:bg-accent/40', rowClass?.(row))}>
                    {cols.map((c) => {
                      const v = (row as Record<string, unknown>)[c.key]
                      return (
                        <td key={c.key} className={cn('whitespace-nowrap px-4 py-2.5', c.align === 'right' ? 'text-right tabular-nums' : 'text-left')}>
                          {c.render ? c.render(v, row) : c.money ? bhd(Number(v ?? 0)) : v == null ? '—' : String(v)}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {limit < view.length && (
              <div className="border-t py-2 text-center text-xs text-muted-foreground">
                Showing {limit} of {view.length} — keep scrolling to load more
              </div>
            )}
          </div>
        </>
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
