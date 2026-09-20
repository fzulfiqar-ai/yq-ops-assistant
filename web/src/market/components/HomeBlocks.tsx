import { Link } from 'react-router-dom'
import { ChevronRight, Clock, MessageCircle, Package, RotateCcw, Tag, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { MyOrderSummary, Offer, RepCard, ShopItem } from '@/lib/shopApi'
import { track } from '../lib/events'
import { categorySlug, firstName, niceCategory, useCountdown } from '../lib/format'
import { S } from '../strings'
import { Chip } from '../ui/Chip'
import { ProductImage } from '../ui/ProductImage'

/* ───────────────────────── wholesale actions ─────────────────────────
   What a shop owner comes to do besides browsing and pasting a list: reorder the last delivery and
   ask their representative. Order again only when this phone has a last order; Ask your rep only
   when the rep shares a WhatsApp link. Paste is NOT here: the continue card right above already is
   the paste offer, and the closing band repeats it — the page makes that offer twice, never more.
   Each action is a row with a line of detail. */

interface Action {
  key: 'again' | 'rep'
  label: string
  sub: string
  icon: LucideIcon
  to?: string
  href?: string
  aria?: string
  tone: 'plum' | 'solid' | 'wa'
}

const DISC: Record<Action['tone'], string> = {
  plum: 'bg-plum-soft text-plum',
  solid: 'bg-plum text-white',
  wa: 'bg-wa text-white',
}

export function MissionStrip({ againCount, rep, className }: { againCount: number; rep: RepCard | null; className?: string }) {
  const actions: Action[] = []
  if (againCount > 0) actions.push({ key: 'again', label: S.rails.again, sub: S.home.againSub(againCount), icon: RotateCcw, to: '/quick?load=last', tone: 'solid' })
  if (rep?.whatsapp_url) {
    const first = rep.first_name || firstName(rep.name) || rep.name
    actions.push({ key: 'rep', label: S.home.askRep, sub: S.home.askSub(first), icon: MessageCircle, href: rep.whatsapp_url, aria: S.cart.ask(first), tone: 'wa' })
  }
  if (!actions.length) return null

  return (
    <nav aria-label={S.home.actions} className={className}>
      <ul className={cn('grid gap-2 lg:gap-3', actions.length === 2 ? 'grid-cols-2' : 'grid-cols-1')}>
        {actions.map((a) => {
          const Icon = a.icon
          const cls =
            'group flex h-16 items-center gap-3 rounded-lg bg-surface px-3.5 text-ink shadow-1 ring-1 ring-line transition duration-2 ease-m hover:-translate-y-0.5 hover:shadow-2 hover:ring-ink/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 active:scale-[.985] lg:px-4'
          const inner = (
            <>
              <span className={cn('grid h-10 w-10 shrink-0 place-items-center rounded-full', DISC[a.tone])} aria-hidden="true">
                <Icon size={18} strokeWidth={1.9} />
              </span>
              <span className="min-w-0 max-w-full flex-1">
                <span className="block truncate text-sm font-semibold">{a.label}</span>
                <span className="block truncate text-xs text-ink-2">{a.sub}</span>
              </span>
              <ChevronRight size={16} className="shrink-0 text-ink-3 transition-transform duration-2 ease-m group-hover:translate-x-0.5 rtl:-scale-x-100" aria-hidden="true" />
            </>
          )
          const onClick = () => track('rail_click', { meta: { rail: 'mission', code: a.key } })
          return (
            <li key={a.key} className="min-w-0">
              {a.href ? (
                <a href={a.href} target="_blank" rel="noreferrer" aria-label={a.aria} onClick={onClick} className={cls}>
                  {inner}
                </a>
              ) : (
                <Link to={a.to!} onClick={onClick} className={cls}>
                  {inner}
                </Link>
              )}
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

/* ───────────────────────── category tiles ─────────────────────────
   Round shelf tiles, the delivery-app way: a pastel disc holding the category's best photo
   (never cropped — object-contain), the name under it and the line count, muted. Four per row on
   a phone (two rows for eight categories), one row on desktop.

   Two things the photos force. (1) The art box is the square inscribed in the circle (70.7%), so
   no product corner — the Power bank's eco badge, the Charger's plug — can be sliced by the ring.
   (2) The disc carries its own ring's wash instead of white: half this catalog is a white cable or
   a white earbud on a white background, and on a white disc they all but vanished. The white the
   thumbs are padded with (167 of 172 files) is dropped by `mix-blend-multiply`, so the product
   floats on the tint instead of printing a white rectangle inside the circle — which is why the
   fill was white before. `isolate` keeps that blend inside the disc. */

/** the pastel rings, cycled across the row — the tile canvases from D8 */
const TILE_RING = ['ring-tile-lilac', 'ring-tile-apricot', 'ring-tile-mint']
/** the same three washes as the disc ground, so a white-on-white product has something to sit on */
const TILE_WASH = ['bg-tile-lilac/60', 'bg-tile-apricot/60', 'bg-tile-mint/60']

export function CategoryTiles({ tiles, className }: { tiles: { category: string; count: number; newCount: number; image: ShopItem | null }[]; className?: string }) {
  if (!tiles.length) return null
  return (
    <section className={className} aria-label={S.categories.title}>
      <ul className="grid grid-cols-4 gap-x-1.5 gap-y-3.5 md:grid-cols-8 lg:gap-x-3">
        {tiles.map((t, i) => (
          <li key={t.category} className="min-w-0">
            <Link
              to={`/t/${categorySlug(t.category)}`}
              onClick={() => track('rail_click', { meta: { rail: 'tile', pos: i } })}
              className="group flex flex-col items-center rounded-md px-0.5 pb-1 pt-1 text-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
            >
              <span className={cn('relative isolate grid h-16 w-16 place-items-center rounded-full shadow-[0_1px_2px_hsl(268_30%_10%/0.06),0_8px_18px_-10px_hsl(268_30%_10%/0.22)] ring-2 transition duration-2 ease-m group-hover:-translate-y-0.5 group-hover:shadow-2 group-hover:ring-plum/25 group-active:scale-95 min-[360px]:h-[72px] min-[360px]:w-[72px] lg:h-[88px] lg:w-[88px]', TILE_WASH[i % TILE_WASH.length], TILE_RING[i % TILE_RING.length])}>
                {/* the square inscribed in the disc (70.7%): no corner of the photo can fall outside the ring */}
                <span className="block h-[70%] w-[70%]">
                  <ProductImage item={t.image} alt="" sizes="(min-width: 1024px) 88px, 72px" size={88} className="h-full w-full bg-transparent" imgClassName="mix-blend-multiply transition-transform duration-3 ease-m group-hover:scale-[1.07]" iconSize={20} showCaption={false} eager={i < 8} />
                </span>
                {t.newCount > 0 && (
                  <span className="absolute -end-2 -top-1">
                    <Chip tone="fresh">{S.home.newCount(t.newCount)}</Chip>
                  </span>
                )}
              </span>
              {/* two label lines reserved, the label sitting on its count: counts line up across a row and a one-line name never floats away from its number */}
              <span className="mt-2 flex min-h-[30px] items-end justify-center lg:mt-2.5 lg:min-h-[36px]">
                {/* one step down on a 320px phone: four columns leave ~69px, and "accessories" —
                    the second line of the longest category — measures ~68px at 12px, i.e. clipped
                    by the clamp's own overflow. At 11px it clears, and the 2-line reserve holds */}
                <span className="line-clamp-2 text-[11px] font-semibold leading-[14px] text-ink min-[360px]:text-xs min-[360px]:leading-[15px] lg:text-sm lg:leading-[18px]">{niceCategory(t.category)}</span>
              </span>
              <span className="text-2xs tnum text-ink-3">{t.count}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ───────────────────────── offer strip ───────────────────────── */

export function OfferStrip({ offer, className }: { offer: Offer; className?: string }) {
  const ends = useCountdown(offer.ends_at)
  const to = offer.scope_codes?.length || offer.kind === 'qty_tier' ? '/shop?f=deals' : '/cart'
  return (
    <Link
      to={to}
      onClick={() => track('rail_click', { meta: { rail: 'offer', code: String(offer.id) } })}
      className={cn('flex h-11 items-center gap-2.5 rounded-md bg-deal-soft px-3.5 text-sm text-deal-ink ring-1 ring-inset ring-deal/50 transition duration-1 ease-m hover:bg-deal/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', className)}
    >
      <Tag size={16} className="shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate">
        <b className="font-semibold">{offer.name}</b>
        {offer.summary ? <span className="opacity-80"> · {offer.summary}</span> : null}
      </span>
      {ends && (
        <span className="hidden shrink-0 items-center gap-1 text-xs font-medium sm:inline-flex">
          <Clock size={13} aria-hidden="true" /> {S.home.offerEnds(ends)}
        </span>
      )}
      <ChevronRight size={16} className="shrink-0 rtl:-scale-x-100" aria-hidden="true" />
    </Link>
  )
}

/* ───────────────────────── track order card ───────────────────────── */

export function TrackCard({ order, className }: { order: MyOrderSummary; className?: string }) {
  return (
    <Link to={`/o/${order.token}`} className={cn('flex items-center gap-3 rounded-lg border border-line bg-surface px-4 py-3 transition duration-1 ease-m hover:border-ink/15 hover:shadow-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', className)}>
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-sm bg-plum-soft text-plum">
        <Package size={18} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2 tnum">{order.order_no}</span>
        <span className="block truncate font-display text-sm font-bold text-ink">
          {order.status_label || order.status}
          {order.expected_delivery ? <span className="font-sans font-medium text-ink-2"> · {order.expected_delivery}</span> : null}
        </span>
      </span>
      <span className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-plum">
        {S.placed.track} <ChevronRight size={15} aria-hidden="true" className="rtl:-scale-x-100" />
      </span>
    </Link>
  )
}
