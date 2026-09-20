import type { CSSProperties } from 'react'
import type { ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { SLIDE_THUMB_SIZES, type SlideCanvas, type SlideSize } from '../lib/slides'
import { ProductImage } from '../ui/ProductImage'

/**
 * A campaign creative composed from real products — no upload, no crop, always on-brand: up to
 * three product photos on white discs, fanned −8° / 0 / +8°, the first (centre) one largest. Each
 * photo is object-contain inside a padding that keeps the whole square inside its circle, so a
 * product is never cut. Geometry lives in the "v3: slider" block of market.css (`.creative`): the
 * root is a size container that fills its parent (give the parent a size), the discs are sized in
 * container units, so the composition scales with the card and reserves its space (no CLS).
 *
 * Motion: when the slide around it is marked `data-slide-state="idle"` (PromoSlider) the discs sit
 * a little lower and tighter; becoming active springs them into the fan (CSS transition, staggered,
 * reduced-motion safe). Decorative: the slide's text says what it is.
 */

const POS = ['c', 'l', 'r'] as const

export interface ComposedCreativeProps {
  products: ShopItem[]
  canvas: SlideCanvas
  size: SlideSize
  /** slide 1: the centre photo loads eager + fetchpriority high (the LCP candidate), the rest eager */
  priority?: boolean
  className?: string
  /** night canvas: paint the static horizon behind the discs (SlideCard paints one for the whole card and passes false) */
  backdrop?: boolean
}

export function ComposedCreative({ products, canvas, size, priority, className, backdrop = true }: ComposedCreativeProps) {
  const list = products.slice(0, 3)
  return (
    <div className={cn('creative', className)} data-count={list.length} data-canvas={canvas} aria-hidden="true">
      {backdrop && canvas === 'night' && (
        <div className="horizon is-small is-offset">
          <i />
          <i />
          <i />
          <i />
        </div>
      )}
      {list.map((item, k) => (
        <div key={item.item_code} className="creative-disc" data-pos={POS[k]} style={{ '--k': k } as CSSProperties}>
          <ProductImage item={item} alt="" sizes={SLIDE_THUMB_SIZES[size]} size={320} priority={Boolean(priority) && k === 0} eager={Boolean(priority) && k > 0} className="h-full w-full rounded-full" imgClassName="p-[15%]" iconSize={22} showCaption={false} />
        </div>
      ))}
    </div>
  )
}
