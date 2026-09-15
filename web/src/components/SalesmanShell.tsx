import { Suspense, useEffect, useState } from 'react'
import { Link, NavLink, useLocation, useOutlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { BookImage, ClipboardList, Loader2, LogOut, type LucideIcon } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { Logo } from './Logo'

/**
 * The shell a salesman lives in — a phone in one hand, a shop owner in front of them.
 *
 * Deliberately NOT the admin sidebar: two destinations, thumb-height targets, and
 * nothing else competing for attention. The office shell (AppShell) is untouched.
 */

const TABBAR_PX = 64

interface Tab {
  to: string
  label: string
  icon: LucideIcon
  badge?: number
}

/** Count of orders still waiting to be looked at — the only number worth interrupting for. */
function useNewOrderCount(): number {
  const { data } = useQuery({
    queryKey: ['shop-orders-new-count'],
    queryFn: () => apiGet<{ count: number }>('/shop/orders?status=new&limit=1'),
    refetchInterval: 60_000,
    retry: 1,
  })
  return Number(data?.count || 0)
}

/**
 * Publish the tab bar's height so pages can keep their own sticky furniture (a cart
 * bar, a save row) clear of it. Zero on desktop, where the tabs move to the top.
 */
function useTabbarVar() {
  useEffect(() => {
    const root = document.documentElement
    const mq = window.matchMedia('(min-width: 768px)')
    const apply = () => root.style.setProperty('--yq-tabbar', mq.matches ? '0px' : `${TABBAR_PX}px`)
    apply()
    mq.addEventListener('change', apply)
    return () => {
      mq.removeEventListener('change', apply)
      root.style.removeProperty('--yq-tabbar')
    }
  }, [])
}

function CountBadge({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <span
      aria-label={`${count} new`}
      className="absolute -right-3 -top-1 min-w-[1.125rem] rounded-full bg-[#6d28d9] px-1 text-center text-[10px] font-bold leading-[1.125rem] text-white"
    >
      {count > 99 ? '99+' : count}
    </span>
  )
}

/**
 * The 15 field logins were handed out with a shared temporary password. Until each
 * salesman picks their own, say so plainly — quietly, once, and with the fix one tap away.
 */
function TempPasswordBanner() {
  const { session } = useAuth()
  const loc = useLocation()
  const mustReset = Boolean(session?.user?.user_metadata?.must_reset)
  if (!mustReset || loc.pathname === '/settings') return null
  return (
    <div className="border-b border-[#f3e3c6] bg-[#fdf3e3]">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-2">
        <p className="min-w-0 flex-1 text-[12.5px] font-medium leading-snug text-[#96600d]">
          You are using a temporary password — set your own.
        </p>
        <Link
          to="/settings"
          className="grid h-11 shrink-0 place-items-center rounded-xl px-3 text-[12.5px] font-bold text-[#96600d] underline underline-offset-2 transition-colors duration-150 hover:bg-[#f7e7cb] motion-reduce:transition-none"
        >
          Change password
        </Link>
      </div>
    </div>
  )
}

function BottomTabs({ tabs }: { tabs: Tab[] }) {
  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-[#ece9f3] bg-white pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <ul className="mx-auto flex max-w-lg" style={{ height: TABBAR_PX }}>
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <li key={t.to} className="flex-1">
              <NavLink
                to={t.to}
                className={({ isActive }) =>
                  cn(
                    'flex h-full flex-col items-center justify-center gap-1 text-[11px] font-semibold transition-colors duration-150',
                    isActive ? 'text-[#6d28d9]' : 'text-[#6b6480]',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <span className="relative">
                      <Icon size={22} strokeWidth={isActive ? 2.3 : 1.8} aria-hidden="true" />
                      <CountBadge count={t.badge ?? 0} />
                    </span>
                    {t.label}
                  </>
                )}
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
    <nav aria-label="Main" className="hidden md:block">
      <ul className="flex items-center gap-1 rounded-full border border-[#ece9f3] bg-[#faf9fc] p-1">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <li key={t.to}>
              <NavLink
                to={t.to}
                className={({ isActive }) =>
                  cn(
                    'flex h-9 items-center gap-2 rounded-full px-3.5 text-[13px] font-semibold transition-colors duration-150',
                    isActive ? 'bg-white text-[#6d28d9] shadow-[0_1px_2px_rgba(24,16,48,.06)]' : 'text-[#6b6480] hover:text-[#1a1430]',
                  )
                }
              >
                <Icon size={16} aria-hidden="true" />
                <span className="relative pr-1">
                  {t.label}
                  <CountBadge count={t.badge ?? 0} />
                </span>
              </NavLink>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

export function SalesmanShell() {
  const { me, signOut } = useAuth()
  const outlet = useOutlet()
  const newCount = useNewOrderCount()
  const [signingOut, setSigningOut] = useState(false)
  useTabbarVar()

  const firstName = (me?.full_name || me?.email?.split('@')[0] || '').split(' ')[0]
  const tabs: Tab[] = [
    { to: '/shop', label: 'Catalog', icon: BookImage },
    { to: '/shop-orders', label: 'Orders', icon: ClipboardList, badge: newCount },
  ]

  return (
    <div className="min-h-screen bg-[#faf9fc] text-[#1a1430]">
      <header className="sticky top-0 z-30 border-b border-[#ece9f3] bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
          <Logo className="h-8 w-8 shrink-0 rounded-lg" />
          <div className="min-w-0 flex-1 leading-tight">
            <div className="font-display text-[14px] font-bold">YQ Bahrain</div>
            {firstName && <div className="truncate text-[11.5px] text-[#6b6480]">{firstName}</div>}
          </div>
          <TopTabs tabs={tabs} />
          <button
            type="button"
            onClick={async () => {
              setSigningOut(true)
              await signOut()
              setSigningOut(false)
            }}
            aria-label="Sign out"
            title="Sign out"
            className="-mr-1.5 grid h-11 w-11 shrink-0 place-items-center rounded-xl text-[#6b6480] transition-colors duration-150 hover:bg-[#f4f2f9] hover:text-[#1a1430] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
          >
            {signingOut ? <Loader2 size={18} className="animate-spin" /> : <LogOut size={18} />}
          </button>
        </div>
      </header>

      <TempPasswordBanner />

      {/* Pages own their own gutters and max-width — the shell only guarantees the
          content clears the tab bar. */}
      <main className="pb-[calc(var(--yq-tabbar,64px)+env(safe-area-inset-bottom))] md:pb-10">
        <Suspense
          fallback={
            <div className="grid h-[60vh] place-items-center text-[#6b6480]">
              <Loader2 className="animate-spin" size={22} />
            </div>
          }
        >
          {outlet}
        </Suspense>
      </main>

      <BottomTabs tabs={tabs} />
    </div>
  )
}
