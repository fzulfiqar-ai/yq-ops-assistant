import { createRoot } from 'react-dom/client'
import './market/market.css'
import { installPreloadReload } from './lib/preloadReload'
import { applyLite } from './market/lib/lite'
import { startGlide } from './market/lib/glide'
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
createRoot(document.getElementById('root')!).render(<MarketApp />)

// glide scrolling (market/lib/glide): a mouse PC only, once the first page has painted
if (typeof window.requestIdleCallback === 'function') window.requestIdleCallback(startGlide, { timeout: 1500 })
else window.setTimeout(startGlide, 600)
