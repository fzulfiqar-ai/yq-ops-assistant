import { useEffect } from 'react'
import { animate, motion, useMotionValue, useTransform } from 'motion/react'

export function CountUp({
  value,
  format = (n) => Math.round(n).toLocaleString('en-US'),
  duration = 1.1,
}: {
  value: number
  format?: (n: number) => string
  duration?: number
}) {
  const mv = useMotionValue(0)
  const text = useTransform(mv, (v) => format(v))
  useEffect(() => {
    const controls = animate(mv, value, { duration, ease: [0.16, 1, 0.3, 1] })
    return () => controls.stop()
  }, [value, duration, mv])
  return <motion.span>{text}</motion.span>
}
