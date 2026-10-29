/**
 * Gentle on weak phones (28-Oct-2026 polish, motion list M16). A phone that asks to save data, or
 * one with 2 GB of memory or less, gets html[data-lite]: no scroll reveals (nothing is ever hidden
 * waiting for one), no shimmer sweeps and no stagger. The fades that carry meaning (a photo, a
 * page, a sheet) stay. Decided once at boot (src/main.market.tsx), before the first render.
 *
 * Both signals are Chromium-only, which is where the low-end Android phones are; a browser without
 * them is treated as capable.
 */
export function isLite(): boolean {
  try {
    const nav = navigator as Navigator & { connection?: { saveData?: boolean }; deviceMemory?: number }
    return Boolean(nav.connection?.saveData) || (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 2)
  } catch {
    return false
  }
}

export function applyLite(): void {
  if (isLite()) document.documentElement.dataset.lite = ''
}
