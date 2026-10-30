/**
 * How many page-code warm-ups are in flight (lib/prefetch). The deploy reload rule (lib/preloadReload)
 * leaves a warm-up's failed chunk alone: a deploy that lands while a tab is still warming must not
 * reload it under a half-filled form; the page the person then opens says it did not load, with
 * Reload (release review, 30-Oct-2026: a capture listener meant to stop the rule ran after it, since a
 * window runs its own listeners in the order they were added). Tiny on purpose: the portal's entry
 * imports it.
 */
let warming = 0

export function warmStarted(): void {
  warming += 1
}

export function warmEnded(): void {
  warming = Math.max(0, warming - 1)
}

export function isWarming(): boolean {
  return warming > 0
}
