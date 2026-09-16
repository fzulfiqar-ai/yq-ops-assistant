import { type ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { ArrowLeft, Home, Package, Search, ShoppingBag } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { cn } from '@/lib/utils'
import { bhd, RING } from '@/pages/shop/shared'
import { useMarket } from '../MarketContext'
import { S } from '../strings'

/**
 * The marketplace's frame: a top bar that scrolls away, a fixed four-tab bottom nav on phones
 * (Home · Search · Cart · Orders) and the same four destinations as links on wider screens.
 * Everything pinned to the bottom clears the safe area (iPhone home indicator).
 */

export const SAFE = 'env(safe-area-inset-bottom, 0px)'
/** Bottom padding a page needs so its last row is never under the nav (+ cart bar when shown). */
export const NAV_H = '4.25rem'

export function TopBar({ back, title, right }: { back?: boolean; title?: ReactNode; right?: ReactNode }) {
  const navigate = useNavigate()
  const { cart } = useMarket()
  return (
    <header className="mx-auto flex max-w-6xl items-center gap-3 px-4 pb-2 pt-3 sm:pt-4">
      {back ? (
        <button
          type="button"
          onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/'))}
          aria-label="Back"
          className={cn('grid h-11 w-11 shrink-0 place-items-center rounded-xl text-[#1a1430] hover:bg-[#f4f2f9]', RING)}
        >
          <ArrowLeft size={20} />
        </button>
      ) : (
        <Link to="/" aria-label={S.brand} className={cn('shrink-0 rounded-[14px]', RING)}>
          <Logo className="h-10 w-10 rounded-[12px]" />
        </Link>
      )}
      <div className="min-w-0 flex-1">
        {title ? (
          <h1 className="truncate font-display text-[18px] font-bold leading-tight tracking-[-0.02em] text-[#1a1430]">
            {title}
          </h1>
        ) : (
          <>
            <div className="font-display text-[18px] font-bold leading-tight tracking-[-0.02em] text-[#1a1430]">
              {S.brand}
            </div>
            <div className="text-[12px] leading-snug text-[#6b6480]">{S.tagline}</div>
          </>
        )}
      </div>
      {right}
      {/* desktop links — the bottom nav is hidden from md up */}
      <nav aria-label="Main" className="hidden items-center gap-1 md:flex">
        {[
          { to: '/', label: S.nav.home, icon: Home },
          { to: '/search', label: S.nav.search, icon: Search },
          { to: '/orders', label: S.nav.orders, icon: Package },
        ].map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              cn(
                'inline-flex h-10 items-center gap-1.5 rounded-xl px-3 text-[13px] font-semibold transition',
                RING,
                isActive ? 'bg-[#f3eefc] text-[#6d28d9]' : 'text-[#4a4360] hover:bg-[#f4f2f9]',
              )
            }
          >
            <Icon size={16} aria-hidden="true" /> {label}
          </NavLink>
        ))}
        <Link
          to="/cart"
          className={cn(
            'ml-1 inline-flex h-10 items-center gap-2 rounded-xl bg-[#6d28d9] px-3.5 text-[13px] font-semibold text-white hover:bg-[#5b21b6]',
            RING,
          )}
        >
          <ShoppingBag size={16} aria-hidden="true" /> {S.nav.cart}
          {cart.items > 0 && (
            <span className="rounded-full bg-white/20 px-1.5 text-[12px] tabular-nums">{cart.items}</span>
          )}
        </Link>
      </nav>
    </header>
  )
}

export function BottomNav() {
  const { cart } = useMarket()
  const tabs = [
    { to: '/', label: S.nav.home, icon: Home, end: true },
    { to: '/search', label: S.nav.search, icon: Search },
    { to: '/cart', label: S.nav.cart, icon: ShoppingBag, badge: cart.items },
    { to: '/orders', label: S.nav.orders, icon: Package },
  ]
  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-[#ece9f3] bg-white/95 backdrop-blur-md md:hidden"
      style={{ paddingBottom: SAFE }}
    >
      <ul className="mx-auto grid max-w-6xl grid-cols-4">
        {tabs.map(({ to, label, icon: Icon, end, badge }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  'relative flex h-[4.25rem] flex-col items-center justify-center gap-0.5 text-[12px] font-semibold transition',
                  isActive ? 'text-[#6d28d9]' : 'text-[#6b6480]',
                  'focus-visible:outline-none focus-visible:bg-[#f4f2f9]',
                )
              }
            >
              <span className="relative">
                <Icon size={22} aria-hidden="true" />
                {badge ? (
                  <span
                    aria-label={`${badge} in cart`}
                    className="absolute -right-2.5 -top-1.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-[#6d28d9] px-1 text-[11px] font-bold tabular-nums text-white"
                  >
                    {badge > 99 ? '99+' : badge}
                  </span>
                ) : null}
              </span>
              {label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}

/** The running total in the thumb zone, shown on browsing screens while the cart has lines. */
export function CartBar() {
  const { cart, quote, itemsByCode } = useMarket()
  const navigate = useNavigate()
  if (!cart.lines.length) return null
  const estimate = cart.lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate
  return (
    <div
      className="fixed inset-x-0 z-30 border-t border-[#ece9f3] bg-white/95 px-4 pt-2.5 backdrop-blur-md md:bottom-0"
      style={{ bottom: `calc(${NAV_H} + ${SAFE})`, paddingBottom: '0.625rem' }}
    >
      <div className="mx-auto flex max-w-6xl items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] text-[#6b6480]">
            <span className="tabular-nums">{cart.items}</span> {cart.items === 1 ? 'product' : 'products'} ·{' '}
            <span className="tabular-nums">{cart.units}</span> pcs
          </div>
          <div className="font-display text-[17px] font-extrabold leading-tight tracking-[-0.015em] tabular-nums text-[#1a1430]">
            {bhd(total)}
          </div>
        </div>
        <button
          type="button"
          onClick={() => navigate('/cart')}
          className={cn(
            'flex h-12 shrink-0 items-center gap-2 rounded-xl bg-[#6d28d9] px-5 text-[14px] font-semibold text-white transition hover:bg-[#5b21b6] active:scale-[.99]',
            RING,
          )}
        >
          <ShoppingBag size={17} aria-hidden="true" /> {S.cart.title}
        </button>
      </div>
    </div>
  )
}

/** Page wrapper: reserves room for the bottom nav (+ the cart bar when a page shows one). */
export function Page({ children, withCartBar, className }: { children: ReactNode; withCartBar?: boolean; className?: string }) {
  const { cart } = useMarket()
  const extra = withCartBar && cart.lines.length ? ' + 6.25rem' : ''
  return (
    <div
      className={cn('min-h-screen bg-[#faf9fc] text-[#1a1430]', className)}
      style={{ paddingBottom: `calc(${NAV_H} + ${SAFE}${extra})` }}
    >
      {children}
    </div>
  )
}
