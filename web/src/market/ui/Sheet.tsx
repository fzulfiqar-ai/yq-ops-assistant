import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { initialFocus, lockScroll, pushLayer, useKeyboardInset, useSheetDrag, OUT_MS } from '@/lib/sheet'
import { S } from '../strings'

/**
 * The market's one modal surface.
 *
 *   variant="sheet"  â€” bottom sheet on phones, centred dialog from `md` up (default)
 *   variant="dialog" â€” centred dialog from `sm` up (palette, keypad)
 *   variant="drawer" â€” bottom sheet on phones, side panel (inline-end) from `md` up
 *
 * Accessible by construction: labelled, focus-trapped, Esc-closable, scroll-locked, focus
 * restored. Enter/exit run on CSS keyframes (market.css); the `closing` state plays the exit and
 * unmounts after it, so nothing snaps. `prefers-reduced-motion` collapses every duration globally.
 *
 * Sheet slide (28-Oct-2026, lib/sheet.ts): on a phone the sheet follows the finger from its handle,
 * its header, and its content when that is scrolled to the top; a flick or a long pull closes it, a
 * short one springs back, and it settles without a bounce (the enter curve used to overshoot ~7 px).
 * The enter animation fills `backwards` only, so once it has played the drag's transform is the
 * sheet's own (with `both`, the finished keyframe kept overriding it and the sheet never moved).
 * Escape closes only the top layer; a field that autoFocus put in focus keeps it; the on-screen
 * keyboard lifts the sheet instead of covering its footer.
 */

const FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetWidth + el.offsetHeight > 0)
}

/** a media query as live state (the bottom-sheet layout is a breakpoint, not a viewport class) */
function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (fn) => {
      const m = window.matchMedia(query)
      m.addEventListener('change', fn)
      return () => m.removeEventListener('change', fn)
    },
    () => window.matchMedia(query).matches,
    () => false,
  )
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
  /** hide the title row (the body renders its own header) â€” the title is still announced */
  bare?: boolean
  className?: string
  /** a view-transition name for the panel root (card â†’ panel morph) */
  vtName?: string
}

export function Sheet({ open, onClose, title, subtitle, variant = 'sheet', size = 'md', children, footer, header, bare, className, vtName }: SheetProps) {
  const restoreRef = useRef<HTMLElement | null>(null)
  const titleId = useId()
  const descId = useId()
  const [closing, setClosing] = useState(false)

  const isDrawer = variant === 'drawer'
  const isDialog = variant === 'dialog'
  // the bottom-sheet layout: phones (a dialog stays a sheet only below `sm`)
  const bottomSheet = useMedia(isDialog ? '(max-width: 639px)' : '(max-width: 767px)')

  const close = useCallback(() => {
    if (closing) return
    setClosing(true)
    window.setTimeout(onClose, OUT_MS)
  }, [closing, onClose])

  const [openSeen, setOpenSeen] = useState(open)
  if (openSeen !== open) {
    setOpenSeen(open)
    setClosing(false)
  }

  // a drag that closed the sheet has already slid it away: unmount, no second exit
  const { attachPanel, attachBackdrop, attachScroller, onPointerDown, panelEl } = useSheetDrag(open && bottomSheet && !closing, onClose)
  useKeyboardInset(panelEl, open && bottomSheet)

  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement as HTMLElement | null
    const unlock = lockScroll()
    const layer = pushLayer()
    document.body.dataset.sheetOpen = '1'
    initialFocus(panelEl())

    const onKey = (e: KeyboardEvent) => {
      if (!layer.isTop()) return
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        close()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelEl()
      const nodes = focusables(panel)
      if (!nodes.length) {
        e.preventDefault()
        panel?.focus({ preventScroll: true })
        return
      }
      const firstNode = nodes[0]
      const lastNode = nodes[nodes.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === firstNode || active === panel)) {
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
      layer.release()
      unlock()
      if (!document.querySelector('[data-sheet-panel]:not([data-leaving])')) delete document.body.dataset.sheetOpen
      restoreRef.current?.focus?.({ preventScroll: true })
    }
  }, [open, close, panelEl])

  if (!open) return null

  const width = size === 'lg' ? 'md:w-[34rem]' : 'md:w-[28rem]'
  const dialogMax = size === 'lg' ? 'sm:max-w-2xl' : 'sm:max-w-lg'

  const shape = isDrawer
    ? cn('mt-auto max-h-[92dvh] w-full rounded-t-xl md:mt-0 md:ms-auto md:h-full md:max-h-none md:rounded-none md:rounded-s-xl', width)
    : isDialog
      ? cn('mt-auto max-h-[92dvh] w-full rounded-t-xl sm:m-auto sm:max-h-[86dvh] sm:rounded-xl', dialogMax)
      : cn('mt-auto max-h-[92dvh] w-full rounded-t-xl md:m-auto md:max-h-[86dvh] md:rounded-xl', dialogMax)

  // enter fills `backwards` only (see above); the exit fills `both` and the sheet unmounts after it
  const enter = isDrawer
    ? 'max-md:[animation:m-sheet-up_var(--m-d-sheet)_var(--m-ease-sheet)_backwards] md:[animation:m-drawer-in_var(--m-d-sheet)_var(--m-ease-sheet)_backwards]'
    : isDialog
      ? 'max-sm:[animation:m-sheet-up_var(--m-d-sheet)_var(--m-ease-sheet)_backwards] sm:[animation:m-pop_var(--m-d-2)_var(--m-ease-out)_backwards]'
      : 'max-md:[animation:m-sheet-up_var(--m-d-sheet)_var(--m-ease-sheet)_backwards] md:[animation:m-pop_var(--m-d-2)_var(--m-ease-out)_backwards]'
  const exit = isDrawer
    ? 'max-md:[animation:m-sheet-down_200ms_var(--m-ease-in)_both] md:[animation:m-drawer-out_200ms_var(--m-ease-in)_both]'
    : isDialog
      ? 'max-sm:[animation:m-sheet-down_200ms_var(--m-ease-in)_both] sm:[animation:m-pop-out_160ms_var(--m-ease-in)_both]'
      : 'max-md:[animation:m-sheet-down_200ms_var(--m-ease-in)_both] md:[animation:m-pop-out_160ms_var(--m-ease-in)_both]'

  // a bottom sheet rises above the on-screen keyboard (--kb, written by useKeyboardInset)
  const lift = bottomSheet ? { marginBottom: 'var(--kb, 0px)', maxHeight: 'calc(92dvh - var(--kb, 0px))' } : undefined

  return createPortal(
    <div className="fixed inset-0 z-sheet flex" role="presentation">
      <button
        ref={attachBackdrop}
        type="button"
        aria-label={S.states.close}
        tabIndex={-1}
        onClick={close}
        style={{ animation: closing ? `m-fade-in ${OUT_MS}ms ease-in reverse both` : 'm-fade-in 200ms ease-out both' }}
        className="absolute inset-0 h-full w-full cursor-default bg-ink/45 [@media(hover:hover)_and_(pointer:fine)]:backdrop-blur-[2px]"
      />
      <div
        ref={attachPanel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? descId : undefined}
        tabIndex={-1}
        data-sheet-panel=""
        data-leaving={closing ? '' : undefined}
        style={{ viewTransitionName: vtName, ...lift }}
        className={cn('relative flex flex-col overflow-hidden bg-surface shadow-3 outline-none', shape, closing ? exit : enter, className)}
      >
        {/* Grab handle â€” the affordance that says "this slides". Phones only; a 24 px strip to catch. */}
        <div aria-hidden="true" onPointerDown={onPointerDown} className={cn('flex touch-none justify-center pb-2 pt-2.5', isDrawer || !isDialog ? 'md:hidden' : 'sm:hidden')}>
          <span className="h-1 w-9 rounded-full bg-line" />
        </div>

        {bare ? (
          <>
            <h2 id={titleId} className="sr-only">
              {title}
            </h2>
            <button type="button" onClick={close} aria-label={S.states.close} className="absolute end-2 top-2 z-10 grid h-11 w-11 place-items-center rounded-sm bg-surface/90 text-ink-2 hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              <X size={18} aria-hidden="true" />
            </button>
          </>
        ) : (
          <div onPointerDown={onPointerDown} className="flex touch-none items-start gap-3 border-b border-line-2 px-4 pb-3 pt-2 md:touch-auto md:px-5 md:pt-4">
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
              aria-label={S.states.close}
              className="-me-1.5 -mt-0.5 grid h-11 w-11 shrink-0 place-items-center rounded-sm text-ink-2 transition duration-1 ease-m hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70"
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>
        )}

        <div ref={attachScroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {children}
        </div>

        {footer ? <div className="border-t border-line-2 bg-surface px-4 pb-[max(0.875rem,var(--m-safe-b))] pt-3 md:px-5">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  )
}
