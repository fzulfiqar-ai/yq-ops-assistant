import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase, getSessionSafe } from './supabase'
import { apiGet, ApiError, API_BASE } from './api'

export type Role = 'admin' | 'member' | 'salesman'

export interface Me {
  email: string
  role: Role
  features: string[]
  full_name?: string
}

/**
 * ok         = provisioned
 * denied     = server SAID 401/403 (a real permissions problem)
 * offline    = the API isn't deployed at all — the host edge answered 404
 *              ("Application not found"). Retrying can NEVER fix this.
 * unreachable = network error / 5xx / timeout — a genuine cold start, worth retrying
 */
export type MeState = 'ok' | 'denied' | 'offline' | 'unreachable' | 'unknown'

interface AuthState {
  session: Session | null
  me: Me | null
  meState: MeState
  /** Why the last /me attempt failed — surfaced in the UI so field issues are diagnosable. */
  meError: string | null
  loading: boolean
  signIn: (email: string, password: string) => Promise<{ ok: boolean; error?: string }>
  signOut: () => Promise<void>
  refreshMe: () => Promise<void>
}

const Ctx = createContext<AuthState | undefined>(undefined)

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const [meState, setMeState] = useState<MeState>('unknown')
  const [meError, setMeError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // A failed /me is USUALLY the API waking from a Railway cold start — NOT a permissions
  // problem. Retry with backoff (~35s total) before giving up, and record WHY it failed so
  // the UI can show "waking the server" instead of the misleading "No access yet".
  async function loadMe() {
    const delays = [0, 2000, 4000, 7000, 10000, 12000]
    for (let i = 0; i < delays.length; i++) {
      if (delays[i]) await sleep(delays[i])
      try {
        const m = await apiGet<Me>('/me')
        setMe(m)
        setMeState('ok')
        setMeError(null)
        return
      } catch (e) {
        // Record WHY, verbatim. A TypeError here means the browser refused to send the
        // request at all (CORS/blocked/DNS) — which looks identical to a cold start
        // from the user's side, and is invisible in server logs.
        const why = e instanceof ApiError
          ? `HTTP ${e.status}${e.body ? ` — ${e.body.slice(0, 120)}` : ''}`
          : `${(e as Error)?.name || 'Error'}: ${(e as Error)?.message || String(e)}`
        setMeError(`${why}  ·  attempt ${i + 1}/${delays.length}  ·  ${API_BASE || '(no API base)'}/me`)
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setMe(null)
          setMeState('denied')
          return
        }
        // 404 = the platform edge has no app behind this URL (service deleted, suspended,
        // or VITE_API_URL points nowhere). A cold start NEVER returns 404, so bail out
        // immediately instead of spinning on "Waking the server…" forever.
        if (e instanceof ApiError && e.status === 404) {
          setMe(null)
          setMeState('offline')
          return
        }
        // network error / 5xx / timeout → keep retrying (cold start)
      }
    }
    setMe(null)
    setMeState('unreachable')
  }

  useEffect(() => {
    let active = true
    // Hard safety net: never let the boot splash hang forever. If getSession or the
    // first /me stalls past 12s, stop the splash — the router then shows Login or the
    // "Waking the server…" screen (both recover on their own), never a frozen logo.
    const bootTimer = setTimeout(() => { if (active) setLoading(false) }, 12000)
    ;(async () => {
      const s = await getSessionSafe()   // cannot hang (races the supabase lock)
      if (!active) return
      setSession(s)
      if (s) await loadMe()
      if (active) { setLoading(false); clearTimeout(bootTimer) }
    })()
    const { data: sub } = supabase.auth.onAuthStateChange(async (_e, s) => {
      setSession(s)
      if (s) await loadMe()
      else { setMe(null); setMeState('unknown') }
    })
    return () => {
      active = false
      clearTimeout(bootTimer)
      sub.subscription.unsubscribe()
    }
  }, [])

  const signIn = async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email: email.trim().toLowerCase(),
      password,
    })
    if (error) return { ok: false, error: error.message }
    // Adopt the session immediately so the router doesn't bounce back to /login.
    if (data.session) setSession(data.session)
    // Deliberately NOT awaited. The API sleeps on the free tier and can take ~50s
    // to wake; awaiting loadMe() here froze the "Signing in…" button for minutes.
    // onAuthStateChange also kicks off loadMe(), and ProtectedRoute renders the
    // self-recovering "Waking the server…" screen until /me answers.
    return { ok: true }
  }

  const signOut = async () => {
    await supabase.auth.signOut()
    setMe(null)
    setMeState('unknown')
  }

  return (
    <Ctx.Provider value={{ session, me, meState, meError, loading, signIn, signOut, refreshMe: loadMe }}>
      {children}
    </Ctx.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth must be used within AuthProvider')
  return v
}
