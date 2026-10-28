import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { lockScroll, pushLayer, OUT_MS } from '@/lib/sheet'
import { S } from '../strings'

export interface LightboxPhoto {
  /** candidates in order — the 1024 / 512 WebP rendition first, the original last (lib/photos.ts) */
  srcs: string[]
  label: string
}

/**
 * Full-screen photo viewer: tap a product photo → the photo fills the screen.
 *
 * Gestures (28-Oct-2026), all on the photo itself and all transforms on one element (no React
 * render per frame):
 *  - swipe sideways → the next / previous photo (reading-direction aware); arrows and keys too
 *  - swipe down → close, the photo following the finger and the night fading with it
 *  - double-tap → 2.5× zoom on the photo, again → back; pinch with two fingers; drag to pan while
 *    zoomed. The page underneath never zooms (it used to: the old native pinch zoomed everything).
 * Escape closes only the lightbox (the shared layer stack, lib/sheet.ts), never the product panel
 * under it; it opens and closes with a fade.
 *
 * Each photo is a fallback chain, not one URL (R6): a candidate that 404s hands over to the next,
 * so the viewer shows the WebP rendition when it exists and the original photo when it does not.
 */
const SWIPE_PX = 56
const CLOSE_PX = 110
const ZOOM = 2.5
const MAX_ZOOM = 4

export function Lightbox({ photos, start = 0, alt, onClose }: { photos: LightboxPhoto[]; start?: number; alt: string; onClose: () => void }) {
  const [i, setI] = useState(start)
  const [failed, setFailed] = useState<Record<number, number>>({})
  const [closing, setClosing] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  const n = photos.length

  const close = useCallback(() => {
    setClosing(true)
    window.setTimeout(onClose, OUT_MS)
  }, [onClose])
  const go = useCallback((dir: 1 | -1) => setI((v) => (v + dir + n) % n), [n])

  // mounted = open: the lock, the layer and the focus are taken once, not again whenever the panel
  // re-renders and hands in a new onClose (that re-ran them, like the sheet's; final review). The
  // key handler reads the latest close and go from here.
  const keys = useRef({ close, go })
  useEffect(() => {
    keys.current = { close, go }
  }, [close, go])

  useEffect(() => {
    const unlock = lockScroll()
    const layer = pushLayer()
    const restore = document.activeElement as HTMLElement | null
    rootRef.current?.focus({ preventScroll: true })
    const onKey = (e: KeyboardEvent) => {
      if (!layer.isTop()) return
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        keys.current.close()
        return
      }
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
      // the arrow that points toward the reading direction's end is "next": → in English, ← in Arabic
      const forward = (e.key === 'ArrowRight') !== (document.documentElement.dir === 'rtl')
      keys.current.go(forward ? 1 : -1)
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      layer.release()
      unlock()
      restore?.focus?.({ preventScroll: true })
    }
  }, [])

  /* ── gestures: one transform, written straight to the photo ── */
  const g = useRef({ scale: 1, tx: 0, ty: 0, pointers: new Map<number, { x: number; y: number }>(), mode: '' as '' | 'swipe-x' | 'swipe-y' | 'pan' | 'pinch', sx: 0, sy: 0, stx: 0, sty: 0, dist: 0, startScale: 1, lastTap: 0, tapX: 0, tapY: 0 })

  const paint = (animate: boolean, x = g.current.tx, y = g.current.ty, s = g.current.scale) => {
    const img = imgRef.current
    if (!img) return
    img.style.transition = animate ? 'transform 260ms cubic-bezier(0.22, 1, 0.36, 1)' : 'none'
    img.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${s})`
  }
  const fadeNight = (alpha: number) => {
    if (rootRef.current) rootRef.current.style.backgroundColor = `hsl(268 28% 11% / ${(0.95 * alpha).toFixed(3)})`
  }
  const resetView = (animate = true) => {
    Object.assign(g.current, { scale: 1, tx: 0, ty: 0 })
    paint(animate)
    fadeNight(1)
  }
  /** inside the photo as drawn (object-contain letterboxes it inside the full-size element) */
  const inPhoto = (x: number, y: number) => {
    const img = imgRef.current
    if (!img || !img.naturalWidth || !img.naturalHeight) return true
    const r = img.getBoundingClientRect()
    const k = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight)
    const w = img.naturalWidth * k
    const h = img.naturalHeight * k
    const left = r.left + (r.width - w) / 2
    const top = r.top + (r.height - h) / 2
    return x >= left && x <= left + w && y >= top && y <= top + h
  }

  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    const st = g.current
    e.currentTarget.setPointerCapture(e.pointerId)
    st.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (st.pointers.size === 2) {
      const [a, b] = [...st.pointers.values()]
      st.mode = 'pinch'
      st.dist = Math.hypot(a.x - b.x, a.y - b.y) || 1
      st.startScale = st.scale
      return
    }
    st.mode = ''
    st.sx = e.clientX
    st.sy = e.clientY
    st.stx = st.tx
    st.sty = st.ty
  }

  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const st = g.current
    if (!st.pointers.has(e.pointerId)) return
    st.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (st.mode === 'pinch' && st.pointers.size >= 2) {
      const [a, b] = [...st.pointers.values()]
      st.scale = Math.min(MAX_ZOOM, Math.max(1, (st.startScale * Math.hypot(a.x - b.x, a.y - b.y)) / st.dist))
      paint(false)
      return
    }
    const dx = e.clientX - st.sx
    const dy = e.clientY - st.sy
    if (!st.mode) {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return
      st.mode = st.scale > 1.05 ? 'pan' : Math.abs(dx) > Math.abs(dy) ? 'swipe-x' : 'swipe-y'
    }
    if (st.mode === 'pan') {
      st.tx = st.stx + dx
      st.ty = st.sty + dy
      paint(false)
    } else if (st.mode === 'swipe-x' && n > 1) {
      paint(false, dx, 0, 1)
    } else if (st.mode === 'swipe-y') {
      const down = Math.max(0, dy)
      paint(false, 0, down, 1 - Math.min(0.15, down / 2000))
      fadeNight(1 - Math.min(0.7, down / 400))
    }
  }

  const onUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const st = g.current
    if (!st.pointers.has(e.pointerId)) return
    st.pointers.delete(e.pointerId)
    if (st.mode === 'pinch') {
      if (st.pointers.size === 0) {
        st.mode = ''
        if (st.scale < 1.05) resetView()
      }
      return
    }
    const dx = e.clientX - st.sx
    const dy = e.clientY - st.sy
    if (!st.mode) {
      // a tap: two within 300 ms in the same place toggle the zoom; one tap outside the photo closes
      const now = performance.now()
      if (now - st.lastTap < 300 && Math.hypot(e.clientX - st.tapX, e.clientY - st.tapY) < 24) {
        st.lastTap = 0
        if (st.scale > 1.05) resetView()
        else {
          const img = imgRef.current?.getBoundingClientRect()
          const cx = img ? img.left + img.width / 2 : e.clientX
          const cy = img ? img.top + img.height / 2 : e.clientY
          // zoom toward the tapped point
          Object.assign(st, { scale: ZOOM, tx: (cx - e.clientX) * (ZOOM - 1), ty: (cy - e.clientY) * (ZOOM - 1) })
          paint(true)
        }
        return
      }
      st.lastTap = now
      st.tapX = e.clientX
      st.tapY = e.clientY
      if (st.scale <= 1.05 && !inPhoto(e.clientX, e.clientY)) {
        // the empty night around the photo: close, unless this was the first half of a double tap
        window.setTimeout(() => {
          if (g.current.lastTap === now) close()
        }, 300)
      }
      return
    }
    if (st.mode === 'swipe-x') {
      st.mode = ''
      const rtl = document.documentElement.dir === 'rtl'
      if (n > 1 && Math.abs(dx) > SWIPE_PX) go((dx < 0) !== rtl ? 1 : -1)
      resetView()
      return
    }
    if (st.mode === 'swipe-y') {
      st.mode = ''
      if (dy > CLOSE_PX) {
        paint(true, 0, window.innerHeight * 0.6, 0.85)
        close()
      } else resetView()
      return
    }
    st.mode = ''
  }

  // a new photo starts unzoomed
  useEffect(() => {
    Object.assign(g.current, { scale: 1, tx: 0, ty: 0 })
  }, [i])

  if (!n) return null
  const k = i % n
  const p = photos[k]
  const step = Math.min(failed[k] || 0, p.srcs.length - 1)
  const src = p.srcs[step]
  return createPortal(
    <div
      ref={rootRef}
      tabIndex={-1}
      className={cn('fixed inset-0 z-[70] flex flex-col bg-ink/95 outline-none', closing ? '[animation:m-fade-in_200ms_ease-in_reverse_both]' : 'anim-fade-in')}
      role="dialog"
      aria-modal="true"
      aria-label={alt}
    >
      <div className="relative z-10 flex items-center justify-between px-3 pt-3" style={{ paddingTop: 'calc(12px + env(safe-area-inset-top, 0px))' }}>
        <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-white">{p.label}{n > 1 ? ` · ${k + 1}/${n}` : ''}</span>
        <button type="button" onClick={close} aria-label={S.states.close} className="grid h-11 w-11 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70">
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      {/* the stage: every touch here is ours (touch-action: none), so the page beneath never zooms */}
      <div className="relative flex min-h-0 flex-1 touch-none select-none items-center justify-center overflow-hidden p-3" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
        <img
          ref={imgRef}
          key={src}
          src={src}
          alt={alt}
          className="anim-fade-in pointer-events-none mx-auto h-full w-full object-contain will-change-transform"
          draggable={false}
          decoding="async"
          onError={() => {
            if (step < p.srcs.length - 1) setFailed((f) => ({ ...f, [k]: step + 1 }))
          }}
        />
        {n > 1 && (
          <>
            <button type="button" onPointerDown={(e) => e.stopPropagation()} onClick={() => go(-1)} aria-label={S.spot.prev} className="absolute start-3 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20">
              <ChevronLeft size={22} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
            <button type="button" onPointerDown={(e) => e.stopPropagation()} onClick={() => go(1)} aria-label={S.spot.next} className="absolute end-3 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20">
              <ChevronRight size={22} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
          </>
        )}
      </div>
      <div className="flex justify-center gap-1.5 pb-4" style={{ paddingBottom: 'calc(16px + env(safe-area-inset-bottom, 0px))' }}>
        {n > 1 &&
          photos.map((ph, j) => (
            <button key={ph.label} type="button" aria-label={ph.label} onClick={() => setI(j)} className="relative grid h-6 place-items-center px-1">
              <span className={cn('block h-1.5 w-5 origin-center rounded-full bg-white transition-[transform,opacity] duration-2 ease-m', j === k ? 'opacity-100' : 'scale-x-[.3] opacity-40')} />
            </button>
          ))}
      </div>
    </div>,
    document.body,
  )
}
