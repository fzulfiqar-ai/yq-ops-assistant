import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { Search, CornerDownLeft } from 'lucide-react'
import { navFor } from '@/lib/nav'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

export function CommandPalette() {
  const { me } = useAuth()
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)

  const items = useMemo(() => navFor(me), [me])
  const filtered = useMemo(
    () => items.filter((i) => i.label.toLowerCase().includes(q.toLowerCase())),
    [items, q],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    const onOpen = () => setOpen(true)
    window.addEventListener('keydown', onKey)
    window.addEventListener('yq:open-cmdk', onOpen)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('yq:open-cmdk', onOpen)
    }
  }, [])

  useEffect(() => {
    if (open) {
      setQ('')
      setActive(0)
    }
  }, [open])
  useEffect(() => setActive(0), [q])

  function go(to: string) {
    setOpen(false)
    nav(to)
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[12vh] backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => setOpen(false)}
        >
          <motion.div
            initial={{ opacity: 0, y: -12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.98 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className="w-full max-w-xl overflow-hidden rounded-2xl border border-white/10 bg-popover text-popover-foreground shadow-[0_30px_80px_-20px_rgba(76,29,149,.55)] ring-1 ring-black/5"
            onClick={(e) => e.stopPropagation()}
          >
            {/* gradient accent bar */}
            <div className="h-1 w-full bg-gradient-to-r from-violet-500 via-fuchsia-500 to-indigo-500" />
            <div className="flex items-center gap-3 border-b bg-gradient-to-b from-primary/[0.04] to-transparent px-4">
              <Search size={18} className="text-primary" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown') setActive((a) => Math.min(a + 1, filtered.length - 1))
                  if (e.key === 'ArrowUp') setActive((a) => Math.max(a - 1, 0))
                  if (e.key === 'Enter' && filtered[active]) go(filtered[active].to)
                }}
                placeholder="Jump to a page…"
                className="h-14 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground"
              />
              <kbd className="rounded-md border bg-card px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground shadow-sm">ESC</kbd>
            </div>
            <div className="max-h-[50vh] overflow-y-auto p-2">
              <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Pages</div>
              {filtered.length === 0 ? (
                <div className="px-3 py-8 text-center text-sm text-muted-foreground">No matches for “{q}”.</div>
              ) : (
                filtered.map((i, idx) => {
                  const Icon = i.icon
                  const on = idx === active
                  return (
                    <button
                      key={i.to}
                      onMouseEnter={() => setActive(idx)}
                      onClick={() => go(i.to)}
                      className={cn(
                        'flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left text-sm transition',
                        on ? 'bg-gradient-to-r from-primary/12 to-fuchsia-500/5 ring-1 ring-primary/20' : 'hover:bg-accent/60',
                      )}
                    >
                      <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-lg transition',
                        on ? 'bg-gradient-to-br from-violet-500 to-fuchsia-500 text-white shadow-[0_6px_16px_-4px_rgba(124,58,237,.6)]' : 'bg-muted text-muted-foreground')}>
                        <Icon size={17} />
                      </span>
                      <span className="flex-1 font-medium">{i.label}</span>
                      {on && (
                        <span className="flex items-center gap-1 rounded-md border bg-card px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                          <CornerDownLeft size={11} /> Enter
                        </span>
                      )}
                    </button>
                  )
                })
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
