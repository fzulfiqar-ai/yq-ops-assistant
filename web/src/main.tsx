import { createRoot } from 'react-dom/client'
import './index.css'

/**
 * Two front doors.
 *
 *  • /c/{token} and /o/{token} — a merchant tapping a WhatsApp link: logged out, on a
 *    phone, often on 4G. PublicApp gives them the shop and the order-status page and
 *    nothing else — no session client, no intro animation, no query cache, no charts.
 *  • everything else — the portal (PortalRoot), exactly as before.
 *
 * Both are dynamic imports, so neither door downloads the other's code. Before this
 * split a catalog link parsed the whole ~570 KB portal bundle and sat through the
 * "Intelligence" intro before it ever asked the API for a product.
 */

// A tab opened before a deploy still holds the old index.html, whose lazy chunks no longer exist
// once the new build is live (the portal is 30+ lazy pages, so this is the common case for a rep
// who keeps the app open all day). The static host used to answer a missing /assets/* file with
// index.html + HTTP 200, so the import failed and the page was stuck until the tab was closed; with
// the R6 Worker it is a real 404, and either way Vite reports it as `vite:preloadError`. One reload
// fetches the new index.html and its chunks. Once per session, so a genuinely broken build cannot
// loop. Same handler as src/main.market.tsx.
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

const root = createRoot(document.getElementById('root')!)

// The third door — the marketplace build (VITE_APP=market, its own Vercel project and hostname) —
// has its own entry, src/main.market.tsx, which vite.config.ts wires into index.html directly.
if (/^\/(c|o)\/[^/]+/.test(window.location.pathname)) {
  void import('./PublicApp').then(({ default: PublicApp }) => root.render(<PublicApp />))
} else {
  void import('./PortalRoot').then(({ default: PortalRoot }) => root.render(<PortalRoot />))
}
