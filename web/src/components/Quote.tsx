import { useEffect, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'

const QUOTES = [
  'Artificial intelligence is the new electricity. — Andrew Ng',
  'The best way to predict the future is to invent it. — Alan Kay',
  'Automate the predictable, so your team can focus on the exceptional.',
  'Data is the new oil; intelligence is the refinery.',
  'Machines that think, so people are free to create.',
  'Great businesses are built on decisions made one day faster.',
]

export function Quote({ className }: { className?: string }) {
  const reduce = useReducedMotion()
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % QUOTES.length), 5200)
    return () => clearInterval(t)
  }, [])

  const [text, author] = (() => {
    const q = QUOTES[i]
    const idx = q.lastIndexOf('—')
    return idx > -1 ? [q.slice(0, idx).trim(), q.slice(idx + 1).trim()] : [q, '']
  })()

  return (
    <div className={className} style={{ minHeight: '3.5em' }}>
      <AnimatePresence mode="wait">
        {/* transform and opacity only (ship 2): the blur(4px) it used to fade through was a filter
            animation on the login every 5 s; reduced motion: the fade alone */}
        <motion.figure
          key={i}
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
          animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, y: -8 }}
          transition={{ duration: reduce ? 0.2 : 0.5, ease: [0.22, 1, 0.36, 1] }}
          className="m-0"
        >
          <blockquote className="font-display text-[15px] font-medium italic leading-relaxed text-white/90 md:text-lg [text-shadow:0_2px_24px_rgba(0,0,0,.55)]">
            “{text}”
          </blockquote>
          {author && (
            <figcaption className="mt-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-200/70">
              {author}
            </figcaption>
          )}
        </motion.figure>
      </AnimatePresence>
    </div>
  )
}
