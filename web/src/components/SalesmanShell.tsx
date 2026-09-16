import { Suspense, useEffect, useState, type CSSProperties } from 'react'
import { Link, NavLink, useLocation, useOutlet } from 'react-router-dom'
import { Bell, BookImage, ClipboardList, CircleUserRound, House, Loader2, LogOut, Users, type LucideIcon } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { navFor } from '@/lib/nav'
import { cn } from '@/lib/utils'
import { firstName, initials, useNewOrderCount } from '@/pages/sales/lib'

/**
 * The shell a salesman lives in — a phone in one hand in a shop, a laptop in the evening.
 *
 * Five destinations (Today · Catalog · Orders · Customers · Me) plus whatever else an admin has
 * granted (Finds, Leads, Field notes, Coach…) under "More". Phone: a floating five-tab nav in the
 * thumb zone, safe-area aware. Tablet: the same tabs across the top. Desktop: a quiet sidebar.
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

/** Publish the bottom stack height so pages keep their own sticky furniture clear of it. */
function useTabbarVar() {
  useEffect(() => {
    const root = document.documentElement
    const md = window.matchMedia('(min-width: 768px)')
    const lg = window.matchMedia('(min-width: 1024px)')
    const apply = () => {
      root.style.setProperty('--yq-tabbar', md.matches ? '0px' : `${TABBAR_PX + 24}px`)
      root.style.setProperty('--yq-topbar', lg.matches ? '0px' : '56px')
    }
    apply()
    md.addEventListener('change', apply)
    lg.addEventListener('change', apply)
    return () => {
      md.removeEventListener('change', apply)
      lg.removeEventListener('change', apply)
      root.style.removeProperty('--yq-tabbar')
      root.style.removeProperty('--yq-topbar')
    }
  }, [])
}

function Badge({ count, className }: { count: number; className?: string }) {
  if (count <= 0) return null
  return (
    <span aria-label={`${count} new`} className={cn('grid h-[18px] min-w-[18px] place-items-center rounded-full bg-primary px-1 text-[10.5px] font-bold tabular-nums text-primary-foreground ring-2 ring-card', className)}>
      {count > 99 ? '99+' : count}
    </span>
  )
}

/** The 15 field logins were handed out with a temporary password: say so once, fix one tap away. */
function TempPasswordBanner() {
  const { session } = useAuth()
  const loc = useLocation()
  const mustReset = Boolean(session?.user?.user_metadata?.must_reset)
  if (!mustReset || loc.pathname === '/account' || loc.pathname === '/settings') return null
  return (
    <div className="border-b border-amber-200 bg-amber-50">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-2">
        <p className="min-w-0 flex-1 text-[12.5px] font-medium leading-snug text-amber-900">You are using a temporary password — set your own.</p>
        <Link to="/account#password" className="grid h-10 shrink-0 place-items-center rounded-lg px-3 text-[12.5px] font-bold text-amber-900 underline underline-offset-2 hover:bg-amber-100">
          Change password
        </Link>
      </div>
    </div>
  )
}

function useActiveIndex(tabs: Tab[]): number {
  const { pathname } = useLocation()
  const i = tabs.findIndex((t) => (t.end ? pathname === t.to : pathname === t.to || pathname.startsWith(t.to + '/') || pathname.startsWith(t.to + '?')))
  return i < 0 ? 0 : i
}

function FloatingTabs({ tabs }: { tabs: Tab[] }) {
  const active = useActiveIndex(tabs)
  return (
    <nav aria-label="Main" className="fixed inset-x-3 z-40 md:hidden" style={{ bottom: 'calc(12px + env(safe-area-inset-bottom, 0px))' }}>
      <ul className="relative grid h-16 grid-cols-5 items-stretch rounded-3xl border border-border/80 bg-card/90 shadow-[0_12px_32px_-12px_rgba(25,20,36,.28)] backdrop-blur-xl" style={{ ['--i' as string]: active }}>
        <li aria-hidden="true" className="pointer-events-none absolute inset-y-1.5 left-1.5 w-[calc((100%-0.75rem)/5)] rounded-2xl bg-accent transition-transform duration-300 ease-[cubic-bezier(.22,1.18,.36,1)] motion-reduce:transition-none" style={{ transform: 'translateX(calc(var(--i) * 100%))' }} />
        {tabs.map((t, i) => {
          const Icon = t.icon
          const on = i === active
          return (
            <li key={t.to} className="relative">
              <NavLink to={t.to} end={t.end} aria-current={on ? 'page' : undefined} className={cn('relative flex h-full flex-col items-center justify-center gap-0.5 rounded-2xl text-[11px] font-semibold transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/70', on ? 'text-accent-foreground' : 'text-muted-foreground')}>
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
  return (
    <nav aria-label="Main" className="hidden md:block lg:hidden">
      <ul className="flex items-center gap-1 rounded-full border border-border bg-muted p-1">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <li key={t.to}>
              <NavLink to={t.to} end={t.end} className={({ isActive }) => cn('flex h-9 items-center gap-2 rounded-full px-3.5 text-[13px] font-semibold transition-colors duration-150', isActive ? 'bg-card text-primary shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
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

function Sidebar({ tabs, more, name, onSignOut, signingOut }: { tabs: Tab[]; more: Tab[]; name: string; onSignOut: () => void; signingOut: boolean }) {
  const item = ({ isActive }: { isActive: boolean }) =>
    cn('flex h-11 items-center gap-3 rounded-xl px-3 text-[14px] font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/70', isActive ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground')
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-card lg:flex" aria-label="Sidebar">
      <Link to="/today" className="flex items-center gap-3 px-5 pb-4 pt-5">
        <img src="/yq-logo-160.webp" alt="" width={36} height={36} className="h-9 w-9 rounded-xl" />
        <span className="leading-tight">
          <span className="block font-display text-[15px] font-bold text-foreground">YQ Sales</span>
          <span className="block text-[11.5px] text-muted-foreground">Field app</span>
        </span>
      </Link>
      <nav className="flex-1 space-y-0.5 px-3" aria-label="Main">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <NavLink key={t.to} to={t.to} end={t.end} className={item}>
              <Icon size={18} strokeWidth={1.9} aria-hidden="true" />
              <span className="flex-1">{t.label}</span>
              {t.badge ? <Badge count={t.badge} /> : null}
            </NavLink>
          )
        })}
        {more.length > 0 && (
          <>
            <div className="px-3 pb-1 pt-4 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">More</div>
            {more.map((t) => {
              const Icon = t.icon
              return (
                <NavLink key={t.to} to={t.to} className={item}>
                  <Icon size={18} strokeWidth={1.9} aria-hidden="true" />
                  <span className="flex-1">{t.label}</span>
                </NavLink>
              )
            })}
          </>
        )}
      </nav>
      <div className="border-t border-border p-3">
        <div className="flex items-center gap-3 rounded-xl px-2 py-2">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent font-display text-[13px] font-bold text-accent-foreground">{initials(name) || 'YQ'}</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-semibold text-foreground">{name}</span>
            <Link to="/account" className="text-[11.5px] font-medium text-primary hover:underline">
              My account
            </Link>
          </span>
          <button type="button" onClick={onSignOut} aria-label="Sign out" title="Sign out" className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground">
            {signingOut ? <Loader2 size={17} className="animate-spin" /> : <LogOut size={17} />}
          </button>
        </div>
      </div>
    </aside>
  )
}

const CORE = new Set(['/today', '/shop', '/shop-orders', '/customers', '/account', '/settings', '/catalog'])

export function SalesmanShell() {
  const { me, signOut } = useAuth()
  const outlet = useOutlet()
  const newCount = useNewOrderCount()
  const [signingOut, setSigningOut] = useState(false)
  useTabbarVar()

  const name = me?.full_name || me?.email?.split('@')[0] || ''
  const first = firstName(name)
  const tabs: Tab[] = [
    { to: '/today', label: 'Today', icon: House, end: true },
    { to: '/shop', label: 'Catalog', icon: BookImage },
    { to: '/shop-orders', label: 'Orders', icon: ClipboardList, badge: newCount },
    { to: '/customers', label: 'Customers', icon: Users },
    { to: '/account', label: 'Me', icon: CircleUserRound },
  ]
  // everything else an admin granted this salesman (Finds, Leads, Field notes, Coach, Reports…)
  const more: Tab[] = navFor(me)
    .filter((n) => !CORE.has(n.to) && n.to !== '/')
    .map((n) => ({ to: n.to, label: n.label, icon: n.icon }))

  const onSignOut = async () => {
    setSigningOut(true)
    await signOut()
    setSigningOut(false)
  }

  return (
    <div data-shell="sales" style={SALES_TOKENS} className="min-h-screen bg-background text-foreground">
      <div className="flex">
        <Sidebar tabs={tabs} more={more} name={name} onSignOut={onSignOut} signingOut={signingOut} />
        <div className="min-w-0 flex-1">
          {/* top bar: phone + tablet (desktop has the sidebar) */}
          <header className="sticky top-0 z-30 border-b border-border bg-card/90 backdrop-blur lg:hidden">
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
              <button type="button" onClick={onSignOut} aria-label="Sign out" title="Sign out" className="-mr-1.5 hidden h-11 w-11 shrink-0 place-items-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground md:grid">
                {signingOut ? <Loader2 size={18} className="animate-spin" /> : <LogOut size={18} />}
              </button>
            </div>
          </header>

          <TempPasswordBanner />

          <main className="pb-[calc(var(--yq-tabbar,88px)+env(safe-area-inset-bottom))] md:pb-10">
            <Suspense
              fallback={
                <div className="grid h-[60vh] place-items-center text-muted-foreground">
                  <Loader2 className="animate-spin" size={22} />
                </div>
              }
            >
              {outlet}
            </Suspense>
          </main>
        </div>
      </div>
      <FloatingTabs tabs={tabs} />
    </div>
  )
}
