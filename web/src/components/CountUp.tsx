import { useEffect, useRef } from 'react'
import { animate, motion, useMotionValue, useTransform } from 'motion/react'
import { ENTER_EASE, useFirstView } from '@/lib/motionOnce'

/**
 * A figure that counts up to its value — on its first view in the session only (lib/motionOnce.ts). It used to
 * count up from 0 on every visit; a revisit, a later figure (a re-read after a data load) and
 * prefers-reduced-motion now all show the final value at once.
 */
export function CountUp({
  id,
  value,
  format = (n) => Math.round(n).toLocaleString('en-US'),
  duration = 0.8,
}: {
  /** the figure's name for the session memory, e.g. "dashboard.sales_mtd" */
  id: string
  value: number
  format?: (n: number) => string
  duration?: number
}) {
  const first = useFirstView(`countup:${id}`)
  const mv = useMotionValue(first ? 0 : value)
  const text = useTransform(mv, (v) => format(v))
  // set once the first count has run to its end: from then on a new value is shown as it is
  const done = useRef(!first)
  useEffect(() => {
    if (done.current) {
      mv.set(value)
      return
    }
    const controls = animate(mv, value, { duration, ease: ENTER_EASE, onComplete: () => { done.current = true } })
    return () => controls.stop()
  }, [value, duration, mv])
  return <motion.span>{text}</motion.span>
}
