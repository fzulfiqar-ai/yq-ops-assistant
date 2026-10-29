import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { EASE_SHEET, SHEET_MS, initialFocus, lockScroll, pushLayer, useKeyboardInset, useSheetDrag } from '@/lib/sheet'

/**
 * The portal's one modal surface: a bottom sheet on a phone, a dialog or a side drawer from `sm` up.
 * Labelled, focus-trapped, Esc-closable, scroll-locked, and it hands focus back to whatever opened it.
 *
 *   variant="dialog" → centred panel on >=sm (size: xs · sm · md · lg; default the wide 2xl)
 *   variant="drawer" → right-hand side panel on >=sm (size="lg": 32rem; default 27rem)
 *
 * Behaviour comes from lib/sheet.ts, shared with the marketplace (ship 2, 30-Oct-2026): one layer
 * stack (Escape closes only the top layer), a scroll lock that trades the Windows scrollbar gutter for
 * padding (the page behind never shifts), a drag that follows the finger 1:1 from the handle, the
 * header and the content's top (a flick or a long pull closes, anything less springs back), the
 * on-screen keyboard lifting the sheet (--kb), and a first focus that keeps an `autoFocus` field.
 *
 * Motion lives in the stylesheet the component carries: the entrance fills `backwards` only, so once
 * it has played the drag's inline transform is the panel's own (with `both`, the last keyframe kept
 * overriding it and the grab handle did nothing). The curves settle without a bounce. Under
 * `prefers-reduced-motion` the panel and its dim fade in and out — nothing slides.
 *
 * The exit. A sheet closes when its parent says so: X, the dim, Escape and a drag ask `onClose`, and a
 * parent that is busy may refuse (nothing moves then). Most parents unmount the sheet to close it, so
 * the exit runs on a snapshot: as the sheet leaves the tree, a copy of its last frame (inert, hidden
 * from assistive tech) slides or fades away above the page, and the scroll lock is released when it
 * has gone. Every close animates — Save, Apply, a pick, a route change — and no caller changes.
 *
 * Colour: the surface wears the portal tokens (bg-card, border, foreground), so the office's dark mode
 * reads. A sheet portals to <body>, outside the salesman shell's inline plum tokens; it copies the
 * tokens of the shell it was opened from, so a rep's sheet stays light and plum whatever the office
 * theme on that device.
 */

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
const FIELDS = 'input,textarea,select,[contenteditable="true"],[contenteditable=""]'

// Curves and times mirror lib/sheet.ts (EASE_SHEET / SHEET_MS in, EASE_IN / OUT_MS out). The exits
// are the snapshot's (.yq-ghost). Phones: the bottom sheet's height leaves room for the keyboard.
const MOTION = `
@keyframes yq-fade-in { from { opacity: 0 } }
@keyframes yq-fade-out { to { opacity: 0 } }
@keyframes yq-sheet-up { from { transform: translate3d(0, 100%, 0) } }
@keyframes yq-sheet-down { to { transform: translate3d(0, 100%, 0) } }
@keyframes yq-pop { from { opacity: 0; transform: translate3d(0, 8px, 0) scale(.985) } }
@keyframes yq-pop-out { to { opacity: 0; transform: translate3d(0, 6px, 0) scale(.985) } }
@keyframes yq-drawer-in { from { transform: translate3d(100%, 0, 0) } }
@keyframes yq-drawer-out { to { transform: translate3d(100%, 0, 0) } }
.yq-veil { animation: yq-fade-in 200ms cubic-bezier(.22, 1, .36, 1) backwards }
.yq-panel { animation: yq-sheet-up 280ms cubic-bezier(.32, .72, 0, 1) backwards }
.yq-ghost .yq-veil { animation: yq-fade-out 200ms cubic-bezier(.4, 0, 1, 1) both }
.yq-ghost .yq-panel { animation: yq-sheet-down 200ms cubic-bezier(.4, 0, 1, 1) both }
@media (max-width: 639.98px) {
  .yq-panel { margin-bottom: var(--kb, 0px); max-height: calc(92vh - var(--kb, 0px)) }
  @supports (height: 1dvh) { .yq-panel { max-height: calc(92dvh - var(--kb, 0px)) } }
}
@media (min-width: 640px) {
  .yq-panel { animation: yq-pop 200ms cubic-bezier(.22, 1, .36, 1) backwards }
  .yq-panel--drawer { animation: yq-drawer-in 280ms cubic-bezier(.32, .72, 0, 1) backwards }
  .yq-ghost .yq-panel { animation: yq-pop-out 160ms cubic-bezier(.4, 0, 1, 1) both }
  .yq-ghost .yq-panel--drawer { animation: yq-drawer-out 200ms cubic-bezier(.4, 0, 1, 1) both }
}
@media (prefers-reduced-motion: reduce) {
  .yq-veil, .yq-panel, .yq-panel--drawer { animation: yq-fade-in 200ms ease-out backwards !important }
  .yq-ghost .yq-veil, .yq-ghost .yq-panel, .yq-ghost .yq-panel--drawer { animation: yq-fade-out 160ms ease-in both !important }
}
`

function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.tabIndex >= 0 && el.offsetWidth + el.offsetHeight > 0,
  )
}

/** a media query as live state (the bottom-sheet layout is a breakpoint) */
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

/* ── <html data-portal-sheet> while a sheet is open: the toast stack steps aside (components/Toast) ── */

let openSheets = 0
function markOpen(delta: 1 | -1): void {
  openSheets = Math.max(0, openSheets + delta)
  const html = document.documentElement
  if (openSheets > 0) html.dataset.portalSheet = ''
  else delete html.dataset.portalSheet
}

/* ── who opened a sheet: focus goes back there (or, if that was inside a sheet now gone, to what opened that one) ── */

const openers = new WeakMap<Element, Element | null>()

/** read during the sheet's first render: before an `autoFocus` inside it has moved the focus */
function captureOpener(): [Element | null, Element | null] {
  const active = document.activeElement
  const host = active?.closest('[data-sheet-panel]')
  return [active, host ? (openers.get(host) ?? null) : null]
}

function restoreFocus([opener, fallback]: [Element | null, Element | null]): void {
  // another sheet that opened as this one closed holds the focus: leave it there
  if (document.activeElement?.closest('[data-sheet-panel]')) return
  const target = [opener, fallback].find((el): el is HTMLElement => el instanceof HTMLElement && el.isConnected && el !== document.body)
  target?.focus({ preventScroll: true })
}

/** the salesman shell re-points the tokens on its own root (inline); a sheet in <body> takes them along */
function wearShellTokens(root: HTMLElement, from: Element | null): void {
  const shell = from?.closest<HTMLElement>('[data-shell]') ?? document.querySelector<HTMLElement>('[data-shell]')
  if (!shell) return
  const s = shell.style
  for (let i = 0; i < s.length; i++) root.style.setProperty(s[i], s.getPropertyValue(s[i]))
}

/* ── the exit: a snapshot of the last frame leaves above the page ── */

interface Ghost {
  node: HTMLElement
  scrollTop: number
}

/** the order sheets opened in (data-sheet-seq on each root): a leaving sheet knows which ones are newer */
let seqNext = 0

function snapshot(root: HTMLElement, scroller: HTMLElement | null): Ghost {
  const node = root.cloneNode(true) as HTMLElement
  node.classList.add('yq-ghost')
  node.classList.remove('z-50')
  delete node.dataset.sheetSeq // not an open sheet
  node.style.pointerEvents = 'none'
  node.removeAttribute('role')
  node.setAttribute('aria-hidden', 'true')
  node.inert = true
  // one dialog in the document, one of each id
  node.querySelectorAll('[role="dialog"]').forEach((el) => {
    el.removeAttribute('role')
    el.removeAttribute('aria-modal')
  })
  node.querySelectorAll('[id]').forEach((el) => el.removeAttribute('id'))
  return { node, scrollTop: scroller?.scrollTop ?? 0 }
}

function playGhost({ node, scrollTop }: Ghost, seq: number, done: () => void): void {
  // Above the sheets still open beneath it (the keypad leaves over the product sheet), but under one
  // opened as this one closed (a pick that opens the next sheet: that one rises over the leaving copy).
  // Under the toasts either way (z-[100]).
  const newer = Array.from(document.querySelectorAll<HTMLElement>('[data-sheet-seq]')).some((el) => Number(el.dataset.sheetSeq) > seq)
  node.style.zIndex = newer ? '49' : '50'
  document.body.appendChild(node)
  // a copy starts at the top of its own scroller: put it where the reader was
  const body = node.querySelector<HTMLElement>('[data-sheet-body]')
  if (body && scrollTop) body.scrollTop = scrollTop
  let over = false
  const finish = () => {
    if (over) return
    over = true
    node.remove()
    done()
  }
  // the exits only (a spinner inside loops for ever)
  const exits = (node.getAnimations?.({ subtree: true }) ?? []).filter(
    (a) => a.effect?.getTiming().iterations !== Infinity && String((a as CSSAnimation).animationName || '').startsWith('yq-'),
  )
  if (!exits.length) return finish()
  void Promise.all(exits.map((a) => a.finished)).then(finish, finish)
  window.setTimeout(finish, 700) // a stalled clock never keeps the page locked
}

/* ── the sheet ── */

export interface SheetProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** Small line under the title (also used as the dialog's accessible description). */
  subtitle?: ReactNode
  variant?: 'dialog' | 'drawer'
  /** The panel's width from `sm` up (see above). Leave it out for today's widths. */
  size?: 'xs' | 'sm' | 'md' | 'lg'
  children: ReactNode
  /** Under the title in the fixed header (chips, a status line): it never scrolls away. */
  header?: ReactNode
  /** Pinned to the bottom of the panel, outside the scroll area (lifted above a phone's keyboard). */
  footer?: ReactNode
  className?: string
  /** The panel's writing direction and language (the Spotted sheet in Arabic reads right to left). */
  dir?: 'ltr' | 'rtl'
  lang?: string
}

export function Sheet(props: SheetProps) {
  // mounted only while open: the layer's effects run with the panel in the page and clean up as it
  // leaves (that cleanup takes the snapshot the exit plays on)
  if (!props.open) return null
  return createPortal(<SheetLayer {...props} />, document.body)
}

const DIALOG_W = { xs: 'sm:max-w-xs', sm: 'sm:max-w-sm', md: 'sm:max-w-md', lg: 'sm:max-w-lg' } as const

function SheetLayer({ onClose, title, subtitle, variant = 'dialog', size, children, header, footer, className, dir, lang }: SheetProps) {
  const titleId = useId()
  const descId = useId()
  const [opener] = useState(captureOpener)
  const rootRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLElement | null>(null)
  const dimRef = useRef<HTMLElement | null>(null)
  const scrollerRef = useRef<HTMLElement | null>(null)
  const mounted = useRef(false)
  const draggedOut = useRef(false)

  // the newest onClose, read when it fires: a parent's new handler on every render (each keystroke)
  // must not re-run the open effect below, whose focus step would close a phone's keyboard
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])
  const close = useCallback(() => onCloseRef.current(), [])

  const bottomSheet = useMedia('(max-width: 639.98px)')

  // a drag past the line has already slid the sheet away with its dim: ask the parent at once (no
  // second exit). A parent that refuses (busy saving) gets its sheet back.
  const onDragDismiss = useCallback(() => {
    draggedOut.current = true
    flushSync(() => onCloseRef.current())
    if (!mounted.current) return
    draggedOut.current = false
    const panel = panelRef.current
    const dim = dimRef.current
    if (panel) {
      panel.style.transition = `transform ${SHEET_MS}ms ${EASE_SHEET}`
      panel.style.transform = ''
    }
    if (dim) {
      dim.style.transition = `opacity ${SHEET_MS}ms`
      dim.style.opacity = ''
    }
    window.setTimeout(() => {
      if (panel) panel.style.transition = ''
      if (dim) dim.style.transition = ''
    }, SHEET_MS)
  }, [])

  const { attachPanel, attachBackdrop, attachScroller, onPointerDown, panelEl } = useSheetDrag(bottomSheet, onDragDismiss)
  useKeyboardInset(panelEl, bottomSheet)

  const setPanel = useCallback(
    (el: HTMLDivElement | null) => {
      attachPanel(el)
      panelRef.current = el
    },
    [attachPanel],
  )
  const setDim = useCallback(
    (el: HTMLButtonElement | null) => {
      attachBackdrop(el)
      dimRef.current = el
    },
    [attachBackdrop],
  )
  const setScroller = useCallback(
    (el: HTMLDivElement | null) => {
      attachScroller(el)
      scrollerRef.current = el
    },
    [attachScroller],
  )

  // A layout effect: the lock lands before the sheet's first paint, so nothing steps sideways during
  // the entrance. It runs once per open (its inputs are stable).
  useLayoutEffect(() => {
    const root = rootRef.current
    const panel = panelEl()
    if (!root || !panel) return
    mounted.current = true
    const seq = ++seqNext
    root.dataset.sheetSeq = String(seq)
    openers.set(panel, opener[0])
    wearShellTokens(root, opener[0])
    const unlock = lockScroll()
    const layer = pushLayer()
    markOpen(1)
    initialFocus(panel)

    const onKey = (e: KeyboardEvent) => {
      if (!layer.isTop()) return
      // the sheet's keys while the focus is in it (or nowhere): a layer opened over it outside the stack —
      // the command palette (Ctrl K) — keeps its own Escape and Tab
      const at = document.activeElement
      if (at && at !== document.body && !panel.contains(at)) return
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        close()
        return
      }
      if (e.key !== 'Tab') return
      const nodes = focusables(panel)
      if (!nodes.length) {
        e.preventDefault()
        panel.focus({ preventScroll: true })
        return
      }
      const firstNode = nodes[0]
      const lastNode = nodes[nodes.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === firstNode || active === panel || !panel.contains(active))) {
        e.preventDefault()
        lastNode.focus()
      } else if (!e.shiftKey && (active === lastNode || !panel.contains(active))) {
        e.preventDefault()
        firstNode.focus()
      }
    }
    document.addEventListener('keydown', onKey, true)

    return () => {
      mounted.current = false
      document.removeEventListener('keydown', onKey, true)
      layer.release()
      markOpen(-1)
      // the snapshot is taken now, while the panel is still in the page; whether it plays is known a
      // microtask later — React has removed the panel by then on a real close, while StrictMode's
      // rehearsal of an unmount (development only) leaves it in place and mounts the effect again
      const ghost = draggedOut.current ? null : snapshot(root, scrollerRef.current)
      queueMicrotask(() => {
        if (root.isConnected) return unlock()
        restoreFocus(opener)
        if (ghost) playGhost(ghost, seq, unlock)
        else unlock()
      })
    }
  }, [panelEl, opener, close])

  // On a phone the keyboard shortens the panel (--kb): keep the field being typed in above its fold.
  useEffect(() => {
    const vv = window.visualViewport
    if (!bottomSheet || !vv) return
    let raf = 0
    const keep = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const body = scrollerRef.current
        const field = document.activeElement
        if (!body || !(field instanceof HTMLElement) || !body.contains(field) || !field.matches(FIELDS)) return
        const over = field.getBoundingClientRect().bottom + 12 - body.getBoundingClientRect().bottom
        if (over > 0) body.scrollTop += over
      })
    }
    vv.addEventListener('resize', keep)
    return () => {
      vv.removeEventListener('resize', keep)
      cancelAnimationFrame(raf)
    }
  }, [bottomSheet])

  const drawer = variant === 'drawer'
  const panelShape = drawer
    ? cn(
        'mt-auto w-full rounded-t-[20px] sm:mt-0 sm:ml-auto sm:h-full sm:max-h-none sm:rounded-none sm:rounded-l-[20px]',
        size === 'lg' ? 'sm:w-[32rem]' : 'sm:w-[27rem]',
      )
    : cn('mt-auto w-full rounded-t-[20px] sm:m-auto sm:max-h-[88vh] sm:w-full sm:rounded-[20px]', size ? DIALOG_W[size] : 'sm:max-w-2xl')

  return (
    <div ref={rootRef} className="fixed inset-0 z-50 flex" role="presentation">
      <style>{MOTION}</style>
      <button
        ref={setDim}
        type="button"
        aria-label="Close"
        tabIndex={-1}
        onClick={close}
        // a plain dim (no blur: his integrated graphics); darker on a desktop, where nothing else sets the layer apart
        className="yq-veil absolute inset-0 h-full w-full cursor-default bg-[#1A1428]/45 lg:bg-[#1A1428]/55"
      />
      <div
        ref={setPanel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? descId : undefined}
        dir={dir}
        lang={lang}
        tabIndex={-1}
        data-sheet-panel=""
        className={cn(
          'yq-panel relative flex flex-col overflow-hidden bg-card text-card-foreground shadow-[0_-8px_44px_-12px_rgba(24,16,48,.32)] outline-none',
          drawer && 'yq-panel--drawer',
          panelShape,
          className,
        )}
      >
        {/* Grab handle — the affordance that says "this slides", and a 24 px strip to catch. Phones only. */}
        <div aria-hidden="true" onPointerDown={onPointerDown} className="flex touch-none justify-center pb-2 pt-2.5 sm:hidden">
          <span className="h-1 w-9 rounded-full bg-border" />
        </div>

        <div onPointerDown={onPointerDown} className="flex touch-none items-start gap-3 border-b border-border px-4 pb-3.5 pt-1 sm:touch-auto sm:px-5 sm:pt-4">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="font-display text-[17px] font-bold leading-tight tracking-[-0.01em] text-foreground">
              {title}
            </h2>
            {subtitle ? (
              <p id={descId} className="mt-0.5 text-[11.5px] leading-snug text-muted-foreground">
                {subtitle}
              </p>
            ) : null}
            {header}
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close"
            className="-me-1.5 -mt-0.5 grid h-11 w-11 shrink-0 place-items-center rounded-xl text-muted-foreground transition duration-150 ease-out hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <div ref={setScroller} data-sheet-body="" className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {children}
        </div>

        {footer ? (
          <div className="border-t border-border bg-card px-4 pb-[max(0.875rem,env(safe-area-inset-bottom))] pt-3 sm:px-5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}
