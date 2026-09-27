import { memo, useState } from 'react'
import { BellRing, Check, Package } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { UpcomingItem } from '../lib/marketApi'
import { useMarket } from '../MarketContext'
import { Chip } from '../ui/Chip'
import { ProductImage, SIZES_GRID, SIZES_RAIL } from '../ui/ProductImage'
import { ComingSoonNotifySheet } from './ComingSoonNotifySheet'
import { readNotified, specPills, upcomingCopy, upcomingName, upcomingWhen } from './ComingSoonShared'
import { CardKicker } from './MarketCard'

/**
 * A "Coming soon" card — mirror MarketCard: the same frame, the same classes, the same reading
 * order, so a WEKOME card sits in a grid or a rail beside a live card and reads as one family.
 *
 *   photo    white 1:1 frame; the fresh "Coming soon" chip top-start; a round box-photo toggle
 *            top-end, in the exact place and style of the live card's round button
 *   → kicker "WEKOME · WS-55"         brand first, then the model code (CardKicker itself)
 *   → name   2 lines, EN or AR by locale
 *   → pills  ONE row of whole short facts: the name's differentiator, the supplier spec, then the
 *            variants (specPills: only whole pills that fit, never an ellipsis)
 *   → price  "Price on arrival" where the live card prints its price
 *   → facts  the arrival month from the payload ("Arriving October")
 *   → action ONE full-width plum "Notify me" → after asking, the check: "We will tell you" — which
 *            still opens the sheet when the shop has a rep, so the per-model WhatsApp ask stays one
 *            tap away (phones and tablets have no tile); with no rep there is nothing left to do
 *
 * `compact` (the home rail) mirrors the live compact card: the fixed rail width, a round bell in
 * the price row instead of the full-width bar, no facts row.
 *
 * Nothing here can be ordered yet: there is no heart, no cart line, no product panel, no stock
 * attribute and never an amount of money. The variant choice and "Ask your rep" live in the
 * notify sheet (ComingSoonNotifySheet); the POST it sends is the same as before.
 */

export interface ComingSoonCardProps {
  item: UpcomingItem
  variant?: 'grid' | 'compact'
  className?: string
}

/** the one plum action — the same ink as the live card's add */
const PLUM = 'bg-plum text-white shadow-1 hover:bg-plum-deep'
/** after asking: the live card's "asked" (ok) state — still a door to the sheet when a rep can be asked */
const ASKED = 'border border-ok/30 bg-ok-soft text-ok'
const ASKED_OPEN = 'hover:border-ok/60'
const ASKED_DONE = 'cursor-default'

export const ComingSoonCard = memo(function ComingSoonCard({ item, variant = 'grid', className }: ComingSoonCardProps) {
  const t = upcomingCopy()
  const { rep } = useMarket()
  const compact = variant === 'compact'
  const [view, setView] = useState<'product' | 'box'>('product')
  const [open, setOpen] = useState(false)
  const [done, setDone] = useState(() => readNotified().has(item.id))
  const name = upcomingName(item)
  const when = upcomingWhen(item)
  const pills = specPills(item)
  const box = view === 'box' && Boolean(item.box_url)
  // both views use a WebP size set like a catalog photo (the box has its own since R1b); the full
  // JPEG is only the fallback chain
  const photo = box
    ? { item: { thumb_urls: item.box_thumb_urls, product_image_url: item.box_url, package_image_url: null } }
    : { item: { thumb_urls: item.photo_thumb_urls, product_image_url: item.photo_url, package_image_url: item.box_url } }
  // once asked, the button only reopens the sheet for its WhatsApp ask — so only when there is a rep to ask
  const canAsk = Boolean(rep?.whatsapp_url)
  const locked = done && !canAsk
  const label = `${done ? (canAsk ? `${t.notified} · ${t.askRange(rep?.first_name || '')}` : t.notified) : t.notify} — ${item.model_code} ${name}`
  const ask = () => setOpen(true)
  const asked = cn(ASKED, locked ? ASKED_DONE : ASKED_OPEN)

  return (
    <article
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-1 transition duration-2 ease-m hover:-translate-y-0.5 hover:border-ink/15 hover:shadow-2',
        compact ? 'cv-compact w-[clamp(10rem,46vw,13rem)] shrink-0 lg:w-auto' : 'cv-card',
        className,
      )}
    >
      <div className="relative">
        {item.box_url && (
          // the live card's round button, same place, same size (36 px drawn, 48 px tap area)
          <button
            type="button"
            onClick={() => setView(box ? 'product' : 'box')}
            aria-pressed={box}
            aria-label={`${t.showBox} — ${item.model_code} ${name}`}
            title={box ? t.showProduct : t.showBox}
            className={cn(
              'absolute end-2 top-2 z-[1] grid h-9 w-9 place-items-center rounded-full border bg-surface/95 shadow-1 transition duration-1 ease-m after:absolute after:-inset-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
              box ? 'border-plum/40 text-plum' : 'border-line text-ink-3 hover:text-plum',
            )}
          >
            <Package size={compact ? 15 : 16} aria-hidden="true" />
          </button>
        )}
        <ProductImage
          {...photo}
          alt={`${item.model_code} ${name}`}
          sizes={compact ? SIZES_RAIL : SIZES_GRID}
          size={compact ? 208 : 320}
          className="w-full"
          imgClassName="p-3 transition-transform duration-3 ease-m group-hover:scale-[1.03]"
          iconSize={compact ? 28 : 40}
          showCaption={!compact}
        />
        <span className="pointer-events-none absolute start-2 top-2">
          <Chip tone="fresh">{t.kicker}</Chip>
        </span>
      </div>

      <div className={cn('flex flex-1 flex-col border-t border-line-2', compact ? 'px-2.5 pb-2.5 pt-2' : 'p-3')}>
        <CardKicker item={{ brand: item.brand, item_code: item.model_code }} />
        <h3 className={cn('mt-0.5 font-sans font-semibold tracking-normal text-ink', compact ? 'min-h-[36px] text-[13px] leading-[17px]' : 'min-h-[36px] text-sm md:min-h-[38px] md:text-[14px] md:leading-[19px]')}>
          <span className="line-clamp-2">{name}</span>
        </h3>
        {/* one 20 px row, reserved even when empty so the price rows line up across a grid row */}
        <span className={cn('flex h-5 flex-wrap gap-1 overflow-hidden', compact ? 'mt-1.5' : 'mt-2')}>
          {pills.map((p) => (
            <Chip key={p} tone="spec" className="px-1.5">
              {p}
            </Chip>
          ))}
        </span>

        <div className={cn('flex min-w-0 gap-x-1', compact ? 'mt-1.5 items-center justify-between' : 'mt-2 items-baseline')}>
          <span className={cn('min-w-0 font-semibold text-ink-2', compact ? 'text-xs leading-5' : 'text-sm leading-[22px]')}>{t.price}</span>
          {compact && (
            <span className="shrink-0">
              {/* 40 px drawn, 48 px of target: the pseudo-element carries the rest of the tap area */}
              <button
                type="button"
                onClick={ask}
                disabled={locked}
                aria-label={label}
                className={cn(
                  'relative grid h-10 w-10 shrink-0 place-items-center rounded-full transition duration-1 ease-m after:absolute after:-inset-1 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2',
                  done ? asked : PLUM,
                )}
              >
                {done ? <Check size={18} aria-hidden="true" /> : <BellRing size={17} aria-hidden="true" />}
              </button>
            </span>
          )}
        </div>

        {!compact && (
          <>
            <div className="mt-1.5 flex min-h-[22px] min-w-0 flex-col justify-start gap-1">{when && <p className="text-2xs leading-[14px] text-ink-3">{when}</p>}</div>
            <div className="mt-auto pt-3">
              <button
                type="button"
                onClick={ask}
                disabled={locked}
                aria-label={label}
                className={cn(
                  'hit relative flex h-11 w-full items-center justify-center gap-1.5 rounded-sm text-sm font-semibold transition duration-1 ease-m active:scale-[.985] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2',
                  done ? asked : PLUM,
                )}
              >
                {done ? <Check size={15} aria-hidden="true" /> : <BellRing size={15} aria-hidden="true" />}
                <span className="truncate">{done ? t.notifiedShort : t.send}</span>
              </button>
            </div>
          </>
        )}
      </div>

      {open && <ComingSoonNotifySheet item={item} open={open} done={done} onClose={() => setOpen(false)} onDone={() => setDone(true)} />}
    </article>
  )
})
