import { Suspense, useEffect, useLayoutEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { lazyPage } from '@/lib/lazyPage'
import { cn } from '@/lib/utils'
import { splashDue } from '../lib/splashGate'
import { useMarket } from '../MarketContext'
import { ErrorBoundary } from '../ui/ErrorBoundary'
import { DesktopShellSkeleton } from '../ui/Skeleton'
import { CartDock } from './CartDock'
import { FloatingNav } from './FloatingNav'
import { PhoneHeader } from './PhoneHeader'
import { RouteOutlet } from './RouteOutlet'
import { ShellProvider, useShell } from './ShellContext'
import { useScrollDirection } from './useScrollDirection'
import { isPhoneLike } from './useViewport'

// lib/lazyPage: once its code is here each renders at once (no Suspense gap, no held fallback), and a
// failed fetch is asked again on the next open instead of staying broken for the tab's life
const DesktopShell = lazyPage(() => import('./DesktopShell'))
// a laptop needs the desktop chrome on its first paint: fetch it with the app, not at first render
if (typeof window !== 'undefined' && window.matchMedia?.('(min-width: 1024px)').matches) void DesktopShell.preload()
const ProductPanel = lazyPage(() => import('../components/ProductPanel'))
// A load that will play the opening (at most once a week per device and once per tab session —
// lib/splashGate.ts, the same predicate as Splash.tsx): start fetching it with the app itself (not at
// the shell's first render), so the overlay lands as close to the first paint as a lazy chunk can.
// NEEDS_SPLASH also colours the Suspense gap: without it the shell paints the finished header and the
// home skeleton first and the night field drops on top a round trip later (seconds on a cold 4G
// cache), which reads as a glitch — app, dark overlay, app — instead of an entrance.
const NEEDS_SPLASH = splashDue()
const Splash = lazyPage<object>(() => import('../components/Splash').then((mod) => ({ default: mod.Splash })))
if (NEEDS_SPLASH) void Splash.preload()
const SearchPalette = lazyPage(() => import('../components/SearchPalette'))
/** a broken opening must not leave its night frame up: the app is under it */
const liftBootFrame = () => document.getElementById('yq-boot')?.remove()

/**
 * The first tap on a card used to wait a round trip for the product panel's code (and ⌘K for the
 * palette's). Fetch both once the first page has painted and the phone is idle; a pointer going
 * down anywhere fetches the panel at once if that has not happened yet.
 */
function useWarmOverlays(phoneLike: boolean) {
  useEffect(() => {
    let done = false
    const warm = () => {
      if (done) return
      done = true
      void ProductPanel.preload()
      if (!phoneLike) void SearchPalette.preload()
    }
    const idle = typeof window.requestIdleCallback === 'function' ? window.requestIdleCallback(warm, { timeout: 4000 }) : window.setTimeout(warm, 2500)
    window.addEventListener('pointerdown', warm, { once: true, passive: true })
    return () => {
      if (typeof window.cancelIdleCallback === 'function') window.cancelIdleCallback(idle)
      else window.clearTimeout(idle)
      window.removeEventListener('pointerdown', warm)
    }
  }, [phoneLike])
}

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
  const { viewport, panelCode, paletteOpen, closeProduct, closePalette } = useShell()
  const phoneLike = isPhoneLike(viewport)
  useWarmOverlays(phoneLike)
  return (
    <>
      <Hotkeys />
      {phoneLike ? (
        <MobileShell />
      ) : (
        <Suspense fallback={<DesktopShellSkeleton />}>
          <DesktopShell />
        </Suspense>
      )}
      {/* each overlay has its own boundary, keyed to what it shows: a product whose panel cannot load
       * (a dropped signal before the warm-up, odd data) gets a small card with Reload and Close, the
       * shop stays under it, and the next product starts clean */}
      <ErrorBoundary key={`panel:${panelCode || ''}`} overlay onDismiss={closeProduct}>
        <Suspense fallback={null}>{panelCode && <ProductPanel code={panelCode} />}</Suspense>
      </ErrorBoundary>
      <ErrorBoundary key={`palette:${paletteOpen ? 1 : 0}`} overlay onDismiss={closePalette}>
        <Suspense fallback={null}>{paletteOpen && <SearchPalette />}</Suspense>
      </ErrorBoundary>
      {/* Its own boundary, and a night field for the gap: the first frame of a session's first load is
       * the brand, with the horizon and the logo animating in on top of it. A product panel or the
       * palette loading later must never paint it, hence the split. Mounted only when the opening is
       * due (at most weekly): any other load no longer downloads its code just to render nothing.
       * An opening that breaks is skipped, and its night frame lifted. */}
      {NEEDS_SPLASH && (
        <ErrorBoundary silent onError={liftBootFrame}>
          <Suspense fallback={<div className="canvas-night fixed inset-0 z-[90]" aria-hidden="true" />}>
            <Splash />
          </Suspense>
        </ErrorBoundary>
      )}
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
        <RouteOutlet />
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
