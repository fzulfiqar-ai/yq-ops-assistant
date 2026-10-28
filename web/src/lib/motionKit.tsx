import type { ReactElement, ReactNode } from 'react'
import { motion } from 'motion/react'
import { ResponsiveContainer } from 'recharts'
import { DURATION, ENTER_EASE, useChartMotion, type ChartSeriesMotion } from '@/lib/motionOnce'

/**
 * A chart's frame (lib/motionOnce.ts): the ResponsiveContainer with its resizes debounced, and the animation
 * props its series spread — on for the chart's first view in the session, off for every later draw. Mounted
 * with the chart itself, so the first view is the one the person sees, not the page's loading render.
 *
 *   <ChartBox id="sales.trend" height={260}>{(m) => <AreaChart …><Area {...m} … /></AreaChart>}</ChartBox>
 */
export function ChartBox({ id, height, children }: {
  /** the chart's name for the session memory: one per chart, e.g. "dashboard.daily" */
  id: string
  /** pixels, or a percentage of a sized parent ("100%"), as ResponsiveContainer takes it */
  height: number | `${number}%`
  children: (series: ChartSeriesMotion) => ReactElement
}) {
  const { series, debounce } = useChartMotion(id)
  return (
    <ResponsiveContainer width="100%" height={height} debounce={debounce}>
      {children(series)}
    </ResponsiveContainer>
  )
}

/**
 * Content that takes its skeleton's place: a 200 ms fade on the enter curve when `on` (useSkeletonFade — a
 * skeleton was shown and the person has not asked for less motion), drawn at once otherwise. Opacity only.
 */
export function FadeIn({ on, className, children }: { on: boolean; className?: string; children: ReactNode }) {
  return (
    <motion.div className={className} initial={on ? { opacity: 0 } : false} animate={{ opacity: 1 }}
      transition={{ duration: DURATION.fade, ease: ENTER_EASE }}>
      {children}
    </motion.div>
  )
}
