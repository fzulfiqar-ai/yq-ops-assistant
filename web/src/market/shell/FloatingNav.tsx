import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { NAV_ICONS } from '../lib/icons'
import { useCartAddTick, useCartCounts } from '../store/cart'
import { S } from '../strings'

/**
 * The phone's persistent navigation: a floating glass pill with the five wholesale destinations —
 * Home · Browse · Restock · Orders · My YQ. The active cell carries a soft plum pill that slides
 * between tabs; Restock wears a live line count that bumps once per add. Safe-area aware. The shell
 * hides the whole bottom stack on scroll-down. Search lives in the header band, not here.
 */

const TABS = [
  { to: '/', label: S.nav.home, icon: NAV_ICONS.home, end: true },
  { to: '/shop', label: S.nav.browse, icon: NAV_ICONS.browse },
  { to: '/cart', label: S.nav.restock, icon: NAV_ICONS.restock, cart: true },
  { to: '/orders', label: S.nav.orders, icon: NAV_ICONS.orders },
  { to: '/me', label: S.nav.me, icon: NAV_ICONS.me },
] as const

/**
 * Which tab a path belongs to, by its FIRST segment exactly — so a storefront slug that merely
 * starts like a route ("/shopfront", "/mearaj") is a storefront (Home), never a false match.
 * /, /p/{code} and /{slug} → Home.
 */
function activeIndex(pathname: string): number {
  const first = pathname.split('/').filter(Boolean)[0] || ''
  switch (first) {
    case 'shop':
    case 't':
    case 'search':
      return 1
    case 'cart':
    case 'checkout':
    case 'quick':
      return 2
    case 'orders':
    case 'o':
      return 3
    case 'me':
    case 'about':
      return 4
    default:
      return 0
  }
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
    <nav aria-label={S.nav.main} className="mx-3 mb-3">
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
                      aria-hidden="true"
                      className={cn('absolute -end-2.5 -top-1.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-plum px-1 text-[10.5px] font-bold tnum text-white ring-2 ring-surface', bump && 'anim-bump')}
                    >
                      {items > 99 ? '99+' : items}
                    </span>
                  )}
                </span>
                <span>{label}</span>
                {showBadge && <span className="sr-only">{`, ${S.nav.inRestock(items)}`}</span>}
              </NavLink>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
