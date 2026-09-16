import { useState } from 'react'
import { Package } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Photo with a fallback chain: the small thumbnail first (it's what the grid
 * needs), then the full product shot, then an honest "Photo coming soon" card.
 * ~21 of the 182 items have no photo yet, so the empty state is a first-class
 * state, not an error.
 */

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
    <div className={cn('bg-white', className)}>
      <img
        src={src}
        alt={alt}
        width={width}
        height={height}
        loading={eager ? 'eager' : 'lazy'}
        decoding="async"
        // the above-the-fold photos are the largest paint: ask the browser for them first
        fetchPriority={eager ? 'high' : undefined}
        draggable={false}
        onError={() => setFailed({ key, idx: idx + 1 })}
        className={cn('h-full w-full object-contain', imgClassName)}
      />
    </div>
  )
}
