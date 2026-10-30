'use client'

import * as React from 'react'
import { AnimatePresence, motion, useReducedMotion, type Variants } from 'motion/react'
import { cn } from '@/lib/utils'
import { NightScene } from '@/boot/BootFrame'
import { releaseBoot } from '@/boot/handoff'
import { IntroContext, introDue, markIntroShown } from '@/boot/introGate'

export type ArcRevealGreeting = { text: string; lang?: string }

export interface ArcRevealHeroProps {
  greetings?: ArcRevealGreeting[]
  /** how long each word stays fully visible, at least (a long word gets the time it takes to read) */
  greetingHold?: number
  /** the night's fade into the page at the end, capped at the hero duration (420 ms) */
  revealDuration?: number
  className?: string
  introClassName?: string
  greetingClassName?: string
  revealClassName?: string
  /** where the weekly stamp lives (boot/introGate.ts); without one the intro never plays */
  storageKey?: string
  children?: React.ReactNode
}

const DEFAULT_GREETINGS: ArcRevealGreeting[] = [
  { text: 'Welcome.' },
  { text: 'Sharp.' },
  { text: 'Calm.' },
  { text: 'Crafted.' },
  { text: 'Considered.' },
  { text: 'Ready.' },
]

/**
 * The portal intro (ship 2, item 15): the words, one at a time, on the login's own night, then the
 * night clears into the login.
 *
 * - At most once per device every 7 days, never twice in a tab, and only onto the night frame — a
 *   signed-out visit (boot/introGate.ts). Never for a salesman's phone, never over a signed-in shell.
 * - Every word gets its full hold: it rises in (240 ms), stays fully visible for its hold — counted
 *   from the moment it is fully in, and never shorter than the word takes to read — leaves (160 ms),
 *   and only then does the next one start. The old timer counted the hold from the moment the index
 *   changed, while the word before was still leaving, so a 300 ms hold never let "Mobile Accessories"
 *   on screen at all.
 * - A tap, a swipe, the wheel or any key skips it (200 ms fade). The key that skips does nothing else:
 *   Enter and Space would press what waits under the night.
 * - Only transform and opacity move: the words, and the night's one fade at the end. The old reveal
 *   redrew a full-screen SVG path every frame.
 * - Reduced motion: the still frame — every word at once, nothing moving — for 700 ms, then a 200 ms fade.
 * - It opens on the served night frame (#yq-boot, boot/frames.ts), paints the same night, and lets the
 *   served node go as it mounts, so the first pixel and the intro are one picture.
 */
type Phase = 'intro' | 'still' | 'done'

const ENTER_MS = 240
const EXIT_MS = 160
const STILL_MS = 700
const SKIP_MS = 200
const REVEAL_MAX_MS = 420
const EASE_ENTER = [0.22, 1, 0.36, 1] as const
const EASE_EXIT = [0.4, 0, 1, 1] as const

/** a word's full hold: the hold asked for, never less than it takes to read the word */
function holdFor(text: string, hold: number): number {
  return Math.max(hold, 120 + 19 * text.length)
}

export function ArcRevealHero({
  greetings = DEFAULT_GREETINGS,
  greetingHold = 560,
  revealDuration = 1400,
  className,
  introClassName,
  greetingClassName,
  revealClassName,
  storageKey,
  children,
}: ArcRevealHeroProps) {
  const prefersReducedMotion = useReducedMotion()
  // Lazy initial state (not an effect): a salesman never sees the intro at all, and a load that will
  // not play it never mounts the overlay for a frame. `yq-role` is the same remembered-role key
  // AuthProvider.warmLanding() reads (auth.tsx) — set the moment /me last answered, so it is there
  // before the session even resolves this time.
  const [phase, setPhase] = React.useState<Phase>(() => {
    if (typeof window === 'undefined') return 'done'
    try {
      if (window.localStorage.getItem('yq-role') === 'salesman') return 'done'
    } catch {
      return 'done'
    }
    if (!storageKey || !introDue(storageKey)) return 'done'
    return prefersReducedMotion ? 'still' : 'intro'
  })
  const [index, setIndex] = React.useState(0)
  const [skipped, setSkipped] = React.useState(false)
  const playing = phase !== 'done'
  const played = React.useRef(playing)

  // (No live-update effect for a mid-intro reduced-motion toggle: the initializer's one-time snapshot
  // covers it in practice — the intro is on screen for about two seconds — and syncing a changing
  // external value into state from inside an effect is exactly the "setState in an effect" pattern
  // react-hooks/set-state-in-effect flags.)

  // The served night frame hands over to this overlay, which paints the same night; and the device and
  // the tab are stamped as it starts, so a reload never plays it twice.
  React.useLayoutEffect(() => {
    if (played.current) releaseBoot()
  }, [])
  React.useEffect(() => {
    if (played.current && storageKey) markIntroShown(storageKey)
  }, [storageKey])

  // The word whose entrance has finished (its own animation reports it): its hold starts THEN, not when
  // the word was asked for. Word i starts once the word before has left (mode="wait"); at boot the main
  // thread is busy mounting the app under the night, so a word can start rising late, and a hold
  // counted from the request was eaten by that delay (measured: 189 of 310 ms on a busy machine).
  const [entered, setEntered] = React.useState(-1)
  React.useEffect(() => {
    if (phase !== 'intro' || entered !== index) return
    const word = greetings[index]
    if (!word) return
    const t = window.setTimeout(() => {
      if (index >= greetings.length - 1) setPhase('done')
      else setIndex(index + 1)
    }, holdFor(word.text, greetingHold))
    return () => window.clearTimeout(t)
  }, [phase, index, entered, greetings, greetingHold])
  // an entrance that never reports (an interrupted animation) still moves the words on
  React.useEffect(() => {
    if (phase !== 'intro') return
    const t = window.setTimeout(() => setEntered((e) => Math.max(e, index)), (index > 0 ? EXIT_MS : 0) + ENTER_MS + 1200)
    return () => window.clearTimeout(t)
  }, [phase, index])

  React.useEffect(() => {
    if (phase !== 'still') return
    const t = window.setTimeout(() => setPhase('done'), STILL_MS)
    return () => window.clearTimeout(t)
  }, [phase])

  const skip = React.useCallback(() => {
    setSkipped(true)
    setPhase('done')
  }, [])
  React.useEffect(() => {
    if (!playing) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') e.preventDefault()
      skip()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [playing, skip])

  // the overlay's way out: the reveal, or a skip (AnimatePresence `custom`: a removed child keeps its
  // last props, so the skip reaches it there). It takes no more taps the moment it starts to go.
  const overlay: Variants = {
    gone: (fast: boolean) => ({
      opacity: 0,
      pointerEvents: 'none',
      transition: {
        duration: (fast ? SKIP_MS : phase === 'still' || prefersReducedMotion ? 200 : Math.min(revealDuration, REVEAL_MAX_MS)) / 1000,
        ease: [0.4, 0, 0.2, 1],
      },
    }),
  }
  const current = greetings[Math.min(index, greetings.length - 1)]

  return (
    <IntroContext.Provider value={playing}>
      <section
        aria-label="Intro"
        // overflow-CLIP, not -hidden: `hidden` makes this section a scroll container, which becomes
        // the nearest positioning ancestor for every `position: sticky` element inside `children`
        // (the desktop sidebar, the phone header, the catalog search bar) — since this section
        // itself never scrolls, they never stick, they just sit still while the real page scrolls
        // past them. `clip` still clips what overflows but, per spec, does not establish a scroll
        // container, so sticky descendants keep sticking to the viewport.
        className={cn('relative isolate min-h-screen w-full overflow-clip bg-background text-foreground', className)}
      >
        <div className={cn('relative z-0', revealClassName)}>{children}</div>

        <AnimatePresence custom={skipped}>
          {playing && (
            <motion.div
              key="arc-reveal-overlay"
              data-intro=""
              aria-hidden="true"
              custom={skipped}
              variants={overlay}
              initial={false}
              exit="gone"
              onPointerDown={skip}
              onWheel={skip}
              className={cn('fixed inset-0 z-30 overflow-hidden', introClassName)}
            >
              <NightScene />
              <div className="absolute inset-0 flex items-center justify-center">
                {phase === 'still' ? (
                  <div data-intro-still="" className="select-none px-6 text-center text-white">
                    <div data-intro-word="" lang={greetings[0]?.lang} className={cn('font-display text-5xl font-semibold tracking-tight sm:text-6xl', greetingClassName)}>
                      {greetings[0]?.text}
                    </div>
                    <div className="mt-4 text-[11px] font-semibold uppercase tracking-[0.24em] text-white/65 sm:text-xs">
                      {greetings.slice(1).map((g, i) => (
                        <React.Fragment key={g.text}>
                          {i > 0 && <span aria-hidden="true"> · </span>}
                          <span data-intro-word="" lang={g.lang}>{g.text}</span>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                ) : (
                  <AnimatePresence mode="wait">
                    {phase === 'intro' && current && (
                      <motion.span
                        key={`${index}-${current.text}`}
                        data-intro-word=""
                        lang={current.lang}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0, transition: { duration: ENTER_MS / 1000, ease: EASE_ENTER } }}
                        exit={{ opacity: 0, y: -8, transition: { duration: EXIT_MS / 1000, ease: EASE_EXIT } }}
                        onAnimationComplete={(def) => {
                          // the entrance (opacity 1), not the exit
                          if (def && typeof def === 'object' && (def as { opacity?: number }).opacity === 1) setEntered(index)
                        }}
                        className={cn(
                          'select-none px-6 text-center font-display text-5xl font-semibold tracking-tight text-white sm:text-6xl md:text-7xl',
                          greetingClassName,
                        )}
                      >
                        {current.text}
                      </motion.span>
                    )}
                  </AnimatePresence>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </section>
    </IntroContext.Provider>
  )
}

export default ArcRevealHero
