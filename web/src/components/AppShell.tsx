import { Suspense, useEffect, useRef, useState } from 'react'
import { Link, Navigate, NavLink, useLocation, useOutlet } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { useQueryClient } from '@tanstack/react-query'
import { Moon, Sun, LogOut, PanelLeftClose, PanelLeft, Loader2, Settings, ChevronDown, Search, Menu, KeyRound } from 'lucide-react'
import { cn } from '@/lib/utils'
import { apiGet } from '@/lib/api'
import { mustResetOf, passwordScreenFor, useAuth } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import { navFor, NAV } from '@/lib/nav'
import { Logo } from './Logo'
import { CommandPalette } from './CommandPalette'
import { FreshnessChip } from './FreshnessChip'

/** Pre-warm the most-clicked pages a moment after login — by the time the owner
 *  clicks Sales/Inventory/Catalog, the data is already in memory (0ms click). */
function usePrefetchPages(enabled: boolean) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!enabled) return
    const t = setTimeout(() => {
      for (const key of ['sales', 'inventory', 'margins', 'receivables']) {
        qc.prefetchQuery({ queryKey: ['report', key], queryFn: () => apiGet(`/report/${key}`) })
      }
      qc.prefetchQuery({ queryKey: ['catalog'], queryFn: () => apiGet('/catalog') })
    }, 2500)
    return () => clearTimeout(t)
  }, [enabled, qc])
}

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

/** A short greeting. The rotating quotes that used to sit here said nothing about the business;
 *  the data-freshness chip beside it now says how current every figure is (plan §8, §27). */
function Greeting({ name }: { name?: string }) {
  return (
    <div className="hidden min-w-0 flex-1 justify-center px-4 lg:flex">
      <span className="truncate text-[13px] font-semibold text-muted-foreground">
        {greeting()}{name ? `, ${name}` : ''}
      </span>
    </div>
  )
}

export function AppShell() {
  const { me, session, signOut } = useAuth()
  const { theme, toggle } = useTheme()
  const loc = useLocation()
  const outlet = useOutlet()
  // A temporary password (server-owned must_reset): every API route but the password change
  // answers 403, so keep the member on Settings and say why instead of a page full of errors.
  const mustReset = mustResetOf(me, session)
  usePrefetchPages(!!me && me.role !== 'salesman' && !mustReset)
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('yq-collapsed') === '1',
  )
  const items = navFor(me)
  // Longest match wins, on segment boundaries only — otherwise /shop-orders would be
  // labelled "Catalog" just because it starts with /shop. The user's own menu first (management
  // names its pages differently: Inventory is "Products & stock"), then every page.
  const matches = (n: { to: string }) =>
    n.to === '/' ? loc.pathname === '/' : loc.pathname === n.to || loc.pathname.startsWith(`${n.to}/`)
  const active = [...items.filter(matches), ...NAV.filter(matches)].sort((a, b) => b.to.length - a.to.length)[0]
  const initials = (me?.email?.[0] || 'U').toUpperCase()
  const [menuOpen, setMenuOpen] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])
  // close the mobile drawer whenever the route changes (adjusted during render, not in an effect)
  const [drawerPath, setDrawerPath] = useState(loc.pathname)
  if (drawerPath !== loc.pathname) {
    setDrawerPath(loc.pathname)
    setMobileOpen(false)
  }

  function toggleCollapse() {
    setCollapsed((c) => {
      localStorage.setItem('yq-collapsed', c ? '0' : '1')
      return !c
    })
  }

  const passwordScreen = passwordScreenFor(me)
  if (mustReset && loc.pathname !== passwordScreen.split('#')[0]) {
    return <Navigate to={passwordScreen} replace />
  }

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <CommandPalette />
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden" onClick={() => setMobileOpen(false)} />
      )}
      {/* Sidebar (static on lg, slide-in drawer on mobile) */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-[260px] shrink-0 flex-col text-white transition-transform duration-300 lg:static lg:z-auto lg:transition-[width]',
          mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
          collapsed ? 'lg:w-[76px]' : 'lg:w-[260px]',
        )}
        style={{ background: 'linear-gradient(180deg,#2a1259 0%,#190a3a 100%)' }}
      >
        <Link to="/" className="flex items-center gap-3 px-5 pb-2 pt-6">
          <Logo className="h-9 w-9 rounded-xl shadow-lift" />
          {!collapsed && (
            <div className="leading-tight">
              <div className="font-display text-[15px] font-bold">YQ Bahrain</div>
              <div className="text-[11px] font-medium text-white/85">Mobile Accessories · AI Ops</div>
            </div>
          )}
        </Link>

        <nav className="mt-2 flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-3 [scrollbar-color:rgba(255,255,255,.2)_transparent] [scrollbar-width:thin]">
          {items.map((n, i) => {
            const Icon = n.icon
            const newSection = i === 0 || items[i - 1].section !== n.section
            // a section whose only page carries the section's own name ("Receivables") needs no heading
            const alone = newSection && items[i + 1]?.section !== n.section && n.label.toLowerCase() === n.section.toLowerCase()
            return (
              <div key={n.to}>
                {newSection && !collapsed && (
                  alone
                    ? i > 0 && <div className="pt-2" aria-hidden="true" />
                    : (
                      <div className={cn('px-2 pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/40', i > 0 && 'pt-3')}>
                        {n.section}
                      </div>
                    )
                )}
                {newSection && collapsed && i > 0 && <div className="mx-2 my-2 border-t border-white/10" />}
                <NavLink
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
              </div>
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
      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* Ambient luxe canvas — soft violet glows + faint grid behind everything */}
        <div className="luxe-canvas" aria-hidden />
        {/* Top bar */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border/60 bg-background/70 px-5 backdrop-blur-xl">
          <button
            onClick={() => setMobileOpen(true)}
            className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground lg:hidden"
            title="Menu"
          >
            <Menu size={18} />
          </button>
          <button
            onClick={toggleCollapse}
            className="hidden h-9 w-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground lg:grid"
            title="Toggle sidebar"
          >
            {collapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
          </button>
          <div className="min-w-0 shrink-0">
            <div className="truncate font-display text-[15px] font-semibold">{active?.label ?? 'Portal'}</div>
          </div>
          <Greeting name={me?.full_name?.split(' ')[0] || me?.email?.split('@')[0]} />
          <div className="ml-auto flex min-w-0 items-center gap-2 lg:ml-0">
            {/* the Command Centre carries the same chip in its own band */}
            {!mustReset && !loc.pathname.startsWith('/command') && <FreshnessChip className="max-w-[46vw] sm:max-w-none" />}
            <button
              onClick={() => window.dispatchEvent(new Event('yq:open-cmdk'))}
              className="hidden items-center gap-2 rounded-lg border bg-card px-2.5 py-1.5 text-xs text-muted-foreground transition hover:text-foreground md:flex"
              title="Search (Ctrl/Cmd + K)"
            >
              <Search size={14} /> Search
              <kbd className="rounded border px-1 text-[10px]">⌘K</kbd>
            </button>
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

        {mustReset && (
          <div role="status" className="relative z-10 flex items-center gap-3 border-b border-amber-200 bg-amber-50 px-5 py-2.5 text-[13px] text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            <KeyRound size={16} className="shrink-0" aria-hidden />
            <p className="min-w-0">
              <span className="font-bold">Set your own password to continue.</span> You signed in with a temporary password; the rest of the portal opens as soon as you choose your own below.
            </p>
          </div>
        )}

        {/* Page content with cross-fade */}
        <main className="relative z-10 flex-1 overflow-y-auto overflow-x-hidden">
          <AnimatePresence mode="wait">
            <motion.div
              key={loc.pathname}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              className="w-full min-w-0 max-w-full overflow-x-hidden px-4 py-6 sm:px-5 md:px-7 lg:px-8"
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
