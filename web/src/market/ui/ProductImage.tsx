import { useCallback, useEffect, useState, type CSSProperties } from 'react'
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
 * Photo fade-in (28-Oct-2026): until the photo decodes, the tile shimmers (`.m-photo`, market.css)
 * and the 2 KB 160 px rendition paints first when the payload has one; the photo then fades in over
 * it and the stand-in is dropped. A photo already in the cache shows at once, with no fade.
 */
const LOW_HOLD_MS = 260 // the fade (--m-d-2) plus a frame, before the 160 px stand-in is dropped

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

  // which src has decoded, and whether its fade is over (then the stand-in can go)
  const [shown, setShown] = useState<{ src: string; settled: boolean } | null>(null)
  const loaded = Boolean(src) && shown?.src === src
  const settled = loaded && Boolean(shown?.settled)
  const low = useSet && set?.['160'] && set['160'] !== src && !settled ? set['160'] : null
  useEffect(() => {
    if (!loaded || settled || !src) return
    const t = window.setTimeout(() => setShown({ src, settled: true }), LOW_HOLD_MS)
    return () => window.clearTimeout(t)
  }, [loaded, settled, src])
  // a photo already in the memory cache is complete when it mounts: show it at once, no fade
  const cached = useCallback((el: HTMLImageElement | null) => {
    const at = el?.getAttribute('src')
    if (el && at && el.complete && el.naturalWidth > 0) setShown((s) => (s?.src === at ? s : { src: at, settled: true }))
  }, [])

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
    <div className={cn('m-photo bg-surface', className)} data-state={loaded ? 'in' : 'wait'} style={{ aspectRatio: '1 / 1', ...style }}>
      {low && (
        <img src={low} alt="" aria-hidden="true" width={size} height={size} loading={eager || priority ? 'eager' : 'lazy'} decoding="async" draggable={false} className={cn('m-photo-low h-full w-full object-contain', imgClassName)} />
      )}
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
        onLoad={(e) => {
          const at = e.currentTarget.getAttribute('src')
          if (at) setShown((s) => (s?.src === at ? s : { src: at, settled: false }))
        }}
        onError={() => setState(useSet ? { key, idx: 0, srcsetDead: true } : { key, idx: idx + 1, srcsetDead })}
        className={cn('m-photo-img h-full w-full object-contain', imgClassName)}
      />
    </div>
  )
}
