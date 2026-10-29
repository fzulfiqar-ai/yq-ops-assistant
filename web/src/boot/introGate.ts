import { createContext, useContext } from 'react'
import { bootKindFor } from './handoff'

/**
 * When the portal intro (components/ui/arc-preloader-hero.tsx) plays. The marketplace opening's rule
 * (market/lib/splashGate.ts, mirrored here, not imported: the market's keys stay the market's): at most
 * once per device every 7 days, never twice in one tab session, and no intro at all when storage is
 * blocked (there is no way to keep it to once a week). Kept under the intro's own `storageKey` — in
 * localStorage the time it last played, in sessionStorage that it played in this tab.
 *
 * And only where it opens onto its own night: a page load that gets the night frame (boot/handoff.ts
 * bootKindFor(): the login, or a protected page with no stored session). Never over a signed-in shell,
 * an invite or a public link. A stamp that is not a time — the old intro's 'done' — counts as at least
 * a week old, so a device that saw the old intro sees this one once, then weekly.
 */
export const INTRO_EVERY_MS = 7 * 864e5

export function introDue(key: string, now: number = Date.now()): boolean {
  try {
    if (sessionStorage.getItem(key)) return false
    if (bootKindFor(window.location.pathname) !== 'night') return false
    const age = now - Number(localStorage.getItem(key) || 0)
    // NaN (a stamp that is not a time) and a stamp from the future (a clock put back) never silence it
    return !(age >= 0 && age < INTRO_EVERY_MS)
  } catch {
    return false
  }
}

/** It played: stamp the device (7 days) and the tab. Blocked storage: introDue() already says no. */
export function markIntroShown(key: string, now: number = Date.now()): void {
  try {
    sessionStorage.setItem(key, '1')
    localStorage.setItem(key, String(now))
  } catch {
    /* quota / blocked */
  }
}

/** True while the intro covers the page: the login holds its entrance until the night clears. */
export const IntroContext = createContext(false)

export function useIntroPlaying(): boolean {
  return useContext(IntroContext)
}
