import { createRoot } from 'react-dom/client'
import './market/market.css'
import MarketApp from './MarketApp'

/**
 * The marketplace entry. vite.config.ts (marketHtml) points index.html at THIS file for the
 * market build, so the merchant's first download is the market shell itself — not main.tsx
 * followed by a second round-trip for MarketApp. Ships market.css only (never index.css).
 */

// A tab opened before a deploy still holds the old index.html, whose lazy chunks no longer exist
// once the new build is live. Both hosts answer a missing /assets/* file with the SPA index.html
// (HTTP 200, text/html, and the immutable cache header), so the import fails and the app is stuck
// until the merchant closes the tab. Vite reports that as `vite:preloadError`; one reload fetches
// the new index.html and its chunks. Once per session, so a genuinely broken build cannot loop.
window.addEventListener('vite:preloadError', (event) => {
  let already = false
  try {
    already = sessionStorage.getItem('yq-preload-reload') === '1'
    sessionStorage.setItem('yq-preload-reload', '1')
  } catch {
    /* ignore */
  }
  if (already) return
  event.preventDefault()
  window.location.reload()
})

createRoot(document.getElementById('root')!).render(<MarketApp />)
