/**
 * Client error telemetry (release R6): what both ErrorBoundaries send when a render throws, as the
 * PII-free meta bag of a shop_events `error` row (app/shop.py _META_KEYS: where / build / code /
 * reason / route). No dependencies on purpose — the market bundle must not pull portal modules and
 * PublicApp must not pull the session client, and this file is imported by all three.
 *
 * `reason` is the error message with anything that could identify a person or a link stripped
 * (URLs, e-mail addresses, runs of 6+ digits — phone numbers, order tokens), then cut at 120 chars,
 * which is also what the server keeps. `build` is the deployed bundle (__BUILD_ID__, the commit),
 * so an error can be matched to a release and a rollback can be seen to end it.
 */
declare const __BUILD_ID__: string

export interface ErrorMeta {
  where: string
  build: string
  code: string
  reason: string
  route: string
}

export function buildId(): string {
  return typeof __BUILD_ID__ !== 'undefined' ? __BUILD_ID__ : 'dev'
}

export function scrub(message: string): string {
  return message
    .replace(/https?:\/\/\S+/g, '<url>')
    .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, '<email>')
    .replace(/\d{6,}/g, '<n>')
    .replace(/\s+/g, ' ')
    .trim()
}

/** A coarse route for the portal: the first path segment only, never an id, a token or a name. */
export function coarseRoute(pathname: string): string {
  const head = pathname.split('/').filter(Boolean)[0] || ''
  return head ? `/${head.toLowerCase().slice(0, 24)}` : '/'
}

export function errorMeta(where: string, error: unknown, route: string): ErrorMeta {
  const e = (error && typeof error === 'object' ? error : null) as { name?: unknown; message?: unknown } | null
  const code = String((e && e.name) || 'Error').slice(0, 40)
  const raw = e && typeof e.message === 'string' ? e.message : String(error ?? '')
  return { where, build: buildId(), code, reason: scrub(raw).slice(0, 120), route }
}

/**
 * The portal / public-link path: POST the event straight to the API (no session, keepalive so it
 * survives the reload the error screen offers). The market uses its own track() instead, which adds
 * the device, session and remembered rep.
 */
export function reportError(where: string, error: unknown): void {
  try {
    const api = (import.meta.env.VITE_API_URL as string) || ''
    if (!api || typeof fetch !== 'function') return
    const body = { event: 'error', src: where, meta: errorMeta(where, error, coarseRoute(window.location.pathname)) }
    void fetch(`${api}/public/market/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* telemetry never throws */
  }
}
