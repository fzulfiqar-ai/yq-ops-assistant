import { useEffect, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Lock, Loader2, CloudOff, RefreshCw, ServerCrash } from 'lucide-react'
import { isManagement, useAuth, type Role } from '@/lib/auth'
import { managementMayOpen } from '@/lib/nav'
import { Logo } from './Logo'
import { Button } from './ui/button'
import { AppShell } from './AppShell'
import { SalesmanShell } from './SalesmanShell'

function Splash() {
  return (
    <div className="grid h-screen place-items-center bg-background">
      <div className="flex flex-col items-center gap-4 text-muted-foreground">
        <Logo float className="h-14 w-14 rounded-2xl" />
        <Loader2 className="animate-spin" size={20} />
      </div>
    </div>
  )
}

/** Server said 401/403 — a REAL permissions problem. */
function NoAccess() {
  const { signOut } = useAuth()
  return (
    <div className="grid h-screen place-items-center bg-background px-4">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-accent text-accent-foreground">
          <Lock size={22} />
        </div>
        <h1 className="font-display text-xl font-bold">No access yet</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your account isn't provisioned for this portal. Please ask an admin to grant you access.
        </p>
        <Button variant="outline" className="mt-5" onClick={signOut}>
          Sign out
        </Button>
      </div>
    </div>
  )
}

/** The API is DELETED / not deployed (edge returned 404). Retrying cannot fix this. */
function ApiOffline() {
  const { refreshMe, signOut } = useAuth()
  const [busy, setBusy] = useState(false)
  return (
    <div className="grid h-screen place-items-center bg-background px-4">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-destructive/10 text-destructive">
          <ServerCrash size={22} />
        </div>
        <h1 className="font-display text-xl font-bold">Backend is offline</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The portal loaded, but its API server isn't running — so there's nothing to sign
          you in to. This is a hosting problem, not a problem with your account, and it
          needs an admin to bring the server back up.
        </p>
        <p className="mt-3 text-xs text-muted-foreground/80">
          Your data is safe. Nothing has been lost.
        </p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <Button variant="outline" disabled={busy}
            onClick={async () => { setBusy(true); await refreshMe(); setBusy(false) }}>
            {busy ? <Loader2 className="animate-spin" size={15} /> : <RefreshCw size={15} />} Try again
          </Button>
          <Button variant="ghost" onClick={signOut}>Sign out</Button>
        </div>
      </div>
    </div>
  )
}

/**
 * No `me` yet and /me has not answered (first sign-in on this device — a returning user opens
 * straight into the app from the last good /me). It used to say "Waking the server… ~30 seconds"
 * for EVERY failure, including a 500 from a server that was wide awake (29-Sep-2026); the copy now
 * says what actually happened. After ~2 minutes of failed polls it escalates to "can't reach",
 * so nobody stares at a spinner believing it's about to recover.
 */
function Connecting() {
  const { refreshMe, retryMe, meError, meFail } = useAuth()
  const [busy, setBusy] = useState(false)
  const [attempts, setAttempts] = useState(0)
  const stalled = attempts >= 6   // 6 × 20s ≈ 2 min
  useEffect(() => {
    if (stalled) return           // stop hammering a server that clearly isn't coming back
    // retryMe joins a load already in flight, so this poll never stacks /me requests.
    const t = setInterval(() => { setAttempts((n) => n + 1); void retryMe() }, 20000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stalled])
  const title = stalled
    ? "Can't reach the server"
    : meFail === 'server' ? 'The server hit an error' : 'Connecting to the server…'
  const body = stalled
    ? "The API hasn't answered properly for a couple of minutes. Your data is safe — please tell an admin."
    : meFail === 'server'
      ? 'It answered with an error instead of your account. Retrying automatically — or tap retry.'
      : 'This is taking longer than usual. It will connect automatically — or tap retry.'
  return (
    <div className="grid h-screen place-items-center bg-background px-4">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-accent text-accent-foreground">
          <CloudOff size={22} />
        </div>
        <h1 className="font-display text-xl font-bold">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{body}</p>
        <Button variant="outline" className="mt-5" disabled={busy}
          onClick={async () => { setBusy(true); setAttempts(0); await refreshMe(); setBusy(false) }}>
          {busy ? <Loader2 className="animate-spin" size={15} /> : <RefreshCw size={15} />} Retry now
        </Button>
        {meError && (
          <p className="mx-auto mt-5 max-w-md break-words rounded-lg bg-muted px-3 py-2 text-left font-mono text-[11px] leading-relaxed text-muted-foreground">
            {meError}
          </p>
        )}
      </div>
    </div>
  )
}

export function ProtectedRoute() {
  const { session, me, meState, loading } = useAuth()
  if (loading) return <Splash />
  if (!session) return <Navigate to="/login" replace />
  if (!me) {
    if (meState === 'denied') return <NoAccess />
    if (meState === 'offline') return <ApiOffline />
    return <Connecting />
  }
  // A salesman works from a phone in a shop — two tabs, no office sidebar.
  if (me.role === 'salesman') return <SalesmanShell />
  return <AppShell />
}

/**
 * Per-page feature gate (admins pass; members need the feature). Management opens only its own
 * pages (lib/nav MANAGEMENT_NAV) even where a feature would let it in, e.g. Coach under Sales.
 * `roles` gates a page by role instead of a feature (the Command Centre: admin and management).
 */
export function Gate({ feature, roles, children }: { feature?: string; roles?: Role[]; children: ReactNode }) {
  const { me } = useAuth()
  const { pathname } = useLocation()
  const allowed = roles
    ? !!me && roles.includes(me.role)
    : !!me && (me.role === 'admin' || (feature ? (me.features || []).includes(feature) : false))
  const ok = allowed && (!isManagement(me) || managementMayOpen(pathname, me?.features || []))
  if (!ok) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <div className="max-w-sm text-center text-muted-foreground">
          <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-2xl bg-accent text-accent-foreground">
            <Lock size={20} />
          </div>
          <p className="text-sm">You don't have access to this page. Ask an admin to grant it.</p>
        </div>
      </div>
    )
  }
  return <>{children}</>
}
