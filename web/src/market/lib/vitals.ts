import { onCLS, onINP, onLCP } from 'web-vitals'
import { track } from './events'

/**
 * Field Core Web Vitals (plan §X): one `vitals` event per page visit with the final LCP, INP and
 * CLS, sent when the tab is hidden or unloaded. Shop Analytics shows the 75th percentile, which is
 * the number Google grades. No PII: three numbers and the usual device/session ids.
 */
const m: { lcp?: number; inp?: number; cls?: number } = {}
let sent = false
let started = false

function flush(): void {
  if (sent || (m.lcp == null && m.inp == null && m.cls == null)) return
  sent = true
  track('vitals', { meta: { ...m } })
}

export function startVitals(): void {
  if (started || typeof window === 'undefined') return
  started = true
  onLCP((v) => { m.lcp = Math.round(v.value) })
  onINP((v) => { m.inp = Math.round(v.value) })
  onCLS((v) => { m.cls = Math.round(v.value * 1000) / 1000 })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
  window.addEventListener('pagehide', flush)
}
