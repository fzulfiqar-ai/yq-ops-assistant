import type { AskAddLine, AskHandoff, AskIntent, AskOrder } from './assistApi'

/**
 * What the chat keeps for this tab (sessionStorage, key 'yq-assist'): the last 30 messages and the
 * session id. Nothing here is private data beyond what the merchant typed, and it dies with the tab.
 */

export type ErrorKind = 'off' | 'busy' | 'slow' | 'net'

export interface Msg {
  id: string
  role: 'user' | 'assistant'
  text: string
  /** assistant: the merchant's question this answers (the WhatsApp hand-off quotes it) */
  question?: string
  intent?: AskIntent
  items?: string[]
  add?: AskAddLine[]
  orders?: AskOrder[]
  handoff?: AskHandoff | null
  /** codes the client search found when the server said fallback */
  searchCodes?: string[]
  fallback?: boolean
  miss?: boolean
  turnId?: number | null
  vote?: 'up' | 'down'
  added?: boolean
  stocked?: boolean
  error?: ErrorKind
  /** error: the text to send again */
  retryText?: string
}

export interface Saved {
  sid: string
  msgs: Msg[]
  itemCode: string | null
}

const KEY = 'yq-assist'
export const MAX_MSGS = 30

function rid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID().replace(/-/g, '').slice(0, 24)
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`
}

export const newId = rid

export function load(): Saved {
  try {
    const raw = sessionStorage.getItem(KEY)
    const v = raw ? (JSON.parse(raw) as Partial<Saved>) : null
    if (v && typeof v.sid === 'string' && Array.isArray(v.msgs)) {
      return { sid: v.sid.slice(0, 64), msgs: v.msgs.slice(-MAX_MSGS), itemCode: typeof v.itemCode === 'string' ? v.itemCode : null }
    }
  } catch {
    /* storage blocked or garbled: start clean */
  }
  return { sid: rid(), msgs: [], itemCode: null }
}

export function save(s: Saved): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...s, msgs: s.msgs.slice(-MAX_MSGS) }))
  } catch {
    /* quota / private mode: the chat still works, it just forgets on reload */
  }
}
