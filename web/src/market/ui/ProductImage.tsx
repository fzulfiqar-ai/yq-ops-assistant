import { useState, type CSSProperties, type SyntheticEvent } from 'react'
import { Package } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { S } from '../strings'

/**
 * A product photo on a white shelf tile.
 *
 * The tile stays pure white on purpose. Measured over the live catalog (17-Sep-2026): 163 of the
 * 172 thumbs are NOT square, so the tile shows beside every photo, and 167 of 172 carry a pure
 * white background of their own. Tinting the tile would therefore draw a white rectangle on
 * ~163 cards to hide the 5 files that came in on another background (TB-D6…D9 orange, F20 grey).
 * Those 5 are a source-image job — normalise the thumbs, do not tint the frame.
 *
 * Uses the WebP size set (`thumb_urls` 160/320/512) as a srcset when the payload has it, else the
 * legacy 256 px JPEG thumb, then the full photo, then an honest "Photo coming soon" tile (7 SKUs
 * today). The box always reserves its aspect ratio, so the grid never jumps while loading.
 * If a srcset candidate 404s the whole srcset is dropped and the fallback chain walks on.
 *
 * Photo fade-in (28-Oct-2026): until the photo decodes the tile wears the skeleton tint (`.m-photo`,
 * market.css; a light sweep over it where a mouse drives the page), then the photo fades in. The
 * state lives on the tile's `data-state`, flipped by the image's own load event: no React render per
 * photo, so a fast flick past forty photos costs nothing but the fades. A photo already in the cache
 * shows at once, with no fade.
 */

/* Smart fade (motion list M7's rule, for photos): a photo that decodes while the page is scrolling
 * shows at once over its tint; only a photo that lands on a page at rest fades. Fading every photo
 * that arrived mid-scroll cost frames on a mid phone (measured at 4x CPU) for a fade nobody sees
 * while the page moves. One passive listener for the whole page; nothing per photo. */
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

/** the photo decoded: its tile drops the tint and the photo fades in (at once, mid-scroll) */
function shown(img: HTMLImageElement | null) {
  const tile = img?.parentElement
  if (!tile || tile.dataset.state === 'in') return
  if (performance.now() - lastScroll < SETTLE_MS) tile.dataset.instant = ''
  tile.dataset.state = 'in'
}

/** a photo already complete when it mounts (memory cache) shows at once, before the first paint */
function cached(img: HTMLImageElement | null) {
  if (img && img.complete && img.naturalWidth > 0) shown(img)
}

export interface ProductImageProps {
  item?: Pick<ShopItem, 'thumb_url' | 'thumb_urls' | 'product_image_url' | 'package_image_url'> | null
  /** explicit chain instead of `item` (e.g. package view) */
  srcs?: (string | null | undefined)[]
  alt: string
  /** the browser's size hint per breakpoint — matches how wide the tile renders */
  sizes?: string
  /** intrinsic square size hint for layout */
  size?: number
  className?: string
  imgClassName?: string
  iconSize?: number
  showCaption?: boolean
  eager?: boolean
  /** first-screen images only: `fetchpriority=high` */
  priority?: boolean
  style?: CSSProperties
  vtName?: string
}

export const SIZES_GRID = '(min-width: 1280px) 240px, (min-width: 768px) 30vw, 46vw'
/** compact card: clamp(10rem, 46vw, 13rem) on phones/tablets, a desktop rail cell (~200–240px) from lg */
export const SIZES_RAIL = '(min-width: 1024px) 240px, (min-width: 768px) 208px, 46vw'
export const SIZES_THUMB = '68px'
export const SIZES_HERO = '(min-width: 1024px) 480px, 90vw'

export function ProductImage({ item, srcs, alt, sizes = SIZES_GRID, size = 320, className, imgClassName, iconSize = 40, showCaption = true, eager, priority, style, vtName }: ProductImageProps) {
  const set = item?.thumb_urls
  const srcset = set && set['320'] ? `${set['160'] || set['320']} 160w, ${set['320']} 320w, ${set['512'] || set['320']} 512w` : null
  const chain = (srcs || [item?.thumb_url, item?.product_image_url, item?.package_image_url]).filter((s): s is string => typeof s === 'string' && s.length > 0)
  const key = `${srcset || ''}|${chain.join('|')}`
  const [state, setState] = useState({ key, idx: 0, srcsetDead: false })
  const idx = state.key === key ? state.idx : 0
  const srcsetDead = state.key === key ? state.srcsetDead : false
  const useSet = Boolean(srcset) && !srcsetDead && !srcs
  const src = useSet ? set!['320'] : chain[idx]

  if (!src) {
    return (
      <div className={cn('grid place-items-center bg-surface text-line', className)} style={{ aspectRatio: '1 / 1', ...style }}>
        <div className="px-2 text-center">
          <Package size={iconSize} strokeWidth={1} className="mx-auto" aria-hidden="true" />
          {showCaption && <div className="mt-1.5 text-2xs font-medium uppercase tracking-wide text-ink-3">{S.card.photoSoon}</div>}
        </div>
      </div>
    )
  }

  return (
    <div className={cn('m-photo bg-surface', className)} data-state="wait" style={{ aspectRatio: '1 / 1', ...style }}>
      <img
        ref={cached}
        src={src}
        srcSet={useSet ? srcset! : undefined}
        sizes={useSet ? sizes : undefined}
        alt={alt}
        width={size}
        height={size}
        loading={eager || priority ? 'eager' : 'lazy'}
        decoding="async"
        fetchPriority={priority ? 'high' : undefined}
        draggable={false}
        style={vtName ? { viewTransitionName: vtName } : undefined}
        onLoad={(e: SyntheticEvent<HTMLImageElement>) => shown(e.currentTarget)}
        onError={() => setState(useSet ? { key, idx: 0, srcsetDead: true } : { key, idx: idx + 1, srcsetDead })}
        className={cn('m-photo-img h-full w-full object-contain', imgClassName)}
      />
    </div>
  )
}
