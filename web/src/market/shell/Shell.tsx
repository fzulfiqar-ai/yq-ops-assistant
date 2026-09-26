import { lazy, Suspense, useEffect, useLayoutEffect, useRef } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { splashDue } from '../lib/splashGate'
import { useMarket } from '../MarketContext'
import { CartDock } from './CartDock'
import { FloatingNav } from './FloatingNav'
import { PhoneHeader } from './PhoneHeader'
import { ShellProvider, useShell } from './ShellContext'
import { useScrollDirection } from './useScrollDirection'
import { isPhoneLike } from './useViewport'

const DesktopShell = lazy(() => import('./DesktopShell'))
const ProductPanel = lazy(() => import('../components/ProductPanel'))
const loadSplash = () => import('../components/Splash')
// A load that will play the opening (at most once a week per device and once per tab session —
// lib/splashGate.ts, the same predicate as Splash.tsx): start fetching it with the app itself (not at
// the shell's first render), so the overlay lands as close to the first paint as a lazy chunk can.
// NEEDS_SPLASH also colours the Suspense gap: without it the shell paints the finished header and the
// home skeleton first and the night field drops on top a round trip later (seconds on a cold 4G
// cache), which reads as a glitch — app, dark overlay, app — instead of an entrance.
const NEEDS_SPLASH = splashDue()
if (NEEDS_SPLASH) void loadSplash()
const Splash = lazy(() => loadSplash().then((mod) => ({ default: mod.Splash })))
const SearchPalette = lazy(() => import('../components/SearchPalette'))

/**
 * The layout route. Picks the phone/tablet shell (this file) or the desktop shell (lazy — phones
 * never download it), mounts the product panel and the ⌘K palette over whichever page is showing,
 * and owns the bottom stack: [page bar] [cart dock] [floating nav], measured into --m-bottom-stack.
 */
export function Shell() {
  return (
    <ShellProvider>
      <ShellBody />
    </ShellProvider>
  )
}

/** ⌘K / Ctrl+K anywhere, "/" outside inputs → the search palette (desktop + tablet). */
function Hotkeys() {
  const { openPalette, paletteOpen, viewport } = useShell()
  useEffect(() => {
    if (viewport === 'phone') return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        if (!paletteOpen) openPalette()
      } else if (e.key === '/' && !typing && !paletteOpen && document.body.dataset.sheetOpen !== '1') {
        e.preventDefault()
        openPalette()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openPalette, paletteOpen, viewport])
  return null
}

function ShellBody() {
  const { viewport, panelCode, paletteOpen } = useShell()
  const phoneLike = isPhoneLike(viewport)
  return (
    <>
      <Hotkeys />
      {phoneLike ? (
        <MobileShell />
      ) : (
        <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
          <DesktopShell />
        </Suspense>
      )}
      <Suspense fallback={null}>
        {panelCode && <ProductPanel code={panelCode} />}
        {paletteOpen && <SearchPalette />}
      </Suspense>
      {/* Its own boundary, and a night field for the gap: the first frame of a session's first load is
       * the brand, with the horizon and the logo animating in on top of it. A product panel or the
       * palette loading later must never paint it, hence the split. */}
      <Suspense fallback={NEEDS_SPLASH ? <div className="canvas-night fixed inset-0 z-[90]" aria-hidden="true" /> : null}>
        <Splash />
      </Suspense>
    </>
  )
}

function MobileShell() {
  const { viewport, setBarHost, hasPageBar, hideNav } = useShell()
  const { status } = useMarket()
  const dir = useScrollDirection()
  const { pathname } = useLocation()
  const stackRef = useRef<HTMLDivElement>(null)
  const phone = viewport === 'phone'
  const showNav = phone && !hideNav

  // hide the nav (+ dock) on scroll-down; page bars always stay. A page change scrolls to the
  // top (EntryParams), which reports 'top' and brings the nav back. While a sheet is open the
  // body cannot scroll, so the direction cannot change.
  const hidden = dir === 'down' && !hasPageBar && pathname !== '/checkout'

  // measure the bottom stack so pages (and toasts) can clear it
  useLayoutEffect(() => {
    const el = stackRef.current
    if (!el) return
    const apply = () => document.documentElement.style.setProperty('--m-bottom-stack', `${el.offsetHeight}px`)
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => {
      ro.disconnect()
      document.documentElement.style.setProperty('--m-bottom-stack', '0px')
    }
  }, [])

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <PhoneHeader />
      <main id="main" className="pb-[calc(var(--m-bottom-stack)+var(--m-safe-b)+16px)]">
        <Outlet />
      </main>
      <div
        ref={stackRef}
        className={cn('fixed inset-x-0 bottom-0 z-nav flex flex-col justify-end transition-transform duration-3 ease-m', hidden && 'pointer-events-none')}
        style={{
          paddingBottom: 'var(--m-safe-b)',
          transform: hidden ? 'translateY(calc(100% + var(--m-safe-b)))' : undefined,
        }}
      >
        {/* page bars portal here (cart total / place order) */}
        <div ref={setBarHost} className="empty:hidden" />
        {status !== 'closed' && <CartDock />}
        {showNav && <FloatingNav />}
      </div>
    </div>
  )
}
