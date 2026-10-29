import { useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'

/**
 * Where each portal page was scrolled to (ship 2, plan item 12; acceptance S4). The portal scrolls the
 * window now, in both shells, so the window's position is what is kept.
 *
 *  - A new page opens at the top. It used to open at the old page's scroll position: the office
 *    scrolled an inner box that nothing reset.
 *  - Back and Forward land where that page was, per history entry.
 *  - The salesman app's tabs keep their own place: Catalog → Orders → Catalog from the tab bar lands
 *    where the catalog was left, as a phone's tab bar does. The tab already open, tapped again, goes
 *    back to its top. A link to a tab from inside a page (Today's "Orders", the order screen's "My
 *    orders") opens it at the top, where the newest is.
 *  - A new entry on the SAME page (a filter written into the query, ?open= for a drawer) leaves the
 *    position alone. A reload restores the entry it reloads; a page opened from a typed address or
 *    another site opens at the top.
 *
 * The browser's own restore is switched off (history.scrollRestoration = 'manual'): it restored before
 * a lazy page had its height. Here the restore follows the page as it grows, for up to RESTORE_MS, and
 * lets go the moment the person scrolls, taps or presses a key. Positions live in sessionStorage, so a
 * reload restores too. The same approach as the marketplace's keeper (lib/scrollKeeper.ts, left as it
 * is: the market build is released), plus the tabs.
 */
const STORE = 'yq-portal-scroll-v1'
const RESTORE_MS = 2500
const STEADY_MS = 300 // the page height must hold this long before the restore lets go
const REMEMBER = 60 // history entries

type Saved = { entries: Record<string, number>; tabs: Record<string, number> }

export type NavKind = 'POP' | 'PUSH' | 'REPLACE'

export type ScrollPlan = { kind: 'keep' } | { kind: 'top'; smooth: boolean } | { kind: 'restore'; y: number }

export interface PlanInput {
  /** the shell's first page since it mounted (a load, a reload, the first page after sign-in) */
  first: boolean
  /** how the document itself was opened (PerformanceNavigationTiming.type) */
  docNav: string
  navType: NavKind
  /** the same pathname as the page before: a filter in the query, a drawer's ?open= */
  samePath: boolean
  /** the person tapped this page's tab in the tab bar (salesman shell) */
  tapped: boolean
  /** where this history entry was left */
  entryY?: number
  /** where this tab was left */
  tabY?: number
}

/** What to do with the window's scroll when the location changes. Pure: web/scripts/portal_shell_test.mjs. */
export function scrollPlan(p: PlanInput): ScrollPlan {
  if (p.first) {
    const reopened = p.docNav === 'reload' || p.docNav === 'back_forward'
    return reopened && p.entryY != null ? { kind: 'restore', y: p.entryY } : { kind: 'keep' }
  }
  if (p.tapped) {
    if (p.samePath) return { kind: 'top', smooth: true }
    return p.tabY != null ? { kind: 'restore', y: p.tabY } : { kind: 'top', smooth: false }
  }
  if (p.navType === 'POP') {
    if (p.entryY != null) return { kind: 'restore', y: p.entryY }
    return p.samePath ? { kind: 'keep' } : { kind: 'top', smooth: false }
  }
  return p.samePath ? { kind: 'keep' } : { kind: 'top', smooth: false }
}

// ── memory ─────────────────────────────────────────────────────────────────────────────────────

function read(): Saved {
  try {
    const v = JSON.parse(sessionStorage.getItem(STORE) || 'null') as Partial<Saved> | null
    return {
      entries: v && typeof v.entries === 'object' && v.entries ? v.entries : {},
      tabs: v && typeof v.tabs === 'object' && v.tabs ? v.tabs : {},
    }
  } catch {
    return { entries: {}, tabs: {} }
  }
}

let saved: Saved = typeof window === 'undefined' ? { entries: {}, tabs: {} } : read()

/** Written to sessionStorage only when an entry is left or the tab is hidden — never per scroll frame. */
function persist(): void {
  const keys = Object.keys(saved.entries)
  if (keys.length > REMEMBER) saved = { ...saved, entries: Object.fromEntries(keys.slice(-REMEMBER).map((k) => [k, saved.entries[k]])) }
  try {
    sessionStorage.setItem(STORE, JSON.stringify(saved))
  } catch {
    /* storage blocked: this tab still restores from memory */
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', persist)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') persist()
  })
}

/** the tab the person just tapped, read once by the next location change */
let tappedTab: string | null = null

/** A tab link's onClick: the navigation it starts came from the tab bar (salesman shell). */
export function noteTabTap(to: string): void {
  tappedTab = to.split(/[?#]/, 1)[0] || '/'
}

function docNavType(): string {
  try {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    return nav?.type || 'navigate'
  } catch {
    return 'navigate'
  }
}

function calm(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** Hold the window at `target` while the page grows into it; let go on any input, or once it is steady. */
function follow(target: number): () => void {
  // Anchoring is paused for this window only: sections that finish rendering above the fold after
  // the first fit would otherwise drift the page (the market's keeper saw ~30 px).
  const root = document.documentElement
  const anchoring = root.style.overflowAnchor
  root.style.overflowAnchor = 'none'
  let done = false
  let raf = 0
  const started = performance.now()
  let height = -1
  let steadySince = started
  const opts = { passive: true, capture: true } as const
  const finish = () => {
    if (done) return
    done = true
    cancelAnimationFrame(raf)
    root.style.overflowAnchor = anchoring
    window.removeEventListener('wheel', finish, opts)
    window.removeEventListener('touchstart', finish, opts)
    window.removeEventListener('keydown', finish, opts)
    window.removeEventListener('pointerdown', finish, opts)
  }
  window.addEventListener('wheel', finish, opts)
  window.addEventListener('touchstart', finish, opts)
  window.addEventListener('keydown', finish, opts)
  window.addEventListener('pointerdown', finish, opts)
  const step = () => {
    if (done) return
    const now = performance.now()
    const h = root.scrollHeight
    if (h !== height) {
      height = h
      steadySince = now
    }
    const room = h - window.innerHeight
    window.scrollTo(0, Math.min(target, Math.max(0, room)))
    if ((room >= target && now - steadySince >= STEADY_MS) || now - started > RESTORE_MS) return finish()
    raf = requestAnimationFrame(step)
  }
  step()
  return finish
}

/**
 * The shell's scroll keeper. `tabs`: the salesman shell's tab paths (each keeps its own place when
 * switched to from the tab bar); the office passes none.
 */
export function useShellScroll(tabs: readonly string[] = []): void {
  const location = useLocation()
  const navType = useNavigationType() as NavKind
  const lastPath = useRef<string | null>(null)
  const tabList = tabs.join('|')

  // This entry's position (and its tab's), kept at most once a frame while it shows. A layout effect,
  // so the listener is gone before the next page's shorter height clamps the scroll and fires an event.
  useLayoutEffect(() => {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual'
    const key = location.key
    const tab = tabList.split('|').includes(location.pathname) ? location.pathname : null
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const y = Math.round(window.scrollY)
        saved.entries[key] = y
        if (tab) saved.tabs[tab] = y
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (raf) cancelAnimationFrame(raf)
      persist()
    }
  }, [location.key, location.pathname, tabList])

  useLayoutEffect(() => {
    const first = lastPath.current === null
    const samePath = lastPath.current === location.pathname
    lastPath.current = location.pathname
    const tapped = tappedTab !== null && tappedTab === location.pathname
    tappedTab = null
    const plan = scrollPlan({
      first, docNav: docNavType(), navType, samePath, tapped,
      entryY: saved.entries[location.key], tabY: saved.tabs[location.pathname],
    })
    if (plan.kind === 'keep') return
    if (plan.kind === 'top') {
      window.scrollTo({ top: 0, behavior: plan.smooth && !calm() ? 'smooth' : 'auto' })
      return
    }
    return follow(plan.y)
  }, [location.key]) // eslint-disable-line react-hooks/exhaustive-deps -- once per history entry
}
