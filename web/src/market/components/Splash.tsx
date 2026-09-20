import { useEffect, useId, useRef, useState, type CSSProperties } from 'react'
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
 * horizon rising from below, and YQ said once. The arcs rise (0–900 ms) → the logo tile springs in
 * with one light sweep (200 ms) → "Where Bahrain restocks." (500 ms) → "Restock faster. Sell more."
 * (800 ms) → under the horizon, the merchant's line (950 ms): the first three promises, or
 * "Welcome back, {shop}" with the "Refill my shelf" shortcut → exit at 1.6 s (2.8 s when the
 * shortcut is offered), a 320 ms fade with a 2 % scale that reveals the app already rendered below.
 *
 * - Once per browser session (sessionStorage), standalone PWA included. The 30-day localStorage
 *   stamp no longer gates the opening: it only decides when a returning merchant gets the longer
 *   welcome with the shortcut (at most once per 30 days per device); other sessions greet them by
 *   name in the brisk timing.
 * - Tap, click, wheel or Esc skips. The skip control takes focus; focus goes back afterwards.
 * - Reduced motion: the same frame, static (no rise, pop or sweep), 600 ms, then a plain fade.
 * - An overlay only: the catalog loads and the page renders underneath the whole time.
 */

const SESSION_KEY = 'yq-splash-session'
const SEEN_KEY = 'yq-splash-seen'
const WELCOME_EVERY_DAYS = 30

const HOLD_MS = 1600
const HOLD_WELCOME_MS = 2800
const HOLD_STILL_MS = 600
const EXIT_MS = 320
const EXIT_STILL_MS = 240
const EXIT_EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)'

/** Choreography, ms after the overlay mounts (CSS delays; ignored under reduced motion). */
const AT = { logo: 200, sweep: 640, kicker: 500, brand: 800, below: 950, action: 1100 } as const

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

const at = (ms: number, extra?: Record<string, string>): CSSProperties => ({ ['--open-delay' as string]: `${ms}ms`, ...extra }) as CSSProperties

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
  const rootRef = useRef<HTMLDivElement>(null)
  const skipRef = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const textId = useId()

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

  return (
    <div
      ref={rootRef}
      data-splash={still ? 'still' : 'motion'}
      onClick={dismiss}
      onWheel={dismiss}
      className="open-fade fixed inset-0 z-[90] touch-none select-none overflow-hidden bg-night text-white"
      style={at(0, { '--open-dur': '200ms' })}
    >
      <div className="horizon is-opening is-rising" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>

      {/* above the horizon: mark, idea, promise of the brand */}
      <div className="absolute inset-x-0 top-0 bottom-[40%] flex flex-col items-center justify-end px-6 text-center" style={{ paddingTop: 'calc(var(--m-safe-t) + 16px)' }}>
        <div className="open-pop relative [@media(max-height:520px)]:hidden" style={at(AT.logo)} aria-hidden="true">
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

      {/* below the horizon: the merchant's line, then the skip control */}
      <div className="absolute inset-x-0 bottom-0 top-[76%] flex flex-col items-center px-6 text-center" style={{ paddingBottom: 'var(--m-safe-b)' }}>
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
          promises.length > 0 && (
            <ul
              className="open-rise flex max-w-md flex-wrap items-center justify-center gap-x-4 gap-y-2 text-sm font-medium text-white/75 lg:max-w-none lg:gap-x-7 lg:text-base [@media(max-height:520px)]:hidden"
              style={at(AT.below)}
            >
              {promises.map((p) => {
                const Icon = PROMISE_ICONS[(p.icon || '').trim().toLowerCase()] || PROMISE_ICON_FALLBACK
                return (
                  <li key={p.key} className="inline-flex items-center gap-1.5">
                    <Icon size={14} strokeWidth={2} aria-hidden="true" className="text-arc-3" />
                    {promiseText(p)}
                  </li>
                )
              })}
            </ul>
          )
        )}
        <button
          ref={skipRef}
          type="button"
          aria-describedby={textId}
          className="mb-2 mt-auto inline-flex h-11 shrink-0 items-center gap-3 px-4 text-[11px] font-semibold uppercase tracking-[0.2em] text-white/55 focus-visible:outline-white/70 [@media(max-height:520px)]:mb-0"
        >
          <span aria-hidden="true" className="h-px w-6 bg-gradient-to-r from-transparent to-white/35" />
          {finePointer ? S.splash.skipClick : S.splash.skip}
          <span aria-hidden="true" className="h-px w-6 bg-gradient-to-l from-transparent to-white/35" />
        </button>
      </div>
    </div>
  )
}
