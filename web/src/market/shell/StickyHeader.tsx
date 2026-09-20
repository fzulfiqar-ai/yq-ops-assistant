import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { ChevronDown, CircleUserRound, Search, Tag, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import { SearchHints } from '../components/SearchField'
import { useMarket, useOrder } from '../MarketContext'
import { isDeal } from '../lib/facets'
import { bhd, categorySlug, niceCategory } from '../lib/format'
import { NAV_ICONS } from '../lib/icons'
import { useCartAddTick, useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'
import { MegaNav } from './MegaNav'
import { useShell } from './ShellContext'

/**
 * Tablet/desktop header. Sticky, glass, 72px. Logo + "Where Bahrain restocks." · Browse (mega-nav
 * on desktop, link on tablet) · a large search trigger with a rotating example that opens the
 * palette · Deals (only when real deals or last-chance lines are in stock) · Quick order · Orders ·
 * My YQ · Restock (line count, and a thin wholesale-progress line when a minimum is set; drawer
 * below 1280, page above). A category strip rides under it on Home and Browse.
 */
export function StickyHeader() {
  const { viewport, openPalette, openCart, paletteOpen } = useShell()
  const { categories, items, itemsByCode, rep } = useMarket()
  const { quote } = useOrder()
  const lines = useCartLines()
  const { items: count, units } = useCartCounts()
  const tick = useCartAddTick()
  const navigate = useNavigate()
  const { pathname, search } = useLocation()
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

  const estimate = lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate
  const hasDeals = useMemo(() => items.some((i) => i.stock_status !== 'out_of_stock' && isDeal(i)), [items])
  const dealsActive = pathname === '/shop' && (new URLSearchParams(search).get('f') || '').split(',').includes('deals')
  const showStrip = pathname === '/' || pathname === '/shop' || pathname.startsWith('/t/') || (pathname.split('/').filter(Boolean).length === 1 && !['/search', '/cart', '/checkout', '/orders', '/me', '/quick', '/about'].includes(pathname))

  // wholesale minimum from the priced quote: a 2px line under the Restock label
  const min = count > 0 && quote?.minimum && Number(quote.minimum.value_bhd) > 0 ? quote.minimum : null
  const remaining = min ? Math.max(0, Number(min.remaining_bhd) || 0) : 0
  const met = Boolean(min && (min.met || remaining <= 0))
  const pct = min ? Math.min(100, Math.max(0, ((Number(min.value_bhd) - remaining) / Number(min.value_bhd)) * 100)) : 0
  const cartLabel = count
    ? [`${S.nav.restock}: ${S.cart.summary(count, units)}, ${bhd(total)}`, min ? (met ? S.wholesale.ready : S.wholesale.away(bhd(remaining))) : ''].filter(Boolean).join(' · ')
    : S.nav.restock
  const RestockIcon = NAV_ICONS.restock

  const openMega = () => {
    window.clearTimeout(closeTimer.current)
    setMega(true)
  }
  const closeMegaSoon = () => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setMega(false), 160)
  }

  const linkBase = 'inline-flex h-10 items-center gap-1.5 rounded-sm px-3 text-sm font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'
  const linkCls = ({ isActive }: { isActive: boolean }) => cn(linkBase, isActive ? 'bg-plum-soft text-plum-ink' : 'text-ink-2 hover:bg-plum-wash hover:text-ink')

  return (
    <header className="sticky top-0 z-header">
      <div className="glass border-b border-line">
        <div className="container-m flex h-header items-center gap-3 lg:gap-4">
          <Link to="/" aria-label={`${S.brand} · ${S.kicker}`} className="flex shrink-0 items-center gap-2.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
            <img src="/yq-logo-160.webp" alt="" width={40} height={40} className="h-10 w-10 rounded-sm" />
            <span className="hidden lg:block">
              <span className="block font-display text-md font-bold leading-tight text-ink">{S.brand}</span>
              <span className="block text-2xs text-ink-2">{S.kicker}</span>
            </span>
          </Link>

          {/* Browse: mega-nav (desktop) / link (tablet) */}
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
                {S.nav.browse} <ChevronDown size={15} className={cn('transition-transform duration-2', mega && 'rotate-180')} aria-hidden="true" />
              </button>
              {mega && <MegaNav onClose={() => setMega(false)} onEnter={openMega} onLeave={closeMegaSoon} />}
            </div>
          ) : (
            <NavLink to="/shop" className={linkCls}>
              {S.nav.browse}
            </NavLink>
          )}

          {/* search → palette; the rotating example stands in for a placeholder */}
          <button
            type="button"
            onClick={() => openPalette()}
            aria-label={S.searchLabel}
            className="group flex h-11 min-w-0 flex-1 items-center gap-2.5 rounded-md border border-line bg-surface pe-2 ps-3.5 text-start text-md transition duration-1 ease-m hover:border-ink/25 hover:shadow-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 lg:max-w-[640px]"
          >
            <Search size={18} strokeWidth={2} aria-hidden="true" className="shrink-0 text-plum" />
            <SearchHints hints={S.search.hints} paused={paletteOpen} className="min-w-0 flex-1" leadClassName="text-ink-3" hintClassName="text-ink-2" />
            <kbd className="hidden shrink-0 rounded-xs border border-line bg-canvas px-1.5 py-0.5 font-sans text-2xs text-ink-3 lg:inline">{S.search.shortcut}</kbd>
          </button>

          <nav aria-label={S.nav.shortcuts} className="ms-auto hidden shrink-0 items-center gap-0.5 md:flex">
            {hasDeals && (
              <Link to="/shop?f=deals" aria-current={dealsActive ? 'page' : undefined} className={linkCls({ isActive: dealsActive })}>
                <span aria-hidden="true" className="grid h-5 w-5 place-items-center rounded-full bg-deal-soft text-deal-ink">
                  <Tag size={12} strokeWidth={2.2} />
                </span>
                {S.nav.deals}
              </Link>
            )}
            {desktop && (
              <NavLink to="/quick" className={linkCls}>
                <Zap size={15} aria-hidden="true" /> {S.nav.quick}
              </NavLink>
            )}
            <NavLink to="/orders" className={linkCls} aria-label={S.nav.orders}>
              <NAV_ICONS.orders size={15} aria-hidden="true" /> <span className="hidden xl:inline">{S.nav.orders}</span>
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
              'relative ms-1 inline-flex h-11 shrink-0 items-center gap-2 overflow-hidden rounded-sm px-3.5 text-sm font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas',
              count ? 'bg-ink text-white hover:bg-ink/90' : 'bg-plum text-white hover:bg-plum-deep',
            )}
            aria-label={cartLabel}
          >
            <RestockIcon size={18} aria-hidden="true" />
            <span>{S.nav.restock}</span>
            {count > 0 && (
              <span aria-hidden="true" className={cn('grid h-5 min-w-[20px] place-items-center rounded-full bg-white/15 px-1.5 text-xs font-bold tnum text-white', bump && 'anim-bump')}>
                {count > 99 ? '99+' : count}
              </span>
            )}
            {count > 0 && (
              <span aria-hidden="true" className="hidden border-s border-white/15 ps-2 font-display tnum 2xl:inline">
                {bhd(total)}
              </span>
            )}
            {min && (
              <span aria-hidden="true" className="absolute inset-x-0 bottom-0 h-[2px] bg-white/15">
                <span className={cn('block h-full transition-[width] duration-3 ease-m', met ? 'bg-fresh' : 'bg-tile-lilac')} style={{ width: `${pct}%` }} />
              </span>
            )}
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
