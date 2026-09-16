import { useState, type CSSProperties } from 'react'
import { Package } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'

/**
 * A product photo on a white shelf tile.
 *
 * Uses the WebP size set (`thumb_urls` 160/320/512) as a srcset when the payload has it, else the
 * legacy 256 px JPEG thumb, then the full photo, then an honest "Photo coming soon" tile (7 SKUs
 * today). The box always reserves its aspect ratio, so the grid never jumps while loading.
 * If a srcset candidate 404s the whole srcset is dropped and the fallback chain walks on.
 */

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
export const SIZES_RAIL = '(min-width: 768px) 200px, 44vw'
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
          {showCaption && <div className="mt-1.5 text-2xs font-medium uppercase tracking-wide text-ink-3">Photo coming soon</div>}
        </div>
      </div>
    )
  }

  return (
    <div className={cn('bg-surface', className)} style={{ aspectRatio: '1 / 1', ...style }}>
      <img
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
        onError={() => setState(useSet ? { key, idx: 0, srcsetDead: true } : { key, idx: idx + 1, srcsetDead })}
        className={cn('h-full w-full object-contain', imgClassName)}
      />
    </div>
  )
}
