import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Session } from '@supabase/supabase-js'
import { supabase, getSessionSafe } from './supabase'
import { apiGet, ApiError, API_BASE, PASSWORD_CHANGE_EVENT } from './api'

export type Role = 'admin' | 'member' | 'salesman' | 'storekeeper' | 'management'

export interface Me {
  email: string
  role: Role
  features: string[]
  full_name?: string
  /** Server-owned: true = still on the temporary password; every route but /me, /auth/features
   *  and POST /auth/password answers 403 until the member sets their own (app/auth.py). */
  must_reset?: boolean
  /** Server-owned: true = every write is refused (management); hide write controls. */
  read_only?: boolean
}

/** What the portal calls each role (the API serves the same list as /auth/features role_labels). */
// eslint-disable-next-line react-refresh/only-export-components
export const ROLE_LABELS: Record<Role, string> = {
  admin: 'Admin',
  member: 'Member',
  salesman: 'Salesman',
  storekeeper: 'Storekeeper',
  management: 'Management',
}

/** Management: reads the whole company, changes nothing. */
// eslint-disable-next-line react-refresh/only-export-components
export function isManagement(me: Me | null | undefined): boolean {
  return me?.role === 'management'
}

/**
 * True when this login may not change anything, so a page hides its write buttons (confirm,
 * cancel, assign, edit, upload...). The API refuses every write from it anyway (app/auth.py,
 * 403 code 'read_only'); this is only so nobody is offered a button that cannot work.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function isReadOnly(me: Me | null | undefined): boolean {
  return Boolean(me?.read_only) || isManagement(me)
}

/** Sees every customer order company-wide (admins, and management to read). A salesman sees his own. */
// eslint-disable-next-line react-refresh/only-export-components
export function seesAllOrders(me: Me | null | undefined): boolean {
  return me?.role === 'admin' || isManagement(me)
}

/** Where a login that must set its own password is sent (the shells redirect there). */
// eslint-disable-next-line react-refresh/only-export-components
export function passwordScreenFor(me: Me | null): string {
  return me?.role === 'salesman' ? '/account#password' : '/settings'
}

/** The server flag first (/me), the session's user_metadata copy as the fallback for an older API. */
// eslint-disable-next-line react-refresh/only-export-components
export function mustResetOf(me: Me | null, session: Session | null): boolean {
  if (me && typeof me.must_reset === 'boolean') return me.must_reset
  return Boolean(session?.user?.user_metadata?.must_reset)
}

/**
 * ok          = provisioned
 * denied      = server SAID 401/403 (a real permissions problem)
 * offline     = the API isn't deployed at all — the host edge answered 404
 *               ("Application not found"). Retrying can NEVER fix this.
 * unreachable = every /me attempt failed (network error / 5xx / timeout); see MeFail for which
 */
export type MeState = 'ok' | 'denied' | 'offline' | 'unreachable' | 'unknown'
/** Why the last /me attempt failed: 'network' = no answer (offline, timeout, 502/503/504 from the
 *  edge); 'server' = the API answered with an error (500, 429…). Only 'network' can be a wake-up. */
export type MeFail = 'network' | 'server' | null

interface AuthState {
  session: Session | null
  me: Me | null
  meState: MeState
  /** Why the last /me attempt failed — surfaced in the UI so field issues are diagnosable. */
  meError: string | null
  meFail: MeFail
  loading: boolean
  signIn: (email: string, password: string) => Promise<{ ok: boolean; error?: string }>
  signOut: () => Promise<void>
  /** A FRESH /me (after a password change, or the Retry button): waits for any load in flight, then asks again. */
  refreshMe: () => Promise<void>
  /** Join the load in flight, or start one (the connecting screen's poll): never stacks requests. */
  retryMe: () => Promise<void>
}

const Ctx = createContext<AuthState | undefined>(undefined)

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/**
 * The last good /me, per user (perf-2609). A reload used to show the logo splash until /me came
 * back (and "Waking the server" when it was slow); now the shell and the page render at once from
 * this copy while /me revalidates in the background. It holds only what /me says about the user
 * (role, features, name) — the same browser already holds the Supabase session for that user —
 * it is keyed on the user id, and wiped on sign-out and on a 401/403. It grants nothing: every
 * API route still checks the token and the role server-side.
 */
const ME_CACHE_KEY = 'yq-me-v1'
const ME_CACHE_MAX_MS = 14 * 24 * 3600_000

function readCachedMe(uid: string): Me | null {
  try {
    const c = JSON.parse(localStorage.getItem(ME_CACHE_KEY) || 'null') as { uid?: string; me?: Me; at?: number } | null
    if (c && c.uid === uid && c.me && typeof c.at === 'number' && Date.now() - c.at < ME_CACHE_MAX_MS) return c.me
  } catch {
    /* storage unavailable or corrupt */
  }
  return null
}

function writeCachedMe(uid: string, m: Me) {
  try {
    localStorage.setItem(ME_CACHE_KEY, JSON.stringify({ uid, me: m, at: Date.now() }))
    localStorage.setItem('yq-role', m.role)
  } catch {
    /* storage unavailable */
  }
}

function clearCachedMe() {
  try {
    localStorage.removeItem(ME_CACHE_KEY)
  } catch {
    /* storage unavailable */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const [meState, setMeState] = useState<MeState>('unknown')
  const [meError, setMeError] = useState<string | null>(null)
  const [meFail, setMeFail] = useState<MeFail>(null)
  const queryClient = useQueryClient()
  // Refs, so the auth-event handler (registered once) sees the current values.
  const sessionRef = useRef<Session | null>(null)
  const meUid = useRef<string | null>(null)       // whose `me` is on screen
  const inflight = useRef<Promise<void> | null>(null)

  function adoptSession(s: Session | null) {
    sessionRef.current = s
    setSession(s)
  }

  function showMe(uid: string, m: Me) {
    meUid.current = uid
    setMe(m)
  }

  function dropMe() {
    meUid.current = null
    setMe(null)
    setMeState('unknown')
    setMeFail(null)
  }

  /**
   * Warm the page the user is landing on, DURING /me instead of after it (ProtectedRoute gates the
   * tree on `me`). Only when they are actually landing on it: a reload of /shop-orders no longer
   * downloads the Dashboard and builds its report on a 0.1-CPU server (perf-2609).
   */
  function warmLanding() {
    const path = window.location.pathname
    if (path !== '/' && path !== '/login' && path !== '/command') return
    let role: string | null = null
    try {
      role = localStorage.getItem('yq-role')
    } catch {
      /* storage unavailable */
    }
    // A salesman never sees the Dashboard: skip the chunk + the 403 (role remembered from the last /me).
    if (role === 'salesman') return
    // Management lands on the Command Centre (R7b): warm that page and its default period instead.
    if (role === 'management') {
      void import('@/pages/CommandCentre')
      void queryClient.prefetchQuery({
        queryKey: ['management', 'overview', 'mtd'],
        queryFn: () => apiGet<unknown>('/management/overview?period=mtd'),
        retry: false,
      })
      return
    }
    if (path === '/command') return
    void import('@/pages/Dashboard')
    void queryClient.prefetchQuery({
      queryKey: ['report', 'dashboard'],
      queryFn: () => apiGet<unknown>('/report/dashboard'),
      retry: false,
    })
  }
  const [loading, setLoading] = useState(true)

  /**
   * ONE /me conversation at a time (perf-2609). Boot, every auth event and the connecting screen's
   * poll used to start their own 6-try loops, never cancelled, and whichever loop failed last set
   * `me` to null — replacing the whole app with "Waking the server" mid-session even when another
   * loop had succeeded. Now callers join the load in flight; `force` (refreshMe) queues one fresh
   * load after it instead of joining it.
   */
  function loadMe(force = false): Promise<void> {
    if (inflight.current && !force) return inflight.current
    const prev = inflight.current
    const p: Promise<void> = (prev ? prev.catch(() => {}).then(runLoadMe) : runLoadMe())
      .finally(() => { if (inflight.current === p) inflight.current = null })
    inflight.current = p
    return p
  }

  async function runLoadMe() {
    const uid = sessionRef.current?.user?.id ?? null
    const stale = () => (sessionRef.current?.user?.id ?? null) !== uid   // signed out / switched user meanwhile
    const delays = [0, 1500, 3000, 6000, 10000, 12000]
    for (let i = 0; i < delays.length; i++) {
      if (delays[i]) await sleep(delays[i])
      if (stale()) return
      try {
        const m = await apiGet<Me>('/me')
        if (stale()) return
        if (uid) {
          showMe(uid, m)
          writeCachedMe(uid, m)
        } else {
          setMe(m)
        }
        setMeState('ok')
        setMeError(null)
        setMeFail(null)
        return
      } catch (e) {
        if (stale()) return
        // Record WHY, verbatim. A TypeError here means the browser refused to send the
        // request at all (CORS/blocked/DNS) — invisible in server logs.
        const why = e instanceof ApiError
          ? `HTTP ${e.status}${e.body ? ` — ${e.body.slice(0, 120)}` : ''}`
          : `${(e as Error)?.name || 'Error'}: ${(e as Error)?.message || String(e)}`
        setMeError(`${why}  ·  attempt ${i + 1}/${delays.length}  ·  ${API_BASE || '(no API base)'}/me`)
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          clearCachedMe()
          meUid.current = null
          setMe(null)
          setMeState('denied')
          return
        }
        // 404 = the platform edge has no app behind this URL (service deleted, suspended,
        // or VITE_API_URL points nowhere). Retrying cannot fix that. A `me` already on screen
        // stays: the pages say what failed, the app is not replaced.
        if (e instanceof ApiError && e.status === 404) {
          setMeState('offline')
          return
        }
        const gateway = e instanceof ApiError && (e.status === 502 || e.status === 503 || e.status === 504)
        setMeFail(e instanceof ApiError && !gateway ? 'server' : 'network')
      }
    }
    // Every try failed. A `me` already on screen (cached or earlier) is KEPT: a failed
    // background check must never take the app away from someone working in it.
    setMeState('unreachable')
  }

  useEffect(() => {
    let active = true
    // Hard safety net: never let the boot splash hang forever. If getSession or the
    // first /me stalls past 12s, stop the splash — the router then shows Login or the
    // connecting screen (both recover on their own), never a frozen logo.
    const bootTimer = setTimeout(() => { if (active) setLoading(false) }, 12000)
    ;(async () => {
      const s = await getSessionSafe()   // cannot hang (races the supabase lock)
      if (!active) return
      adoptSession(s)
      if (s) {
        const cached = readCachedMe(s.user.id)
        if (cached) {
          // Last good /me: paint the shell and the page NOW, revalidate below.
          showMe(s.user.id, cached)
          setLoading(false)
          clearTimeout(bootTimer)
        }
        warmLanding()
        await loadMe()
      }
      if (active) { setLoading(false); clearTimeout(bootTimer) }
    })()
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      adoptSession(s)
      if (!s) {
        dropMe()
        return
      }
      if (meUid.current !== s.user.id) {
        // Nobody on screen yet (boot's INITIAL_SESSION, a fresh sign-in): join boot's /me.
        // A DIFFERENT user in this tab: never show the previous user's data, even for a frame.
        const switching = meUid.current !== null
        if (switching) queryClient.clear()
        const cached = readCachedMe(s.user.id)
        if (cached) showMe(s.user.id, cached)
        else dropMe()
        void loadMe(switching)
        return
      }
      // Same user. Token refreshes, INITIAL_SESSION and the SIGNED_IN that auth-js re-announces on
      // every tab switch change nothing about who this is: no /me. A profile change does.
      if (event === 'USER_UPDATED') void loadMe(true)
    })
    return () => {
      active = false
      clearTimeout(bootTimer)
      sub.subscription.unsubscribe()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The api layer saw 403 password_change_required: flip the flag at once so the shell
  // redirects, without waiting for the next /me.
  useEffect(() => {
    const onRequired = () => setMe((m) => (m && !m.must_reset ? { ...m, must_reset: true } : m))
    window.addEventListener(PASSWORD_CHANGE_EVENT, onRequired)
    return () => window.removeEventListener(PASSWORD_CHANGE_EVENT, onRequired)
  }, [])

  const signIn = async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email: email.trim().toLowerCase(),
      password,
    })
    if (error) return { ok: false, error: error.message }
    // Adopt the session immediately so the router doesn't bounce back to /login.
    if (data.session) { adoptSession(data.session); warmLanding() }
    // Deliberately NOT awaited: onAuthStateChange (SIGNED_IN, a new user for this tab) starts
    // the /me load, and ProtectedRoute shows the self-recovering connecting screen until it answers.
    return { ok: true }
  }

  const signOut = async () => {
    clearCachedMe()
    await supabase.auth.signOut()
    dropMe()
    queryClient.clear()   // the next person on this browser starts with nothing of ours in memory
  }

  return (
    <Ctx.Provider value={{
      session, me, meState, meError, meFail, loading, signIn, signOut,
      refreshMe: () => loadMe(true), retryMe: () => loadMe(),
    }}>
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
