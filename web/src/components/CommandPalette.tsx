import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { Search, CornerDownLeft, User, Package, BadgeCheck, Loader2 } from 'lucide-react'
import { navFor } from '@/lib/nav'
import { useAuth } from '@/lib/auth'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'

interface DataHit { type: 'customer' | 'item' | 'salesman'; label: string; sub: string }
type Row =
  | { kind: 'page'; label: string; to: string; icon: React.ComponentType<{ size?: number; className?: string }> }
  | { kind: 'data'; hit: DataHit; to: string }

const HIT_ICON = { customer: User, item: Package, salesman: BadgeCheck }
function hitRoute(h: DataHit): string {
  const q = encodeURIComponent(h.label)
  if (h.type === 'customer') return `/receivables?q=${q}`
  if (h.type === 'item') return `/inventory?q=${q}`
  return '/sales'
}

export function CommandPalette() {
  const { me } = useAuth()
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const [hits, setHits] = useState<DataHit[]>([])
  const [loading, setLoading] = useState(false)
  const reqRef = useRef(0)

  const pages = useMemo(() => navFor(me), [me])
  const filteredPages = useMemo(
    () => pages.filter((i) => i.label.toLowerCase().includes(q.toLowerCase())),
    [pages, q],
  )

  // debounced data search
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); setLoading(false); return }
    setLoading(true)
    const id = ++reqRef.current
    const t = setTimeout(async () => {
      try {
        const r = await apiGet<DataHit[]>(`/search?q=${encodeURIComponent(q.trim())}`)
        if (id === reqRef.current) setHits(r || [])
      } catch {
        if (id === reqRef.current) setHits([])
      } finally {
        if (id === reqRef.current) setLoading(false)
      }
    }, 220)
    return () => clearTimeout(t)
  }, [q])

  const rows: Row[] = useMemo(() => [
    ...filteredPages.map((p) => ({ kind: 'page' as const, label: p.label, to: p.to, icon: p.icon })),
    ...hits.map((h) => ({ kind: 'data' as const, hit: h, to: hitRoute(h) })),
  ], [filteredPages, hits])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen((o) => !o) }
      if (e.key === 'Escape') setOpen(false)
    }
    const onOpen = () => setOpen(true)
    window.addEventListener('keydown', onKey)
    window.addEventListener('yq:open-cmdk', onOpen)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('yq:open-cmdk', onOpen) }
  }, [])
  useEffect(() => { if (open) { setQ(''); setActive(0); setHits([]) } }, [open])
  useEffect(() => setActive(0), [q])

  function go(to: string) { setOpen(false); nav(to) }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[12vh] backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onClick={() => setOpen(false)}
        >
          <motion.div
            initial={{ opacity: 0, y: -12, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -12, scale: 0.98 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className="w-full max-w-xl overflow-hidden rounded-2xl border border-white/10 bg-popover text-popover-foreground shadow-[0_30px_80px_-20px_rgba(76,29,149,.55)] ring-1 ring-black/5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="h-1 w-full bg-gradient-to-r from-violet-500 via-fuchsia-500 to-indigo-500" />
            <div className="flex items-center gap-3 border-b bg-gradient-to-b from-primary/[0.04] to-transparent px-4">
              {loading ? <Loader2 size={18} className="animate-spin text-primary" /> : <Search size={18} className="text-primary" />}
              <input
                autoFocus value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown') setActive((a) => Math.min(a + 1, rows.length - 1))
                  if (e.key === 'ArrowUp') setActive((a) => Math.max(a - 1, 0))
                  if (e.key === 'Enter' && rows[active]) go(rows[active].to)
                }}
                placeholder="Search pages, customers, items, salesmen…"
                className="h-14 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground"
              />
              <kbd className="rounded-md border bg-card px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground shadow-sm">ESC</kbd>
            </div>

            <div className="max-h-[52vh] overflow-y-auto p-2">
              {filteredPages.length > 0 && (
                <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Pages</div>
              )}
              {rows.map((row, idx) => {
                const on = idx === active
                const isFirstData = row.kind === 'data' && (idx === 0 || rows[idx - 1].kind === 'page')
                return (
                  <div key={idx}>
                    {isFirstData && (
                      <div className="px-2.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Results</div>
                    )}
                    <button
                      onMouseEnter={() => setActive(idx)} onClick={() => go(row.to)}
                      className={cn('flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left text-sm transition',
                        on ? 'bg-gradient-to-r from-primary/12 to-fuchsia-500/5 ring-1 ring-primary/20' : 'hover:bg-accent/60')}
                    >
                      <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-lg transition',
                        on ? 'bg-gradient-to-br from-violet-500 to-fuchsia-500 text-white shadow-[0_6px_16px_-4px_rgba(124,58,237,.6)]' : 'bg-muted text-muted-foreground')}>
                        {row.kind === 'page' ? <row.icon size={17} /> : (() => { const I = HIT_ICON[row.hit.type]; return <I size={17} /> })()}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{row.kind === 'page' ? row.label : row.hit.label}</span>
                        {row.kind === 'data' && <span className="block truncate text-[12px] text-muted-foreground">{row.hit.type} · {row.hit.sub}</span>}
                      </span>
                      {on && <span className="flex items-center gap-1 rounded-md border bg-card px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground"><CornerDownLeft size={11} /> Enter</span>}
                    </button>
                  </div>
                )
              })}
              {rows.length === 0 && (
                <div className="px-3 py-8 text-center text-sm text-muted-foreground">
                  {loading ? 'Searching…' : q.trim().length >= 2 ? `No matches for “${q}”.` : 'Type to search pages and your data.'}
                </div>
              )}
            </div>

            <div className="flex items-center gap-4 border-t bg-muted/30 px-4 py-2 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1"><kbd className="rounded border bg-card px-1">↑</kbd><kbd className="rounded border bg-card px-1">↓</kbd> navigate</span>
              <span className="flex items-center gap-1"><kbd className="rounded border bg-card px-1">↵</kbd> open</span>
              <span className="ml-auto font-medium text-primary">YQ Bahrain · AI Portal</span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
