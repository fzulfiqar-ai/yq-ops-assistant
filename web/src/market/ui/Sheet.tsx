import { useCallback, useEffect, useId, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The market's one modal surface.
 *
 *   variant="sheet"  — bottom sheet on phones, centred dialog from `md` up (default)
 *   variant="dialog" — centred dialog from `sm` up (palette, keypad)
 *   variant="drawer" — bottom sheet on phones, side panel (inline-end) from `md` up
 *
 * Accessible by construction: labelled, focus-trapped, Esc-closable, scroll-locked, focus
 * restored. Enter/exit run on CSS keyframes (market.css): `closing` state plays the exit and
 * unmounts on animationend, so nothing snaps. The phone handle can be dragged down to dismiss.
 * `prefers-reduced-motion` collapses every duration globally.
 */

const FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetWidth + el.offsetHeight > 0)
}

export interface SheetProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** small line under the title (also the dialog's accessible description) */
  subtitle?: ReactNode
  variant?: 'sheet' | 'dialog' | 'drawer'
  size?: 'md' | 'lg'
  children: ReactNode
  /** pinned under the scroll area */
  footer?: ReactNode
  /** extra header content (e.g. a search field) */
  header?: ReactNode
  /** hide the title row (the body renders its own header) — the title is still announced */
  bare?: boolean
  className?: string
  /** a view-transition name for the panel root (card → panel morph) */
  vtName?: string
}

const EXIT_MS = 170

export function Sheet({ open, onClose, title, subtitle, variant = 'sheet', size = 'md', children, footer, header, bare, className, vtName }: SheetProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)
  const titleId = useId()
  const descId = useId()
  const [closing, setClosing] = useState(false)
  const [drag, setDrag] = useState(0)
  const dragStart = useRef<number | null>(null)

  const close = useCallback(() => {
    if (closing) return
    setClosing(true)
    window.setTimeout(onClose, EXIT_MS)
  }, [closing, onClose])

  const [openSeen, setOpenSeen] = useState(open)
  if (openSeen !== open) {
    setOpenSeen(open)
    setClosing(false)
  }

  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement as HTMLElement | null
    const body = document.body
    const prevOverflow = body.style.overflow
    body.style.overflow = 'hidden'
    body.dataset.sheetOpen = '1'
    const panel = panelRef.current
    const first = focusables(panel).find((el) => !el.closest('[data-autofocus-skip]'))
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
      delete body.dataset.sheetOpen
      restoreRef.current?.focus?.({ preventScroll: true })
    }
  }, [open, close])

  if (!open) return null

  const isDrawer = variant === 'drawer'
  const isDialog = variant === 'dialog'
  const width = size === 'lg' ? 'md:w-[34rem]' : 'md:w-[28rem]'
  const dialogMax = size === 'lg' ? 'sm:max-w-2xl' : 'sm:max-w-lg'

  const shape = isDrawer
    ? cn('mt-auto max-h-[92dvh] w-full rounded-t-xl md:mt-0 md:ms-auto md:h-full md:max-h-none md:rounded-none md:rounded-s-xl', width)
    : isDialog
      ? cn('mt-auto max-h-[92dvh] w-full rounded-t-xl sm:m-auto sm:max-h-[86dvh] sm:rounded-xl', dialogMax)
      : cn('mt-auto max-h-[92dvh] w-full rounded-t-xl md:m-auto md:max-h-[86dvh] md:rounded-xl', dialogMax)

  const enter = isDrawer
    ? 'max-md:[animation:m-sheet-up_260ms_var(--m-ease-spring)_both] md:[animation:m-drawer-in_260ms_var(--m-ease-spring)_both]'
    : isDialog
      ? 'max-sm:[animation:m-sheet-up_260ms_var(--m-ease-spring)_both] sm:[animation:m-pop_200ms_var(--m-ease-spring)_both]'
      : 'max-md:[animation:m-sheet-up_260ms_var(--m-ease-spring)_both] md:[animation:m-pop_200ms_var(--m-ease-spring)_both]'
  const exit = isDrawer
    ? 'max-md:[animation:m-sheet-down_170ms_ease-in_both] md:[animation:m-drawer-out_170ms_ease-in_both]'
    : isDialog
      ? 'max-sm:[animation:m-sheet-down_170ms_ease-in_both] sm:[animation:m-pop-out_150ms_ease-in_both]'
      : 'max-md:[animation:m-sheet-down_170ms_ease-in_both] md:[animation:m-pop-out_150ms_ease-in_both]'

  const onHandleDown = (e: ReactPointerEvent) => {
    dragStart.current = e.clientY
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  }
  const onHandleMove = (e: ReactPointerEvent) => {
    if (dragStart.current == null) return
    setDrag(Math.max(0, e.clientY - dragStart.current))
  }
  const onHandleUp = () => {
    const d = drag
    dragStart.current = null
    setDrag(0)
    if (d > 80) close()
  }

  return createPortal(
    <div className="fixed inset-0 z-sheet flex" role="presentation">
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        onClick={close}
        style={{ animation: closing ? 'm-fade-in 150ms ease-in reverse both' : 'm-fade-in 200ms ease-out both' }}
        className="absolute inset-0 h-full w-full cursor-default bg-ink/45 backdrop-blur-[2px]"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? descId : undefined}
        tabIndex={-1}
        style={{ transform: drag ? `translateY(${drag}px)` : undefined, transition: drag ? 'none' : undefined, viewTransitionName: vtName }}
        className={cn('relative flex flex-col overflow-hidden bg-surface shadow-3 outline-none', shape, closing ? exit : enter, className)}
      >
        {/* Grab handle — the affordance that says "this slides". Phones only. */}
        <div
          aria-hidden="true"
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
          className={cn('flex touch-none justify-center pb-1 pt-2', isDrawer || !isDialog ? 'md:hidden' : 'sm:hidden')}
        >
          <span className="h-1 w-9 rounded-full bg-line" />
        </div>

        {bare ? (
          <>
            <h2 id={titleId} className="sr-only">
              {title}
            </h2>
            <button type="button" onClick={close} aria-label="Close" className="absolute end-2 top-2 z-10 grid h-11 w-11 place-items-center rounded-sm bg-surface/80 text-ink-2 backdrop-blur hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              <X size={18} aria-hidden="true" />
            </button>
          </>
        ) : (
          <div className="flex items-start gap-3 border-b border-line-2 px-4 pb-3 pt-2 md:px-5 md:pt-4">
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className="font-display text-lg font-bold leading-tight text-ink">
                {title}
              </h2>
              {subtitle ? (
                <p id={descId} className="mt-0.5 text-xs leading-snug text-ink-2">
                  {subtitle}
                </p>
              ) : null}
              {header}
            </div>
            <button
              type="button"
              onClick={close}
              aria-label="Close"
              className="-me-1.5 -mt-0.5 grid h-11 w-11 shrink-0 place-items-center rounded-sm text-ink-2 transition duration-1 ease-m hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70"
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{children}</div>

        {footer ? <div className="border-t border-line-2 bg-surface px-4 pb-[max(0.875rem,var(--m-safe-b))] pt-3 md:px-5">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  )
}
