import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { Lock, Loader2 } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { Logo } from './Logo'
import { Button } from './ui/button'
import { AppShell } from './AppShell'

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

export function ProtectedRoute() {
  const { session, me, loading } = useAuth()
  if (loading) return <Splash />
  if (!session) return <Navigate to="/login" replace />
  if (!me) return <NoAccess />
  return <AppShell />
}

/** Per-page feature gate (admins pass; members need the feature). */
export function Gate({ feature, children }: { feature?: string; children: ReactNode }) {
  const { me } = useAuth()
  const ok = !!me && (me.role === 'admin' || (feature ? (me.features || []).includes(feature) : false))
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
