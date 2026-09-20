import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { useViewport, isPhoneLike, type Viewport } from './useViewport'

/**
 * What pages tell the shell and what the shell offers pages.
 *
 *  • `usePageTitle(title, back)` — the phone header shows it (with a back arrow).
 *  • `useSearchBand()` — the phone header becomes the plum band with the pinned search.
 *  • `<PageBar>` — a page's sticky bottom action (cart total + Place order, checkout submit).
 *    On phones/tablets it is portalled into the shell's bottom stack, ABOVE the nav, and the
 *    shell measures the stack into `--m-bottom-stack` so content never hides under it. On
 *    desktop it renders inline where the page put it.
 *  • `openProduct(code)` — opens the product panel over the current page (history push, so Back
 *    closes it) · `openCart()` — the cart drawer on desktop < 1280 / tablet · `openPalette()`.
 */

export interface PageTitle {
  title: string
  back?: boolean
}

interface ShellValue {
  viewport: Viewport
  title: PageTitle | null
  setTitle: (t: PageTitle | null) => void
  barHost: HTMLElement | null
  setBarHost: (el: HTMLElement | null) => void
  hasPageBar: boolean
  setHasPageBar: (v: boolean) => void
  cartOpen: boolean
  openCart: () => void
  closeCart: () => void
  paletteOpen: boolean
  openPalette: (q?: string) => void
  closePalette: () => void
  paletteQuery: string
  openProduct: (code: string, from?: string, viewTransition?: boolean) => void
  /** the product panel currently open (from /p/:code or history state) */
  panelCode: string | null
  closeProduct: () => void
  /** kept alongside setHasPageBar so nav can hide on focused flows (checkout) */
  hideNav: boolean
  setHideNav: (v: boolean) => void
  /** phone/tablet: the page asked for the plum band with the pinned search (useSearchBand) */
  searchBand: boolean
  setSearchBand: (v: boolean) => void
}

const Ctx = createContext<ShellValue | null>(null)

export function ShellProvider({ children }: { children: ReactNode }) {
  const viewport = useViewport()
  const navigate = useNavigate()
  const location = useLocation()
  const [title, setTitle] = useState<PageTitle | null>(null)
  const [barHost, setBarHost] = useState<HTMLElement | null>(null)
  const [hasPageBar, setHasPageBar] = useState(false)
  const [cartOpen, setCartOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')
  const [hideNav, setHideNav] = useState(false)
  const [searchBand, setSearchBand] = useState(false)

  const state = location.state as { panel?: string } | null
  const routeCode = location.pathname.startsWith('/p/') ? decodeURIComponent(location.pathname.slice(3).split('/')[0] || '') : null
  const panelCode = state?.panel || routeCode || null

  const openProduct = useCallback(
    (code: string, from?: string, viewTransition?: boolean) => {
      navigate(`${location.pathname}${location.search}`, {
        state: { ...(location.state as object | null), panel: code, from: from || 'card' },
        viewTransition: Boolean(viewTransition),
      })
    },
    [navigate, location.pathname, location.search, location.state],
  )
  const closeProduct = useCallback(() => {
    if (state?.panel) {
      navigate(-1)
      return
    }
    // deep link /p/{code}: nothing underneath in history — go to the storefront root
    navigate('/', { replace: true })
  }, [navigate, state?.panel])

  // any route change closes transient surfaces (derived during render)
  const [pathSeen, setPathSeen] = useState(location.pathname)
  if (pathSeen !== location.pathname) {
    setPathSeen(location.pathname)
    setCartOpen(false)
    setPaletteOpen(false)
  }

  const value = useMemo<ShellValue>(
    () => ({
      viewport,
      title,
      setTitle,
      barHost,
      setBarHost,
      hasPageBar,
      setHasPageBar,
      cartOpen,
      openCart: () => setCartOpen(true),
      closeCart: () => setCartOpen(false),
      paletteOpen,
      openPalette: (q?: string) => {
        setPaletteQuery(q || '')
        setPaletteOpen(true)
      },
      closePalette: () => setPaletteOpen(false),
      paletteQuery,
      openProduct,
      panelCode,
      closeProduct,
      hideNav,
      setHideNav,
      searchBand,
      setSearchBand,
    }),
    [viewport, title, barHost, hasPageBar, cartOpen, paletteOpen, paletteQuery, openProduct, panelCode, closeProduct, hideNav, searchBand],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useShell(): ShellValue {
  const v = useContext(Ctx)
  if (!v) throw new Error('useShell must be used inside ShellProvider')
  return v
}

/** Phone header title. Pass null on Home (brand row). */
// eslint-disable-next-line react-refresh/only-export-components
export function usePageTitle(title: string | null, back = true, docTitle?: string) {
  const { setTitle } = useShell()
  useLayoutEffect(() => {
    setTitle(title ? { title, back } : null)
    return () => setTitle(null)
  }, [title, back, setTitle])
  useLayoutEffect(() => {
    if (docTitle) document.title = docTitle
  }, [docTitle])
}

/** Hide the floating nav for this page (checkout). */
// eslint-disable-next-line react-refresh/only-export-components
export function useHideNav(on = true) {
  const { setHideNav } = useShell()
  useLayoutEffect(() => {
    setHideNav(on)
    return () => setHideNav(false)
  }, [on, setHideNav])
}

/**
 * Phone + tablet: show the plum header band with the search pinned under it (Home, Browse, a
 * category shelf). The brand/title row scrolls away and the search stays — pure CSS sticky. While
 * the band is up, the shell writes `--m-search-h` (px, the pinned part incl. the top safe area) on
 * <html>, so a page can pin its own row under it: `top: var(--m-search-h, 0px)`. Desktop ignores it.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useSearchBand(on = true) {
  const { setSearchBand } = useShell()
  useLayoutEffect(() => {
    setSearchBand(on)
    return () => setSearchBand(false)
  }, [on, setSearchBand])
}

/** A page's sticky bottom action. See ShellContext. */
export function PageBar({ children }: { children: ReactNode }) {
  const { viewport, barHost, setHasPageBar } = useShell()
  const portal = isPhoneLike(viewport)
  useLayoutEffect(() => {
    if (!portal) return
    setHasPageBar(true)
    return () => setHasPageBar(false)
  }, [portal, setHasPageBar])
  if (!portal) return <>{children}</>
  if (!barHost) return null
  return createPortal(<div className="glass border-t border-line px-gutter pb-2 pt-2.5">{children}</div>, barHost)
}
