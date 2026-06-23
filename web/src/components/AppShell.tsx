import { Suspense, useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useOutlet } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { Moon, Sun, LogOut, PanelLeftClose, PanelLeft, Loader2, Settings, ChevronDown, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import { navFor, NAV } from '@/lib/nav'
import { Logo } from './Logo'
import { CommandPalette } from './CommandPalette'

const HEADER_QUOTES = [
  'Decisions made one day faster compound into a year of advantage.',
  'Automate the predictable, so the team can focus on the exceptional.',
  'What gets measured gets managed; what gets surfaced gets fixed.',
  'Cash is reality. Margin is truth. Velocity is momentum.',
  'The best inventory is the one already on its way to a customer.',
]

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

function HeaderMotivator({ name }: { name?: string }) {
  const [i, setI] = useState(() => Math.floor(Math.random() * HEADER_QUOTES.length))
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % HEADER_QUOTES.length), 9000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="hidden min-w-0 flex-1 flex-col items-center justify-center px-4 lg:flex">
      <div className="text-[13px] font-semibold leading-tight">
        <span className="bg-gradient-to-r from-violet-600 to-fuchsia-500 bg-clip-text text-transparent dark:from-violet-300 dark:to-fuchsia-300">
          {greeting()}{name ? `, ${name}` : ''}
        </span>{' '}
        <span aria-hidden>✦</span>
      </div>
      <AnimatePresence mode="wait">
        <motion.span
          key={i}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.5 }}
          className="max-w-full truncate text-[11px] italic text-muted-foreground/70"
        >
          “{HEADER_QUOTES[i]}”
        </motion.span>
      </AnimatePresence>
    </div>
  )
}

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
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

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

        <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-3 [scrollbar-color:rgba(255,255,255,.2)_transparent] [scrollbar-width:thin]">
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
          <Link
            to="/settings"
            className={cn(
              'flex items-center gap-3 rounded-xl px-2 py-2 transition hover:bg-white/10',
              collapsed && 'justify-center px-0',
            )}
            title="Profile & settings"
          >
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/15 text-sm font-bold">
              {initials}
            </div>
            {!collapsed && (
              <div className="min-w-0 flex-1 leading-tight">
                <div className="truncate text-[13px] font-semibold">
                  {me?.full_name || me?.email?.split('@')[0]}
                </div>
                <div className="text-[10px] uppercase tracking-wide text-white/50">{me?.role} · view profile</div>
              </div>
            )}
          </Link>
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
          <div className="shrink-0">
            <div className="font-display text-[15px] font-semibold">{active?.label ?? 'Portal'}</div>
          </div>
          <HeaderMotivator name={me?.full_name?.split(' ')[0] || me?.email?.split('@')[0]} />
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
            {/* User menu */}
            <div className="relative" ref={menuRef}>
              <button
                onClick={() => setMenuOpen((o) => !o)}
                className="flex items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-1.5 transition hover:bg-accent"
                title="Account"
              >
                <span className="grid h-9 w-9 place-items-center rounded-full bg-primary text-sm font-bold text-primary-foreground">{initials}</span>
                <ChevronDown size={15} className={cn('text-muted-foreground transition-transform', menuOpen && 'rotate-180')} />
              </button>
              <AnimatePresence>
                {menuOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -8, scale: 0.97 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: -8, scale: 0.97 }}
                    transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
                    className="absolute right-0 top-12 w-60 overflow-hidden rounded-xl border bg-card shadow-lift"
                  >
                    <div className="border-b px-4 py-3">
                      <div className="truncate text-sm font-semibold">{me?.full_name || me?.email?.split('@')[0]}</div>
                      <div className="truncate text-xs text-muted-foreground">{me?.email}</div>
                      <div className="mt-1.5 inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent-foreground">{me?.role}</div>
                    </div>
                    <div className="p-1.5">
                      <Link to="/settings" onClick={() => setMenuOpen(false)}
                        className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition hover:bg-accent">
                        <Settings size={16} className="text-muted-foreground" /> Settings
                      </Link>
                      <button onClick={toggle}
                        className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition hover:bg-accent">
                        {theme === 'dark' ? <Sun size={16} className="text-muted-foreground" /> : <Moon size={16} className="text-muted-foreground" />}
                        {theme === 'dark' ? 'Light mode' : 'Dark mode'}
                      </button>
                      <button onClick={signOut}
                        className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-rose-600 transition hover:bg-rose-50 dark:hover:bg-rose-500/10">
                        <LogOut size={16} /> Sign out
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
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
