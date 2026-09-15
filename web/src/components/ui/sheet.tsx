import { useCallback, useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Modal surface that reads as a bottom sheet on a phone and as a proper dialog on
 * a desktop. Accessible by construction: labelled, focus-trapped, Esc-closable,
 * scroll-locked, and it hands focus back to whatever opened it.
 *
 *   variant="dialog" → centred panel on >=sm
 *   variant="drawer" → right-hand side panel on >=sm
 *
 * Motion lives in a stylesheet the component carries with it rather than in state:
 * a CSS animation runs on mount, so there is no enter flag to set, no cascading
 * render and nothing to get out of sync. The curve overshoots by a hair and
 * settles fast — that hair is the whole difference between "a panel appeared" and
 * "a sheet slid up". `prefers-reduced-motion` collapses every duration to ~0
 * globally (index.css), so this degrades to an instant swap on its own.
 */

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]'

const MOTION = `
@keyframes yq-sheet-veil { from { opacity: 0 } to { opacity: 1 } }
@keyframes yq-sheet-up { from { transform: translate3d(0, 100%, 0) } to { transform: translate3d(0, 0, 0) } }
@keyframes yq-sheet-pop { from { opacity: 0; transform: translate3d(0, 8px, 0) scale(.985) } to { opacity: 1; transform: translate3d(0, 0, 0) scale(1) } }
@keyframes yq-sheet-right { from { opacity: 0; transform: translate3d(18px, 0, 0) } to { opacity: 1; transform: translate3d(0, 0, 0) } }
.yq-veil { animation: yq-sheet-veil 200ms ease-out both }
.yq-panel { animation: yq-sheet-up 230ms cubic-bezier(.22, 1.18, .36, 1) both; will-change: transform }
@media (min-width: 640px) {
  .yq-panel { animation: yq-sheet-pop 200ms cubic-bezier(.22, 1.18, .36, 1) both }
  .yq-panel--drawer { animation: yq-sheet-right 200ms cubic-bezier(.22, 1.18, .36, 1) both }
}
`

function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.tabIndex >= 0 && el.offsetWidth + el.offsetHeight > 0,
  )
}

export interface SheetProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** Small line under the title (also used as the dialog's accessible description). */
  subtitle?: ReactNode
  variant?: 'dialog' | 'drawer'
  children: ReactNode
  /** Pinned to the bottom of the panel, outside the scroll area. */
  footer?: ReactNode
  className?: string
}

export function Sheet({ open, onClose, title, subtitle, variant = 'dialog', children, footer, className }: SheetProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)
  const titleId = useId()
  const descId = useId()
  const close = useCallback(() => onClose(), [onClose])

  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement as HTMLElement | null
    const body = document.body
    const prevOverflow = body.style.overflow
    body.style.overflow = 'hidden'

    const panel = panelRef.current
    const first = focusables(panel)[0]
    ;(first || panel)?.focus({ preventScroll: true })

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        close()
        return
      }
      if (e.key !== 'Tab') return
      const nodes = focusables(panelRef.current)
      if (!nodes.length) {
        e.preventDefault()
        panelRef.current?.focus({ preventScroll: true })
        return
      }
      const firstNode = nodes[0]
      const lastNode = nodes[nodes.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === firstNode || active === panelRef.current)) {
        e.preventDefault()
        lastNode.focus()
      } else if (!e.shiftKey && active === lastNode) {
        e.preventDefault()
        firstNode.focus()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      body.style.overflow = prevOverflow
      restoreRef.current?.focus?.({ preventScroll: true })
    }
  }, [open, close])

  if (!open) return null

  const panelShape =
    variant === 'drawer'
      ? 'mt-auto max-h-[92vh] w-full rounded-t-[20px] sm:mt-0 sm:ml-auto sm:h-full sm:max-h-none sm:w-[27rem] sm:rounded-none sm:rounded-l-[20px]'
      : 'mt-auto max-h-[92vh] w-full rounded-t-[20px] sm:m-auto sm:max-h-[88vh] sm:w-full sm:max-w-2xl sm:rounded-[20px]'

  return createPortal(
    <div className="fixed inset-0 z-50 flex" role="presentation">
      <style>{MOTION}</style>
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        onClick={close}
        className="yq-veil absolute inset-0 h-full w-full cursor-default bg-[#1a1430]/45 backdrop-blur-[2px]"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? descId : undefined}
        tabIndex={-1}
        className={cn(
          'yq-panel relative flex flex-col overflow-hidden bg-white shadow-[0_-8px_44px_-12px_rgba(24,16,48,.32)] outline-none',
          variant === 'drawer' && 'yq-panel--drawer',
          panelShape,
          className,
        )}
      >
        {/* Grab handle — the affordance that says "this slides". Phones only. */}
        <div aria-hidden="true" className="flex justify-center pt-2 sm:hidden">
          <span className="h-1 w-9 rounded-full bg-[#e4e0ee]" />
        </div>

        <div className="flex items-start gap-3 border-b border-[#ece9f3] px-4 pb-3.5 pt-3 sm:px-5 sm:pt-4">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="font-display text-[17px] font-bold leading-tight tracking-[-0.01em] text-[#1a1430]">
              {title}
            </h2>
            {subtitle ? (
              <p id={descId} className="mt-0.5 text-[11.5px] leading-snug text-[#6b6480]">
                {subtitle}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close"
            className="-mr-1.5 -mt-0.5 grid h-11 w-11 shrink-0 place-items-center rounded-xl text-[#6b6480] transition duration-150 ease-out hover:bg-[#f4f2f9] hover:text-[#1a1430] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#6d28d9]/70"
          >
            <X size={18} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{children}</div>

        {footer ? (
          <div className="border-t border-[#ece9f3] bg-white px-4 pb-[max(0.875rem,env(safe-area-inset-bottom))] pt-3 sm:px-5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}
