/**
 * When the opening (components/Splash.tsx) plays: at most once per device every 7 days, and never
 * twice in one tab session. A session gate alone played it again on every rep or WhatsApp link,
 * because each one opens a new tab — the night overlay then covered a ready catalog each time.
 *
 * ONE predicate for the three places that must agree: shell/Shell.tsx (fetch the chunk early and
 * paint the night Suspense gap), components/Splash.tsx (play, and stamp both keys when it does) and
 * public/catalog-prefetch.js (hide the served night frame #yq-boot on any other load — plain ES5
 * there, MIRRORING these two keys and the 7 days). Blocked storage: there is no way to keep it to
 * once a week, so the opening stays out of the way. Only the predicate lives here: this module
 * rides in the entry chunk.
 */
export const AT_KEY = 'yq-splash-at'
export const SESSION_KEY = 'yq-splash-session'
export const EVERY_MS = 7 * 864e5

export function splashDue(now: number = Date.now()): boolean {
  try {
    if (sessionStorage.getItem(SESSION_KEY)) return false
    const age = now - Number(localStorage.getItem(AT_KEY) || 0)
    // a stamp from the future (a clock put back) never silences it for good
    return !(age >= 0 && age < EVERY_MS)
  } catch {
    return false
  }
}

/** The opening played: stamp the device (7 days) and the tab. Blocked storage: nothing to keep. */
export function markSplashShown(now: number = Date.now()): void {
  try {
    sessionStorage.setItem(SESSION_KEY, '1')
    localStorage.setItem(AT_KEY, String(now))
  } catch {
    /* quota / blocked: splashDue() already answers false when storage throws */
  }
}
