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
const root = createRoot(document.getElementById('root')!)

// A third door (16-Sep-2026): the marketplace build (VITE_APP=market, its own Vercel project and
// hostname) owns the root URL and every /{slug} storefront. The portal build is untouched.
const APP = (import.meta.env.VITE_APP as string | undefined) || 'portal'

if (APP === 'market') {
  void import('./MarketApp').then(({ default: MarketApp }) => root.render(<MarketApp />))
} else if (/^\/(c|o)\/[^/]+/.test(window.location.pathname)) {
  void import('./PublicApp').then(({ default: PublicApp }) => root.render(<PublicApp />))
} else {
  void import('./PortalRoot').then(({ default: PortalRoot }) => root.render(<PortalRoot />))
}
