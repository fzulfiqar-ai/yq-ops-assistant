import { useState, type SyntheticEvent } from 'react'
import { Package } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Photo with a fallback chain: the small thumbnail first (it's what the grid
 * needs), then the full product shot, then an honest "Photo coming soon" card.
 * ~21 of the 182 items have no photo yet, so the empty state is a first-class
 * state, not an error.
 *
 * Photo fade-in (ship 2, 30-Oct-2026; the marketplace's market/ui/ProductImage pattern): until the
 * photo decodes, a soft tint lies over the white tile; then the tint fades away (200 ms) and the
 * photo is there. The state is the tile's `data-state`, flipped by the image's own load event: no
 * React render per photo, so a fast flick past forty rows costs nothing but the fades. A photo already
 * in the cache, or one that decodes while the list is scrolling, shows at once (`data-instant`).
 * The tint sits above the photo (::after), so a card's own hover zoom on the <img> keeps its transition.
 */

/* Smart fade: only a photo that lands on a page at rest fades; one that decodes mid-scroll shows at
 * once over its tint. One passive capture listener for the whole page (the catalog's own scrollers
 * included); nothing per photo. */
let lastScroll = -1e9
if (typeof window !== 'undefined') {
  window.addEventListener(
    'scroll',
    () => {
      lastScroll = performance.now()
    },
    { passive: true, capture: true },
  )
}
const SETTLE_MS = 150

/** the photo decoded: its tile drops the tint (at once, mid-scroll) */
function shown(img: HTMLImageElement | null, instant = false) {
  const tile = img?.parentElement
  if (!tile || tile.dataset.state === 'in') return
  if (instant || performance.now() - lastScroll < SETTLE_MS) tile.dataset.instant = ''
  tile.dataset.state = 'in'
}

/** a photo already complete when it mounts (memory cache) shows at once, before the first paint */
function cached(img: HTMLImageElement | null) {
  if (img && img.complete && img.naturalWidth > 0) shown(img, true)
}

export interface ProductImageProps {
  srcs: (string | null | undefined)[]
  alt: string
  /** intrinsic size hint — set on the <img> so the grid never jumps while loading */
  width?: number
  height?: number
  className?: string
  imgClassName?: string
  iconSize?: number
  showCaption?: boolean
  eager?: boolean
}

// the tint: a light plum-grey wash over the white tile while the photo decodes; opacity only
const TINT =
  "after:pointer-events-none after:absolute after:inset-0 after:bg-[#F1EDF5] after:content-[''] after:transition-opacity after:duration-200 after:ease-[cubic-bezier(.22,1,.36,1)] data-[state=in]:after:opacity-0 data-[instant]:after:transition-none"

export function ProductImage({
  srcs,
  alt,
  width = 320,
  height = 320,
  className,
  imgClassName,
  iconSize = 40,
  showCaption = true,
  eager = false,
}: ProductImageProps) {
  const chain = srcs.filter((s): s is string => typeof s === 'string' && s.length > 0)
  const key = chain.join('|')
  // Keyed to the URL chain: a different product (or a refreshed signed URL)
  // restarts at the thumbnail, without an effect that re-renders on mount.
  const [failed, setFailed] = useState({ key, idx: 0 })
  const idx = failed.key === key ? failed.idx : 0

  const src = chain[idx]

  if (!src) {
    return (
      <div className={cn('grid place-items-center bg-white text-[#d6d1e4]', className)}>
        <div className="px-2 text-center">
          <Package size={iconSize} strokeWidth={1} className="mx-auto" aria-hidden="true" />
          {showCaption && (
            <div className="mt-1.5 text-[11px] font-medium uppercase tracking-wide text-[#6b6480]">Photo coming soon</div>
          )}
        </div>
      </div>
    )
  }

  return (
    // keyed to the photo: another photo in the same tile starts again from the tint
    <div key={src} className={cn('relative bg-white', TINT, className)} data-state="wait">
      <img
        ref={cached}
        src={src}
        alt={alt}
        width={width}
        height={height}
        loading={eager ? 'eager' : 'lazy'}
        decoding="async"
        // the above-the-fold photos are the largest paint: ask the browser for them first
        fetchPriority={eager ? 'high' : undefined}
        draggable={false}
        onLoad={(e: SyntheticEvent<HTMLImageElement>) => shown(e.currentTarget)}
        onError={() => setFailed({ key, idx: idx + 1 })}
        className={cn('h-full w-full object-contain', imgClassName)}
      />
    </div>
  )
}
