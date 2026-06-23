import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { motion } from 'motion/react'
import GlowHorizon from '@/components/ui/glow-horizon'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Logo } from '@/components/Logo'
import { apiGet, apiPost } from '@/lib/api'
import { supabase } from '@/lib/supabase'

interface InviteInfo { email: string; role: string; features: string[] }

export default function AcceptInvite() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const nav = useNavigate()
  const [info, setInfo] = useState<InviteInfo | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [p1, setP1] = useState('')
  const [p2, setP2] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!token) {
      setLoadErr('This invite link is missing its token.')
      return
    }
    apiGet<InviteInfo>(`/team/invite/${token}`)
      .then(setInfo)
      .catch(() => setLoadErr('This invite is invalid or has expired.'))
  }, [token])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (p1.length < 8) return setErr('Password must be at least 8 characters.')
    if (p1 !== p2) return setErr('Passwords do not match.')
    setErr('')
    setBusy(true)
    try {
      await apiPost('/team/accept', { token, password: p1 })
      await supabase.auth.signInWithPassword({ email: info!.email, password: p1 })
      nav('/', { replace: true })
    } catch {
      setErr('Could not activate the account. The invite may have expired.')
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-screen w-full items-center justify-center overflow-hidden bg-[#140f24] px-4">
      <div className="absolute inset-0"><GlowHorizon variant="top" /></div>
      <motion.div
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
        className="relative z-10 w-full max-w-md"
      >
        <div className="rounded-2xl border border-white/15 bg-white/95 p-8 shadow-2xl backdrop-blur-xl">
          <Logo float className="h-16 w-16 rounded-2xl shadow-lift" />
          {loadErr ? (
            <>
              <h1 className="mt-5 font-display text-2xl font-bold text-[#1a1430]">Invite unavailable</h1>
              <p className="mt-2 text-sm text-[#6b6480]">{loadErr}</p>
              <Button className="mt-5 w-full" onClick={() => nav('/login')}>Go to sign in</Button>
            </>
          ) : !info ? (
            <p className="mt-6 text-sm text-[#6b6480]">Loading your invite…</p>
          ) : (
            <>
              <h1 className="mt-5 font-display text-2xl font-bold text-[#1a1430]">Set your password</h1>
              <p className="mt-1 text-sm text-[#6b6480]">
                You've been invited as a <b className="capitalize">{info.role}</b>
                <br />
                <b>{info.email}</b>
              </p>
              <form onSubmit={submit} className="mt-6 space-y-3">
                <Input type="password" placeholder="New password" value={p1} onChange={(e) => setP1(e.target.value)} autoFocus />
                <Input type="password" placeholder="Confirm password" value={p2} onChange={(e) => setP2(e.target.value)} />
                {err && <div className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{err}</div>}
                <Button type="submit" size="lg" className="w-full" disabled={busy}>
                  {busy ? 'Activating…' : 'Activate my account  →'}
                </Button>
              </form>
            </>
          )}
        </div>
      </motion.div>
    </div>
  )
}
