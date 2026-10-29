import { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import { AssistChat } from './AssistChat'

/**
 * The chat's frame. Phone (< md): a full-height dialog with a focus trap, scroll lock and Escape.
 * md and up: a 400 x 620 card at the bottom-end, above the launcher, which does not trap focus or
 * lock the page (the shop stays usable beside it). Lazy-loaded by the launcher, so none of this is
 * in the first download.
 */

const FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
const PHONE = '(max-width: 767px)'

export default function AssistPanel({ itemCode, first, onClose }: { itemCode: string | null; first: { id: number; text: string } | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const closeRef = useRef(onClose)
  useEffect(() => {
    closeRef.current = onClose
  })

  useEffect(() => {
    const panel = ref.current
    if (!panel) return
    const phone = window.matchMedia(PHONE).matches
    const restore = document.activeElement as HTMLElement | null
    const body = document.body
    const prevOverflow = body.style.overflow
    if (phone) body.style.overflow = 'hidden'
    // desktop: straight to the message box; phone: to the panel, so the keyboard does not cover the welcome
    if (phone) panel.focus({ preventScroll: true })
    else panel.querySelector<HTMLElement>('textarea')?.focus({ preventScroll: true })

    const inside = () => panel.contains(document.activeElement) || document.activeElement === document.body
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && inside()) {
        e.preventDefault()
        closeRef.current()
        return
      }
      if (e.key !== 'Tab' || !phone || !panel.contains(document.activeElement)) return
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => n.offsetWidth + n.offsetHeight > 0)
      if (!nodes.length) return
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      body.style.overflow = prevOverflow
      restore?.focus?.({ preventScroll: true })
    }
  }, [])

  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      data-assist-panel=""
      className="fixed inset-0 z-sheet flex flex-col overflow-hidden bg-surface outline-none max-md:[animation:m-sheet-up_260ms_var(--m-ease-spring)_both] md:inset-auto md:bottom-[calc(var(--m-bottom-stack,0px)+84px)] md:end-5 md:h-[min(620px,calc(100dvh-7.5rem))] md:w-[400px] md:rounded-xl md:shadow-3 md:ring-1 md:ring-line md:[animation:m-pop_200ms_var(--m-ease-spring)_both]"
    >
      <AssistChat itemCode={itemCode} first={first} onClose={onClose} titleId={titleId} />
    </div>,
    document.body,
  )
}
