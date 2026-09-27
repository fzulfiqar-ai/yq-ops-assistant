'use client'

import * as React from 'react'
import {
  animate,
  AnimatePresence,
  motion,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from 'motion/react'
import { cn } from '@/lib/utils'

export type ArcRevealGreeting = { text: string; lang?: string }

export interface ArcRevealHeroProps {
  greetings?: ArcRevealGreeting[]
  greetingHold?: number
  revealDuration?: number
  className?: string
  introClassName?: string
  greetingClassName?: string
  revealClassName?: string
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

type Phase = 'intro' | 'reveal' | 'done'

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
  // Lazy initial state (not an effect): a salesman never sees the greeting at all, and a viewer
  // who already finished it this session doesn't get a one-frame flash of it before it's swapped
  // out. `yq-role` is the same remembered-role key AuthProvider.warmDashboard() reads (auth.tsx) —
  // set the moment /me last answered, so it is there before the session even resolves this time.
  const [phase, setPhase] = React.useState<Phase>(() => {
    if (prefersReducedMotion) return 'done'
    if (typeof window === 'undefined') return 'intro'
    try {
      if (storageKey && window.localStorage.getItem(storageKey) === 'done') return 'done'
      if (window.localStorage.getItem('yq-role') === 'salesman') return 'done'
    } catch {
      /* private mode */
    }
    return 'intro'
  })
  const [index, setIndex] = React.useState(0)

  const progress = useMotionValue(0)
  const arcPath = useTransform(progress, (p: number) => {
    const edge = 110 - p * 140
    const control = edge + 25
    return `M 0 ${edge} Q 50 ${control} 100 ${edge} L 100 110 L 0 110 Z`
  })

  // (No live-update effect for a mid-session reduced-motion toggle: the initializer's one-time
  // snapshot already covers it in practice — this intro is on screen for well under a second —
  // and syncing a changing external value into state from inside an effect is exactly the
  // "setState in an effect" pattern react-hooks/set-state-in-effect flags.)

  React.useEffect(() => {
    if (phase !== 'intro') return
    const isLast = index >= greetings.length - 1
    if (isLast) {
      const t = window.setTimeout(() => setPhase('reveal'), greetingHold + 200)
      return () => window.clearTimeout(t)
    }
    const t = window.setTimeout(() => setIndex((i) => i + 1), greetingHold)
    return () => window.clearTimeout(t)
  }, [phase, index, greetingHold, greetings.length])

  React.useEffect(() => {
    if (phase !== 'reveal') return
    const controls = animate(progress, 1, {
      duration: revealDuration / 1000,
      ease: [0.85, 0, 0.15, 1],
      onComplete: () => {
        if (storageKey && typeof window !== 'undefined') {
          try {
            window.localStorage.setItem(storageKey, 'done')
          } catch {
            /* ignore */
          }
        }
        setPhase('done')
      },
    })
    return () => controls.stop()
  }, [phase, progress, revealDuration, storageKey])

  const showOverlay = phase !== 'done'
  const current = greetings[Math.min(index, greetings.length - 1)]

  return (
    <section
      aria-label="Intro"
      // overflow-CLIP, not -hidden: `hidden` makes this section a scroll container, which becomes
      // the nearest positioning ancestor for every `position: sticky` element inside `children`
      // (the desktop sidebar, the phone header, the catalog search bar) — since this section
      // itself never scrolls, they never stick, they just sit still while the real page scrolls
      // past them. `clip` still clips the arc-reveal overlay's transforms but, per spec, does not
      // establish a scroll container, so sticky descendants keep sticking to the viewport.
      className={cn('relative isolate min-h-screen w-full overflow-clip bg-background text-foreground', className)}
    >
      <div className={cn('relative z-0', revealClassName)}>{children}</div>

      <AnimatePresence>
        {showOverlay && (
          <motion.div
            key="arc-reveal-overlay"
            initial={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
            className={cn('absolute inset-x-0 top-0 z-30 h-screen overflow-hidden', introClassName)}
            style={{ background: 'linear-gradient(135deg,#2a1259,#140f24)' }}
          >
            <div className="absolute inset-0 flex items-center justify-center">
              <AnimatePresence mode="wait">
                {phase === 'intro' && current && (
                  <motion.span
                    key={`${index}-${current.text}`}
                    lang={current.lang}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
                    className={cn(
                      'select-none px-6 text-center font-display text-5xl font-semibold tracking-tight text-white sm:text-6xl md:text-7xl',
                      greetingClassName,
                    )}
                  >
                    {current.text}
                  </motion.span>
                )}
              </AnimatePresence>
            </div>

            <svg
              className="pointer-events-none absolute inset-0 h-full w-full"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              aria-hidden
            >
              <motion.path d={arcPath} style={{ fill: 'hsl(var(--background))' }} />
            </svg>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}

export default ArcRevealHero
