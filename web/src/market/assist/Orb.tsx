import { forwardRef, type CSSProperties } from 'react'
import { cn } from '@/lib/utils'

/**
 * The assistant's face (YQ Assist v2): a small liquid sphere in the logo plum whose motion is its state.
 * Pure CSS (market.css "v3: assist"): idle breathes, listening follows the voice (--orb-level), thinking
 * swirls fast, answered pulses once, busy slows and cools. Decorative: the words beside it say the state.
 * Shared by the launcher (first download) and the chat (lazy), so it stays a handful of spans.
 */

export type OrbState = 'idle' | 'listening' | 'thinking' | 'answered' | 'busy'

// written out so the build keeps every state class
const STATE_CLASS: Record<OrbState, string> = {
  idle: 'is-idle',
  listening: 'is-listening',
  thinking: 'is-thinking',
  answered: 'is-answered',
  busy: 'is-busy',
}

export const Orb = forwardRef<HTMLSpanElement, { state?: OrbState; size?: number; className?: string }>(function Orb(
  { state = 'idle', size = 36, className },
  ref,
) {
  return (
    <span ref={ref} aria-hidden="true" className={cn('assist-orb', STATE_CLASS[state], className)} style={{ '--orb-size': `${size}px` } as CSSProperties}>
      <span className="assist-orb__swirl" />
      <span className="assist-orb__glint" />
    </span>
  )
})
