import { Suspense, useEffect, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { Link, Navigate, NavLink, useLocation, useOutlet } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { useQueryClient } from '@tanstack/react-query'
import { Moon, Sun, LogOut, PanelLeftClose, PanelLeft, Settings, ChevronDown, Search, Menu, KeyRound } from 'lucide-react'
import { cn } from '@/lib/utils'
import { apiGet } from '@/lib/api'
import { mustResetOf, passwordScreenFor, useAuth, type Me } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import { navFor, NAV } from '@/lib/nav'
import { useShellScroll } from '@/lib/shellScroll'
import { useCodeWarm, useWarmOnIntent } from '@/lib/prefetch'
import { PageErrorBoundary, PageGlide, PageSkeleton } from '@/lib/pageFrame'
import { lockScroll } from '@/lib/sheet'
import { Logo } from './Logo'
import { CommandPalette } from './CommandPalette'
import { FreshnessChip } from './FreshnessChip'
import { MarketCapture } from './MarketCapture'

const PREFETCH_REPORTS: [key: string, feature: string][] = [
  ['sales', 'Sales'], ['inventory', 'Inventory'], ['margins', 'Margins'], ['receivables', 'Receivables'],
]

/** Pre-warm the most-clicked pages once the page in front of the user has its data — by the time
 *  they click Sales/Inventory/Catalog, it is already in memory (0ms click). perf-2609: only pages
 *  this login can open, only after the current page's own requests have finished (they used to
 *  compete with it on a 0.1-CPU server, 2.5 s after every load), and a failure is not retried. */
function usePrefetchPages(me: Me | null, enabled: boolean) {
  const qc = useQueryClient()
  const role = me?.role
  const features = (me?.features || []).join('|')
  useEffect(() => {
    if (!enabled) return
    const can = (feature: string) => role === 'admin' || features.split('|').includes(feature)
    let timer: ReturnType<typeof setTimeout>
    let waited = 0
    const run = () => {
      // Wait (up to ~20 s) until nothing else is loading, then warm quietly.
      if (qc.isFetching() > 0 && waited < 20_000) {
        waited += 1000
        timer = setTimeout(run, 1000)
        return
      }
      for (const [key, feature] of PREFETCH_REPORTS) {
        if (can(feature)) {
          void qc.prefetchQuery({ queryKey: ['report', key], queryFn: () => apiGet(`/report/${key}`), retry: false })
        }
      }
      if ((role === 'admin' || role === 'member') && can('Catalog')) {
        void qc.prefetchQuery({ queryKey: ['catalog'], queryFn: () => apiGet('/catalog'), retry: false })
      }
    }
    timer = setTimeout(run, 3000)
    return () => clearTimeout(timer)
  }, [enabled, qc, role, features])
}

/**
 * The window scrolls (ship 2, item 12; S9) — it used to be an inner box (`h-screen overflow-hidden`
 * around a scrolling <main>): a phone's address bar never hid, tap-to-top was dead, every `sticky`
 * inside a page sat still, and a new page opened at the old one's scroll position. While the office
 * is on screen it publishes its top bar's height, so a page's sticky bar hangs just under it (the
 * same variable the salesman shell publishes; pages/shop/StaffCatalog reads it), and the page keeps
 * a stable scrollbar gutter, so a short page and a long one line up instead of shifting sideways by
 * the scrollbar's width (a classic Windows scrollbar; phones have none). lib/sheet's lock knows it.
 */
const TOPBAR_PX = 64
function useWindowFrame(collapsed: boolean) {
  useLayoutEffect(() => {
    const root = document.documentElement
    const gutter = root.style.scrollbarGutter
    root.style.setProperty('--yq-topbar', `${TOPBAR_PX}px`)
    root.style.scrollbarGutter = 'stable'
    return () => {
      root.style.removeProperty('--yq-topbar')
      root.style.scrollbarGutter = gutter
    }
  }, [])
  // and what a page's fixed bar must keep clear of (pages/shop/StaffCatalog's order bar): no tab bar in the
  // office, and the sidebar's width from 1024 px up — without them the bar floated 64 px up and over it
  useLayoutEffect(() => {
    const root = document.documentElement
    const lg = window.matchMedia('(min-width: 1024px)')
    const apply = () => {
      root.style.setProperty('--yq-tabbar', '0px')
      root.style.setProperty('--yq-sidebar', lg.matches ? (collapsed ? '76px' : '260px') : '0px')
    }
    apply()
    lg.addEventListener('change', apply)
    return () => {
      lg.removeEventListener('change', apply)
      root.style.removeProperty('--yq-tabbar')
      root.style.removeProperty('--yq-sidebar')
    }
  }, [collapsed])
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
  usePrefetchPages(me, !!me && me.role !== 'salesman' && !mustReset)
  // a new page at the top, Back where the page was (lib/shellScroll)
  useShellScroll()
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('yq-collapsed') === '1',
  )
  useWindowFrame(collapsed)
  const items = navFor(me)
  // the code of every page this login can open, at idle once the page in front has its answers; a
  // hover, a press or a focus on a menu item fetches that page's code and its main read at once
  useCodeWarm([...items.map((n) => n.to), '/settings'], loc.pathname, !!me && !mustReset)
  const warmOn = useWarmOnIntent()
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
  // close the mobile drawer whenever the route changes (adjusted during render, not in an effect); from
  // the first page change on, each new page glides in (never the shell's first page: the boot did that)
  const [drawerPath, setDrawerPath] = useState(loc.pathname)
  const [navigated, setNavigated] = useState(false)
  // The menu item just clicked, lit at once while its page renders (a chart page can take a few hundred
  // ms of the CPU: the click is acknowledged on the next frame, not when the page is ready).
  const [pendingTo, setPendingTo] = useState<string | null>(null)
  if (drawerPath !== loc.pathname) {
    setDrawerPath(loc.pathname)
    setMobileOpen(false)
    setNavigated(true)
    setPendingTo(null)
  }
  // a plain click that React Router turns into a navigation (a Ctrl/⌘ or middle click opens a tab instead)
  const markPending = (to: string) => (e: ReactMouseEvent) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || to === loc.pathname) return
    setPendingTo(to)
  }
  // the open drawer holds the page still behind it (phones and tablets; it is the static sidebar from 1024 px)
  useEffect(() => {
    if (!mobileOpen) return
    const lg = window.matchMedia('(min-width: 1024px)')
    if (lg.matches) return
    const release = lockScroll()
    const onWide = () => { if (lg.matches) setMobileOpen(false) }
    lg.addEventListener('change', onWide)
    return () => {
      lg.removeEventListener('change', onWide)
      release()
    }
  }, [mobileOpen])

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
    <div className="flex min-h-screen w-full">
      <CommandPalette />
      {/* Ambient luxe canvas — soft violet glows + faint grid behind everything. Pinned to the window, so
          it stays the window's size however long the page is (the window scrolls, not a box). */}
      <div className="pointer-events-none fixed inset-0 z-0" aria-hidden>
        <div className="luxe-canvas" />
      </div>
      {/* Mobile backdrop (no blur: a full-screen blur on a phone, recomputed while the drawer slides) */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setMobileOpen(false)} />
      )}
      {/* Sidebar: a slide-in drawer below 1024 px; from 1024 px it sticks to the window while the page
          scrolls (h-dvh). Collapsing swaps its width at once — no width animation, which reflowed the
          page and redrew every chart on each frame of it (item 23). */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-[260px] shrink-0 flex-col text-white transition-transform duration-300 lg:sticky lg:top-0 lg:bottom-auto lg:z-auto lg:h-dvh lg:transition-none',
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

        <nav className="mt-2 flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overscroll-contain px-3 [scrollbar-color:rgba(255,255,255,.2)_transparent] [scrollbar-width:thin]">
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
                  {...warmOn(n.to)}
                  onClick={markPending(n.to)}
                  className={({ isActive }) =>
                    cn(
                      'group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-white/70 transition-colors hover:bg-white/10 hover:text-white',
                      (pendingTo ? pendingTo === n.to : isActive) && 'bg-white/15 text-white shadow-[0_6px_18px_rgba(124,58,237,.35)]',
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
        {/* Top bar: sticks to the window on a solid surface (a 24 px blur over the moving canvas used to be
            recomputed on every scrolled frame) */}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border/60 bg-background px-5">
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

        {/* The page (lib/pageFrame). It no longer waits for the old page to leave (AnimatePresence
            mode="wait": a 280 ms exit before the next page could even start): the new page glides in as
            soon as it is there; while its code downloads, one page-shaped skeleton; a page that breaks
            shows its own error with Reload, the shell stays. overflow-x CLIP, not hidden: hidden made
            this box a scroll container, so nothing `sticky` inside a page ever stuck.
            --yq-capture-room: the floating Spotted button's height while it shows (MarketCapture, office
            mount below), so a page's last line can scroll out from under it */}
        <main className="relative z-10 flex-1">
          <PageGlide
            key={loc.pathname}
            glide={navigated}
            className="w-full min-w-0 max-w-full overflow-x-clip px-4 py-6 pb-[calc(1.5rem+var(--yq-capture-room,0px))] sm:px-5 md:px-7 lg:px-8"
          >
            <PageErrorBoundary resetKey={loc.pathname}>
              <Suspense fallback={<PageSkeleton />}>{outlet}</Suspense>
            </PageErrorBoundary>
          </PageGlide>
        </main>
      </div>
      {/* Market Intel v2 (r8m): the office reports what it sees too — Spotted on the Market Intel pages
          (members, admins and reviewers holding the page; management is read-only and never sees it) */}
      <MarketCapture office />
    </div>
  )
}
