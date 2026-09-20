import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { MarketPromise } from '@/lib/shopApi'
import { useMarket } from '../MarketContext'
import { readCustomer, rememberedOrders } from '../lib/device'
import { PROMISE_ICON_FALLBACK, PROMISE_ICONS } from '../lib/icons'
import { useFinePointer, useReducedMotion } from '../shell/useViewport'
import { locale, S } from '../strings'

/**
 * The opening — the marketplace opens the way the ops portal signs in: deep night, a glowing
 * horizon rising from below, and YQ said once. The arcs rise (0–900 ms) → the logo tile is already
 * there (it is in the served HTML, see below) and takes one light sweep (560 ms) → "Where Bahrain
 * restocks." (420 ms) → "Restock faster. Sell more." (640 ms) → under the horizon, the merchant's
 * line (820 ms): the first three promises, or "Welcome back, {shop}" with the "Refill my shelf"
 * shortcut → the finished composition then RESTS: the last line settles at 820 + 680 = 1500 ms and
 * the frame holds for ~400 ms before it leaves at 1.9 s (2.8 s when the shortcut is offered), a
 * 320 ms fade with a 2 % scale that reveals the app already rendered below. Every element must
 * settle before HOLD_MS — an opening that is still moving when it exits reads as a glitch.
 *
 * - Once per browser session (sessionStorage), standalone PWA included. The 30-day localStorage
 *   stamp no longer gates the opening: it only decides when a returning merchant gets the longer
 *   welcome with the shortcut (at most once per 30 days per device); other sessions greet them by
 *   name in the brisk timing.
 * - Tap, click, wheel or Esc skips. The skip control takes focus; focus goes back afterwards.
 * - Reduced motion: the same frame, static (no rise, pop or sweep), 600 ms, then a plain fade.
 * - An overlay only: the catalog loads and the page renders underneath the whole time.
 * - It owns the first frame all the same, and now from the very first pixel: the served HTML carries
 *   a static #yq-boot node with the same night field and the same logo tile in the same place
 *   (marketHtml() in web/vite.config.ts), Shell.tsx paints the same night field as this chunk's
 *   Suspense fallback, and this component removes the static node as it mounts — so the shell's
 *   header and skeleton never show through the gap while the chunk arrives (seconds on a cold
 *   connection), and the mark never blinks out in between. Hence no fade-in on the root, and hence
 *   the logo does not replay its pop when the static frame already showed it.
 */

const SESSION_KEY = 'yq-splash-session'
const SEEN_KEY = 'yq-splash-seen'
const WELCOME_EVERY_DAYS = 30

/** the static first frame in the served HTML (web/vite.config.ts), handed over to this overlay */
const BOOT_ID = 'yq-boot'

const HOLD_MS = 1900
const HOLD_WELCOME_MS = 2800
const HOLD_STILL_MS = 600
const EXIT_MS = 320
const EXIT_STILL_MS = 240
const EXIT_EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)'

/**
 * Choreography, ms after the overlay mounts (CSS delays; ignored under reduced motion). Each line
 * takes 680 ms to rise (.open-rise) and the logo 620 ms to spring (.open-pop), so the last delay
 * plus 680 must leave a visible beat before HOLD_MS: 820 + 680 = 1500 against a 1900 ms hold.
 */
const AT = { logo: 160, sweep: 560, kicker: 420, brand: 640, below: 820, action: 980 } as const

interface Opening {
  /** shop (or contact) name for the greeting, '' when unknown */
  shop: string
  /** this device has placed orders (the existing returning-merchant detection) */
  returning: boolean
  /** offer the longer welcome with "Refill my shelf" (≤ once per 30 days per device) */
  refill: boolean
}

function planOpening(recognized: boolean): Opening | null {
  try {
    if (sessionStorage.getItem(SESSION_KEY)) return null
    const customer = readCustomer()
    const returning = recognized && rememberedOrders().length > 0
    const seen = Number(localStorage.getItem(SEEN_KEY) || 0)
    return {
      shop: customer.shop || customer.name,
      returning,
      refill: returning && Date.now() - seen > WELCOME_EVERY_DAYS * 864e5,
    }
  } catch {
    return null // storage blocked: no way to keep it to once a session, so stay out of the way
  }
}

const at = (ms: number): CSSProperties => ({ ['--open-delay' as string]: `${ms}ms` }) as CSSProperties

function promiseText(p: MarketPromise): string {
  return (locale.lang === 'ar' && p.ar) || p.en
}

type Stage = 'on' | 'leaving' | 'off'

export function Splash() {
  const { settings, recognized } = useMarket()
  const navigate = useNavigate()
  const still = useReducedMotion()
  const finePointer = useFinePointer()
  const [plan] = useState(() => planOpening(recognized))
  const [stage, setStage] = useState<Stage>(plan ? 'on' : 'off')
  // The static first frame is on screen (it is in the HTML): adopt its logo tile instead of
  // replaying the spring, so the mark does not blink out between the first paint and this overlay.
  const [adopted] = useState(() => !!document.getElementById(BOOT_ID))
  const rootRef = useRef<HTMLDivElement>(null)
  const skipRef = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const textId = useId()

  // Hand over from the static first frame. A layout effect so it goes before this overlay paints,
  // and unconditionally — later loads in the same session play no opening and must not keep it.
  useLayoutEffect(() => {
    document.getElementById(BOOT_ID)?.remove()
  }, [])

  // mark the session, take focus for the skip control
  useEffect(() => {
    if (!plan) return
    try {
      sessionStorage.setItem(SESSION_KEY, '1')
      if (plan.refill) localStorage.setItem(SEEN_KEY, String(Date.now()))
    } catch {
      /* quota / blocked: it simply plays again on the next load */
    }
    const active = document.activeElement
    if (active instanceof HTMLElement && active !== document.body && !rootRef.current?.contains(active)) returnFocus.current = active
    skipRef.current?.focus({ preventScroll: true })
  }, [plan])

  // hold, then leave
  useEffect(() => {
    if (!plan) return
    const hold = plan.refill ? HOLD_WELCOME_MS : still ? HOLD_STILL_MS : HOLD_MS
    const t = window.setTimeout(() => setStage((s) => (s === 'on' ? 'leaving' : s)), hold)
    return () => window.clearTimeout(t)
  }, [plan, still])

  // Esc skips
  useEffect(() => {
    if (stage !== 'on') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setStage('leaving')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [stage])

  // exit: WAAPI, so the fade still plays where the reduced-motion kill switch zeroes CSS transitions
  useEffect(() => {
    if (stage !== 'leaving') return
    const el = rootRef.current
    const duration = still ? EXIT_STILL_MS : EXIT_MS
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      setStage('off')
    }
    const frames: Keyframe[] = still
      ? [{ opacity: 1 }, { opacity: 0 }]
      : [
          { opacity: 1, transform: 'scale(1)' },
          { opacity: 0, transform: 'scale(1.02)' },
        ]
    const anim = el && typeof el.animate === 'function' ? el.animate(frames, { duration, easing: EXIT_EASE, fill: 'forwards' }) : null
    if (anim) anim.onfinish = finish
    // background tabs may never deliver onfinish; the overlay must not outlive its fade
    const safety = window.setTimeout(finish, anim ? duration + 250 : 0)
    return () => {
      window.clearTimeout(safety)
      if (anim) {
        anim.onfinish = null
        if (!settled) anim.cancel()
      }
    }
  }, [stage, still])

  // hand focus back unless the page (e.g. the refill route) has taken it
  useEffect(() => {
    if (stage !== 'off' || !plan) return
    const prev = returnFocus.current
    const active = document.activeElement
    if (prev?.isConnected && (!active || active === document.body)) prev.focus({ preventScroll: true })
  }, [stage, plan])

  if (!plan || stage === 'off') return null

  const dismiss = () => setStage((s) => (s === 'on' ? 'leaving' : s))
  const welcome = plan.returning && plan.shop ? S.splash.welcome(plan.shop) : null
  const promises = (settings.promises || []).filter((p) => p && p.en).slice(0, 3)

  // No fade-in on the overlay itself: Shell.tsx paints the same night field as this boundary's
  // Suspense fallback, so the opening is already on screen when this mounts. Fading the root up from
  // transparent would show the app through the gap — the glitch this sequence exists to avoid.
  // Everything inside still animates in; the exit is the WAAPI fade above.
  return (
    <div
      ref={rootRef}
      data-splash={still ? 'still' : 'motion'}
      onClick={dismiss}
      onWheel={dismiss}
      className="fixed inset-0 z-[90] touch-none select-none overflow-hidden bg-night text-white"
    >
      <div className="horizon is-opening is-rising" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      {/* The portal sign-in's two veils over the glow (web/src/pages/Login.tsx:52-53), mirrored for a
       * horizon that rises from the bottom: a radial darkening that leaves the crest bright and calms
       * the spill, then a fade to #0c0720 over the lower third so the merchant's line reads on deep
       * purple instead of on the white rim. Both stay under the text (later siblings paint above). */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(120%_80%_at_50%_110%,transparent,rgba(12,7,32,.45))]" aria-hidden="true" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[30%] bg-gradient-to-t from-[#0c0720] via-[#0c0720]/85 to-transparent" aria-hidden="true" />

      {/* above the horizon: mark, idea, promise of the brand */}
      <div className="absolute inset-x-0 top-0 bottom-[40%] flex flex-col items-center justify-end px-6 text-center" style={{ paddingTop: 'calc(var(--m-safe-t) + 16px)' }}>
        <div className={`relative [@media(max-height:520px)]:hidden${adopted ? '' : ' open-pop'}`} style={adopted ? undefined : at(AT.logo)} aria-hidden="true">
          <span className="absolute -inset-7 rounded-full bg-arc-3/30 blur-2xl" />
          <span
            className="sweep block h-[84px] w-[84px] rounded-[22px] shadow-[0_0_0_1px_rgba(255,255,255,0.16),0_22px_48px_-18px_rgba(165,88,251,0.85),0_8px_20px_-8px_rgba(8,4,20,0.6)] lg:h-24 lg:w-24"
            style={{ ['--sweep-delay' as string]: `${AT.sweep}ms` }}
          >
            <img src="/yq-logo-160.webp" alt="" width={96} height={96} decoding="async" draggable={false} className="h-full w-full" />
          </span>
        </div>
        <div id={textId} aria-live="polite">
          <p
            className="open-rise mt-7 text-balance font-display text-[28px] font-bold leading-[1.08] tracking-[-0.03em] sm:text-[34px] lg:mt-8 lg:text-[40px] [@media(max-height:520px)]:mt-0"
            style={at(AT.kicker)}
          >
            {S.splash.kicker}
          </p>
          <p className="open-rise mt-3 text-[15px] font-medium leading-snug text-white/75 lg:text-lg" style={at(AT.brand)}>
            {S.splash.brand}
          </p>
        </div>
      </div>

      {/* Below the horizon: the merchant's line, then the skip control. The white rim crests at ~66%
       * of the height and the glow is still bright at 76%, so the line starts at 80% (82% on the
       * roomier desktop frame) where the veil above has taken the field back to deep purple. The
       * refill layout — a line plus a 48px pill — keeps more room and gets its contrast from the
       * veil. Move these with .horizon.is-opening in market.css. */}
      <div
        className={`absolute inset-x-0 bottom-0 ${plan.refill ? 'top-[74%]' : 'top-[80%] lg:top-[82%]'} flex flex-col items-center px-6 text-center`}
        style={{ paddingBottom: 'var(--m-safe-b)' }}
      >
        {plan.refill ? (
          <>
            {welcome && (
              <p className="open-rise max-w-[22rem] text-balance text-base font-semibold text-white [@media(max-height:520px)]:hidden" style={at(AT.below)}>
                {welcome}
              </p>
            )}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                dismiss()
                navigate('/quick?load=regular')
              }}
              className="open-rise mt-4 inline-flex h-12 shrink-0 items-center gap-2 rounded-full bg-white px-6 text-base font-semibold text-plum-ink shadow-[0_16px_36px_-14px_rgba(165,88,251,0.9)] focus-visible:outline-white/80 [@media(max-height:520px)]:mt-0"
              style={at(welcome ? AT.action : AT.below)}
            >
              {S.splash.refill} <ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
          </>
        ) : welcome ? (
          <p className="open-rise max-w-[22rem] text-balance text-base font-semibold text-white/90" style={at(AT.below)}>
            {welcome}
          </p>
        ) : (
          /* Three promises never fit one phone line: below 420px the third stands down and the rest
           * stack, icons on one edge so the block reads as a list; from md they read as one row.
           * Anything in between wraps 1+2, which looks accidental rather than composed. */
          promises.length > 0 && (
            <ul
              className="open-rise flex max-w-md flex-col items-start gap-2.5 text-sm font-medium text-white/85 md:max-w-none md:flex-row md:flex-wrap md:items-center md:justify-center md:gap-x-7 md:gap-y-2 lg:text-base [@media(max-height:520px)]:hidden"
              style={at(AT.below)}
            >
              {promises.map((p, i) => {
                const Icon = PROMISE_ICONS[(p.icon || '').trim().toLowerCase()] || PROMISE_ICON_FALLBACK
                return (
                  <li key={p.key} className={`inline-flex items-center gap-1.5${i === 2 ? ' [@media(max-width:419px)]:hidden' : ''}`}>
                    <Icon size={14} strokeWidth={2} aria-hidden="true" className="text-arc-3" />
                    {promiseText(p)}
                  </li>
                )
              })}
            </ul>
          )
        )}
        {/* The opening leaves on its own at 1.9 s, so this is an escape hatch, not an instruction:
         * one quiet word, no hairline rules framing it as a dialog button. It still takes focus for
         * the keyboard, with a ring subtle enough that a cold desktop load does not end on it. */}
        <button
          ref={skipRef}
          type="button"
          aria-describedby={textId}
          className="mb-2 mt-auto inline-flex h-11 shrink-0 items-center px-4 text-[11px] font-semibold uppercase tracking-[0.2em] text-white/45 outline-offset-4 focus-visible:outline-white/40 [@media(max-height:520px)]:mb-0"
        >
          {finePointer ? S.splash.skipClick : S.splash.skip}
        </button>
      </div>
    </div>
  )
}
