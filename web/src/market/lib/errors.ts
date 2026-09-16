import { pingMarketEvent } from './marketApi'
import { deviceId } from './device'

/**
 * A broken screen on a merchant's phone used to be invisible to us. Every uncaught error and
 * unhandled rejection now lands in shop_events as kind "error" (message trimmed, route, build id)
 * — three per session at most, so a render loop cannot flood the API.
 */
const MAX_PER_SESSION = 3
let sent = 0

function report(kind: string, raw: unknown) {
  if (sent >= MAX_PER_SESSION) return
  sent++
  const msg = raw instanceof Error ? `${raw.name}: ${raw.message}` : typeof raw === 'string' ? raw : (() => { try { return JSON.stringify(raw) } catch { return String(raw) } })()
  try {
    pingMarketEvent({
      event: 'error',
      device_id: deviceId(),
      meta: { from: kind, code: msg.replace(/\s+/g, ' ').slice(0, 120), to: location.pathname.slice(0, 80), value: String(import.meta.env.VITE_BUILD_ID || '').slice(0, 24) },
    })
  } catch {
    /* never throw from the reporter */
  }
}

export function startErrorReporting(): () => void {
  const onError = (e: ErrorEvent) => report('error', e.error || e.message)
  const onReject = (e: PromiseRejectionEvent) => report('unhandledrejection', e.reason)
  window.addEventListener('error', onError)
  window.addEventListener('unhandledrejection', onReject)
  return () => {
    window.removeEventListener('error', onError)
    window.removeEventListener('unhandledrejection', onReject)
  }
}
