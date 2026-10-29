import { request } from '@/lib/shopApi'

/** Transport for YQ Assistant. Same idiom as lib/marketApi: no auth header, JSON in and out. */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export type AskIntent = 'find' | 'fit' | 'add' | 'order_status' | 'usual' | 'handoff' | 'feedback' | 'chat' | 'list' | 'photo'

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
