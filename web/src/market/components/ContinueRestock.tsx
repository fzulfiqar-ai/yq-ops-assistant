import { lazy, Suspense, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Boxes, CheckCheck, ClipboardList, PackageOpen, RotateCcw, SquarePen } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { useMarket, useOrder } from '../MarketContext'
import { track } from '../lib/events'
import { bhd, fmtDateShort, productName, unitAt } from '../lib/format'
import type { RegularLine } from '../lib/home'
import { useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button, LinkButton } from '../ui/Button'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { Skeleton } from '../ui/Skeleton'

/**
 * "Continue your restock" — one card that always answers "what do I do next?":
 *  • lines in the restock list → how far the list is from a wholesale order (WholesaleState),
 *    the lines as thumbnails, Review restock;
 *  • a recognised merchant with a last order → Order again: the lines, one tap to add them all;
 *  • anyone else → paste the list you would have sent on WhatsApp (`paste`, phone only: the
 *    desktop home already makes that offer in the hero and the closing band).
 * Home places it right under the categories, on every visit.
 */
// The wholesale state is the Restock page's card; on Home it only appears once the restock has a
// priced quote (network time anyway), so its code loads on demand instead of with the first screen
// — as soon as the catalog says the shop has a minimum, so the chunk is there when the quote is.
const WholesaleState = lazy(() => import('./WholesaleState').then((mod) => ({ default: mod.WholesaleState })))

export function ContinueRestock({
  lastLines,
  placedAt,
  pending,
  paste = true,
  className,
}: {
  /** the lines of the last order that are on the shelf — what "Add all" can honestly add (Home filters) */
  lastLines: RegularLine[]
  placedAt?: string | null
  /** this phone remembers an order and it is still loading: hold the slot instead of offering the paste card */
  pending?: boolean
  /** false where the page already makes the paste offer twice (desktop): nothing to continue → nothing */
  paste?: boolean
  className?: string
}) {
  const { recognized } = useMarket()
  const lines = useCartLines()
  if (lines.length) return <ResumeCard lines={lines} className={className} />
  if (recognized && lastLines.length) return <AgainCard lines={lastLines} placedAt={placedAt} className={className} />
  if (pending) return <AgainSkeleton className={className} />
  return paste ? <PasteCard className={className} /> : null
}

/** A square photo tile with the quantity on its corner. */
function Thumb({ item, code, qty, low, size = 'md' }: { item?: ShopItem | null; code: string; qty: number; low?: boolean; size?: 'md' | 'lg' }) {
  return (
    <div className={cn('relative shrink-0', size === 'lg' ? 'h-14 w-14 lg:h-16 lg:w-16' : 'h-14 w-14')}>
      <ProductImage item={item} alt={item ? productName(item) : code} sizes={SIZES_THUMB} size={64} className="h-full w-full overflow-hidden rounded-md ring-1 ring-line-2" imgClassName="p-1" iconSize={16} showCaption={false} />
      <span className="absolute -end-1.5 -top-1.5 grid h-5 min-w-5 place-items-center rounded-full bg-ink px-1 text-[10px] font-bold tnum text-white ring-2 ring-surface">{qty}</span>
      {low && <span className="absolute bottom-1 start-1 h-2 w-2 rounded-full bg-warn ring-2 ring-surface" aria-hidden="true" />}
    </div>
  )
}

function ResumeCard({ lines, className }: { lines: { item_code: string; qty: number }[]; className?: string }) {
  const { itemsByCode, settings } = useMarket()
  const { quote } = useOrder()
  const units = lines.reduce((s, l) => s + l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const low = lines.filter((l) => itemsByCode.get(l.item_code)?.stock_status === 'low_stock').length
  // The wholesale state carries its own Review link, but it renders only once the server has priced
  // the restock. Before the first quote the catalog's minimum says whether it will: if it does, the
  // card keeps its box (WholesalePending — same height, same Review link) instead of leaving the
  // merchant with no action and a block that drops in later.
  const ready = Boolean(quote?.minimum)
  const reserve = quote ? ready : Number(settings.min_order_bhd) > 0
  useEffect(() => {
    // the chunk is wanted the moment the shop has a minimum, not when the first quote arrives
    if (reserve && !ready) void import('./WholesaleState')
  }, [reserve, ready])

  return (
    <section aria-labelledby="home-continue" className={cn('overflow-hidden rounded-xl bg-surface shadow-2 ring-1 ring-plum/20', className)}>
      <div className="flex items-center gap-3 px-4 pt-4 lg:px-5 lg:pt-5">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-plum text-white" aria-hidden="true">
          <PackageOpen size={18} strokeWidth={1.9} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="home-continue" className="truncate font-display text-base font-bold text-ink lg:text-lg">
            {S.rails.continue}
          </h2>
          {/* not a live region: the restock total is announced once, by the bar that owns it */}
          <p className="truncate text-xs text-ink-2">
            {S.cart.summary(lines.length, units)} · <span className="font-semibold tnum text-ink">{bhd(total)}</span>
            {low > 0 && (
              <>
                {' · '}
                <span className="font-medium text-warn">{S.home.fewLeftCount(low)}</span>
              </>
            )}
          </p>
        </div>
        {!reserve && (
          <LinkButton to="/cart" variant="primary" size="md" className="shrink-0 rounded-full" onClick={() => track('rail_click', { meta: { rail: 'continue', code: 'review' } })}>
            {S.restock.review}
            <ArrowRight size={15} aria-hidden="true" className="rtl:-scale-x-100" />
          </LinkButton>
        )}
      </div>
      <div className="rail px-4 pb-3 pt-4 lg:px-5" style={{ ['--m-rail-gap' as string]: '10px' }}>
        {lines.map((l) => {
          const item = itemsByCode.get(l.item_code)
          return <Thumb key={l.item_code} item={item} code={l.item_code} qty={l.qty} low={item?.stock_status === 'low_stock'} />
        })}
        <span aria-hidden="true" className="w-1 shrink-0" />
      </div>
      {reserve && (
        // the box is reserved at the height of the state it will hold (two heading lines)
        <div className="min-h-[187px] border-t border-line-2 px-4 pb-4 pt-3.5 lg:px-5">
          {ready ? (
            <Suspense fallback={<WholesalePending />}>
              <WholesaleState variant="home" />
            </Suspense>
          ) : (
            <WholesalePending />
          )}
        </div>
      )}
    </section>
  )
}

/**
 * The wholesale state's box before the first quote, and while its chunk loads: WholesaleState's
 * `home` variant to the pixel — kicker, two heading lines, the 10 px rail, Review restock — with
 * placeholders where its numbers go, since none are known yet. The Review link is real, so the
 * card always has somewhere to go.
 */
function WholesalePending() {
  return (
    <div aria-busy="true">
      {/* a flex row, like the state's own kicker: an inline chip would sit on a taller line box */}
      <div className="flex items-center gap-2">
        <span className="inline-flex h-6 items-center gap-1.5 rounded-full bg-plum-wash px-2.5 text-2xs font-semibold uppercase tracking-[0.1em] text-plum-ink ring-1 ring-inset ring-plum/10">
          <Boxes size={12} strokeWidth={2} aria-hidden="true" />
          {S.wholesale.kicker}
        </span>
      </div>
      <Skeleton className="mt-2.5 h-[18px] w-full rounded-xs" />
      <Skeleton className="mt-[9px] h-[18px] w-3/5 rounded-xs" />
      <Skeleton className="mt-4 h-2.5 w-full rounded-full" />
      <div className="mt-2 flex items-center justify-end">
        <Link to="/cart" className="-me-2 inline-flex h-11 shrink-0 items-center gap-1 rounded-sm px-2 text-sm font-semibold text-plum transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          {S.wholesale.review}
          <ArrowRight size={14} aria-hidden="true" className="rtl:-scale-x-100" />
        </Link>
      </div>
    </div>
  )
}

/**
 * The Order again card's box while this phone's remembered orders are still loading. Home reserves
 * the slot as soon as it knows there is an order to load, so the top zone is laid out once.
 */
function AgainSkeleton({ className }: { className?: string }) {
  return (
    <section aria-hidden="true" className={cn('overflow-hidden rounded-xl bg-surface shadow-2 ring-1 ring-line', className)}>
      <div className="flex items-center gap-3 px-4 pt-4 lg:px-5 lg:pt-5">
        <Skeleton className="h-10 w-10 shrink-0 rounded-full" />
        <div className="min-w-0 flex-1">
          <div className="flex h-6 items-center lg:h-7">
            <Skeleton className="h-4 w-32 rounded-xs" />
          </div>
          <div className="flex h-4 items-center">
            <Skeleton className="h-2.5 w-48 max-w-full rounded-xs" />
          </div>
        </div>
      </div>
      {/* the same box as the card's rail (10 px gaps), without making a scroll region of it */}
      <div className="flex gap-2.5 overflow-hidden px-4 pb-1 pt-4 lg:px-5">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-14 w-14 shrink-0 rounded-md lg:h-16 lg:w-16" />
        ))}
      </div>
      <div className="flex gap-2 p-4 pt-3 lg:px-5 lg:pb-5">
        <Skeleton className="h-12 min-w-0 flex-1 rounded-full" />
        <Skeleton className="h-12 w-12 shrink-0 rounded-full lg:w-36" />
      </div>
    </section>
  )
}

function AgainCard({ lines, placedAt, className }: { lines: RegularLine[]; placedAt?: string | null; className?: string }) {
  const m = useMarket()
  const navigate = useNavigate()
  const total = lines.reduce((s, l) => s + (unitAt(l.item, l.qty) ?? 0) * l.qty, 0)
  const date = fmtDateShort(placedAt)
  const addAll = () => {
    m.addMany(lines, 'order_again')
    navigate('/cart')
  }
  return (
    <section aria-labelledby="home-again" className={cn('overflow-hidden rounded-xl bg-surface shadow-2 ring-1 ring-line', className)}>
      <div className="flex items-center gap-3 px-4 pt-4 lg:px-5 lg:pt-5">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-plum-soft text-plum" aria-hidden="true">
          <RotateCcw size={18} strokeWidth={1.9} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="home-again" className="truncate font-display text-base font-bold text-ink lg:text-lg">
            {S.rails.again}
          </h2>
          <p className="truncate text-xs text-ink-2">
            {S.home.lastOrder}
            {date ? ` · ${date}` : ''} · {S.states.products(lines.length)}
          </p>
        </div>
      </div>
      <ul className="rail px-4 pb-1 pt-4 lg:px-5" style={{ ['--m-rail-gap' as string]: '10px' }} aria-label={S.home.lastOrder}>
        {lines.map((l) => (
          <li key={l.item.item_code}>
            <Thumb item={l.item} code={l.item.item_code} qty={l.qty} low={l.item.stock_status === 'low_stock'} size="lg" />
          </li>
        ))}
        <li aria-hidden="true" className="w-1 shrink-0" />
      </ul>
      <div className="flex gap-2 p-4 pt-3 lg:px-5 lg:pb-5">
        <Button size="lg" className="min-w-0 flex-1 rounded-full" onClick={addAll} icon={<ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />}>
          <span className="truncate">{S.home.addAll(lines.length, bhd(total))}</span>
        </Button>
        <LinkButton to="/quick?load=last" size="lg" variant="secondary" aria-label={S.home.editQty} title={S.home.editQty} className="w-12 shrink-0 rounded-full px-0 lg:w-auto lg:px-5">
          <SquarePen size={17} aria-hidden="true" />
          <span className="hidden lg:inline">{S.home.editQty}</span>
        </LinkButton>
      </div>
    </section>
  )
}

function PasteCard({ className }: { className?: string }) {
  return (
    <section aria-labelledby="home-paste" className={cn('canvas-lilac overflow-hidden rounded-xl', className)}>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 p-5 lg:gap-x-10 lg:px-8 lg:py-7">
        <div className="min-w-0">
          <p className="inline-flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-[0.12em] text-plum">
            <ClipboardList size={13} aria-hidden="true" />
            {S.home.quick}
          </p>
          <h2 id="home-paste" className="mt-2 text-balance font-display text-xl font-bold leading-tight text-ink lg:text-2xl">
            {S.home.pasteTitle}
          </h2>
        </div>
        <PasteBubble className="row-span-1 mt-1 self-start lg:row-span-2 lg:mt-0 lg:self-center" />
        <div className="col-span-2 lg:col-span-1">
          <p className="mt-2 max-w-md text-sm text-ink-2">{S.home.pasteLine}</p>
          <LinkButton to="/quick" variant="primary" size="lg" className="mt-4 rounded-full" icon={<ClipboardList size={17} aria-hidden="true" />} onClick={() => track('rail_click', { meta: { rail: 'continue', code: 'paste' } })}>
            {S.band.paste}
          </LinkButton>
        </div>
      </div>
    </section>
  )
}

/**
 * The list a shop owner would send on WhatsApp, as a sent message — decoration (aria-hidden) that
 * explains the paste flow at a glance. The lines are an example in the quick-order parser's format.
 * `size="lg"` is the closing band's copy: it keeps growing past 1280 so the note holds its half of
 * a 1500 px band instead of sitting in it as a stamp with a void beside it.
 */
export function PasteBubble({ className, size = 'sm' }: { className?: string; size?: 'sm' | 'lg' }) {
  const big = size === 'lg'
  return (
    <div aria-hidden="true" className={cn('w-[7.5rem] rotate-[4deg] rounded-[18px] rounded-se-[5px] bg-fresh-soft px-3 pb-1.5 pt-2.5 text-ink shadow-3 lg:w-48 lg:px-4 lg:pb-2 lg:pt-3.5', big && 'xl:w-56 2xl:w-64 2xl:px-5 2xl:pb-2.5 2xl:pt-4 3xl:w-72', className)}>
      {S.home.pasteExample.map((line) => (
        <p key={line} className={cn('whitespace-nowrap text-xs font-semibold leading-5 tnum lg:text-base lg:leading-7', big && '2xl:text-lg 2xl:leading-8')}>
          {line}
        </p>
      ))}
      <span className="mt-0.5 flex justify-end text-fresh-ink/70">
        <CheckCheck size={14} strokeWidth={2} />
      </span>
    </div>
  )
}
