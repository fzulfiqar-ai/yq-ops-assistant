import { useLayoutEffect, useRef } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { PromiseBar } from '../components/PromiseBar'
import { Spotlight } from '../components/Spotlight'
import { MiniCart } from './MiniCart'
import { StickyHeader } from './StickyHeader'
import { useShell } from './ShellContext'
import { S } from '../strings'
import { Sheet } from '../ui/Sheet'

/**
 * Tablet + desktop chrome: a sticky glass header (logo · Shop mega-nav · search · New · Offers ·
 * Quick order · Orders · My YQ · Cart), the page in a fluid container, and from 1280px a
 * persistent mini-cart aside on the browsing pages. Below 1280 the header's Cart opens the same
 * mini-cart as a side drawer. Tablets keep a bottom stack for page bars + the cart dock.
 */
export default function DesktopShell() {
  const { viewport, cartOpen, closeCart } = useShell()
  const { pathname } = useLocation()
  const wide = viewport === 'wide'
  const browsing = !/^\/(cart|checkout|o\/|me|quick)/.test(pathname)
  const showAside = wide && browsing
  const stackRef = useRef<HTMLDivElement>(null)
  const { setBarHost } = useShell()

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
      <PromiseBar />
      <StickyHeader />
      <div className="container-m flex items-start gap-6 2xl:gap-8">
        <main id="main" className="min-w-0 flex-1 pb-[calc(var(--m-bottom-stack)+40px)]">
          <Outlet />
        </main>
        {showAside && (
          <aside className="sticky top-[calc(var(--m-header-h)+16px)] hidden max-h-[calc(100dvh-var(--m-header-h)-32px)] w-[320px] shrink-0 flex-col gap-4 overflow-y-auto overscroll-contain pb-2 2xl:w-[360px] xl:flex" aria-label={S.cart.mini}>
            <MiniCart />
            <Spotlight />
          </aside>
        )}
      </div>
      {/* tablet + desktop < 1280: the cart as a side drawer */}
      {!showAside && cartOpen && (
        <Sheet open onClose={closeCart} title={S.cart.mini} variant="drawer" bare>
          <MiniCart inDrawer />
        </Sheet>
      )}
      {/* page bars (tablet) */}
      <div ref={stackRef} className="fixed inset-x-0 bottom-0 z-nav flex flex-col justify-end lg:hidden" style={{ paddingBottom: 'var(--m-safe-b)' }}>
        <div ref={setBarHost} className="empty:hidden" />
      </div>
    </div>
  )
}
