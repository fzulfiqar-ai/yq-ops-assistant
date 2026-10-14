import { ApiError } from './api'

/**
 * The one plain-English line a portal screen shows for a failed request (R7a).
 *
 *   • the request never got an answer (offline, DNS, the API asleep or restarting — fetch rejects
 *     with a TypeError: "Failed to fetch" / "Load failed" / "NetworkError …") → the connection line;
 *   • the 60 s fetch timeout fired (AbortError) → the slow-server line;
 *   • a 5xx → "Server error (ref …)": the ref is the one on the API's log line (app/main.py
 *     ServerErrorJSON), so a screenshot can be matched to the traceback;
 *   • any other API answer → its `detail` text when it is a string, else the raw body (cut short).
 */
export const NETWORK_ERROR_TEXT = 'Could not reach the server. Check your connection and retry.'
export const TIMEOUT_ERROR_TEXT = 'The server took too long to answer. Please retry.'

/** fetch()'s own rejection — Chrome, Firefox and Safari word it differently; a TypeError from a bug is not one. */
export function isNetworkError(e: unknown): boolean {
  return e instanceof TypeError && /fetch|network|load failed/i.test(e.message)
}

function isAbort(e: unknown): boolean {
  return !!e && typeof e === 'object' && (e as { name?: unknown }).name === 'AbortError'
}

export function serverErrorText(ref: string | null | undefined): string {
  return ref ? `Server error (ref ${ref}). Please retry.` : 'Server error. Please retry.'
}

/**
 * The API's own words in an error body: its `detail` line, or — a 422 from FastAPI's validation, where
 * detail is a list — the first item's message with its field. Null when the body says neither.
 */
export function detailText(body: string): string | null {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (parsed && typeof parsed.detail === 'string') return parsed.detail
    if (parsed && Array.isArray(parsed.detail) && parsed.detail.length) return validationText(parsed.detail[0])
  } catch {
    /* body wasn't JSON */
  }
  return null
}

/** One FastAPI validation item ({loc, msg}) as a line: "text: String should have at most 600 characters". */
function validationText(item: unknown): string {
  if (typeof item === 'string') return item
  const d = (item || {}) as { loc?: unknown; msg?: unknown }
  const msg = typeof d.msg === 'string' && d.msg ? d.msg : 'The request was not accepted.'
  const loc = Array.isArray(d.loc) ? d.loc.filter((x) => typeof x === 'string' && x !== 'body' && x !== 'query') : []
  const field = loc.length ? String(loc[loc.length - 1]).replace(/_/g, ' ') : ''
  return field ? `${field}: ${msg}` : msg
}

export function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status >= 500) return serverErrorText(e.ref)
    const said = detailText(e.body)
    if (said) return said
    return e.body ? e.body.slice(0, 200) : e.message
  }
  if (isNetworkError(e)) return NETWORK_ERROR_TEXT
  if (isAbort(e)) return TIMEOUT_ERROR_TEXT
  if (e instanceof Error && e.message) return e.message
  return fallback
}
