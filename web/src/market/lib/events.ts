import type { EventPing, ShopEventKind } from '@/lib/shopApi'
import { sessionId } from './format'
import { pingMarketEvent } from './marketApi'
import { currentRef, deviceId } from './device'

/**
 * One line to record a funnel fact (plan §R). Adds the device, the session, the remembered
 * salesman and a small PII-free meta bag, then fires and forgets. Search pings are debounced so a
 * merchant typing "type c" produces one event, not six.
 */

let searchTimer: number | undefined
let src = 'direct'

export function setSource(s: string | null | undefined): void {
  if (s && s.length <= 80) src = s
}

export function track(event: ShopEventKind, extra: Partial<EventPing> = {}): void {
  pingMarketEvent({
    event,
    session_id: sessionId(),
    device_id: deviceId(),
    referral_code: extra.referral_code ?? currentRef() ?? undefined,
    src,
    ...extra,
  })
}

export function trackSearch(q: string, results: number): void {
  if (searchTimer) window.clearTimeout(searchTimer)
  const query = q.trim()
  if (query.length < 2) return
  searchTimer = window.setTimeout(() => {
    track(results === 0 ? 'search_zero' : 'search', { meta: { q: query.slice(0, 60), results } })
  }, 800)
}
