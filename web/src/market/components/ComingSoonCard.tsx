import { memo, useState } from 'react'
import { BellRing, Check, MessageCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { UpcomingItem } from '../lib/marketApi'
import { useMarket } from '../MarketContext'
import { S } from '../strings'
import { AnchorButton, Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { ProductImage, SIZES_GRID, SIZES_RAIL } from '../ui/ProductImage'
import { ComingSoonNotifySheet } from './ComingSoonNotifySheet'
import { askRepUrl, readNotified, upcomingCopy, upcomingName, upcomingSpec, upcomingWhen, variantLabel } from './ComingSoonShared'

/**
 * A "Coming soon" card (plan §6b): the Stockbook anatomy without a price —
 *
 *   photo (white 1:1 frame; a Product | Box toggle on its bottom edge; "Coming soon" chip)
 *   → kicker   "WS-55 · WEKOME"       model code FIRST — the code is what a shop asks for
 *   → name     2 lines, EN or AR by locale
 *   → spec     one muted line
 *   → chips    the variants (colours, connectors, sizes) — tap one to name it in the messages
 *   → "Price on arrival" · "Arriving October"
 *   → actions  "Notify me when it lands" (the restock flow, optional quantity, no commitment)
 *              "Ask your rep" (WhatsApp, prefilled with the model and the picked variant)
 *
 * There is no Add: nothing here can be ordered yet, and no price, quantity or "was" ever shows.
 * `compact` is the rail density (same fixed width as MarketCard's compact card). The shared
 * helpers (copy pick, WhatsApp link, "already asked" memory) live in ComingSoonShared.ts.
 */

export interface ComingSoonCardProps {
  item: UpcomingItem
  variant?: 'grid' | 'compact'
  className?: string
}

export const ComingSoonCard = memo(function ComingSoonCard({ item, variant = 'grid', className }: ComingSoonCardProps) {
  const t = upcomingCopy()
  const { rep } = useMarket()
  const compact = variant === 'compact'
  const [view, setView] = useState<'product' | 'box'>('product')
  const [picked, setPicked] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [done, setDone] = useState(() => readNotified().has(item.id))
  const name = upcomingName(item)
  const spec = upcomingSpec(item)
  const when = upcomingWhen(item)
  const askUrl = askRepUrl(rep, item, picked)
  const maxChips = compact ? 3 : 8
  const chips = item.variants.slice(0, maxChips)
  const more = item.variants.length - chips.length
  // the product view uses the WebP size set like a catalog photo; the box view is the one full box photo
  const photo = view === 'box' && item.box_url ? { srcs: [item.box_url] } : { item: { thumb_urls: item.photo_thumb_urls, product_image_url: item.photo_url, package_image_url: item.box_url } }

  return (
    <article
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-1 transition duration-2 ease-m hover:-translate-y-0.5 hover:border-ink/15 hover:shadow-2',
        compact ? 'cv-compact w-[clamp(10rem,46vw,13rem)] shrink-0 lg:w-auto' : 'cv-card',
        className,
      )}
    >
      <div className="relative">
        <ProductImage {...photo} alt={`${item.model_code} ${name}`} sizes={compact ? SIZES_RAIL : SIZES_GRID} size={compact ? 208 : 320} className="w-full" imgClassName="p-3" iconSize={compact ? 28 : 40} showCaption={!compact} />
        <span className="pointer-events-none absolute start-2 top-2">
          <Chip tone="fresh">{t.kicker}</Chip>
        </span>
        {item.box_url && (
          // Product | Box on the photo's bottom edge — the same two words the product panel uses
          <div role="group" aria-label={S.card.photo} className="absolute bottom-2 end-2 inline-flex rounded-full border border-line bg-surface/95 p-0.5 shadow-1 backdrop-blur">
            {(['product', 'box'] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={cn('relative h-7 rounded-full px-2.5 text-2xs font-semibold transition duration-1 ease-m after:absolute after:-inset-y-1.5 after:inset-x-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', view === v ? 'bg-ink text-white' : 'text-ink-2 hover:text-ink')}
              >
                {v === 'product' ? t.photoProduct : t.photoBox}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className={cn('flex flex-1 flex-col border-t border-line-2', compact ? 'px-2.5 pb-2.5 pt-2' : 'p-3')}>
        {/* code first: many WEKOME models differ only here */}
        <p className="flex min-w-0 items-baseline gap-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-ink-3">
          <span className="truncate font-display text-xs tracking-normal text-plum-ink">{item.model_code}</span>
          <span className="truncate">{item.brand}</span>
        </p>
        <h3 className={cn('mt-0.5 font-sans font-semibold tracking-normal text-ink', compact ? 'min-h-[34px] text-[13px] leading-[17px]' : 'min-h-[38px] text-sm md:text-[14px] md:leading-[19px]')}>
          <span className="line-clamp-2">{name}</span>
        </h3>
        {spec && <p className={cn('mt-1 text-ink-2', compact ? 'line-clamp-1 text-2xs leading-[14px]' : 'line-clamp-2 text-xs leading-4')}>{spec}</p>}
        {chips.length > 0 && (
          <div role="group" aria-label={t.variant} className={cn('flex flex-wrap gap-1', compact ? 'mt-1.5' : 'mt-2')}>
            {chips.map((v) => {
              const label = variantLabel(v)
              const on = picked === label
              return (
                <button key={v.label} type="button" aria-pressed={on} onClick={() => setPicked(on ? null : label)} className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                  <Chip tone={on ? 'plum' : 'spec'}>{label}</Chip>
                </button>
              )
            })}
            {more > 0 && <Chip tone="grey">+{more}</Chip>}
          </div>
        )}

        <div className={cn('mt-auto flex min-w-0 flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5', compact ? 'pt-2' : 'pt-3')}>
          <span className={cn('font-semibold text-ink-2', compact ? 'text-xs leading-5' : 'text-sm leading-[22px]')}>{t.price}</span>
          {when && <span className="text-2xs text-ink-3">{when}</span>}
        </div>

        <div className="mt-2 grid gap-1.5">
          <Button variant={done ? 'secondary' : 'primary'} size="sm" full disabled={done} onClick={() => setOpen(true)} icon={done ? <Check size={15} aria-hidden="true" /> : <BellRing size={15} aria-hidden="true" />} aria-label={`${done ? t.notified : t.notify} — ${item.model_code} ${name}`}>
            <span className="truncate">{done ? t.notified : t.notify}</span>
          </Button>
          {askUrl && (
            <AnchorButton href={askUrl} target="_blank" rel="noreferrer" variant="wa" size="sm" full icon={<MessageCircle size={15} aria-hidden="true" />} aria-label={`${t.ask} — ${item.model_code} ${name}`}>
              <span className="truncate">{rep?.first_name ? t.ask : t.askYq}</span>
            </AnchorButton>
          )}
        </div>
      </div>

      {open && (
        <ComingSoonNotifySheet
          item={item}
          variant={picked}
          open={open}
          onClose={() => setOpen(false)}
          onDone={() => setDone(true)}
        />
      )}
    </article>
  )
})
