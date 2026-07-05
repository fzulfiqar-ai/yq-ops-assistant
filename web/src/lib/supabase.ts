import { createClient, type Session } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL as string
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY as string

if (!url || !anon) {
  // Surface a clear console hint during local dev if env is missing.
  console.warn('[YQ] Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY — login will not work.')
}

export const supabase = createClient(url, anon, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: false,
  },
})

/** The session supabase-js persisted to localStorage (sb-<ref>-auth-token). */
function storedSession(): Session | null {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (!k || !/^sb-.+-auth-token$/.test(k)) continue
      const raw = localStorage.getItem(k)
      if (!raw) continue
      const obj = JSON.parse(raw)
      const s = obj?.currentSession ?? obj // older vs current storage shape
      if (s?.access_token) return s as Session
    }
  } catch { /* private mode / corrupt json */ }
  return null
}

/**
 * getSession that CANNOT hang. supabase-js's getSession() awaits a cross-tab
 * navigator.locks mutex that is known to deadlock (esp. with many open tabs) —
 * which froze the app on its loading splash until a refresh. Race it against a
 * short timer and fall back to the persisted session from localStorage.
 */
export async function getSessionSafe(timeoutMs = 3000): Promise<Session | null> {
  const viaSdk = supabase.auth.getSession().then(({ data }) => data.session)
  const viaTimeout = new Promise<Session | null>((resolve) =>
    setTimeout(() => resolve(storedSession()), timeoutMs))
  try {
    return await Promise.race([viaSdk, viaTimeout])
  } catch {
    return storedSession()
  }
}
