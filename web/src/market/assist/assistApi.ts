import { API_BASE, ShopApiError, request } from '@/lib/shopApi'

/** Transport for YQ Assistant. Same idiom as lib/marketApi: no auth header, JSON in and out. */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export type AskIntent = 'find' | 'fit' | 'add' | 'order_status' | 'usual' | 'handoff' | 'feedback' | 'chat' | 'list' | 'photo'
  | 'top' | 'deals' | 'plan' | 'ticket' | 'ticket_status' | 'policy' | 'account'
  // 2.1: a question asked back, a suggested card, the opening line; 2.2: the bubble's line, how YQ helps a shop
  // sell more, a price question
  | 'clarify' | 'show' | 'hello' | 'nudge' | 'advice' | 'price'

/** v2: the chat's tiles, answered from data with no model call (app/market_assist.py ACTIONS); 2.1 adds the phone
 * question, a card by its code, catalogue words and the opening line; 2.2 the bubble's line and the sales help */
export type AskAction = 'top' | 'deals' | 'plan' | 'usual' | 'orders' | 'handoff' | 'ticket' | 'tickets'
  | 'fit' | 'show' | 'find' | 'hello' | 'nudge' | 'advice'

/** 2.2: a chip the page answers itself, never sent (app/market_assist.py PAGE_ACTIONS): "Tell me when it's back" on a
 * sold-out card, the market's own restock request */
export type PageAction = 'notify'

/** 2.1: a quick reply under an answer: `say` is sent as the merchant's words; `action` (+ `q`) is answered from data */
export interface AskNext {
  label: string
  say?: string
  action?: AskAction | PageAction
  q?: string
}

export interface AskHistory {
  role: 'user' | 'assistant'
  text: string
}

export interface AskBody {
  message: string
  history: AskHistory[]
  lang: 'en' | 'ar'
  device_id: string | null
  session_id: string | null
  item_code: string | null
  order_tokens: string[]
  referral_code: string | null
  /** v2: a tile tapped (the message is its label) */
  action?: AskAction
  /** 2.1: what a chip narrows its action to (a category, catalogue words, a card's code) */
  q?: string
  /** v2: the turn ids on screen; the server reads the chat so far from its own log */
  history_ids?: number[]
  /** 2.2: the reach-out lines this browser saw lately (a new one each visit) */
  seen?: string[]
  /** 2.2: the line whose tap opened this turn (logged with it) */
  nudge?: string
}

export interface AskAddLine {
  item_code: string
  qty: number
}

export interface AskOrder {
  order_no: string
  token: string
  status: string
  status_label: string
  created_at: string | null
  items: number
  units: number
  total_bhd: number | null
  salesman: string | null
}

export interface AskHandoff {
  name: string | null
  whatsapp_url: string | null
}

export type LineStatus = 'matched' | 'pick' | 'missing'

/** One line of an order list: what the merchant wrote, and what the catalog made of it. */
export interface AskLine {
  text: string
  qty: number | null
  item_code: string | null
  /** 0-2 other codes the merchant can swap in */
  alternatives: string[]
  status: LineStatus
  /** v2, a plan's line: why it is there (usual, selling, deal, minimum) */
  why?: string
}

/** v2: why a card is shown (the card itself shows its badges) */
export interface AskCard {
  code: string
  why: string
}

export interface AskPlan {
  usual: number
  selling: number
  deal: number
  /** 2.2: the plan's clearing lines (the normal trade price, never called an offer); absent on an older API */
  clearance?: number
  minimum: boolean
  budget: number | null
}

export interface AskTicketCard {
  issue: string
  issues: string[]
  orders: AskOrder[]
}

export interface AskTicketRow {
  ref: string
  kind: string
  status: string
  created_at: string
  updated_at?: string | null
}

export interface AskResponse {
  intent: AskIntent
  /** plain text, never contains prices */
  reply: string
  items: string[]
  add: AskAddLine[]
  orders: AskOrder[]
  /** an order list read from a paste, a voice note or a photo (often empty) */
  lines?: AskLine[]
  handoff: AskHandoff | null
  /** true = AI busy/off: the client runs its own search */
  fallback: boolean
  /** nothing found for a product ask */
  miss: boolean
  turn_id: number | null
  // v2 (absent on an older API)
  cards?: AskCard[]
  /** a sold-out card's in-stock alternatives */
  alts?: Record<string, string[]>
  plan?: AskPlan | null
  ticket?: AskTicketCard | null
  tickets?: AskTicketRow[]
  policy?: { topic: string; official: boolean } | null
  /** offer "Ask for a call back" */
  callback?: boolean
  /** the rep was brought in */
  escalated?: boolean
  /** 2.1: the quick replies (absent on an older API) */
  next?: AskNext[]
  /** 2.2: the opening line's reason (reach_hooks' id), remembered so the next visit gets another */
  hook?: string
}

export type AskStage = 'reading' | 'checking' | 'writing'

export interface StreamHandlers {
  onStage?: (key: AskStage) => void
  /** the products, before the sentence is written */
  onCards?: (partial: AskResponse) => void
}

/**
 * v2: the same turn, streamed (Accept: text/event-stream). Stages and the cards arrive as they are known,
 * then the result. An API that answers JSON (older, or the QA harness's mock) is read as one result. A stream
 * that breaks before its result is asked again once as plain JSON.
 */
export async function postAskStream(body: AskBody, h: StreamHandlers, signal?: AbortSignal): Promise<AskResponse> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 60000)
  const onAbort = () => ctrl.abort()
  signal?.addEventListener('abort', onAbort)
  try {
    const res = await fetch(`${API_BASE}/public/market/ask`, {
      method: 'POST',
      headers: { ...JSON_HEADERS, Accept: 'text/event-stream, application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      let detail = ''
      try {
        detail = String((JSON.parse(text) as { detail?: unknown }).detail || '')
      } catch {
        detail = text.slice(0, 200)
      }
      throw new ShopApiError(res.status, detail)
    }
    if (!(res.headers.get('content-type') || '').includes('text/event-stream') || !res.body) {
      return (await res.json()) as AskResponse
    }
    const reader = res.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    let result: AskResponse | null = null
    for (;;) {
      const { done, value } = await reader.read()
      if (value) buf += dec.decode(value, { stream: true })
      let cut = buf.indexOf('\n\n')
      while (cut >= 0) {
        const block = buf.slice(0, cut)
        buf = buf.slice(cut + 2)
        cut = buf.indexOf('\n\n')
        let kind = ''
        let data = ''
        for (const line of block.split('\n')) {
          if (line.startsWith('event: ')) kind = line.slice(7).trim()
          else if (line.startsWith('data: ')) data += line.slice(6)
        }
        if (!kind || !data) continue
        try {
          const payload = JSON.parse(data)
          if (kind === 'stage') h.onStage?.(payload.key as AskStage)
          else if (kind === 'cards') h.onCards?.(payload as AskResponse)
          else if (kind === 'result') result = payload as AskResponse
        } catch {
          /* a broken event is skipped */
        }
      }
      if (done) break
    }
    if (result) return result
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
    return postAsk(body, signal)            // the stream broke before its result: once more, plainly
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}

/** v2: the ticket card's one request (app/market_support.py create_ticket). */
export interface TicketBody {
  kind: string
  order_token?: string | null
  lines: { index?: number | null; item_code: string; qty: number }[]
  note?: string
  photos?: string[]
  client_ticket_id: string
  device_id: string | null
  session_id: string | null
  turn_id?: number | null
  referral_code: string | null
  lang: 'en' | 'ar'
}

export interface TicketReceipt {
  ok: boolean
  ref: string
  kind: string
  status: string
  rep: AskHandoff | null
  repeat?: boolean
}

export function postTicket(body: TicketBody): Promise<TicketReceipt> {
  return request<TicketReceipt>(`/public/market/ticket`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
}

export type FeedbackKind = 'idea' | 'problem' | 'up' | 'down' | 'question'

export interface FeedbackBody {
  kind: FeedbackKind
  text?: string
  turn_id: number | null
  device_id: string | null
  session_id: string | null
  lang: 'en' | 'ar'
  page: string
}

export function postAsk(body: AskBody, signal?: AbortSignal): Promise<AskResponse> {
  return request<AskResponse>(`/public/market/ask`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }, signal)
}

export function postFeedback(body: FeedbackBody): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/public/market/feedback`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
}

/** The route template of the current page (never the real code or token): what feedback is tagged with. */
export function pageTemplate(pathname: string): string {
  const seg = pathname.split('/').filter(Boolean)
  if (!seg.length) return '/'
  const head = seg[0]
  const dyn: Record<string, string> = { p: '/p/:code', t: '/t/:category', o: '/o/:token', brands: '/brands/:brand', c: '/c/:token' }
  if (dyn[head]) return dyn[head]
  const fixed = new Set(['shop', 'search', 'quick', 'about', 'cart', 'checkout', 'orders', 'me', 'ask', 'saved'])
  return (fixed.has(head) ? `/${head}` : '/:slug').slice(0, 80)
}

export interface VoiceResult {
  text: string
  busy: boolean
}

/** One recorded clip (max ~2 MB). The device id header is what the server's rate limiter reads. */
export function postVoice(blob: Blob, deviceId: string, filename: string, signal?: AbortSignal): Promise<VoiceResult> {
  const fd = new FormData()
  fd.append('file', blob, filename)
  return request<VoiceResult>(`/public/market/voice`, { method: 'POST', headers: { 'x-device-id': deviceId }, body: fd }, signal)
}

export interface PhotoBody {
  image: string
  lang: 'en' | 'ar'
  device_id: string | null
  session_id: string | null
  referral_code: string | null
}

export function postPhoto(body: PhotoBody, signal?: AbortSignal): Promise<AskResponse> {
  return request<AskResponse>(`/public/market/photo`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }, signal)
}
