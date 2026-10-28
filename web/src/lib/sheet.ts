import { useEffect, useState, type PointerEvent as ReactPointerEvent, type RefObject } from 'react'

/**
 * Sheet behaviour shared by both builds (28-Oct-2026 polish, motion list M5): the marketplace's
 * ui/Sheet now, the portal's components/ui/sheet next. Behaviour only — each build keeps its look.
 *
 *  - lockScroll(): a body scroll lock that nests (a sheet over a sheet). The page never shifts
 *    sideways: html carries `scrollbar-gutter: stable` (market.css / index.css).
 *  - pushLayer(): one stack of open layers, so Escape closes only the top one (a keypad over the
 *    product panel used to close both).
 *  - initialFocus(): keeps a focus React's autoFocus already put inside the panel (the keypad, the
 *    ⌘K field); otherwise the panel itself — never the close button, which showed a ring on touch.
 *  - useSheetDrag(): the sheet follows the finger 1:1 (a transform on the panel, no React render per
 *    move), a pull upward meets resistance, a flick or a long pull closes it and anything less
 *    springs back. From the handle and header (pointer events), and from the content when it is
 *    scrolled to its top and the finger pulls down (touch events), as native sheets do.
 *  - useKeyboardInset(): how much of the panel the on-screen keyboard covers. iOS keeps the layout
 *    viewport when the keyboard opens, so a bottom sheet's footer sat behind it.
 */

export const EASE_SHEET = 'cubic-bezier(0.32, 0.72, 0, 1)' // settles, never overshoots
export const EASE_IN = 'cubic-bezier(0.4, 0, 1, 1)'
export const SHEET_MS = 280
export const OUT_MS = 200

/* ── scroll lock ─────────────────────────────────────────────────────────────────────────────── */

let locks = 0
let savedOverflow = ''

export function lockScroll(): () => void {
  const body = document.body
  if (locks++ === 0) {
    savedOverflow = body.style.overflow
    body.style.overflow = 'hidden'
  }
  let released = false
  return () => {
    if (released) return
    released = true
    if (--locks === 0) body.style.overflow = savedOverflow
  }
}

/* ── layers ──────────────────────────────────────────────────────────────────────────────────── */

const layers: symbol[] = []

export interface Layer {
  isTop: () => boolean
  release: () => void
}

export function pushLayer(): Layer {
  const id = Symbol('layer')
  layers.push(id)
  return {
    isTop: () => layers[layers.length - 1] === id,
    release: () => {
      const i = layers.indexOf(id)
      if (i >= 0) layers.splice(i, 1)
    },
  }
}

/* ── focus ───────────────────────────────────────────────────────────────────────────────────── */

export function initialFocus(panel: HTMLElement | null): void {
  if (!panel || panel.contains(document.activeElement)) return
  const wanted = panel.querySelector<HTMLElement>('[data-autofocus]')
  ;(wanted || panel).focus({ preventScroll: true })
}

/* ── drag to dismiss ─────────────────────────────────────────────────────────────────────────── */

/** an upward pull gives way less and less, never more than ~40 px */
const resist = (px: number) => 40 * (1 - 1 / (1 + px / 120))

const INTERACTIVE = 'button,a[href],input,textarea,select,label,[role="button"],[role="tab"],[contenteditable="true"]'
const FIELDS = 'textarea,input,select,[contenteditable="true"],[contenteditable=""]'

/**
 * May a downward touch that started on `target` pull the sheet whose scrolling body is `body`?
 * Only when the body is at its top, the finger is not in a field (there it moves the caret or
 * scrolls the field: the pasted list of Quick order is a 7-row textarea), and nothing scrollable
 * between the finger and the body is scrolled down (that scroll is the finger's first).
 */
export function pullAllowed(target: Element | null, body: Element): boolean {
  if (body.scrollTop > 0) return false
  for (let el: Element | null = target; el && el !== body; el = el.parentElement) {
    if (el.matches(FIELDS) || el.scrollTop > 0) return false
  }
  return true
}

/**
 * One sheet's drag. A plain object rather than hook state: it moves DOM nodes on every pointer
 * frame, which must never re-render React. The elements arrive through the callback refs it hands
 * out (attachPanel / attachBackdrop / attachScroller); the component tells it when it may drag.
 */
export class SheetDrag {
  private panel: HTMLElement | null = null
  private backdrop: HTMLElement | null = null
  private unwatch: (() => void) | null = null
  private enabled = false
  private dismiss: () => void = () => {}
  private d = { active: false, startY: 0, dy: 0, lastY: 0, lastT: 0, v: 0, height: 0 }

  configure(enabled: boolean, dismiss: () => void): void {
    this.enabled = enabled
    this.dismiss = dismiss
  }

  readonly attachPanel = (el: HTMLElement | null): void => {
    this.panel = el
  }

  /** the panel element, for effects and handlers (focus, the keyboard inset, the Tab trap) */
  readonly panelEl = (): HTMLElement | null => this.panel

  readonly attachBackdrop = (el: HTMLElement | null): void => {
    this.backdrop = el
  }

  /** the scrolling body: a pull down from its top drags the sheet */
  readonly attachScroller = (el: HTMLElement | null): void => {
    this.unwatch?.()
    this.unwatch = el ? this.watch(el) : null
  }

  private begin(y: number): boolean {
    const el = this.panel
    if (!this.enabled || !el) return false
    this.d = { active: true, startY: y, dy: 0, lastY: y, lastT: performance.now(), v: 0, height: el.getBoundingClientRect().height }
    el.style.transition = 'none'
    if (this.backdrop) this.backdrop.style.transition = 'none'
    return true
  }

  private follow(y: number): void {
    const d = this.d
    const el = this.panel
    if (!d.active || !el) return
    const t = performance.now()
    if (t > d.lastT) d.v = (y - d.lastY) / (t - d.lastT) // px per ms, + is down
    d.lastY = y
    d.lastT = t
    const raw = y - d.startY
    d.dy = raw >= 0 ? raw : -resist(-raw)
    el.style.transform = `translate3d(0, ${d.dy}px, 0)`
    if (this.backdrop) this.backdrop.style.opacity = String(Math.max(0, 1 - Math.max(0, d.dy) / d.height))
  }

  private end(): void {
    const d = this.d
    const el = this.panel
    if (!d.active || !el) return
    d.active = false
    const dismiss = d.dy > Math.min(120, d.height * 0.25) || (d.dy > 24 && d.v > 0.5)
    el.style.transition = dismiss ? `transform ${OUT_MS}ms ${EASE_IN}` : `transform ${SHEET_MS}ms ${EASE_SHEET}`
    el.style.transform = dismiss ? 'translate3d(0, 100%, 0)' : ''
    const bd = this.backdrop
    if (bd) {
      bd.style.transition = `opacity ${dismiss ? OUT_MS : SHEET_MS}ms`
      bd.style.opacity = dismiss ? '0' : ''
    }
    window.setTimeout(
      () => {
        if (dismiss) this.dismiss()
        else {
          el.style.transition = ''
          if (bd) bd.style.transition = ''
        }
      },
      dismiss ? OUT_MS : SHEET_MS,
    )
  }

  /** the handle and the header: pointer events (mouse and touch; give the area touch-action: none) */
  readonly onPointerDown = (e: ReactPointerEvent<HTMLElement>): void => {
    if (e.button !== 0 || (e.target as HTMLElement).closest(INTERACTIVE)) return
    if (!this.begin(e.clientY)) return
    const area = e.currentTarget
    area.setPointerCapture(e.pointerId)
    const move = (ev: PointerEvent) => this.follow(ev.clientY)
    const up = () => {
      area.removeEventListener('pointermove', move)
      area.removeEventListener('pointerup', up)
      area.removeEventListener('pointercancel', up)
      this.end()
    }
    area.addEventListener('pointermove', move)
    area.addEventListener('pointerup', up)
    area.addEventListener('pointercancel', up)
  }

  // The content: a pull DOWN that starts while it is scrolled to its top drags the sheet instead of
  // bouncing the list. Non-passive touchmove, so the browser's own pan can be held back for it.
  private watch(body: HTMLElement): () => void {
    let startX = 0
    let startY = 0
    let startTarget: Element | null = null
    let pulling = false
    let decided = false
    const onStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      startX = e.touches[0].clientX
      startY = e.touches[0].clientY
      startTarget = e.target instanceof Element ? e.target : null
      pulling = false
      decided = false
    }
    const onMove = (e: TouchEvent) => {
      const x = e.touches[0].clientX
      const y = e.touches[0].clientY
      if (!decided) {
        const dx = Math.abs(x - startX)
        const dy = y - startY
        if (Math.abs(dy) < 6 && dx < 6) return
        decided = true
        // only a pull that is mostly DOWN, from the very top: a sideways swipe (a photo row), an
        // upward scroll, a field and an inner list still scrolled down stay the content's own
        pulling = dy > dx && pullAllowed(startTarget, body) && this.begin(startY)
      }
      if (!pulling) return
      e.preventDefault()
      this.follow(y)
    }
    const onEnd = () => {
      if (pulling) this.end()
      pulling = false
    }
    body.addEventListener('touchstart', onStart, { passive: true })
    body.addEventListener('touchmove', onMove, { passive: false })
    body.addEventListener('touchend', onEnd)
    body.addEventListener('touchcancel', onEnd)
    return () => {
      body.removeEventListener('touchstart', onStart)
      body.removeEventListener('touchmove', onMove)
      body.removeEventListener('touchend', onEnd)
      body.removeEventListener('touchcancel', onEnd)
    }
  }
}

export interface SheetDragApi {
  /** callback refs: the panel that moves, the backdrop that fades with it, the scrolling body */
  attachPanel: (el: HTMLElement | null) => void
  attachBackdrop: (el: HTMLElement | null) => void
  attachScroller: (el: HTMLElement | null) => void
  /** on the handle and the header (give them touch-action: none) */
  onPointerDown: (e: ReactPointerEvent<HTMLElement>) => void
  /** the panel element, for effects and handlers */
  panelEl: () => HTMLElement | null
}

/** `enabled`: a bottom-sheet layout that is open and not closing. `onDismiss`: the drag already slid
 *  the sheet off-screen — unmount it without playing the exit again. Returns stable functions only
 *  (destructure them): the controller itself never reaches render. */
export function useSheetDrag(enabled: boolean, onDismiss: () => void): SheetDragApi & { configure: SheetDrag['configure'] } {
  const [api] = useState(() => {
    const d = new SheetDrag()
    return {
      configure: (on: boolean, done: () => void) => d.configure(on, done),
      attachPanel: d.attachPanel,
      attachBackdrop: d.attachBackdrop,
      attachScroller: d.attachScroller,
      onPointerDown: d.onPointerDown,
      panelEl: d.panelEl,
    }
  })
  const { configure } = api
  useEffect(() => {
    configure(enabled, onDismiss)
  }, [configure, enabled, onDismiss])
  return api
}

/* ── the on-screen keyboard ──────────────────────────────────────────────────────────────────── */

/** Writes --kb (px of the layout viewport the keyboard covers) on the panel while `active`. */
export function useKeyboardInset(panel: RefObject<HTMLElement | null> | (() => HTMLElement | null), active: boolean): void {
  useEffect(() => {
    const vv = window.visualViewport
    const el = typeof panel === 'function' ? panel() : panel.current
    if (!active || !vv || !el) return
    const apply = () => {
      const kb = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop))
      el.style.setProperty('--kb', `${kb}px`)
    }
    apply()
    vv.addEventListener('resize', apply)
    vv.addEventListener('scroll', apply)
    return () => {
      vv.removeEventListener('resize', apply)
      vv.removeEventListener('scroll', apply)
      el.style.removeProperty('--kb')
    }
  }, [panel, active])
}
