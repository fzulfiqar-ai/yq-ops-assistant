import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { ChevronDown, CircleUserRound, Package, Percent, Search, ShoppingBag, Sparkles, Tag, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket, useOrder } from '../MarketContext'
import { bhd, categorySlug, hasBadge, niceCategory } from '../lib/format'
import { useCartAddTick, useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'
import { MegaNav } from './MegaNav'
import { useShell } from './ShellContext'

/**
 * Tablet/desktop header. Sticky, glass, 72px. Logo · Shop (mega-nav on desktop, link on tablet)
 * · a large search field that opens the palette · New · Offers · Quick order · Orders · My YQ ·
 * Cart (count + running total; drawer below 1280, page above). A category strip rides under it
 * on Home and Shop.
 */
export function StickyHeader() {
  const { viewport, openPalette, openCart } = useShell()
  const { categories, items, rep } = useMarket()
  const { quote } = useOrder()
  const lines = useCartLines()
  const { items: count, units } = useCartCounts()
  const tick = useCartAddTick()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [mega, setMega] = useState(false)
  const [bump, setBump] = useState(false)
  const [tickSeen, setTickSeen] = useState(tick)
  const [pathSeen, setPathSeen] = useState(pathname)
  const closeTimer = useRef<number | undefined>(undefined)
  const desktop = viewport === 'desktop' || viewport === 'wide'
  const wide = viewport === 'wide'

  // a new add → pulse the badge once; a new page → close the mega-nav (both derived in render)
  if (tickSeen !== tick) {
    setTickSeen(tick)
    setBump(true)
  }
  if (pathSeen !== pathname) {
    setPathSeen(pathname)
    setMega(false)
  }
  useEffect(() => {
    if (!bump) return
    const id = window.setTimeout(() => setBump(false), 340)
    return () => window.clearTimeout(id)
  }, [bump])

  const { itemsByCode } = useMarket()
  const estimate = lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate
  const hasNew = items.some((i) => hasBadge(i, 'new'))
  const hasOffers = items.some((i) => hasBadge(i, 'on_offer') || i.compare_at_bhd != null)
  const hasClearance = items.some((i) => hasBadge(i, 'clearance'))
  const showStrip = pathname === '/' || pathname === '/shop' || pathname.startsWith('/t/') || (pathname.split('/').filter(Boolean).length === 1 && !['/search', '/cart', '/checkout', '/orders', '/me', '/quick'].includes(pathname))

  const openMega = () => {
    window.clearTimeout(closeTimer.current)
    setMega(true)
  }
  const closeMegaSoon = () => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setMega(false), 160)
  }

  const linkCls = ({ isActive }: { isActive: boolean }) =>
    cn(
      'inline-flex h-10 items-center gap-1.5 rounded-sm px-3 text-sm font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
      isActive ? 'bg-plum-soft text-plum-ink' : 'text-ink-2 hover:bg-plum-wash hover:text-ink',
    )

  return (
    <header className="sticky top-0 z-header">
      <div className="glass border-b border-line">
        <div className="container-m flex h-header items-center gap-3 lg:gap-4">
          <Link to="/" aria-label={`${S.brand} · ${S.tagline}`} className="flex shrink-0 items-center gap-2.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
            <img src="/yq-logo-160.webp" alt="" width={40} height={40} className="h-10 w-10 rounded-sm" />
            <span className="hidden lg:block">
              <span className="block font-display text-md font-bold leading-tight text-ink">{S.brand}</span>
              <span className="block text-2xs text-ink-2">{S.tagline}</span>
            </span>
          </Link>

          {/* Shop: mega-nav (desktop) / link (tablet) */}
          {desktop ? (
            <div className="relative shrink-0" onMouseEnter={openMega} onMouseLeave={closeMegaSoon}>
              <button
                type="button"
                aria-haspopup="true"
                aria-expanded={mega}
                onClick={() => setMega((v) => !v)}
                className={cn(
                  'inline-flex h-11 items-center gap-1.5 rounded-sm px-3.5 text-sm font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
                  mega || pathname.startsWith('/shop') || pathname.startsWith('/t/') ? 'bg-plum-soft text-plum-ink' : 'text-ink hover:bg-plum-wash',
                )}
              >
                {S.nav.shop} <ChevronDown size={15} className={cn('transition-transform duration-2', mega && 'rotate-180')} aria-hidden="true" />
              </button>
              {mega && <MegaNav onClose={() => setMega(false)} onEnter={openMega} onLeave={closeMegaSoon} />}
            </div>
          ) : (
            <NavLink to="/shop" className={linkCls}>
              {S.nav.shop}
            </NavLink>
          )}

          {/* search → palette */}
          <button
            type="button"
            onClick={() => openPalette()}
            aria-label={S.searchLabel}
            className="flex h-11 min-w-0 flex-1 items-center gap-2.5 rounded-md border border-line bg-surface ps-3.5 pe-2 text-start text-md text-ink-3 transition duration-1 ease-m hover:border-ink/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 lg:max-w-[640px]"
          >
            <Search size={18} aria-hidden="true" className="shrink-0" />
            <span className="min-w-0 flex-1 truncate">{S.searchPlaceholder}</span>
            <kbd className="hidden shrink-0 rounded-xs border border-line bg-canvas px-1.5 py-0.5 font-sans text-2xs text-ink-3 lg:inline">{S.search.shortcut}</kbd>
          </button>

          <nav aria-label="Secondary" className="ms-auto hidden shrink-0 items-center gap-0.5 md:flex">
            {hasNew && (
              <NavLink to="/shop?f=new" className={linkCls}>
                <Sparkles size={15} aria-hidden="true" /> {S.nav.new}
              </NavLink>
            )}
            {hasOffers && (
              <NavLink to="/shop?f=offers" className={(a) => cn(linkCls(a), 'hidden 2xl:inline-flex')}>
                <Tag size={15} aria-hidden="true" /> {S.nav.offers}
              </NavLink>
            )}
            {hasClearance && (
              <NavLink to="/shop?f=clearance" className={linkCls}>
                <Percent size={15} aria-hidden="true" /> {S.nav.clearance}
              </NavLink>
            )}
            {desktop && (
              <NavLink to="/quick" className={linkCls}>
                <Zap size={15} aria-hidden="true" /> {S.nav.quick}
              </NavLink>
            )}
            <NavLink to="/orders" className={linkCls}>
              <Package size={15} aria-hidden="true" /> <span className="hidden xl:inline">{S.nav.orders}</span>
            </NavLink>
            <NavLink to="/me" className={linkCls} aria-label={S.nav.me}>
              {rep?.photo_url ? <img src={rep.photo_url} alt="" width={22} height={22} className="h-[22px] w-[22px] rounded-full object-cover" /> : <CircleUserRound size={17} aria-hidden="true" />}
              <span className="hidden xl:inline">{S.nav.me}</span>
            </NavLink>
          </nav>

          <button
            type="button"
            onClick={() => (wide ? navigate('/cart') : pathname === '/cart' ? undefined : openCart())}
            className={cn(
              'ms-1 inline-flex h-11 shrink-0 items-center gap-2 rounded-sm px-3.5 text-sm font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
              count ? 'bg-ink text-white hover:bg-ink/90' : 'bg-plum text-white hover:bg-plum-deep',
            )}
            aria-label={count ? `${S.nav.cart}: ${S.cart.summary(count, units)}, ${bhd(total)}` : S.nav.cart}
          >
            <span className="relative">
              <ShoppingBag size={18} aria-hidden="true" />
              {count > 0 && (
                <span className={cn('absolute -end-2.5 -top-2 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-plum px-1 text-[10.5px] font-bold tnum text-white ring-2 ring-ink', bump && 'anim-bump')}>
                  {count > 99 ? '99+' : count}
                </span>
              )}
            </span>
            <span className={cn(count && 'ms-1')}>{count ? <span className="tnum">{bhd(total)}</span> : S.nav.cart}</span>
          </button>
        </div>
      </div>

      {showStrip && categories.length > 0 && (
        <div className="glass border-b border-line">
          <div className="container-m no-scrollbar flex h-11 items-center gap-1.5 overflow-x-auto">
            {categories.map((c) => {
              const to = `/t/${categorySlug(c)}`
              const active = pathname === to
              return (
                <Link
                  key={c}
                  to={to}
                  className={cn(
                    'shrink-0 rounded-full px-3.5 py-1.5 text-sm font-medium transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
                    active ? 'bg-ink text-white' : 'text-ink-2 hover:bg-plum-wash hover:text-ink',
                  )}
                >
                  {niceCategory(c)}
                </Link>
              )
            })}
          </div>
        </div>
      )}
    </header>
  )
}
