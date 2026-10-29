import { useLayoutEffect } from 'react'
import { BOOT_ID } from './frames'

/**
 * The served first frame (#yq-boot, src/boot/frames.ts) and the app agreeing on it.
 *
 * The node stays above the app until a screen that paints the same picture takes over: the screen
 * calls useReleaseBoot(), which removes the node in a layout effect — after React has put that screen
 * in the page and before the browser paints it, so no frame ever shows neither. The screens that do:
 * the intro overlay (components/ui/arc-preloader-hero.tsx, the same night), the login (pages/Login.tsx,
 * the same night) and every screen of a protected page (components/guards.tsx: the shell's outline while
 * it waits, then the shell). Anything else is covered by the node's own rules: /invite's flat night sits
 * BEHIND the app, the public pages get no node, and the node hides itself after 10 s.
 */
export function releaseBoot(): void {
  document.getElementById(BOOT_ID)?.remove()
}

export function useReleaseBoot(): void {
  useLayoutEffect(releaseBoot, [])
}

export type BootChoice = 'off' | 'plain' | 'night' | 'shell' | 'sales'

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/** A session supabase-js persisted (lib/supabase.ts storedSession(): sb-<ref>-auth-token with an access token). */
export function hasStoredSession(): boolean {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (!key || !/^sb-.+-auth-token$/.test(key)) continue
      const raw = JSON.parse(localStorage.getItem(key) || 'null')
      const s = raw && (raw.currentSession || raw)
      if (s && s.access_token) return true
    }
  } catch {
    /* blocked storage or a corrupt value */
  }
  return false
}

/**
 * Which frame a page load of `path` gets — the decision src/boot/portal-boot.js makes before the first
 * paint (MIRRORED there in ES5; web/scripts/portal_boot_test.mjs holds the two to the same table).
 */
export function bootKindFor(path: string): BootChoice {
  if (/^\/(c|o|f|optout)\/[^/]/.test(path)) return 'off'
  if (/^\/invite\/?$/.test(path)) return 'plain'
  if (/^\/login\/?$/.test(path) || !hasStoredSession()) return 'night'
  return stored('yq-role') === 'salesman' ? 'sales' : 'shell'
}

/** The outline a signed-in page shows while its user's /me is on the way: the field app's for a remembered salesman. */
export function shellKind(): 'shell' | 'sales' {
  return stored('yq-role') === 'salesman' ? 'sales' : 'shell'
}

/** The rail width the outline draws (the served frame read it at load; a toggle since then is honoured). */
export function syncRail(kind: 'shell' | 'sales'): void {
  const narrow = stored(kind === 'sales' ? 'yq-sales-rail' : 'yq-collapsed') === '1'
  if (narrow) document.documentElement.setAttribute('data-boot-rail', 'narrow')
  else document.documentElement.removeAttribute('data-boot-rail')
}
