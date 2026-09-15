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
 */

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]'

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
      ? 'mt-auto max-h-[92vh] w-full rounded-t-2xl sm:mt-0 sm:ml-auto sm:h-full sm:max-h-none sm:w-[26rem] sm:rounded-none sm:rounded-l-2xl'
      : 'mt-auto max-h-[92vh] w-full rounded-t-2xl sm:m-auto sm:max-h-[88vh] sm:w-full sm:max-w-2xl sm:rounded-2xl'

  return createPortal(
    <div className="fixed inset-0 z-50 flex" role="presentation">
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        onClick={close}
        className="absolute inset-0 h-full w-full cursor-default bg-[#1a1430]/45 backdrop-blur-[2px]"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? descId : undefined}
        tabIndex={-1}
        className={cn(
          'relative flex animate-fade-up flex-col overflow-hidden bg-white shadow-[0_-8px_40px_-12px_rgba(24,16,48,.35)] outline-none',
          panelShape,
          className,
        )}
      >
        <div className="flex items-start gap-3 border-b border-[#ece9f3] px-4 py-3.5 sm:px-5">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="font-display text-base font-bold leading-tight text-[#1a1430]">
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
            className="-mr-1.5 -mt-1 grid h-11 w-11 shrink-0 place-items-center rounded-xl text-[#6b6480] transition hover:bg-[#f4f2f9] hover:text-[#1a1430] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
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
