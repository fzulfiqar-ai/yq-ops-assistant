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
 * Tablet + desktop chrome: the dark promise bar, a sticky glass header (logo + kicker · Browse
 * mega-nav · search · Deals · Quick order · Orders · My YQ · Restock), the page in a fluid container, and from 1280px a
 * persistent mini-cart aside on the browsing pages. Below 1280 the header's Cart opens the same
 * mini-cart as a side drawer. Tablets keep a bottom stack for page bars + the cart dock.
 *
 * The aside pins under the measured sticky header (`--m-sticky-h`, written by StickyHeader —
 * the category strip makes it taller than the header row on browsing routes), and the page's
 * closing tail (`<PageTail>`: the CTA band and the footer) is rendered after the column row, at
 * full container width, so the aside's run ends above it.
 */
export default function DesktopShell() {
  const { viewport, cartOpen, closeCart } = useShell()
  const { pathname } = useLocation()
  const wide = viewport === 'wide'
  const browsing = !/^\/(cart|checkout|o\/|me|quick)/.test(pathname)
  const showAside = wide && browsing
  const stackRef = useRef<HTMLDivElement>(null)
  const mainRef = useRef<HTMLElement>(null)
  const asideRef = useRef<HTMLElement>(null)
  const { setBarHost, setTailHost } = useShell()

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

  /* The aside's run-out. A sticky box is clamped by its own MARGIN box, so its bottom margin decides
     where the pinning ends: with none, the aside stayed pinned to the very bottom of the column row
     and the sticky header sliced it in half on the last screen (a heading cut through its letterforms
     at 1920, a naked 25px white edge at 1440). One viewport of margin lets it finish sliding out of
     view while the column is still scrolling — but a fixed viewport of margin would also stretch the
     row on a short page (a 2-item category, an empty Orders list) and open ~800px of dead cream above
     the footer. So the margin is measured: as much run-out as the column can spare and not one pixel
     more (`main − aside`), which is zero on a page that never scrolls the aside anyway. */
  useLayoutEffect(() => {
    const main = mainRef.current
    const aside = asideRef.current
    if (!showAside || !main || !aside) return
    const px = (name: string) => parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)) || 0
    const apply = () => {
      const runOut = Math.max(0, window.innerHeight - (px('--m-sticky-h') || px('--m-header-h')))
      const room = Math.max(0, main.offsetHeight - aside.offsetHeight)
      aside.style.marginBlockEnd = `${Math.round(Math.min(runOut, room))}px`
    }
    apply()
    // margin never changes either observed box, so this cannot feed back into the observer
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(apply)
    ro?.observe(main)
    ro?.observe(aside)
    window.addEventListener('resize', apply)
    return () => {
      ro?.disconnect()
      window.removeEventListener('resize', apply)
      aside.style.marginBlockEnd = ''
    }
  }, [showAside])

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <PromiseBar />
      <StickyHeader />
      <div className="container-m flex items-start gap-6 2xl:gap-8">
        <main ref={mainRef} id="main" className="min-w-0 flex-1 pb-[calc(var(--m-bottom-stack)+40px)]">
          <Outlet />
        </main>
        {showAside && (
          <aside
            ref={asideRef}
            /* the bottom margin is measured above: it ends the sticky run before the column does, so
               the last screen before the tail shows a whole aside or none of it — never a fragment
               sliced by the header */
            className="sticky hidden w-[320px] shrink-0 flex-col gap-4 overflow-y-auto overscroll-contain pb-2 2xl:w-[360px] xl:flex"
            style={{ top: 'calc(var(--m-sticky-h, var(--m-header-h)) + 16px)', maxHeight: 'calc(100dvh - var(--m-sticky-h, var(--m-header-h)) - 32px)' }}
            aria-label={S.cart.mini}
          >
            <MiniCart />
            <Spotlight />
          </aside>
        )}
      </div>
      {/* the page's closing full-width tail (PageTail): the CTA band and the footer span the whole
          container and end the aside's run, instead of being squeezed into the main column */}
      <div ref={setTailHost} className="container-m flex flex-col gap-7 pb-[calc(var(--m-bottom-stack)+40px)] empty:hidden lg:gap-14" />
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
