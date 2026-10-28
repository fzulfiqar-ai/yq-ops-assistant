import { useLayoutEffect, useRef } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'

/**
 * Where each page was scrolled to, per history entry (28-Oct-2026 polish, harness check S4). A new
 * page opens at the top; Back and Forward land where that page was.
 *
 * The browser's own restore is switched off (history.scrollRestoration = 'manual'). It restored
 * before a lazy page or the market's two-phase home had its height, so Back landed a few hundred
 * pixels down instead of where the merchant left the page. Here the restore follows the page as it
 * grows, for up to RESTORE_MS, and lets go the moment the user scrolls, taps or presses a key.
 *
 * A new entry on the SAME page (a product panel over it, a filter written into the query) leaves the
 * position alone, as before. Positions live in sessionStorage, so a reload restores too.
 */
const STORE = 'yq-scroll-v1'
const RESTORE_MS = 2500
const STEADY_MS = 300 // the page height must hold this long before the restore lets go
const REMEMBER = 60 // entries

let positions: Record<string, number> = read()

function read(): Record<string, number> {
  try {
    const v = JSON.parse(sessionStorage.getItem(STORE) || '{}')
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

function remember(key: string, y: number): void {
  positions[key] = y
  const keys = Object.keys(positions)
  if (keys.length > REMEMBER) positions = Object.fromEntries(keys.slice(-REMEMBER).map((k) => [k, positions[k]]))
  try {
    sessionStorage.setItem(STORE, JSON.stringify(positions))
  } catch {
    /* storage blocked: this tab still restores from memory */
  }
}

export function useScrollKeeper(): void {
  const location = useLocation()
  const navType = useNavigationType()
  const lastPath = useRef<string | null>(null)

  // This entry's position, written at most once a frame while it shows. A layout effect, so the
  // listener is gone before the next page's shorter height clamps the scroll and fires an event.
  useLayoutEffect(() => {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual'
    const key = location.key
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        remember(key, Math.round(window.scrollY))
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [location.key])

  useLayoutEffect(() => {
    const samePage = lastPath.current === location.pathname
    lastPath.current = location.pathname
    const target = navType === 'POP' ? positions[location.key] : undefined
    if (target == null) {
      if (!samePage) window.scrollTo(0, 0)
      return
    }
    // Follow the page as it grows, and hold the position until its height has been steady for
    // STEADY_MS: sections above the fold that finish rendering after the first fit would otherwise
    // let the browser's scroll anchoring drift the page (Back landed ~30 px off). Anchoring is
    // paused for that window only; the user scrolling, tapping or typing ends it at once.
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
    }
    window.addEventListener('wheel', finish, opts)
    window.addEventListener('touchstart', finish, opts)
    window.addEventListener('keydown', finish, opts)
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
  }, [location.key]) // eslint-disable-line react-hooks/exhaustive-deps -- once per history entry
}
