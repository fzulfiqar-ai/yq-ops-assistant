import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { MarketPromise } from '@/lib/shopApi'
import { useMarket } from '../MarketContext'
import { readCustomer, rememberedOrders } from '../lib/device'
import { markSplashShown, splashDue } from '../lib/splashGate'
import { PROMISE_ICON_FALLBACK, PROMISE_ICONS } from '../lib/icons'
import { useFinePointer, useReducedMotion } from '../shell/useViewport'
import { locale, S } from '../strings'

/**
 * The opening — the marketplace opens the way the ops portal signs in: deep night, a glowing
 * horizon rising from below, and YQ said once. The arcs rise (0–900 ms) → the logo tile is already
 * there (it is in the served HTML, see below) and takes one light sweep (560 ms) → "Where Bahrain
 * restocks." (420 ms) → "Restock faster. Sell more." (640 ms) → under the horizon, the merchant's
 * line (820 ms): the first three promises, or "Welcome back, {shop}" with the "Refill my shelf"
 * shortcut → the finished composition then DRIFTS: through the whole hold the frame moves toward
 * the viewer (a slow 2.5 % push) while the dawn breathes, and at 1.9 s (2.8 s when the shortcut is
 * offered) the night lifts off the app already rendered below, a 560 ms fade while the push gathers
 * pace (the camera, PUSH below). Every line still settles its own entrance before HOLD_MS (the last at
 * 820 + 680 = 1500 ms): only the camera moves into the exit, never a line still rising.
 *
 * - At most once per device every 7 days, and never twice in a tab session (lib/splashGate.ts),
 *   standalone PWA included — every rep or WhatsApp link opens a new tab, so a session gate alone
 *   covered a ready catalog again each time. The 30-day stamp only decides when a returning
 *   merchant gets the longer welcome with the shortcut (at most once per 30 days per device); other
 *   openings greet them by name in the brisk timing.
 * - Tap, click, wheel or Esc skips. The skip control takes focus; focus goes back afterwards.
 * - Reduced motion: the same frame, static (no rise, pop or sweep), 600 ms, then a plain fade.
 * - An overlay only: the catalog loads and the page renders underneath the whole time.
 * - It owns the first frame all the same, and from the very first pixel: the served HTML carries a
 *   #yq-boot node with the same night field, the same logo tile in the same place and the dawn
 *   already rising (CSS on the compositor, from the first paint, while the app's code is parsed:
 *   marketHtml() in web/vite.config.ts). This component adopts that node as its background (night,
 *   dawn, veils) and lets it go with the exit, so the dawn carries on instead of restarting
 *   (29-Oct-2026); Shell.tsx paints a night field as the chunk's Suspense fallback only if the node
 *   is gone. The shell's header and skeleton never show through while the chunk arrives (seconds on
 *   a cold connection), and the mark never blinks out. Hence no fade-in on the root, and hence the
 *   logo does not replay its pop when the static frame already showed it.
 */

const SEEN_KEY = 'yq-splash-seen'
const WELCOME_EVERY_DAYS = 30

/** the static first frame in the served HTML (web/vite.config.ts), handed over to this overlay */
const BOOT_ID = 'yq-boot'

const HOLD_MS = 1900
const HOLD_WELCOME_MS = 2800
const HOLD_STILL_MS = 600
const EXIT_MS = 560
const EXIT_STILL_MS = 240
/** a skip leaves from wherever the timeline is, this fast */
const SKIP_MS = 320
/**
 * The camera (29-Oct-2026): the frame drifts toward the viewer through the hold (linear, to PUSH),
 * and the push gathers pace through the exit (to PUSH_OUT). The exit's curve leaves at the hold's
 * speed (start slope 0.066 / 0.4 = 0.165 ≈ 0.025 / 1900 ms × 560 ms / 0.045), so the move never
 * stalls at the seam; the fade has its own curve and is mostly done halfway, so the shop shows early.
 */
const PUSH = 1.025
const PUSH_OUT = 1.07
const PUSH_EASE = 'cubic-bezier(0.4, 0.066, 0.8, 0.6)'
const FADE_EASE = 'cubic-bezier(0.4, 0, 0.2, 1)'

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
  if (!splashDue()) return null
  try {
    const customer = readCustomer()
    const returning = recognized && rememberedOrders().length > 0
    const seen = Number(localStorage.getItem(SEEN_KEY) || 0)
    return {
      shop: customer.shop || customer.name,
      returning,
      refill: returning && Date.now() - seen > WELCOME_EVERY_DAYS * 864e5,
    }
  } catch {
    return null // storage blocked: no way to keep it to once a week, so stay out of the way
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
  // The static first frame is on screen (it is in the HTML, its 8 s safety fade not begun): adopt it.
  // It stays under this overlay as its night, dawn and veils, so the dawn that rose from the first
  // paint carries on, and its logo tile hands over to this one without replaying the spring.
  const [adopted] = useState(() => {
    const boot = document.getElementById(BOOT_ID)
    return !!boot && getComputedStyle(boot).opacity === '1'
  })
  const rootRef = useRef<HTMLDivElement>(null)
  // the logo and the lines: what the camera moves (round 2) - the night frame stands still once risen
  const fgRef = useRef<HTMLDivElement>(null)
  const skipRef = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const leaveRef = useRef<() => void>(() => {})
  const textId = useId()

  // The whole timeline is booked as this overlay mounts, before it paints (29-Oct-2026, the owner's
  // video): the hold's push and the exit are Web Animations the compositor runs on its own clock (and
  // the reduced-motion kill switch, which zeroes CSS transitions, leaves alone). A timer used to start
  // the exit; with the app loading underneath it fired up to a second late and the fade found no
  // frames, so the opening froze and then cut to the shop. The adopted first frame moves and leaves
  // with this overlay. Without a plan the first frame simply goes: later loads in the same session
  // play no opening and must not keep it.
  useLayoutEffect(() => {
    const boot = document.getElementById(BOOT_ID)
    if (!plan) {
      boot?.remove()
      return
    }
    if (adopted && boot) boot.dataset.adopted = ''
    else boot?.remove()
    // the night (this overlay and the adopted first frame) fades; only the foreground is pushed: a
    // screen-sized layer scaled on every frame of the hold was what an integrated GPU could not carry
    const usable = <T extends HTMLElement>(el: T | null | undefined): el is T => !!el && typeof el.animate === 'function'
    const fading = [rootRef.current, adopted ? boot : null].filter(usable)
    const pushed = still ? [] : [fgRef.current].filter(usable)
    const hold = plan.refill ? HOLD_WELCOME_MS : still ? HOLD_STILL_MS : HOLD_MS
    const total = hold + (still ? EXIT_STILL_MS : EXIT_MS)
    const at = hold / total
    let anims: Animation[] = []
    let settled = false
    let leaving = false
    let safety = 0
    const finish = () => {
      if (settled) return
      settled = true
      window.clearTimeout(safety)
      document.getElementById(BOOT_ID)?.remove()
      setStage('off')
    }
    // a background tab may never deliver onfinish, and an overlay must not outlive its fade
    const arm = (ms: number) => {
      window.clearTimeout(safety)
      safety = window.setTimeout(finish, ms)
    }
    for (const el of fading) anims.push(el.animate([{ opacity: 1 }, { opacity: 1, offset: at, easing: FADE_EASE }, { opacity: 0 }], { duration: total, fill: 'forwards' }))
    for (const el of pushed) anims.push(el.animate([{ transform: 'scale(1)' }, { transform: `scale(${PUSH})`, offset: at, easing: PUSH_EASE }, { transform: `scale(${PUSH_OUT})` }], { duration: total, fill: 'forwards' }))
    if (anims[0]) anims[0].onfinish = finish
    arm(anims.length ? total + 400 : hold)
    // A skip (tap, click, wheel, Esc) leaves from wherever the camera is: the booked timeline hands its
    // current state to a short exit, so nothing jumps. Inside its last SKIP_MS it simply finishes.
    leaveRef.current = () => {
      if (settled || leaving) return
      leaving = true
      if (!anims.length) return finish()
      if (total - Number(anims[0].currentTime ?? 0) <= SKIP_MS) return
      for (const a of anims) {
        a.onfinish = null
        try {
          a.commitStyles()
        } catch {
          /* not rendered: nothing to keep */
        }
        a.cancel()
      }
      const opts = { duration: still ? EXIT_STILL_MS : SKIP_MS, easing: FADE_EASE, fill: 'forwards' } as const
      anims = [...fading.map((el) => el.animate([{}, { opacity: 0 }], opts)), ...pushed.map((el) => el.animate([{}, { transform: `scale(${PUSH_OUT})` }], opts))]
      anims[0].onfinish = finish
      arm((still ? EXIT_STILL_MS : SKIP_MS) + 400)
    }
    return () => {
      window.clearTimeout(safety)
      for (const a of anims) {
        a.onfinish = null
        a.cancel()
      }
      leaveRef.current = () => {}
    }
    // booked once, as the overlay mounts: the plan, the adoption and reduced motion as they were then
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // stamp the device (7 days) and the tab, take focus for the skip control
  useEffect(() => {
    if (!plan) return
    markSplashShown()
    try {
      if (plan.refill) localStorage.setItem(SEEN_KEY, String(Date.now()))
    } catch {
      /* quota / blocked: the welcome simply offers the shortcut again next time */
    }
    const active = document.activeElement
    if (active instanceof HTMLElement && active !== document.body && !rootRef.current?.contains(active)) returnFocus.current = active
    skipRef.current?.focus({ preventScroll: true })
  }, [plan])

  // Esc skips
  useEffect(() => {
    if (stage !== 'on') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setStage('leaving')
      leaveRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [stage])

  // hand focus back unless the page (e.g. the refill route) has taken it
  useEffect(() => {
    if (stage !== 'off' || !plan) return
    const prev = returnFocus.current
    const active = document.activeElement
    if (prev?.isConnected && (!active || active === document.body)) prev.focus({ preventScroll: true })
  }, [stage, plan])

  if (!plan || stage === 'off') return null

  const dismiss = () => {
    setStage((s) => (s === 'on' ? 'leaving' : s))
    leaveRef.current()
  }
  const welcome = plan.returning && plan.shop ? S.splash.welcome(plan.shop) : null
  const promises = (settings.promises || []).filter((p) => p && p.en).slice(0, 3)

  // No fade-in on the overlay itself: the opening is already on screen when this mounts (the adopted
  // first frame, or Shell.tsx's night field when there was none). Fading the root up from transparent
  // would show the app through the gap — the glitch this sequence exists to avoid. Everything inside
  // still animates in; the push and the exit are the booked timeline above.
  return (
    <div
      ref={rootRef}
      data-splash={still ? 'still' : 'motion'}
      onClick={dismiss}
      onWheel={dismiss}
      // a swipe skips it too (touch-none keeps a swipe from ever becoming a click): any touch that
      // is not on the shortcut button starts the exit
      onPointerDown={(e) => {
        if (!(e.target as HTMLElement).closest('button,a')) dismiss()
      }}
      className={`fixed inset-0 z-[90] touch-none select-none overflow-hidden text-white${adopted ? '' : ' bg-night'}`}
    >
      {/* the night, the dawn and the two veils (market.css .open-veil-*): the adopted first frame's,
       * under this overlay, or this overlay's own when there was none. The text paints above them. */}
      {!adopted && (
        <>
          <div className="horizon is-opening is-rising" aria-hidden="true">
            <i />
            <div className="open-glow">
              <i />
              <i />
              <i />
            </div>
          </div>
          <div className="open-veils" aria-hidden="true" />
        </>
      )}

      {/* the foreground, which the camera moves (fgRef): the night and the dawn stand still once risen */}
      <div ref={fgRef} className="absolute inset-0">
        {/* above the horizon: mark, idea, promise of the brand */}
        <div className="absolute inset-x-0 top-0 bottom-[40%] flex flex-col items-center justify-end px-6 text-center" style={{ paddingTop: 'calc(var(--m-safe-t) + 16px)' }}>
          <div className={`relative [@media(max-height:520px)]:hidden${adopted ? '' : ' open-pop'}`} style={adopted ? undefined : at(AT.logo)} aria-hidden="true">
            <span className="open-halo absolute -inset-16 rounded-full" />
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
    </div>
  )
}
