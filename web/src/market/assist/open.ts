/**
 * The launcher's tiny, always-loaded half: whether the chat was open in this tab, and the one-line
 * way for any page to open it: openAssist({ itemCode, message }). Nothing here pulls in the chat.
 */

const OPEN_KEY = 'yq-assist-open'

export function wasOpen(): boolean {
  try {
    return sessionStorage.getItem(OPEN_KEY) === '1'
  } catch {
    return false
  }
}

export function rememberOpen(on: boolean): void {
  try {
    if (on) sessionStorage.setItem(OPEN_KEY, '1')
    else sessionStorage.removeItem(OPEN_KEY)
  } catch {
    /* ignore */
  }
}

/* ───────────────────────── opening the chat from anywhere ───────────────────────── */

export const OPEN_EVENT = 'yq-assist-open'

export interface OpenOptions {
  /** the product the chat is about (sent as item_code) */
  itemCode?: string
  /** sent as the first message as soon as the panel is open (once) */
  message?: string
}

/** Open the assistant. A no-op when the assistant is off (nothing listens). */
export function openAssist(opts: OpenOptions = {}): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<OpenOptions>(OPEN_EVENT, { detail: opts }))
}

/* ───────────────────────── an answer that arrived while the chat was closed (v2) ───────────────────────── */

export const READY_EVENT = 'yq-assist-ready'

/** The chat (lazy chunk) tells the launcher an answer is waiting; the launcher shows a dot until opened. */
export function announceReady(): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new Event(READY_EVENT))
}
