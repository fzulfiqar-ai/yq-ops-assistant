import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { useReveal } from '../hooks/useReveal'
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
 *  • `<PageTail>` — a page's closing full-width tail (CTA band, footer). On desktop it is portalled
 *    below the main column + aside row so it spans the container; elsewhere it renders in place.
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
  tailHost: HTMLElement | null
  setTailHost: (el: HTMLElement | null) => void
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

/**
 * What lists need from the shell, kept apart from it (29-Oct-2026, round 2): a product panel opening
 * changes the shell's value, and every card that read `openProduct` from it, up to 60, re-rendered on
 * each open before the panel could start. These two change only when they must: `openProduct` never,
 * the viewport class on a resize across a breakpoint.
 */
interface ShellActions {
  openProduct: (code: string, from?: string, viewTransition?: boolean) => void
}
const ActionsCtx = createContext<ShellActions | null>(null)
const ViewportCtx = createContext<Viewport | null>(null)

export function ShellProvider({ children }: { children: ReactNode }) {
  const viewport = useViewport()
  const navigate = useNavigate()
  const location = useLocation()
  const [title, setTitle] = useState<PageTitle | null>(null)
  const [barHost, setBarHost] = useState<HTMLElement | null>(null)
  const [tailHost, setTailHost] = useState<HTMLElement | null>(null)
  const [hasPageBar, setHasPageBar] = useState(false)
  const [cartOpen, setCartOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')
  const [hideNav, setHideNav] = useState(false)
  const [searchBand, setSearchBand] = useState(false)

  const state = location.state as { panel?: string } | null
  const routeCode = location.pathname.startsWith('/p/') ? decodeURIComponent(location.pathname.slice(3).split('/')[0] || '') : null
  const panelCode = state?.panel || routeCode || null

  // read when called, not when made, so the callback stays the same from one open to the next
  const here = useRef(location)
  useLayoutEffect(() => {
    here.current = location
  })
  const openProduct = useCallback(
    (code: string, from?: string, viewTransition?: boolean) => {
      const at = here.current
      navigate(`${at.pathname}${at.search}`, {
        state: { ...(at.state as object | null), panel: code, from: from || 'card' },
        viewTransition: Boolean(viewTransition),
      })
    },
    [navigate],
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
      tailHost,
      setTailHost,
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
    [viewport, title, barHost, tailHost, hasPageBar, cartOpen, paletteOpen, paletteQuery, openProduct, panelCode, closeProduct, hideNav, searchBand],
  )
  const actions = useMemo<ShellActions>(() => ({ openProduct }), [openProduct])
  return (
    <ViewportCtx.Provider value={viewport}>
      <ActionsCtx.Provider value={actions}>
        <Ctx.Provider value={value}>{children}</Ctx.Provider>
      </ActionsCtx.Provider>
    </ViewportCtx.Provider>
  )
}

/** `openProduct` without the rest of the shell: a list's cards do not re-render when a panel opens. */
// eslint-disable-next-line react-refresh/only-export-components
export function useShellActions(): ShellActions {
  const v = useContext(ActionsCtx)
  if (!v) throw new Error('useShellActions must be used inside ShellProvider')
  return v
}

/** The viewport class without the rest of the shell (for the same reason as useShellActions). */
// eslint-disable-next-line react-refresh/only-export-components
export function useShellViewport(): Viewport {
  const v = useContext(ViewportCtx)
  if (!v) throw new Error('useShellViewport must be used inside ShellProvider')
  return v
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

/**
 * A page's closing tail — the CTA band and the footer. On desktop the page lives in the main
 * column next to the sticky mini-cart aside; a closing statement squeezed into that column reads
 * as an accident (and the aside would still be floating beside the copyright). So the shell hosts
 * the tail after the column row, at full container width, and the page portals into it. Phone and
 * tablet have no aside and no host: the children stay exactly where the page put them.
 *
 * The portal leaves the page's reveal root behind, so the tail scans itself — without this a
 * `.reveal` section in here would never be marked `.is-in`, and would never become visible.
 */
export function PageTail({ children }: { children: ReactNode }) {
  const { tailHost } = useShell()
  const root = useMemo(() => ({ current: tailHost }), [tailHost])
  useReveal(root, [tailHost])
  if (!tailHost) return <>{children}</>
  return createPortal(children, tailHost)
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
