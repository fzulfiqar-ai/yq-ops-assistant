import { API_BASE } from '@/lib/shopApi'
import { locale } from '../i18n'
import { currentRef, deviceId, rememberedOrders } from '../lib/device'
import type { AskAction } from './assistApi'

/**
 * The launcher's tiny, always-loaded half: whether the chat was open in this tab, and the one-line
 * way for any page to open it: openAssist({ itemCode, message }). Nothing here pulls in the chat.
 * 2.2: the bubble's line from the server, and the lines this browser saw lately.
 */

/** What the chat sends once it is open: words, or (the launcher's line) an action with what it narrows to, and the
 * line whose tap it was (2.2: logged with the turn, so which line sells can be counted). */
export interface FirstMessage {
  id: number
  text: string
  action?: AskAction
  q?: string
  nudge?: string
}

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

/* ───────────────────────── 2.1: the line beside the button, once per visit ───────────────────────── */

const PEEK_KEY = 'yq-assist-peek' // this tab: shown already, or the chat was opened
const PEEK_OFF = 'yq-assist-peek-off' // this browser: dismissed at (ms)
const PEEK_REST_MS = 24 * 3600 * 1000

/** Whether the line may show: not yet this visit, not dismissed in the last day. Blocked storage = never. */
export function peekAllowed(): boolean {
  try {
    if (sessionStorage.getItem(PEEK_KEY) === '1') return false
    const off = Number(localStorage.getItem(PEEK_OFF) || 0)
    return !(off > 0 && Date.now() - off < PEEK_REST_MS)
  } catch {
    return false
  }
}

/** Shown once, or the chat was opened: not again this visit. */
export function peekSpent(): void {
  try {
    sessionStorage.setItem(PEEK_KEY, '1')
  } catch {
    /* ignore */
  }
}

/** "Not now": not for a day. */
export function peekDismissed(): void {
  peekSpent()
  try {
    localStorage.setItem(PEEK_OFF, String(Date.now()))
  } catch {
    /* ignore */
  }
}

/* ───────────────────────── 2.2: a new line each visit, from the server ───────────────────────── */

const SEEN_KEY = 'yq-assist-seen' // this browser: the reach-out lines shown lately (the bubble's and the chat's first)
const SEEN_MAX = 4
const NUDGE_WAIT_MS = 2000

/** The ids of the last few lines shown here, oldest first. Blocked storage = none (the strongest line again). */
export function seenLines(): string[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]')
    return Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string' && s.length <= 24).slice(-SEEN_MAX) : []
  } catch {
    return []
  }
}

export function noteSeen(id: string | undefined): void {
  if (!id) return
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify([...seenLines().filter((s) => s !== id), id].slice(-SEEN_MAX)))
  } catch {
    /* ignore */
  }
}

/** The bubble's line: the strongest honest reason this phone has not seen lately (app/market_assist.py reach_hooks)
 * and the one thing a tap opens. From data on the server (no model), so it is quick; 2 s or the static line. */
export interface Nudge {
  text: string
  hook: string
  label: string
  action: AskAction
  q?: string
}

export async function fetchNudge(itemCode: string | null): Promise<Nudge | null> {
  const ctrl = new AbortController()
  const timer = window.setTimeout(() => ctrl.abort(), NUDGE_WAIT_MS)
  try {
    const res = await fetch(`${API_BASE}/public/market/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: 'hello',
        action: 'nudge',
        lang: locale.lang,
        device_id: deviceId(),
        session_id: null,
        item_code: itemCode,
        order_tokens: rememberedOrders()
          .map((o) => o.token)
          .slice(0, 20),
        referral_code: currentRef(),
        history: [],
        seen: seenLines(),
      }),
      signal: ctrl.signal,
    })
    if (!res.ok) return null
    const d = (await res.json()) as { reply?: unknown; hook?: unknown; next?: { label?: unknown; action?: unknown; q?: unknown }[] }
    const tap = Array.isArray(d.next) ? d.next[0] : undefined
    if (typeof d.reply !== 'string' || !d.reply || typeof d.hook !== 'string' || !tap || typeof tap.action !== 'string' || typeof tap.label !== 'string') return null
    return { text: d.reply.slice(0, 160), hook: d.hook.slice(0, 24), label: tap.label.slice(0, 80), action: tap.action as AskAction, q: typeof tap.q === 'string' ? tap.q.slice(0, 60) : undefined }
  } catch {
    return null
  } finally {
    window.clearTimeout(timer)
  }
}
