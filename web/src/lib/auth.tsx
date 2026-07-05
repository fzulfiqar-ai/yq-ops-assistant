import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase, getSessionSafe } from './supabase'
import { apiGet, ApiError } from './api'

export type Role = 'admin' | 'member' | 'salesman'

export interface Me {
  email: string
  role: Role
  features: string[]
  full_name?: string
}

/** ok = provisioned · denied = server SAID 401/403 · unreachable = network/5xx/cold start */
export type MeState = 'ok' | 'denied' | 'unreachable' | 'unknown'

interface AuthState {
  session: Session | null
  me: Me | null
  meState: MeState
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
        return
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setMe(null)
          setMeState('denied')
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
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim().toLowerCase(),
      password,
    })
    if (error) return { ok: false, error: error.message }
    await loadMe()
    return { ok: true }
  }

  const signOut = async () => {
    await supabase.auth.signOut()
    setMe(null)
    setMeState('unknown')
  }

  return (
    <Ctx.Provider value={{ session, me, meState, loading, signIn, signOut, refreshMe: loadMe }}>
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
