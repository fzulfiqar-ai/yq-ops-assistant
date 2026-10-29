import { createRoot } from 'react-dom/client'
import './market/market.css'
import { installPreloadReload } from './lib/preloadReload'
import { applyLite } from './market/lib/lite'
import { startGlide } from './market/lib/glide'
import { splashDue } from './market/lib/splashGate'
import MarketApp from './MarketApp'

/**
 * The marketplace entry. vite.config.ts (marketHtml) points index.html at THIS file for the
 * market build, so the merchant's first download is the market shell itself — not main.tsx
 * followed by a second round-trip for MarketApp. Ships market.css only (never index.css).
 */

// A tab opened before a deploy still holds the old index.html, whose lazy chunks no longer exist
// once the new build is live: one reload per build fetches the new one (lib/preloadReload).
installPreloadReload()

applyLite()
markIntro()
createRoot(document.getElementById('root')!).render(<MarketApp />)

/**
 * The first screen comes alive (F5, 29-Oct-2026; market.css "html[data-intro]"): once per visit, on a
 * mouse PC, when the page was opened (not Back/Forward), the opening is not playing (it is the
 * entrance then), and motion is welcome. Taken away after 2.6 s, so a later render never replays it.
 */
function markIntro(): void {
  try {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    const fresh = !nav || nav.type === 'navigate' || nav.type === 'reload'
    const pc = window.matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)').matches
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches || 'lite' in document.documentElement.dataset
    if (!fresh || !pc || calm || splashDue()) return
    document.documentElement.dataset.intro = ''
    window.setTimeout(() => delete document.documentElement.dataset.intro, 2600)
  } catch {
    /* no entrance: the page shows as it is */
  }
}

// glide scrolling (market/lib/glide): a mouse PC only, once the first page has painted
if (typeof window.requestIdleCallback === 'function') window.requestIdleCallback(startGlide, { timeout: 1500 })
else window.setTimeout(startGlide, 600)
