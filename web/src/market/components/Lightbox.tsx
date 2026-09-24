import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { S } from '../strings'

export interface LightboxPhoto {
  /** candidates in order — the 1024 / 512 WebP rendition first, the original last (lib/photos.ts) */
  srcs: string[]
  label: string
}

/**
 * Full-screen photo viewer: tap a product photo → the photo fills the screen; pinch to zoom
 * (native, via touch-action), swipe or arrows between Product and Package, Esc / tap outside to
 * close. No library — the browser already knows how to pinch an image.
 *
 * Each photo is a fallback chain, not one URL (R6): a candidate that 404s hands over to the next,
 * so the viewer shows the WebP rendition when it exists and the original photo when it does not.
 */
export function Lightbox({ photos, start = 0, alt, onClose }: { photos: LightboxPhoto[]; start?: number; alt: string; onClose: () => void }) {
  const [i, setI] = useState(start)
  const [failed, setFailed] = useState<Record<number, number>>({})
  const n = photos.length
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight') setI((v) => (v + 1) % n)
      if (e.key === 'ArrowLeft') setI((v) => (v - 1 + n) % n)
    }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [n, onClose])
  if (!n) return null
  const k = i % n
  const p = photos[k]
  const step = Math.min(failed[k] || 0, p.srcs.length - 1)
  const src = p.srcs[step]
  return createPortal(
    <div className="fixed inset-0 z-[70] flex flex-col bg-ink/95 anim-fade-in" role="dialog" aria-modal="true" aria-label={alt} onClick={onClose}>
      <div className="flex items-center justify-between px-3 pt-3" style={{ paddingTop: 'calc(12px + env(safe-area-inset-top, 0px))' }}>
        <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-white">{p.label}{n > 1 ? ` · ${k + 1}/${n}` : ''}</span>
        <button type="button" onClick={onClose} aria-label={S.states.close} className="grid h-11 w-11 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70">
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center p-3" onClick={(e) => e.stopPropagation()}>
        <div className="h-full w-full overflow-auto overscroll-contain [touch-action:pinch-zoom]">
          <img
            key={src}
            src={src}
            alt={alt}
            className="mx-auto h-full w-full object-contain"
            draggable={false}
            decoding="async"
            onError={() => {
              if (step < p.srcs.length - 1) setFailed((f) => ({ ...f, [k]: step + 1 }))
            }}
          />
        </div>
        {n > 1 && (
          <>
            <button type="button" onClick={() => setI((v) => (v - 1 + n) % n)} aria-label={S.spot.prev} className={cn('absolute start-3 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20')}>
              <ChevronLeft size={22} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
            <button type="button" onClick={() => setI((v) => (v + 1) % n)} aria-label={S.spot.next} className={cn('absolute end-3 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20')}>
              <ChevronRight size={22} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
          </>
        )}
      </div>
      <div className="flex justify-center gap-1.5 pb-4" style={{ paddingBottom: 'calc(16px + env(safe-area-inset-bottom, 0px))' }}>
        {n > 1 && photos.map((ph, j) => <button key={ph.label} type="button" aria-label={ph.label} onClick={(e) => { e.stopPropagation(); setI(j) }} className={cn('h-1.5 rounded-full transition-all', j === k ? 'w-5 bg-white' : 'w-1.5 bg-white/40')} />)}
      </div>
    </div>,
    document.body,
  )
}
