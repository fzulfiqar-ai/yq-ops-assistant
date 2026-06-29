import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import { Eye, EyeOff } from 'lucide-react'
import GlowHorizon from '@/components/ui/glow-horizon'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Logo } from '@/components/Logo'
import { Quote } from '@/components/Quote'
import { useAuth } from '@/lib/auth'

export default function Login() {
  const { signIn } = useAuth()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [showPw, setShowPw] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!email || !pw) {
      setErr('Please enter your email and password.')
      return
    }
    setErr('')
    setBusy(true)
    const r = await signIn(email, pw)
    setBusy(false)
    if (r.ok) nav('/', { replace: true })
    else setErr(r.error || 'Invalid credentials or access not granted.')
  }

  return (
    <div className="relative flex min-h-screen w-full flex-col overflow-hidden bg-[#140f24]">
      {/* Animated purple glow backdrop */}
      <div className="absolute inset-0">
        <GlowHorizon variant="top" />
      </div>
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(120%_80%_at_50%_-10%,transparent,rgba(12,7,32,.65))]" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-[5] h-64 bg-gradient-to-t from-[#0c0720] via-[#0c0720]/85 to-transparent" />

      {/* Sign-in card, centered in the flexible space */}
      <main className="relative z-10 flex flex-1 items-center justify-center px-4 pt-10">
        <motion.div
          initial={{ opacity: 0, y: 18, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
          className="w-full max-w-md"
        >
          <div className="rounded-2xl border border-white/15 bg-white/95 p-8 shadow-2xl backdrop-blur-xl">
            <Logo float className="h-16 w-16 rounded-2xl shadow-lift" />
            <h1 className="mt-5 font-display text-2xl font-bold text-[#1a1430]">Welcome back</h1>
            <p className="mt-1 text-sm text-[#6b6480]">Sign in to your control room.</p>

            <form onSubmit={submit} className="mt-6 space-y-3">
              <Input
                type="email"
                placeholder="you@email.com"
                autoComplete="username"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <div className="relative">
                <Input
                  type={showPw ? 'text' : 'password'}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  value={pw}
                  onChange={(e) => setPw(e.target.value)}
                  className="pr-11"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((s) => !s)}
                  aria-label={showPw ? 'Hide password' : 'Show password'}
                  className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-[#6b6480] transition-colors hover:text-[#1a1430]"
                  tabIndex={-1}
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {err && (
                <div className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{err}</div>
              )}
              <Button type="submit" size="lg" className="w-full" disabled={busy}>
                {busy ? 'Signing in…' : 'Sign In  →'}
              </Button>
            </form>

            <p className="mt-6 text-center text-xs text-[#9a93ad]">
              Authorised access only · YQ Bahrain W.L.L
            </p>
          </div>
        </motion.div>
      </main>

      {/* Cinematic quote footer — in normal flow, never overlaps the card */}
      <footer className="relative z-10 mx-auto w-full max-w-xl px-6 pb-10 pt-4 text-center">
        <div className="mb-3 flex items-center justify-center gap-3 text-[11px] font-semibold uppercase tracking-[0.24em] text-white/55">
          <span className="h-px w-7 bg-gradient-to-r from-transparent to-white/40" />
          YQ Bahrain · AI Portal
          <span className="h-px w-7 bg-gradient-to-l from-transparent to-white/40" />
        </div>
        <Quote />
      </footer>
    </div>
  )
}
