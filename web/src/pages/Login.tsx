import { useState, type CSSProperties, type FormEvent, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { ArrowRight, Eye, EyeOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Logo } from '@/components/Logo'
import { Quote } from '@/components/Quote'
import { useAuth } from '@/lib/auth'
import { warmApi } from '@/lib/api'
import { NightScene } from '@/boot/BootFrame'
import { useReleaseBoot } from '@/boot/handoff'
import { useIntroPlaying } from '@/boot/introGate'

/**
 * The card's own light palette, whatever the saved theme: the login is a fixed night page with a white
 * card, so its fields and its button read these instead of the dark theme's (which put dark inputs on
 * the white card). The logo plum #6D4091 is the button, the ink #1A1428 the type.
 */
const CARD_TOKENS = {
  '--background': '0 0% 100%',
  '--foreground': '258 33% 12%',
  '--card': '0 0% 100%',
  '--card-foreground': '258 33% 12%',
  '--primary': '273 39% 41%',
  '--primary-foreground': '0 0% 100%',
  '--ring': '273 45% 52%',
  '--input': '270 16% 86%',
  '--border': '270 16% 90%',
  '--muted-foreground': '262 10% 44%',
  '--destructive': '0 72% 45%',
  colorScheme: 'light',
} as CSSProperties

/**
 * The sign-in (ship 2, item 15). Its first frame is the served night (src/boot/frames.ts), which it takes
 * over as its background: a static night — the dome and its lit horizon, drawn once — with ONE glow
 * that blooms once by transform and opacity as the card arrives. Nothing blurs and nothing loops behind
 * the card any more (the old horizon animated a full-screen `filter: blur()` for 2 s, and the card sat
 * on a backdrop blur). When the intro plays, the card waits for it, then rises as the night clears.
 * Reduced motion: the still night and a 120 ms fade.
 */
export default function Login() {
  useReleaseBoot()
  const intro = useIntroPlaying()
  const reduce = useReducedMotion()
  const { signIn } = useAuth()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  // Wake the API while the user is still typing. The free-tier container sleeps
  // after 15 min idle and takes ~50s to boot, so starting it now usually means it
  // is ready by the time they hit Sign In.
  useEffect(() => { warmApi() }, [])
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

  const shown = reduce ? { opacity: 1 } : { opacity: 1, y: 0 }

  return (
    <div className="relative flex min-h-dvh w-full flex-col overflow-hidden bg-[#140F24]">
      <NightScene glow={!intro} />

      {/* Sign-in card, centered in the flexible space */}
      <main className="relative z-10 flex flex-1 items-center justify-center px-4 pt-10">
        <motion.div
          data-login-card=""
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: 14 }}
          animate={intro ? undefined : shown}
          transition={{ duration: reduce ? 0.12 : 0.32, ease: [0.22, 1, 0.36, 1] }}
          className="w-full max-w-md"
        >
          <div style={CARD_TOKENS} className="rounded-2xl border border-white/15 bg-white p-8 text-[#1A1428] shadow-2xl">
            <Logo float className="h-16 w-16 rounded-2xl shadow-lift" />
            <h1 className="mt-5 font-display text-2xl font-bold text-[#1A1428]">Welcome back</h1>
            <p className="mt-1 text-sm text-[#615A74]">Sign in to your control room.</p>

            <form onSubmit={submit} className="mt-6 space-y-3">
              <Input
                type="email"
                placeholder="you@email.com"
                autoComplete="username"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="text-base"
              />
              <div className="relative">
                <Input
                  type={showPw ? 'text' : 'password'}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  value={pw}
                  onChange={(e) => setPw(e.target.value)}
                  className="pr-11 text-base"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((s) => !s)}
                  aria-label={showPw ? 'Hide password' : 'Show password'}
                  className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-[#615A74] transition-colors hover:text-[#1A1428]"
                  tabIndex={-1}
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {err && (
                <div className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{err}</div>
              )}
              <Button type="submit" size="lg" className="w-full hover:bg-[#5A3478]" disabled={busy}>
                {busy ? 'Signing in…' : <>Sign in <ArrowRight className="h-[18px] w-[18px]" strokeWidth={2} aria-hidden="true" /></>}
              </Button>
            </form>

            <p className="mt-6 text-center text-xs text-[#8E879F]">
              Authorised access only · YQ Bahrain W.L.L
            </p>
          </div>
        </motion.div>
      </main>

      {/* Cinematic quote footer — in normal flow, never overlaps the card */}
      <footer className="relative z-10 mx-auto w-full max-w-xl px-6 pb-[calc(2.5rem+env(safe-area-inset-bottom,0px))] pt-4 text-center">
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
