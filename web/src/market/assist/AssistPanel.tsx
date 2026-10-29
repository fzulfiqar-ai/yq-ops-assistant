import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { lockScroll, OUT_MS, pushLayer, useKeyboardInset } from '@/lib/sheet'
import { cn } from '@/lib/utils'
import { AssistChat } from './AssistChat'

/**
 * The chat's frame (YQ Assist v2). Phone (< md): a bottom sheet over the dimmed page, a focus trap, scroll lock,
 * Escape and the iPhone keyboard handled (lib/sheet: lockScroll, pushLayer, useKeyboardInset); it lands with a
 * water line that settles flat. md and up: a frosted 420 x 660 card at the bottom-end that does not trap focus or
 * lock the page; it fills from the launcher's corner like water. Closing plays a 200 ms exit while the page is
 * already usable again (pointer-events off at once: nothing waits for an exit). Lazy-loaded by the launcher.
 */

const FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
const PHONE = '(max-width: 767px)'

export default function AssistPanel({ itemCode, first, onClose }: { itemCode: string | null; first: { id: number; text: string } | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const [phone] = useState(() => typeof window !== 'undefined' && window.matchMedia(PHONE).matches)
  const [closing, setClosing] = useState(false)
  const closeRef = useRef(onClose)
  const timer = useRef(0)
  useEffect(() => {
    closeRef.current = onClose
  })
  useKeyboardInset(ref, phone)

  const requestClose = useCallback(() => {
    setClosing((was) => {
      if (!was) timer.current = window.setTimeout(() => closeRef.current(), OUT_MS)
      return true
    })
  }, [])
  useEffect(() => () => window.clearTimeout(timer.current), [])

  useEffect(() => {
    const panel = ref.current
    if (!panel) return
    const restore = document.activeElement as HTMLElement | null
    const unlock = phone ? lockScroll() : null
    const layer = pushLayer()
    // desktop: straight to the message box; phone: to the panel, so the keyboard does not cover the welcome
    if (phone) panel.focus({ preventScroll: true })
    else panel.querySelector<HTMLElement>('textarea')?.focus({ preventScroll: true })

    const inside = () => panel.contains(document.activeElement) || document.activeElement === document.body
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && inside() && layer.isTop()) {
        e.preventDefault()
        requestClose()
        return
      }
      if (e.key !== 'Tab' || !phone || !panel.contains(document.activeElement)) return
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => n.offsetWidth + n.offsetHeight > 0)
      if (!nodes.length) return
      const firstNode = nodes[0]
      const last = nodes[nodes.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === firstNode || active === panel)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        firstNode.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      layer.release()
      unlock?.()
      restore?.focus?.({ preventScroll: true })
    }
  }, [phone, requestClose])

  return createPortal(
    <>
      {phone ? (
        <div
          aria-hidden="true"
          onClick={requestClose}
          className={cn('fixed inset-0 z-sheet bg-ink/45', closing ? 'opacity-0 transition-opacity duration-2' : 'motion-safe:[animation:m-fade-in_280ms_var(--m-ease-out)_both]')}
        />
      ) : null}
      <div
        ref={ref}
        role="dialog"
        aria-modal={phone ? 'true' : undefined}
        aria-labelledby={titleId}
        tabIndex={-1}
        data-assist-panel=""
        style={phone ? { paddingBottom: 'var(--kb, 0px)' } : undefined}
        className={cn(
          'glass fixed z-sheet flex flex-col outline-none',
          'max-md:inset-x-0 max-md:bottom-0 max-md:top-[max(5dvh,calc(var(--m-safe-t)+16px))] max-md:rounded-t-xl max-md:bg-surface',
          'md:bottom-[calc(var(--m-bottom-stack,0px)+84px)] md:end-5 md:h-[min(660px,calc(100dvh-7.5rem))] md:w-[420px] md:overflow-hidden md:rounded-xl md:shadow-3 md:ring-1 md:ring-line',
          phone ? (closing ? 'assist-sheet-out' : 'assist-sheet-in') : closing ? 'assist-out' : 'assist-in',
          closing && 'pointer-events-none',
        )}
      >
        {phone ? (
          <svg className="assist-wave go" viewBox="0 0 400 18" preserveAspectRatio="none" aria-hidden="true">
            <path d="M0 18 C 70 2, 150 2, 205 10 S 330 20, 400 4 L 400 18 Z" fill="currentColor" />
          </svg>
        ) : (
          <span className="assist-fill go z-10" aria-hidden="true" />
        )}
        <AssistChat itemCode={itemCode} first={first} onClose={requestClose} titleId={titleId} />
      </div>
    </>,
    document.body,
  )
}
