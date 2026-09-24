import { onCLS, onINP, onLCP } from 'web-vitals/attribution'
import { track } from './events'
import { catalogNote } from './rum'

/**
 * Field Core Web Vitals (plan §X, widened in R6): one `vitals` event per page visit, sent when the
 * tab is hidden or unloaded. Shop Analytics shows the 75th percentile, which is the number Google
 * grades. No PII: numbers, a route TEMPLATE (never the path), the viewport bucket, and where the
 * catalog came from.
 *
 * What changed in R6 and why (audit SPD-11/12: RUM lost CLS on 440 of 442 samples and never saw an
 * abandoned cold visit):
 *   • onCLS({ reportAllChanges: true }) — the default reports only at the visibility change, which
 *     raced our own flush; now every shift updates the value we hold, and the final report is a no-op.
 *   • web-vitals/attribution — the LCP element and its four phases (TTFB → resource load delay →
 *     resource load → render delay), so a 12 s LCP can be read as "the origin was cold" (ttfb) vs
 *     "the photo was slow" (load) vs "the app took long to paint it" (render).
 *   • the beacon goes out on pagehide EVEN WITH NO CATALOG (catalog_src: 'none'): a merchant who
 *     gives up on a cold origin is the visit that matters most, and it used to leave no trace.
 *
 * The meta bag is exactly the 12 keys app/shop.py record_event keeps, core numbers first.
 */
type Num = number | undefined
const m: { lcp?: number; inp?: number; cls?: number } = {}
let lcpAttr: { lcp_el?: string; lcp_ttfb?: number; lcp_delay?: number; lcp_load?: number; lcp_render?: number } = {}
let sent = false
let started = false
let route = '/'

/** Route TEMPLATE for a pathname — mirrors the routes in MarketApp.tsx and the reserved words in
 *  catalog-prefetch.js. A single unreserved segment is a rep storefront, reported as '/:slug'. */
const RESERVED = new Set(['search', 'cart', 'checkout', 'orders', 'p', 't', 'o', 'c', 'join', 'shop', 'me', 'quick', 'about', 'help', 'ask', 'saved', 'brands', 'wekome', 'coming-soon'])
export function routeName(pathname: string): string {
  const seg = pathname.split('/').filter(Boolean)
  if (!seg.length) return '/'
  const head = seg[0].toLowerCase()
  if (seg.length === 1) return RESERVED.has(head) ? `/${head}` : '/:slug'
  switch (head) {
    case 'p': return '/p/:code'
    case 't': return '/t/:category'
    case 'o': return '/o/:token'
    case 'c': return '/c/:token'
    case 'brands': return '/brands/:brand'
    default: return `/${head}/*`
  }
}

/** phone / tablet / desktop / wide — the same thresholds as shell/useViewport.ts. */
export function viewportName(): string {
  if (typeof window === 'undefined' || !window.matchMedia) return 'phone'
  if (window.matchMedia('(min-width: 1280px)').matches) return 'wide'
  if (window.matchMedia('(min-width: 1024px)').matches) return 'desktop'
  if (window.matchMedia('(min-width: 768px)').matches) return 'tablet'
  return 'phone'
}

const r = (v: Num): Num => (v == null ? undefined : Math.round(v))

/** The meta bag with the unknowns left out (a visit with no LCP yet still reports its route). */
function compact(bag: Record<string, string | number | undefined>): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(bag)) if (v !== undefined) out[k] = v
  return out
}

function flush(): void {
  if (sent) return
  sent = true
  const note = catalogNote()
  track('vitals', {
    meta: compact({
      lcp: m.lcp,
      inp: m.inp,
      cls: m.cls,
      route,
      vp: viewportName(),
      catalog_ms: note?.ms,
      catalog_src: note?.src || 'none',
      ...lcpAttr,
    }),
  })
}

export function startVitals(): void {
  if (started || typeof window === 'undefined') return
  started = true
  route = routeName(window.location.pathname)
  onLCP((v) => {
    m.lcp = Math.round(v.value)
    const a = v.attribution
    lcpAttr = {
      lcp_el: (a.element || '').slice(0, 60),
      lcp_ttfb: r(a.timeToFirstByte),
      lcp_delay: r(a.resourceLoadDelay),
      lcp_load: r(a.resourceLoadDuration),
      lcp_render: r(a.elementRenderDelay),
    }
  })
  onINP((v) => { m.inp = Math.round(v.value) })
  onCLS((v) => { m.cls = Math.round(v.value * 1000) / 1000 }, { reportAllChanges: true })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
  window.addEventListener('pagehide', flush)
}
