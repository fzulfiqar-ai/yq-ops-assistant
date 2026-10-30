import { Suspense, useEffect, useState, type CSSProperties } from 'react'
import { Link, Navigate, NavLink, useLocation, useOutlet } from 'react-router-dom'
import { Bell, BookImage, ClipboardList, CircleUserRound, House, KeyRound, Loader2, LogOut, PanelLeftClose, PanelLeftOpen, Users, type LucideIcon } from 'lucide-react'
import { mustResetOf, passwordScreenFor, useAuth } from '@/lib/auth'
import { MarketCapture } from '@/components/MarketCapture'
import { navFor } from '@/lib/nav'
import { PageErrorBoundary } from '@/lib/pageFrame'
import { useCodeWarm, useWarmOnIntent } from '@/lib/prefetch'
import { noteTabTap, useShellScroll } from '@/lib/shellScroll'
import { cn } from '@/lib/utils'
import { firstName, initials, useNewOrderCount } from '@/pages/sales/lib'
import { SignOutSheet } from '@/pages/sales/SignOutSheet'
import { activeTab, tabAllowed } from '@/pages/sales/tabs'

/**
 * The shell a salesman lives in — a phone in one hand in a shop, a laptop in the evening.
 *
 * Five destinations (Today · Catalog · Orders · Customers · Me) plus whatever else an admin has
 * granted (Finds, Leads, Field notes, Coach…) under "More". Phone: a floating five-tab nav in the
 * thumb zone, safe-area aware. Tablet: the same tabs across the top. Desktop: a sidebar FIXED to the
 * window (Sprint 5, audit D4 — it used to scroll away with a long catalog): a 64 px icon rail from
 * 1024 px, the full 240 px from 1280 px unless the rep collapses it to the rail (remembered on this
 * device). The width is published as --yq-sidebar so pages can keep fixed bars clear of it.
 *
 * Colour: the salesman surfaces share the marketplace's plum identity. This wrapper re-points the
 * portal's HSL tokens (`--primary`, `--accent`, `--ring`, `--background`…) so every Tailwind
 * token class inside reads plum — the office AppShell is untouched.
 */

const SALES_TOKENS: CSSProperties = {
  ['--primary' as string]: '273 39% 41%',
  ['--primary-foreground' as string]: '0 0% 100%',
  ['--accent' as string]: '273 40% 94%',
  ['--accent-foreground' as string]: '272 40% 22%',
  ['--ring' as string]: '273 60% 55%',
  ['--background' as string]: '36 33% 97%',
  ['--foreground' as string]: '268 28% 11%',
  ['--card' as string]: '0 0% 100%',
  ['--card-foreground' as string]: '268 28% 11%',
  ['--muted' as string]: '36 25% 95%',
  ['--muted-foreground' as string]: '266 12% 38%',
  ['--border' as string]: '270 14% 89%',
  ['--input' as string]: '270 14% 89%',
  ['--secondary' as string]: '36 25% 95%',
  ['--secondary-foreground' as string]: '268 28% 11%',
  ['--radius' as string]: '0.875rem',
  colorScheme: 'light',
}

const TABBAR_PX = 64

interface Tab {
  to: string
  label: string
  icon: LucideIcon
  badge?: number
  end?: boolean
}

const RAIL_KEY = 'yq-sales-rail'

function readRail(): boolean {
  try {
    return localStorage.getItem(RAIL_KEY) === '1'
  } catch {
    return false
  }
}

/** Publish the bottom stack height and the sidebar width so pages keep their own fixed furniture clear of them. */
function useShellVars(collapsed: boolean) {
  useEffect(() => {
    const root = document.documentElement
    const md = window.matchMedia('(min-width: 768px)')
    const lg = window.matchMedia('(min-width: 1024px)')
    const xl = window.matchMedia('(min-width: 1280px)')
    const apply = () => {
      root.style.setProperty('--yq-tabbar', md.matches ? '0px' : `${TABBAR_PX + 24}px`)
      root.style.setProperty('--yq-topbar', lg.matches ? '0px' : '56px')
      root.style.setProperty('--yq-sidebar', !lg.matches ? '0px' : xl.matches && !collapsed ? '240px' : '64px')
    }
    apply()
    md.addEventListener('change', apply)
    lg.addEventListener('change', apply)
    xl.addEventListener('change', apply)
    return () => {
      md.removeEventListener('change', apply)
      lg.removeEventListener('change', apply)
      xl.removeEventListener('change', apply)
      root.style.removeProperty('--yq-tabbar')
      root.style.removeProperty('--yq-topbar')
      root.style.removeProperty('--yq-sidebar')
    }
  }, [collapsed])
}

function Badge({ count, className }: { count: number; className?: string }) {
  if (count <= 0) return null
  return (
    <span aria-label={`${count} new`} className={cn('grid h-[18px] min-w-[18px] place-items-center rounded-full bg-primary px-1 text-[10.5px] font-bold tabular-nums text-primary-foreground ring-2 ring-card', className)}>
      {count > 99 ? '99+' : count}
    </span>
  )
}

/**
 * The 15 field logins were handed out with a temporary password. While the server-owned
 * `must_reset` flag is set (/me, or the session metadata on an older API) every other API
 * route answers 403, so the shell keeps the rep on the password screen and says why, instead
 * of letting each page show a raw error.
 */
function TempPasswordBanner({ mustReset }: { mustReset: boolean }) {
  if (!mustReset) return null
  return (
    <div role="status" className="border-b border-amber-200 bg-amber-50">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-2.5">
        <KeyRound size={16} className="shrink-0 text-amber-800" aria-hidden="true" />
        <p className="min-w-0 flex-1 text-[12.5px] font-medium leading-snug text-amber-900">
          <span className="font-bold">Set your own password to continue.</span> You signed in with a temporary password; the rest of the app opens as soon as you choose your own.
        </p>
      </div>
    </div>
  )
}

/** The tab the page is under; -1 (no tab lit) on any other page (pages/sales/tabs.ts). */
function useActiveIndex(tabs: Tab[]): number {
  const { pathname } = useLocation()
  return activeTab(pathname, tabs)
}

/**
 * What a tab link does besides navigating (ship 2, items 12 and 13): a press, a resting hover or a focus
 * fetches its page's code (and Today's read) ahead, so the first tap never freezes on a download; the
 * tap tells the scroll keeper it came from the tab bar, so the tab lands where it was left (or, the tab
 * already open, back at its top) — lib/shellScroll.
 */
function useTabLink(): (to: string) => ReturnType<ReturnType<typeof useWarmOnIntent>> & { onClick: () => void } {
  const warmOn = useWarmOnIntent()
  return (to) => ({ ...warmOn(to), onClick: () => noteTabTap(to) })
}

function FloatingTabs({ tabs }: { tabs: Tab[] }) {
  const active = useActiveIndex(tabs)
  const tabLink = useTabLink()
  // as many columns as the rep has tabs; the lit pill hides on a page under none of them
  const n = Math.max(1, tabs.length)
  return (
    <nav aria-label="Main" className="fixed inset-x-3 z-40 md:hidden" style={{ bottom: 'calc(12px + env(safe-area-inset-bottom, 0px))' }}>
      <ul className="relative grid h-16 items-stretch rounded-3xl border border-border/80 bg-card shadow-[0_12px_32px_-12px_rgba(25,20,36,.28)]" style={{ ['--i' as string]: Math.max(0, active), gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
        <li aria-hidden="true" className={cn('pointer-events-none absolute inset-y-1.5 left-1.5 rounded-2xl bg-accent transition-transform duration-300 ease-[cubic-bezier(.22,1.18,.36,1)] motion-reduce:transition-none', active < 0 && 'opacity-0')} style={{ width: `calc((100% - 0.75rem) / ${n})`, transform: 'translateX(calc(var(--i) * 100%))' }} />
        {tabs.map((t, i) => {
          const Icon = t.icon
          const on = i === active
          return (
            <li key={t.to} className="relative">
              <NavLink to={t.to} end={t.end} {...tabLink(t.to)} aria-current={on ? 'page' : undefined} className={cn('relative flex h-full flex-col items-center justify-center gap-0.5 rounded-2xl text-[11px] font-semibold transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/70', on ? 'text-accent-foreground' : 'text-muted-foreground')}>
                <span className={cn('relative transition-transform duration-200', on && 'scale-[1.08]')}>
                  <Icon size={22} strokeWidth={on ? 2.1 : 1.75} aria-hidden="true" />
                  {t.badge ? <Badge count={t.badge} className="absolute -right-2.5 -top-1.5" /> : null}
                </span>
                {t.label}
              </NavLink>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

function TopTabs({ tabs }: { tabs: Tab[] }) {
  const tabLink = useTabLink()
  return (
    <nav aria-label="Main" className="hidden md:block lg:hidden">
      <ul className="flex items-center gap-1 rounded-full border border-border bg-muted p-1">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <li key={t.to}>
              <NavLink to={t.to} end={t.end} {...tabLink(t.to)} className={({ isActive }) => cn('flex h-9 items-center gap-2 rounded-full px-3.5 text-[13px] font-semibold transition-colors duration-150', isActive ? 'bg-card text-primary shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
                <Icon size={16} aria-hidden="true" />
                <span className="relative pr-1">
                  {t.label}
                  {t.badge ? <Badge count={t.badge} className="absolute -right-3.5 -top-2" /> : null}
                </span>
              </NavLink>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

function Sidebar({ tabs, more, name, onSignOut, collapsed, onToggle }: { tabs: Tab[]; more: Tab[]; name: string; onSignOut: () => void; collapsed: boolean; onToggle: () => void }) {
  // 1024-1279: always the icon rail; 1280+: full width unless collapsed. Labels and the wide layout
  // are switched by CSS (xl:), so the first paint is already right — no media query in JS.
  const full = !collapsed
  const tabLink = useTabLink()
  const label = cn('flex-1 truncate', full ? 'hidden xl:inline' : 'hidden')
  const item = ({ isActive }: { isActive: boolean }) =>
    cn(
      'relative flex h-11 items-center justify-center gap-3 rounded-xl text-[14px] font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/70',
      full && 'xl:justify-start xl:px-3',
      isActive ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
    )
  return (
    <aside className={cn('fixed inset-y-0 left-0 z-40 hidden w-16 flex-col border-r border-border bg-card lg:flex', full && 'xl:w-60')} aria-label="Sidebar">
      <div className={cn('flex items-center gap-2 px-3 pb-3 pt-4', full && 'xl:px-4')}>
        <Link to="/today" className={cn('flex min-w-0 flex-1 items-center justify-center gap-3', full && 'xl:justify-start')} title="YQ Sales">
          <img src="/yq-logo-160.webp" alt="" width={36} height={36} className="h-9 w-9 shrink-0 rounded-xl" />
          <span className={cn('leading-tight', full ? 'hidden xl:block' : 'hidden')}>
            <span className="block font-display text-[15px] font-bold text-foreground">YQ Sales</span>
            <span className="block text-[11.5px] text-muted-foreground">Field app</span>
          </span>
        </Link>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2.5" aria-label="Main">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <NavLink key={t.to} to={t.to} end={t.end} {...tabLink(t.to)} className={item} title={t.label} aria-label={t.badge ? `${t.label}, ${t.badge} new` : t.label}>
              <Icon size={18} strokeWidth={1.9} aria-hidden="true" className="shrink-0" />
              <span className={label}>{t.label}</span>
              {t.badge ? (
                <>
                  <Badge count={t.badge} className={full ? 'hidden xl:grid' : 'hidden'} />
                  <span aria-hidden="true" className={cn('absolute right-2 top-2 h-2 w-2 rounded-full bg-primary ring-2 ring-card', full && 'xl:hidden')} />
                </>
              ) : null}
            </NavLink>
          )
        })}
        {more.length > 0 && (
          <>
            <div className={cn('px-3 pb-1 pt-4 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted-foreground', full ? 'hidden xl:block' : 'hidden')}>More</div>
            <div className={cn('mx-auto my-3 h-px w-8 bg-border', full && 'xl:hidden')} aria-hidden="true" />
            {more.map((t) => {
              const Icon = t.icon
              return (
                <NavLink key={t.to} to={t.to} className={item} title={t.label} aria-label={t.label}>
                  <Icon size={18} strokeWidth={1.9} aria-hidden="true" className="shrink-0" />
                  <span className={label}>{t.label}</span>
                </NavLink>
              )
            })}
          </>
        )}
      </nav>
      <div className="border-t border-border p-2.5">
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          title={collapsed ? 'Expand the sidebar' : 'Collapse to icons'}
          className={cn('mb-1 hidden h-10 w-full items-center gap-3 rounded-xl text-[12.5px] font-semibold text-muted-foreground hover:bg-muted hover:text-foreground xl:flex', collapsed ? 'justify-center' : 'px-3')}
        >
          {collapsed ? <PanelLeftOpen size={17} aria-hidden="true" /> : <PanelLeftClose size={17} aria-hidden="true" />}
          <span className={collapsed ? 'hidden' : ''}>Collapse</span>
        </button>
        <div className={cn('flex flex-col items-center gap-1 rounded-xl py-1', full && 'xl:flex-row xl:gap-3 xl:px-2 xl:py-2')}>
          <Link to="/account" title="My account" aria-label="My account" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent font-display text-[13px] font-bold text-accent-foreground">
            {initials(name) || 'YQ'}
          </Link>
          <span className={cn('min-w-0 flex-1', full ? 'hidden xl:block' : 'hidden')}>
            <span className="block truncate text-[13px] font-semibold text-foreground">{name}</span>
            <Link to="/account" className="text-[11.5px] font-medium text-primary hover:underline">
              My account
            </Link>
          </span>
          <button type="button" onClick={onSignOut} aria-label="Sign out" aria-haspopup="dialog" title="Sign out" className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground">
            <LogOut size={17} />
          </button>
        </div>
      </div>
    </aside>
  )
}

const CORE = new Set(['/today', '/shop', '/shop-orders', '/customers', '/account', '/settings', '/catalog'])

export function SalesmanShell() {
  const { me, session } = useAuth()
  const outlet = useOutlet()
  const loc = useLocation()
  const newCount = useNewOrderCount()
  // sign-out asks first and clears this phone's carts and shop (pages/sales/SignOutSheet)
  const [signOutOpen, setSignOutOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(readRail)
  useShellVars(collapsed)
  const toggleRail = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(RAIL_KEY, c ? '0' : '1')
      } catch {
        /* private mode — the choice lasts this visit */
      }
      return !c
    })
  }

  // A temporary password: only the password screen is useful (the API refuses everything else).
  const mustReset = mustResetOf(me, session)
  const passwordScreen = passwordScreenFor(me)

  // only the tabs whose page this login may open (the route's Gate in App.tsx; pages/sales/tabs.ts)
  const tabs: Tab[] = (
    [
      { to: '/today', label: 'Today', icon: House, end: true },
      { to: '/shop', label: 'Catalog', icon: BookImage },
      { to: '/shop-orders', label: 'Orders', icon: ClipboardList, badge: newCount },
      { to: '/customers', label: 'Shops', icon: Users },
      { to: '/account', label: 'Me', icon: CircleUserRound },
    ] satisfies Tab[]
  ).filter((t) => tabAllowed(me, t.to))
  const tabPaths = tabs.map((t) => t.to)
  // the window's scroll: a new page at the top, Back where it was, each tab where it was left (lib/shellScroll)
  useShellScroll(tabPaths)
  // the tabs' code at idle once the shell has painted (the first tap used to freeze on its download)
  useCodeWarm(tabPaths, loc.pathname, !!me && !mustReset)

  if (mustReset && loc.pathname !== passwordScreen.split('#')[0]) {
    return <Navigate to={passwordScreen} replace />
  }

  const name = me?.full_name || me?.email?.split('@')[0] || ''
  const first = firstName(name)
  // everything else an admin granted this salesman (Finds, Leads, Field notes, Coach, Reports…)
  const more: Tab[] = navFor(me)
    .filter((n) => !CORE.has(n.to) && n.to !== '/')
    .map((n) => ({ to: n.to, label: n.label, icon: n.icon }))

  const onSignOut = () => setSignOutOpen(true)

  return (
    <div data-shell="sales" style={SALES_TOKENS} className="min-h-screen bg-background text-foreground">
      <div className="flex">
        <Sidebar tabs={tabs} more={more} name={name} onSignOut={onSignOut} collapsed={collapsed} onToggle={toggleRail} />
        {/* the sidebar is fixed: the page keeps its width clear (64 px rail, 240 px from 1280 unless collapsed) */}
        <div className={cn('min-w-0 flex-1 lg:pl-16', !collapsed && 'xl:pl-60')}>
          {/* top bar: phone + tablet (desktop has the sidebar) */}
          <header className="sticky top-0 z-30 border-b border-border bg-card lg:hidden">
            <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
              <Link to="/account" aria-label="My account" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent font-display text-[13px] font-bold text-accent-foreground">
                {initials(name) || 'YQ'}
              </Link>
              <div className="min-w-0 flex-1 leading-tight">
                <div className="truncate font-display text-[14px] font-bold">YQ Sales</div>
                {first && <div className="truncate text-[11.5px] text-muted-foreground">{first}</div>}
              </div>
              <TopTabs tabs={tabs} />
              <Link to="/shop-orders" aria-label={newCount ? `${newCount} new orders` : 'Orders'} className="relative grid h-11 w-11 shrink-0 place-items-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground">
                <Bell size={19} aria-hidden="true" />
                {newCount > 0 && <Badge count={newCount} className="absolute right-1 top-1" />}
              </Link>
              <button type="button" onClick={onSignOut} aria-label="Sign out" aria-haspopup="dialog" title="Sign out" className="-mr-1.5 hidden h-11 w-11 shrink-0 place-items-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground md:grid">
                <LogOut size={18} />
              </button>
            </div>
          </header>

          <TempPasswordBanner mustReset={mustReset} />

          {/* --yq-capture-room: the floating "Spotted" button's height while it shows (MarketCapture),
             so the last line of a page can scroll out from under it */}
          <main className="pb-[calc(var(--yq-tabbar,88px)+env(safe-area-inset-bottom)+var(--yq-capture-room,0px))] md:pb-[calc(2.5rem+var(--yq-capture-room,0px))]">
            {/* a page that breaks shows its own error with Reload; the tabs keep working (lib/pageFrame) */}
            <PageErrorBoundary resetKey={loc.pathname}>
              <Suspense
                fallback={
                  <div className="grid h-[60vh] place-items-center text-muted-foreground">
                    <Loader2 className="animate-spin" size={22} />
                  </div>
                }
              >
                {outlet}
              </Suspense>
            </PageErrorBoundary>
          </main>
        </div>
      </div>
      <FloatingTabs tabs={tabs} />
      <MarketCapture />
      <SignOutSheet open={signOutOpen} onClose={() => setSignOutOpen(false)} />
    </div>
  )
}
