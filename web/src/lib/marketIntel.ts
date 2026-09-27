import { useSyncExternalStore } from 'react'
import { ApiError, apiUpload } from './api'
import { isNetworkError } from './errorText'

/**
 * Market Intelligence (release R7b, app/market_intel.py): the shapes the API answers, the photo
 * resize the capture sheet runs on the phone, and the in-page retry queue.
 *
 * The queue keeps a capture that could not reach the server (weak signal in a shop, the API
 * waking up) in memory with its client UUID and retries it while the app is open. The server
 * treats the UUID as the capture's identity, so a retry that lands twice is still one sighting.
 * True offline (a service worker) is later (plan §18.1): closing the tab loses what is queued,
 * and the sheet says so.
 */

export type MiKind = 'new_product' | 'competitor_price' | 'promotion' | 'shop_asked' | 'complaint' | 'other'
export type MiResult = 'already_in_yq' | 'seen_before' | 'new_find' | 'saved_for_review'
export type MiStatus = 'new' | 'researching' | 'opportunity' | 'approved' | 'rejected' | 'merged'
export type MiAction = 'source' | 'price_response' | 'promo' | 'ignore'

export const KINDS: { value: MiKind; label: string }[] = [
  { value: 'new_product', label: 'New product' },
  { value: 'competitor_price', label: 'Competitor price' },
  { value: 'promotion', label: 'Promotion' },
  { value: 'shop_asked', label: 'Shop asked for' },
  { value: 'complaint', label: 'Complaint' },
  { value: 'other', label: 'Other' },
]
export const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.value, k.label]))

export const STATUS_LABEL: Record<MiStatus, string> = {
  new: 'New',
  researching: 'Researching',
  opportunity: 'Opportunity',
  approved: 'Approved',
  rejected: 'Rejected',
  merged: 'Merged',
}
export const ACTIONS: { value: MiAction; label: string }[] = [
  { value: 'source', label: 'Source it' },
  { value: 'price_response', label: 'Price response' },
  { value: 'promo', label: 'Run a promotion' },
  { value: 'ignore', label: 'Ignore' },
]
export const DEMAND_LEVELS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
] as const

export interface MiCaps {
  office: boolean
  can_review: boolean
  can_approve: boolean
  can_capture: boolean
}

export interface MiYqItem {
  item_code: string
  display_name: string
  category?: string | null
  brand?: string | null
  thumb_url?: string | null
}

export interface MiCard {
  ok: boolean
  result: MiResult
  message: string
  observation_id: number | null
  client_uuid: string
  item: { id: number; title?: string | null; kind: MiKind; kind_label: string; status: MiStatus; status_label: string; observations: number; shops: number; reps: number } | null
  yq_item: MiYqItem | null
  near_duplicate: { observation_id: number } | null
  photos: number
  replayed: boolean
  /** 'legacy' = saved to the old Finds / Field Notes tables (the R7b migration is not applied yet) */
  fallback?: string
}

export interface MiCluster {
  item_id: number
  kind: MiKind
  kind_label: string
  title?: string | null
  brand?: string | null
  competitor?: string | null
  category?: string | null
  barcode?: string | null
  yq_item_code?: string | null
  status: MiStatus
  status_label: string
  action?: MiAction | null
  action_label?: string | null
  observations: number
  shops: number
  reps: number
  price_min?: string | null
  price_max?: string | null
  first_seen?: string | null
  last_seen?: string | null
  photos: number
  cover_url?: string | null
  system_signals: number
  demand_qty?: number | null
  summary: string
  mine?: boolean
  near_duplicates?: number
  merged_into?: number | null
  has_ai_suggestion?: boolean
  ai_confidence?: number | null
  verified?: boolean
  photos_people?: number
}

export interface MiFacets {
  kinds?: { value: MiKind; label: string }[]
  statuses?: { value: MiStatus; label: string; count: number }[]
  actions?: { value: MiAction; label: string }[]
  categories?: string[]
  brands?: string[]
  demand_levels?: string[]
  reps?: { id: number; name: string }[]
  areas?: string[]
}

export interface MiBoard {
  available: boolean
  hint?: string
  items: MiCluster[]
  count?: number
  facets: MiFacets
  queue: { new_items: number; unassigned: number; library: number } | null
  capabilities: MiCaps
}

export interface MiPhoto {
  id: number
  url?: string | null
  width?: number | null
  height?: number | null
  has_people: boolean
}

export interface MiObservation {
  id: number
  kind: MiKind
  kind_label: string
  source: 'rep' | 'import' | 'system'
  signal?: string | null
  title?: string | null
  note?: string | null
  brand?: string | null
  competitor?: string | null
  category?: string | null
  price_bhd?: string | null
  demand_level?: string | null
  demand_qty?: number | null
  barcode?: string | null
  yq_item_code?: string | null
  result?: MiResult | null
  observed_at?: string | null
  area?: string | null
  shop_name?: string | null
  item_id?: number | null
  mine: boolean
  near_duplicate_of?: number | null
  photos: MiPhoto[]
  by?: string
  item_title?: string | null
  item_status?: MiStatus | null
  status_label?: string | null
  item_shops?: number
}

export interface MiDecision {
  id: number
  event: 'status' | 'edit' | 'identify' | 'merge' | 'approve'
  from_status?: MiStatus | null
  to_status?: MiStatus | null
  action?: MiAction | null
  action_label?: string | null
  reason?: string | null
  actor: string
  actor_role?: string | null
  detail?: Record<string, unknown> | null
  created_at?: string | null
}

export interface MiItemDetail {
  available: boolean
  item: {
    id: number
    kind: MiKind
    kind_label: string
    title?: string | null
    brand?: string | null
    competitor?: string | null
    category?: string | null
    barcode?: string | null
    yq_item_code?: string | null
    status: MiStatus
    status_label: string
    action?: MiAction | null
    action_label?: string | null
    merged_into?: number | null
    next_statuses: MiStatus[]
  }
  cluster: MiCluster | null
  yq_item: MiYqItem | null
  gap: boolean
  observations: MiObservation[]
  shops?: { shop: string; area?: string | null; sightings: number }[]
  ai_suggestion?: { suggestion: Record<string, unknown>; confidence?: number | null; verified: boolean; label: string } | null
  decisions?: MiDecision[]
  capabilities: MiCaps
}

export interface MiReview {
  available: boolean
  hint?: string
  new_items: MiCluster[]
  unassigned: MiObservation[]
  count: number
  offset?: number
  limit?: number
  capabilities: MiCaps
}

export interface MiMine {
  available: boolean
  hint?: string
  observations: MiObservation[]
  capabilities: MiCaps
}

/* ───────────────────────── small helpers ───────────────────────── */

export function newUuid(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch {
    /* fall through */
  }
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

/** "2.500" / "2.500–3.000" with the currency, from the API's 3-dp strings. */
export function priceRange(min?: string | null, max?: string | null): string | null {
  if (!min) return null
  return max && max !== min ? `BHD ${min}–${max}` : `BHD ${min}`
}

export function shortDate(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

/* ───────────────────────── photo resize (on the phone) ───────────────────────── */

export const MAX_PHOTOS = 4
const MAX_EDGE = 1600
const QUALITY = 0.75

export interface ReadyPhoto {
  blob: Blob
  name: string
  url: string
}

async function decode(file: File): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file, { imageOrientation: 'from-image' })
    } catch {
      /* fall back to an <img> decode below */
    }
  }
  const url = URL.createObjectURL(file)
  try {
    const img = new Image()
    img.decoding = 'async'
    img.src = url
    await img.decode()
    return img
  } finally {
    URL.revokeObjectURL(url)
  }
}

function toBlob(canvas: HTMLCanvasElement, type: string): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), type, QUALITY))
}

/**
 * Longest side 1600 px, WebP (JPEG where the browser cannot write WebP), quality 0.75: about
 * 300 KB. Drawing onto a canvas keeps pixels only, so the phone's EXIF (GPS, device) never leaves
 * it; the server strips again anyway. A photo the browser cannot decode (HEIC outside Safari) is
 * sent as it is when it is a JPEG / PNG / WEBP, and refused with a plain reason otherwise.
 */
export async function preparePhoto(file: File, index: number): Promise<ReadyPhoto> {
  let src: ImageBitmap | HTMLImageElement
  try {
    src = await decode(file)
  } catch {
    if (/^image\/(jpeg|png|webp)$/.test(file.type)) {
      const ext = file.type === 'image/png' ? 'png' : file.type === 'image/webp' ? 'webp' : 'jpg'
      return { blob: file, name: `spotted-${index + 1}.${ext}`, url: URL.createObjectURL(file) }
    }
    throw new Error("This photo type can't be read here. Take it with the camera button instead.")
  }
  const w = 'naturalWidth' in src ? src.naturalWidth : src.width
  const h = 'naturalHeight' in src ? src.naturalHeight : src.height
  const scale = Math.min(1, MAX_EDGE / Math.max(w, h, 1))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(w * scale))
  canvas.height = Math.max(1, Math.round(h * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Could not prepare the photo on this phone.')
  ctx.drawImage(src, 0, 0, canvas.width, canvas.height)
  if ('close' in src && typeof src.close === 'function') src.close()
  let blob = await toBlob(canvas, 'image/webp')
  let ext = 'webp'
  if (!blob || blob.type !== 'image/webp') {
    blob = await toBlob(canvas, 'image/jpeg')
    ext = 'jpg'
  }
  if (!blob) throw new Error('Could not prepare the photo on this phone.')
  return { blob, name: `spotted-${index + 1}.${ext}`, url: URL.createObjectURL(blob) }
}

/* ───────────────────────── the in-page retry queue ───────────────────────── */

export interface CaptureDraft {
  client_uuid: string
  fields: Record<string, string>
  photos: ReadyPhoto[]
}

export interface QueuedCapture extends CaptureDraft {
  state: 'sending' | 'waiting' | 'failed'
  error?: string
  tries: number
}

const RETRY_MS = 20_000
let queue: QueuedCapture[] = []
const listeners = new Set<() => void>()
let timer: number | undefined

function emit() {
  queue = [...queue]
  listeners.forEach((fn) => fn())
}

function patch(id: string, p: Partial<QueuedCapture>) {
  queue = queue.map((q) => (q.client_uuid === id ? { ...q, ...p } : q))
  emit()
}

function drop(id: string, revoke = true) {
  const gone = queue.find((q) => q.client_uuid === id)
  if (revoke) gone?.photos.forEach((p) => URL.revokeObjectURL(p.url))
  queue = queue.filter((q) => q.client_uuid !== id)
  emit()
}

/** Worth retrying: no answer at all, a timeout, or the server / its host being unwell. */
function retryable(e: unknown): boolean {
  if (isNetworkError(e)) return true
  if (e && typeof e === 'object' && (e as { name?: unknown }).name === 'AbortError') return true
  return e instanceof ApiError && (e.status >= 500 || e.status === 408 || e.status === 429)
}

async function send(d: CaptureDraft): Promise<MiCard> {
  const form = new FormData()
  form.append('client_uuid', d.client_uuid)
  for (const [k, v] of Object.entries(d.fields)) if (v !== '') form.append(k, v)
  d.photos.forEach((p) => form.append('photos', p.blob, p.name))
  return apiUpload<MiCard>('/market-intel/observations', form)
}

function schedule() {
  if (timer !== undefined || typeof window === 'undefined') return
  timer = window.setTimeout(() => {
    timer = undefined
    void retryWaiting()
  }, RETRY_MS)
}

async function attempt(id: string): Promise<MiCard | null> {
  const q = queue.find((x) => x.client_uuid === id)
  if (!q) return null
  patch(id, { state: 'sending', error: undefined, tries: q.tries + 1 })
  try {
    const card = await send(q)
    drop(id)
    return card
  } catch (e) {
    if (retryable(e)) {
      patch(id, { state: 'waiting', error: 'No connection. Kept on this phone; trying again.' })
      schedule()
      return null
    }
    let msg = 'The server refused this sighting.'
    if (e instanceof ApiError) {
      try {
        const j = JSON.parse(e.body) as { detail?: unknown }
        if (typeof j.detail === 'string') msg = j.detail
      } catch {
        /* keep the plain line */
      }
    }
    patch(id, { state: 'failed', error: msg })
    throw new Error(msg, { cause: e })
  }
}

/**
 * Send one capture. Resolves with the result card, or with null when the capture was queued (no
 * connection: it is retried every 20 s and when the phone comes back online, while the app is
 * open). Rejects with the server's reason for a capture it refused (a bad price, a bad photo).
 */
export async function submitCapture(d: CaptureDraft): Promise<MiCard | null> {
  queue = [...queue.filter((q) => q.client_uuid !== d.client_uuid), { ...d, state: 'sending', tries: 0 }]
  emit()
  return attempt(d.client_uuid)
}

export async function retryWaiting(): Promise<void> {
  for (const q of queue.filter((x) => x.state === 'waiting')) {
    try {
      await attempt(q.client_uuid)
    } catch {
      /* marked failed; the sheet shows it */
    }
  }
}

/** Forget a queued capture. `keepPhotos`: the sheet still shows its photos (a refused save it will fix). */
export function discardCapture(id: string, keepPhotos = false) {
  drop(id, !keepPhotos)
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => void retryWaiting())
}

export function useCaptureQueue(): QueuedCapture[] {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => queue,
    () => queue,
  )
}
