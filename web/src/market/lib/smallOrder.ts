/**
 * The merchant's "yes, send it as a small order request" for this browser session.
 *
 * Under the wholesale minimum (mode `request`) the Restock page asks once, in SmallOrderSheet,
 * before checkout; checkout reads the acknowledgement to title itself "Small order request".
 * Session-scoped on purpose: a new visit asks again. Storage can be blocked (private mode,
 * previews) — then the helpers simply forget, they never throw.
 */

const KEY = 'yq-small-ack'

export function ackSmallOrder(): void {
  try {
    sessionStorage.setItem(KEY, '1')
  } catch {
    /* storage blocked: the sheet asks again next time */
  }
}

export function hasSmallAck(): boolean {
  try {
    return sessionStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function clearSmallAck(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* storage blocked: nothing to clear */
  }
}
