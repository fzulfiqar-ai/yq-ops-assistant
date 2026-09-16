/**
 * The marketplace service worker's minder (plan §Y).
 *
 * WHY THIS EXISTS. The portal once shipped a service worker that kept serving a bundle whose
 * baked-in API host had died; a hard refresh does not evict a controlling worker, so every
 * merchant with that cache was stuck. The rule here: the app itself decides whether a worker
 * may exist, from a file the worker can never serve.
 *
 *  • `/version.json` is fetched with `cache: 'no-store'` and is NetworkOnly for the worker, so it
 *    always reflects the deployment that is live right now: `{ build, kill }`.
 *  • The file is read BEFORE registering. `kill: true` (deploy with VITE_SW_KILL=1) means: no
 *    worker is registered, any existing worker is unregistered, every cache is dropped, and the
 *    page reloads once (a session flag stops it looping). Recovery needs nothing from the merchant.
 *  • `build !== __BUILD_ID__` → ask the worker to update and show "New version ready" (no silent
 *    reload while a merchant is filling a cart).
 *  • Re-checked when the tab becomes visible again and every 30 minutes.
 */
import { registerSW } from 'virtual:pwa-register'

const CHECK_MS = 30 * 60 * 1000
const NUKED_FLAG = 'yq-sw-nuked'
const listeners = new Set<(needRefresh: boolean) => void>()
let needRefresh = false
let update: ((reload?: boolean) => Promise<void>) | null = null
let started = false
let registered = false

interface Version {
  build?: string
  kill?: boolean
}

export function onSwState(fn: (needRefresh: boolean) => void): () => void {
  listeners.add(fn)
  fn(needRefresh)
  return () => listeners.delete(fn)
}

function setNeed(v: boolean) {
  needRefresh = v
  listeners.forEach((fn) => fn(v))
}

export async function applyUpdate(): Promise<void> {
  if (update) await update(true)
  else window.location.reload()
}

async function fetchVersion(): Promise<Version | null> {
  try {
    const res = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' })
    if (!res.ok) return null
    return (await res.json()) as Version
  } catch {
    return null // offline — whatever is installed keeps serving
  }
}

/** Remove every worker and cache. Reloads at most once per session so a kill can never loop. */
async function nuke(): Promise<void> {
  let hadSomething = false
  try {
    const regs = await navigator.serviceWorker.getRegistrations()
    hadSomething = regs.length > 0
    await Promise.all(regs.map((r) => r.unregister()))
    const keys = await caches.keys()
    hadSomething = hadSomething || keys.length > 0
    await Promise.all(keys.map((k) => caches.delete(k)))
  } catch {
    /* best effort */
  }
  let already = false
  try {
    already = sessionStorage.getItem(NUKED_FLAG) === '1'
    sessionStorage.setItem(NUKED_FLAG, '1')
  } catch {
    /* ignore */
  }
  if (hadSomething && !already) window.location.reload()
}

function register(): void {
  if (registered) return
  registered = true
  update = registerSW({
    immediate: true,
    onNeedRefresh: () => setNeed(true),
    onRegisterError: () => {
      /* registration failures are silent — the app works without a worker */
    },
  })
}

async function check(): Promise<void> {
  const v = await fetchVersion()
  if (v?.kill) {
    await nuke()
    return
  }
  try {
    sessionStorage.removeItem(NUKED_FLAG)
  } catch {
    /* ignore */
  }
  if (!registered) {
    register()
    return
  }
  if (v?.build && v.build !== __BUILD_ID__) {
    const reg = await navigator.serviceWorker.getRegistration()
    await reg?.update()
    // an HTML-only change installs nothing new — a plain reload still brings the new build
    if (!reg?.waiting && !reg?.installing) setNeed(true)
  }
}

export function startSw(): void {
  if (started || typeof window === 'undefined' || !('serviceWorker' in navigator)) return
  started = true
  void check()
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void check()
  })
  window.setInterval(() => void check(), CHECK_MS)
}
