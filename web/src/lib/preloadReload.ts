/**
 * A tab opened before a deploy still holds the old index.html, whose lazy chunks no longer exist
 * once the new build is live, so the next lazy import fails (Vite reports it as
 * `vite:preloadError`). One reload fetches the new index.html and its chunks.
 *
 * One reload per BUILD, not per session (28-Oct-2026). The tab remembers which build it already
 * reloaded from: a broken build fails again from the same build and stops (no loop), while the
 * next deploy is a different build and may reload too. The old once-per-session flag was never
 * cleared, so the second deploy a tab lived through left it on "Something went wrong" (the
 * portal) or a dead tap (the marketplace). Both entries use this: src/main.tsx, src/main.market.tsx.
 */
const KEY = 'yq-preload-reload'

/** the deployed bundle — the commit (vite.config.ts `define`; a local build gets its own id) */
declare const __BUILD_ID__: string
const BUILD = typeof __BUILD_ID__ !== 'undefined' ? __BUILD_ID__ : 'dev'

type TabStore = Pick<Storage, 'getItem' | 'setItem'>

/** true when this build has not reloaded this tab yet (and marks it); never with storage blocked */
export function shouldReload(build: string, store: TabStore | null): boolean {
  if (!store) return false
  const id = build || 'dev'
  try {
    if (store.getItem(KEY) === id) return false
    store.setItem(KEY, id)
    return true
  } catch {
    return false // blocked storage could not stop a loop, so it never reloads
  }
}

function tabStore(): TabStore | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function installPreloadReload(): void {
  window.addEventListener('vite:preloadError', (event) => {
    // offline the chunk is missing because there is no network, not because of a deploy: a reload
    // would land on the browser's "No internet" page. Let the page's own error boundary say so.
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return
    if (!shouldReload(BUILD, tabStore())) return
    event.preventDefault()
    window.location.reload()
  })
}
