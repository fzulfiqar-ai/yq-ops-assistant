import { useEffect, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { RING } from './shared'

/**
 * The salesman catalog's Undo: one offer at a time ("Removed C18 × 3", "Added 4 usual lines · kept
 * your quantities"), a new one replaces the last and starts its clock again. It stays 6 s, and
 * while a finger, the pointer or the keyboard is on it the clock waits.
 *
 * The app's toasts (components/Toast) carry no action, so this is its own small surface, drawn like
 * them: a white card in the bottom stack, above the order bar and the Spotted button (the room the
 * page and the shell publish: --yq-tabbar, --yq-dock-bottom, --yq-capture-room) and, from 1280 px,
 * left of the docked order slip (--yq-dock-right). It is portalled to <body> above the sheets
 * (z-100, like the toasts), so a line removed in the checkout can be put back from there. Colours
 * are the shop's hex values: outside the shell's root the plum tokens do not apply.
 *
 * Motion: in 200 ms on the enter curve, out 200 ms on the exit curve, transform and opacity only;
 * still under reduced motion.
 */

export interface UndoOffer {
  /** new for every offer: a second offer replaces the first and restarts its clock */
  id: number
  text: string
  undo: () => void
}

const SHOW_MS = 6000
const LEAVE_MS = 200

const MOTION = `
@keyframes yq-undo-in { from { opacity: 0; transform: translate3d(0, 8px, 0) } to { opacity: 1; transform: translate3d(0, 0, 0) } }
@keyframes yq-undo-out { from { opacity: 1; transform: translate3d(0, 0, 0) } to { opacity: 0; transform: translate3d(0, 8px, 0) } }
.yq-undo { animation: yq-undo-in 200ms cubic-bezier(.22, 1, .36, 1) both }
.yq-undo--out { animation: yq-undo-out 200ms cubic-bezier(.4, 0, 1, 1) both }
@media (prefers-reduced-motion: reduce) { .yq-undo, .yq-undo--out { animation: none } }
`

const PLACE: CSSProperties = {
  bottom: 'calc(var(--yq-tabbar, 0px) + env(safe-area-inset-bottom, 0px) + var(--yq-dock-bottom, 0px) + var(--yq-capture-room, 0px) + 8px)',
  ['--yq-undo-right' as string]: 'max(var(--yq-dock-right, 0px), 1.25rem)',
}

/** `onDone(id)` once that offer has left — a newer offer may already be on screen, so the page
 *  drops only the one with this id. */
export function UndoToast({ offer, onDone }: { offer: UndoOffer | null; onDone: (id: number) => void }) {
  // the offer that is leaving (its exit runs, then onDone), and whether the clock waits
  const [leaving, setLeaving] = useState<number | null>(null)
  const [held, setHeld] = useState(false)

  useEffect(() => {
    if (!offer || held) return
    const t = setTimeout(() => setLeaving(offer.id), SHOW_MS)
    return () => clearTimeout(t)
  }, [offer, held])

  useEffect(() => {
    if (leaving == null) return
    const t = setTimeout(() => onDone(leaving), LEAVE_MS)
    return () => clearTimeout(t)
  }, [leaving, onDone])

  if (!offer) return null
  const out = leaving === offer.id
  return createPortal(
    <div className="pointer-events-none fixed left-3 right-3 z-[100] flex justify-center md:left-auto md:right-[var(--yq-undo-right)] md:w-[24rem]" style={PLACE}>
      <style>{MOTION}</style>
      <div
        key={offer.id}
        role="status"
        onPointerEnter={() => setHeld(true)}
        onPointerLeave={() => setHeld(false)}
        onFocus={() => setHeld(true)}
        onBlur={() => setHeld(false)}
        className={cn(
          'yq-undo pointer-events-auto flex w-full items-center gap-2 rounded-xl border border-[#E2DCEA] bg-white py-1 pl-3.5 pr-1 shadow-[0_12px_32px_-12px_rgba(25,20,36,.35)]',
          out && 'yq-undo--out',
        )}
      >
        <p className="min-w-0 flex-1 text-[13px] font-medium leading-snug text-[#1A1428]">{offer.text}</p>
        <button
          type="button"
          disabled={out}
          onClick={() => {
            offer.undo()
            setLeaving(offer.id)
          }}
          className={cn('inline-flex h-11 shrink-0 items-center gap-1.5 rounded-lg px-3 text-[13px] font-semibold text-[#6D4091] hover:bg-[#EEE8F4]', RING)}
        >
          <RotateCcw size={14} aria-hidden="true" /> Undo
        </button>
      </div>
    </div>,
    document.body,
  )
}
