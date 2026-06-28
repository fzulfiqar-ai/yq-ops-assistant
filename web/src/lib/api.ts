import { supabase } from './supabase'

const BASE = (import.meta.env.VITE_API_URL as string) || ''

export class ApiError extends Error {
  status: number
  body: string
  constructor(status: number, body: string) {
    super(`API ${status}: ${body.slice(0, 200)}`)
    this.status = status
    this.body = body
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => ''))
  const ct = res.headers.get('content-type') || ''
  return (ct.includes('application/json') ? res.json() : res.text()) as Promise<T>
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { ...(await authHeaders()) } })
  return handle<T>(res)
}

export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return handle<T>(res)
}

export const apiPost = <T,>(path: string, body?: unknown) => apiSend<T>('POST', path, body)
export const apiPatch = <T,>(path: string, body?: unknown) => apiSend<T>('PATCH', path, body)
export const apiDelete = <T,>(path: string) => apiSend<T>('DELETE', path)

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  // Do NOT set Content-Type — the browser adds the multipart boundary.
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers: { ...(await authHeaders()) }, body: form })
  return handle<T>(res)
}

/** POST JSON and download the binary response as a file (used to export the order .xlsx). */
export async function apiDownload(path: string, body?: unknown, fallbackName = 'download'): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => ''))
  const blob = await res.blob()
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="?([^"]+)"?/)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = m ? m[1] : fallbackName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/**
 * Stream Server-Sent-Events-ish text from the backend (used by /ask/stream).
 * Calls onChunk for each decoded text fragment. Bearer auth is attached.
 */
export async function apiStream(
  path: string,
  body: unknown,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw new ApiError(res.status, await res.text().catch(() => ''))
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    onChunk(decoder.decode(value, { stream: true }))
  }
}

export { BASE as API_BASE }
