import { useMemo } from 'react'
import type { Campaign, ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { categorySlide, useClaimSlides } from '../lib/slides'
import { SlideCard } from './SlideCard'

/**
 * Campaigns are the promotions an admin schedules in the portal (title, line, creative, link,
 * window, placement). Hero/strip/aside campaigns render through the ONE slide model
 * (lib/slides.ts → PromoSlider, the desktop hero tiles, Spotlight), so a campaign is never shown
 * twice. What lives here is the category shelf's header creative.
 */

const NONE: readonly string[] = []

/**
 * The header creative of a category shelf: the campaign placed on this category (a tile-size
 * SlideCard), else — when the shelf's `items` are passed — a banner composed from that category's
 * own data (lib/slides.ts categorySlide). Null when there is nothing honest to show. A slim 5:1
 * banner on desktop, 5:2 on phones. A campaign shown here is claimed, so the desktop aside
 * Spotlight beside the shelf never repeats it (a campaign may be placed on a category AND the hero).
 */
export function CategoryBanner({ campaigns, category, items, className }: { campaigns: Campaign[]; category: string; items?: ShopItem[] | null; className?: string }) {
  const slide = useMemo(() => categorySlide(campaigns, category, items), [campaigns, category, items])
  const claim = useMemo(() => (slide?.kind === 'campaign' ? [slide.id] : NONE), [slide])
  useClaimSlides(claim)
  if (!slide) return null
  return (
    <div className={cn('mb-3', className)}>
      <SlideCard slide={slide} size="tile" where="category" className="aspect-[5/2] sm:aspect-[7/2] lg:aspect-[5/1]" />
    </div>
  )
}
