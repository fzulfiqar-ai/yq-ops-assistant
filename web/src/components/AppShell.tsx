import { Suspense, useState } from 'react'
import { Link, NavLink, useLocation, useOutlet } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { Moon, Sun, LogOut, PanelLeftClose, PanelLeft, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import { Search } from 'lucide-react'
import { navFor, NAV } from '@/lib/nav'
import { Logo } from './Logo'
import { CommandPalette } from './CommandPalette'

export function AppShell() {
  const { me, signOut } = useAuth()
  const { theme, toggle } = useTheme()
  const loc = useLocation()
  const outlet = useOutlet()
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('yq-collapsed') === '1',
  )
  const items = navFor(me)
  const active = NAV.find((n) => (n.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(n.to)))
  const initials = (me?.email?.[0] || 'U').toUpperCase()

  function toggleCollapse() {
    setCollapsed((c) => {
      localStorage.setItem('yq-collapsed', c ? '0' : '1')
      return !c
    })
  }

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <CommandPalette />
      {/* Sidebar */}
      <aside
        className={cn(
          'relative flex shrink-0 flex-col text-white transition-[width] duration-300',
          collapsed ? 'w-[76px]' : 'w-[260px]',
        )}
        style={{ background: 'linear-gradient(180deg,#2a1259 0%,#190a3a 100%)' }}
      >
        <Link to="/" className="flex items-center gap-3 px-5 pb-2 pt-6">
          <Logo className="h-9 w-9 rounded-xl shadow-lift" />
          {!collapsed && (
            <div className="leading-tight">
              <div className="font-display text-[15px] font-bold">YQ Bahrain</div>
              <div className="text-[11px] text-white/55">Mobile Accessories · AI Ops</div>
            </div>
          )}
        </Link>

        {!collapsed && (
          <div className="px-5 pb-2 pt-4 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/40">
            Portal
          </div>
        )}

        <nav className="flex flex-1 flex-col gap-1 px-3">
          {items.map((n) => {
            const Icon = n.icon
            return (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === '/'}
                className={({ isActive }) =>
                  cn(
                    'group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-white/70 transition-all hover:bg-white/10 hover:text-white',
                    isActive && 'bg-white/15 text-white shadow-[0_6px_18px_rgba(124,58,237,.35)]',
                    collapsed && 'justify-center px-0',
                  )
                }
                title={collapsed ? n.label : undefined}
              >
                <Icon size={19} className="shrink-0" />
                {!collapsed && <span>{n.label}</span>}
              </NavLink>
            )
          })}
        </nav>

        <div className="mt-auto border-t border-white/10 p-3">
          <div className={cn('flex items-center gap-3 px-2 py-2', collapsed && 'justify-center px-0')}>
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/15 text-sm font-bold">
              {initials}
            </div>
            {!collapsed && (
              <div className="min-w-0 leading-tight">
                <div className="truncate text-[13px] font-semibold">
                  {me?.full_name || me?.email?.split('@')[0]}
                </div>
                <div className="text-[10px] uppercase tracking-wide text-white/50">{me?.role}</div>
              </div>
            )}
          </div>
          <button
            onClick={signOut}
            className={cn(
              'mt-1 flex w-full items-center gap-2 rounded-xl bg-white/10 px-3 py-2 text-[13px] font-semibold text-white/85 transition hover:bg-white/20',
              collapsed && 'justify-center px-0',
            )}
            title="Sign out"
          >
            <LogOut size={16} />
            {!collapsed && 'Sign Out'}
          </button>
        </div>
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/80 px-5 backdrop-blur-xl">
          <button
            onClick={toggleCollapse}
            className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground"
            title="Toggle sidebar"
          >
            {collapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
          </button>
          <div className="flex-1">
            <div className="font-display text-[15px] font-semibold">{active?.label ?? 'Portal'}</div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => window.dispatchEvent(new Event('yq:open-cmdk'))}
              className="hidden items-center gap-2 rounded-lg border bg-card px-2.5 py-1.5 text-xs text-muted-foreground transition hover:text-foreground md:flex"
              title="Search (Ctrl/Cmd + K)"
            >
              <Search size={14} /> Search
              <kbd className="rounded border px-1 text-[10px]">⌘K</kbd>
            </button>
            <span className="hidden items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-[11px] font-semibold text-muted-foreground sm:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-success" /> Live
            </span>
            <button
              onClick={toggle}
              className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground"
              title="Toggle theme"
            >
              {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <div className="grid h-9 w-9 place-items-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
              {initials}
            </div>
          </div>
        </header>

        {/* Page content with cross-fade */}
        <main className="flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={loc.pathname}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              className="mx-auto max-w-[1400px] px-5 py-7 md:px-8"
            >
              <Suspense
                fallback={
                  <div className="grid h-[60vh] place-items-center text-muted-foreground">
                    <Loader2 className="animate-spin" size={22} />
                  </div>
                }
              >
                {outlet}
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  )
}
