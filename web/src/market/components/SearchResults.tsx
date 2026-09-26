import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, Clock, Hash, MessageCircle } from 'lucide-react'
import type { ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { recentSearches } from '../lib/device'
import { categorySlug, niceCategory } from '../lib/format'
import { bestSellers, categoryTiles } from '../lib/home'
import type { Grouped } from '../lib/searchGroups'
import { S } from '../strings'
import { AnchorButton } from '../ui/Button'
import { ProductImage } from '../ui/ProductImage'
import { SectionHeader } from '../ui/SectionHeader'
import { Skeleton } from '../ui/Skeleton'
import { MarketCard } from './MarketCard'
import { soldOutSplit } from '../lib/facets'
import { SoldOutDivider } from './SoldOut'

/**
 * Search, shared by the Search page and the desktop palette.
 *
 * Empty query (Search page): recent searches → Popular restocks — real best sellers in stock as
 * thumb rows with variant chips and one-tap Add, so a restock can start without typing →
 * categories as photo tiles with their line counts.
 * Typing: instant grouped results — Codes (exact/prefix, ≤3) · Products (≤20, list rows with
 * inline Add so a mission shopper never leaves) · Categories (≤2).
 * Nothing found: the copy sends the merchant to their representative and the card carries that
 * WhatsApp action, then the shelf's own category tiles (photo + line count) — never bare pills.
 */

const POPULAR_MAX = 6
const LABEL = 'text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2'

export function SearchEmpty({ onPick }: { onPick: (q: string) => void }) {
  const { items, categories } = useMarket()
  const [recent] = useState<string[]>(() => recentSearches())
  const popular = useMemo(() => bestSellers(items).slice(0, POPULAR_MAX), [items])
  const tiles = useMemo(() => categoryTiles(items, categories), [items, categories])
  const chip = 'hit relative inline-flex h-10 items-center gap-1.5 rounded-full bg-surface px-3.5 text-sm text-ink shadow-1 ring-1 ring-inset ring-line transition duration-1 ease-m hover:bg-plum-wash hover:ring-ink/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'
  return (
    <div className="space-y-7">
      <p className="flex items-center gap-1.5 text-xs text-ink-2">
        <Hash size={13} aria-hidden="true" className="text-plum" /> {S.search.codeHint}
      </p>
      {recent.length > 0 && (
        <section aria-label={S.search.recent}>
          <h2 className={LABEL}>{S.search.recent}</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {recent.map((r) => (
              <button key={r} type="button" onClick={() => onPick(r)} className={chip}>
                <Clock size={13} className="text-ink-3" aria-hidden="true" /> {r}
              </button>
            ))}
          </div>
        </section>
      )}
      {popular.length > 0 && (
        <section aria-labelledby="search-popular">
          <SectionHeader id="search-popular" title={S.search.popularRestocks} seeAllTo="/shop?f=best" />
          <div className="mt-2 rounded-lg bg-surface px-3 shadow-1 ring-1 ring-line [&>article:last-child]:border-b-0">
            {popular.map((it) => (
              <MarketCard key={it.item_code} item={it} variant="list" from="search_popular" />
            ))}
          </div>
        </section>
      )}
      {tiles.length > 0 && (
        <section aria-labelledby="search-categories">
          <SectionHeader id="search-categories" title={S.search.categories} />
          <CategoryGrid tiles={tiles} className="mt-2" />
        </section>
      )}
    </div>
  )
}

/** The photo tiles of the shelf: a category with its own picture and its real line count.
 *
 * This grid is the rescue after a failed search, so a name it cannot show is the one thing it must
 * never do: on a phone two columns leave ~80px beside the disc, which truncated six of the eight
 * names and made "Bluetooth headset" and "Bluetooth speaker" the same tile ("Bluetoo…"). The name
 * wraps to two lines instead, and on a phone the disc steps down and the chevron stands down to pay
 * for it — the whole row is the link, so the arrow was decoration. */
function CategoryGrid({ tiles, className }: { tiles: { category: string; count: number; image: ShopItem | null }[]; className?: string }) {
  return (
    <ul className={cn('grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4', className)}>
      {tiles.map((t) => (
        <li key={t.category} className="min-w-0">
          <Link
            to={`/t/${categorySlug(t.category)}`}
            className="group flex min-h-[4rem] items-center gap-2.5 rounded-lg bg-surface py-1.5 pe-2 ps-2 shadow-1 ring-1 ring-line transition duration-2 ease-m hover:-translate-y-0.5 hover:shadow-2 hover:ring-ink/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 active:scale-[.985]"
          >
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface ring-1 ring-line-2 sm:h-12 sm:w-12">
              <span className="block h-[72%] w-[72%]">
                <ProductImage item={t.image} alt="" sizes="48px" size={48} className="h-full w-full bg-transparent" imgClassName="transition-transform duration-3 ease-m group-hover:scale-[1.07]" iconSize={16} showCaption={false} />
              </span>
            </span>
            <span className="min-w-0 flex-1">
              {/* NO `block` here: Tailwind emits `.block{display:block}` AFTER
                  `.line-clamp-2{display:-webkit-box}` at equal specificity, so it won and
                  -webkit-line-clamp went inert — "Bluetooth headset" ran to as many lines as it
                  liked and took the row with it. The clamp IS the display rule; nothing may
                  overwrite it (the same trap as MarketCard.tsx:481). */}
              <span className="line-clamp-2 text-xs font-semibold leading-4 text-ink sm:text-sm sm:leading-[18px]">{niceCategory(t.category)}</span>
              <span className="block text-2xs tnum text-ink-3">{S.shop.products(t.count)}</span>
            </span>
            <ChevronRight size={15} aria-hidden="true" className="hidden shrink-0 text-ink-3 transition-transform duration-2 ease-m group-hover:translate-x-0.5 sm:block rtl:-scale-x-100" />
          </Link>
        </li>
      ))}
    </ul>
  )
}

export function SearchGroups({ q, grouped, hints, onPick, activeCode, from = 'search_row', className }: { q: string; grouped: Grouped; hints: string[]; onPick: (q: string) => void; activeCode?: string | null; from?: string; className?: string }) {
  const { categories, items, rep } = useMarket()
  const tiles = useMemo(() => categoryTiles(items, categories), [items, categories])
  const total = grouped.codes.length + grouped.products.length
  // each group keeps the sold-out rule (lib/searchGroups, lib/search): available first, then — under
  // the divider, with that group's own count — the sold-out lines
  const codesSold = useMemo(() => soldOutSplit(grouped.codes, grouped.codes), [grouped.codes])
  const productsSold = useMemo(() => soldOutSplit(grouped.products, grouped.products), [grouped.products])
  const askUrl = rep?.whatsapp_url && q.trim() ? `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(S.shop.askHave(rep.first_name || '', q.trim()))}` : null
  const chip = 'hit relative h-9 rounded-full border border-line bg-surface px-3.5 text-sm text-ink hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'

  if (total === 0 && grouped.categories.length === 0) {
    return (
      <div className={cn('rounded-lg border border-line bg-surface p-5 text-start lg:p-6', className)}>
        <p className="font-display text-base font-bold text-ink">{S.shop.notOnShelf(q.trim())}</p>
        <p className="mt-1 text-sm text-ink-2">{S.shop.notOnShelfHint}</p>
        {hints.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-ink-2">{S.states.didYouMean}</span>
            {hints.map((h) => (
              <button key={h} type="button" onClick={() => onPick(h)} className={cn(chip, 'font-semibold text-plum')}>
                {h}
              </button>
            ))}
          </div>
        )}
        {/* the hint above sends the merchant to their representative — so the card carries that action */}
        {askUrl && (
          <AnchorButton href={askUrl} target="_blank" rel="noreferrer" variant="wa" className="mt-4" icon={<MessageCircle size={15} aria-hidden="true" />}>
            {S.cart.ask(rep!.first_name || 'us')}
          </AnchorButton>
        )}
        {tiles.length > 0 && (
          <div className="mt-6">
            <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.categories}</h3>
            <CategoryGrid tiles={tiles} className="mt-2" />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={className}>
      {grouped.codes.length > 0 && (
        <section aria-label={S.search.codes}>
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.codes}</h2>
          <div className="mt-1">
            {grouped.codes.map((it, i) => (
              <Fragment key={it.item_code}>
                {i === codesSold.firstOut && <SoldOutDivider count={codesSold.soldTotal} className="mb-1 mt-3" />}
                <div data-code={it.item_code} className={cn('rounded-sm transition-colors', activeCode === it.item_code && 'bg-plum-wash ring-1 ring-plum/20')}>
                  <MarketCard item={it} variant="list" from={from} />
                </div>
              </Fragment>
            ))}
          </div>
        </section>
      )}
      {grouped.products.length > 0 && (
        <section aria-label={S.search.products} className={cn(grouped.codes.length > 0 && 'mt-5')}>
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">
            {S.search.products} <span className="tnum text-ink-3">· {grouped.products.length}</span>
          </h2>
          <div className="mt-1">
            {grouped.products.map((it, i) => (
              <Fragment key={it.item_code}>
                {i === productsSold.firstOut && <SoldOutDivider count={productsSold.soldTotal} className="mb-1 mt-3" />}
                <div data-code={it.item_code} className={cn('rounded-sm transition-colors', activeCode === it.item_code && 'bg-plum-wash ring-1 ring-plum/20')}>
                  <MarketCard item={it} variant="list" from={from} />
                </div>
              </Fragment>
            ))}
          </div>
        </section>
      )}
      {grouped.categories.length > 0 && (
        <section aria-label={S.search.categories} className="mt-5">
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.categories}</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {grouped.categories.map((c) => (
              <Link key={c} to={`/t/${categorySlug(c)}`} className={cn(chip, 'inline-flex items-center font-semibold')}>
                {niceCategory(c)} <span className="ms-1 text-xs font-normal tnum text-ink-3">{items.filter((i) => (i.category || 'OTHER') === c).length}</span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

export function SearchPreparing() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label={S.search.preparing}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="h-14 w-14" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-3/5" />
            <Skeleton className="h-3 w-2/5" />
          </div>
          <Skeleton className="h-10 w-28" />
        </div>
      ))}
    </div>
  )
}
