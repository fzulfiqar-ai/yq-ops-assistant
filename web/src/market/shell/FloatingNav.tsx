import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { CircleUserRound, House, LayoutGrid, Search, ShoppingBag } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCartAddTick, useCartCounts } from '../store/cart'
import { S } from '../strings'

/**
 * The phone's persistent navigation: a floating glass pill with five destinations. The active
 * cell carries a soft plum pill that slides between tabs; the Cart cell wears a live count that
 * bumps once per add. Safe-area aware. The shell hides the whole bottom stack on scroll-down.
 */

const TABS = [
  { to: '/', label: S.nav.home, icon: House, end: true },
  { to: '/shop', label: S.nav.shop, icon: LayoutGrid },
  { to: '/search', label: S.nav.search, icon: Search },
  { to: '/cart', label: S.nav.cart, icon: ShoppingBag, cart: true },
  { to: '/me', label: S.nav.me, icon: CircleUserRound },
] as const

function activeIndex(pathname: string): number {
  if (pathname === '/' || /^\/(p|t)\//.test(pathname) || pathname.split('/').filter(Boolean).length === 1 && !TABS.some((t) => t.to === pathname) && !['/orders', '/quick', '/checkout'].includes(pathname)) {
    if (pathname.startsWith('/t/')) return 1
    return 0
  }
  if (pathname.startsWith('/shop')) return 1
  if (pathname.startsWith('/search')) return 2
  if (pathname.startsWith('/cart') || pathname.startsWith('/checkout')) return 3
  if (pathname.startsWith('/me') || pathname.startsWith('/orders') || pathname.startsWith('/o/')) return 4
  if (pathname.startsWith('/quick')) return 2
  return 0
}

export function FloatingNav() {
  const { pathname } = useLocation()
  const { items } = useCartCounts()
  const tick = useCartAddTick()
  const [bump, setBump] = useState(false)
  const [tickSeen, setTickSeen] = useState(tick)
  if (tickSeen !== tick) {
    setTickSeen(tick)
    setBump(true)
  }
  useEffect(() => {
    if (!bump) return
    const id = window.setTimeout(() => setBump(false), 340)
    return () => window.clearTimeout(id)
  }, [bump])
  const active = activeIndex(pathname)

  return (
    <nav aria-label="Main" className="mx-3 mb-3">
      <ul className="glass relative grid h-nav grid-cols-5 items-stretch rounded-xl shadow-nav" style={{ ['--i' as string]: active }}>
        {/* the sliding active pill */}
        <li
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-1.5 start-1.5 w-[calc((100%-0.75rem)/5)] rounded-lg bg-plum-soft transition-transform duration-3 ease-spring"
          style={{ transform: 'translateX(calc(var(--i) * 100%))' }}
        />
        {TABS.map(({ to, label, icon: Icon, ...rest }, i) => {
          const isActive = i === active
          const showBadge = 'cart' in rest && items > 0
          return (
            <li key={to} className="relative">
              <NavLink
                to={to}
                end={'end' in rest}
                aria-current={isActive ? 'page' : undefined}
                className={cn(
                  'relative flex h-full flex-col items-center justify-center gap-0.5 rounded-lg text-2xs font-semibold transition-colors duration-2 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70',
                  isActive ? 'text-plum-ink' : 'text-ink-2',
                )}
              >
                <span className={cn('relative transition-transform duration-2 ease-spring', isActive && 'scale-[1.08]')}>
                  <Icon size={22} strokeWidth={isActive ? 2.1 : 1.75} aria-hidden="true" />
                  {showBadge && (
                    <span
                      aria-label={`${items} in cart`}
                      className={cn('absolute -end-2.5 -top-1.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-plum px-1 text-[10.5px] font-bold tnum text-white ring-2 ring-surface', bump && 'anim-bump')}
                    >
                      {items > 99 ? '99+' : items}
                    </span>
                  )}
                </span>
                <span>{label}</span>
              </NavLink>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
