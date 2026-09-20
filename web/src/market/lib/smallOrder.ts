/**
 * The merchant's "yes, send it as a small order request" for this browser session.
 *
 * Under the wholesale minimum (mode `request`) the Restock page asks once, in SmallOrderSheet,
 * before checkout (checkout itself decides "Small order request" from the quote, and clears the
 * acknowledgement once the order is placed). Session-scoped on purpose: a new visit asks again.
 * Storage can be blocked (private mode, previews) — then the helpers simply forget, they never throw.
 */

const KEY = 'yq-small-ack'

export function ackSmallOrder(): void {
  try {
    sessionStorage.setItem(KEY, '1')
  } catch {
    /* storage blocked: the sheet asks again next time */
  }
}

export function clearSmallAck(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* storage blocked: nothing to clear */
  }
}
