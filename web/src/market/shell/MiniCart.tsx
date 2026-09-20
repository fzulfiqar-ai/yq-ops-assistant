import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, ClipboardList, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ProgressBar } from '../components/ProgressBar'
import { PopularRows } from '../components/Spotlight'
import { WholesaleState } from '../components/WholesaleState'
import { useMarket, useOrder } from '../MarketContext'
import { rememberedOrders } from '../lib/device'
import { bhd, minQtyOf, money, stepOf, productName } from '../lib/format'
import { NAV_ICONS } from '../lib/icons'
import { useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button, LinkButton } from '../ui/Button'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'
import { useShell } from './ShellContext'

/**
 * The running restock, always in view on wide screens (aside) and one click away below that
 * (drawer). Lines with tiny steppers, then the wholesale state (how far to the minimum, three
 * one-tap fillers), the free-delivery progress and live server totals.
 * The CTA follows the minimum: under it → Review restock (the Restock page is where the merchant
 * decides — top up, or send a small order request); at it, or with no minimum → Place wholesale
 * order. Empty: paste a list, load the last order, popular lines.
 */
export function MiniCart({ inDrawer }: { inDrawer?: boolean }) {
  const { itemsByCode, setQty, remove } = useMarket()
  const { quote, quoting } = useOrder()
  const lines = useCartLines()
  const { items, units } = useCartCounts()
  const { closeCart, openProduct } = useShell()
  const navigate = useNavigate()
  const hasLast = rememberedOrders().length > 0
  const quoteLines = useMemo(() => new Map((quote?.lines || []).map((l) => [l.item_code, l])), [quote])
  const estimate = lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate
  const minimum = quote?.minimum || null
  const under = Boolean(minimum && !minimum.met && Number(minimum.remaining_bhd) > 0)
  // the mini-cart's one spoken line, as on the Restock page: the visible total and the wholesale
  // state are silent, and this is said once the quote settles — the estimate lands first and the
  // priced quote right after, so a live total would announce twice per stepper tap.
  const spoken = [`${S.cart.total} ${bhd(total)}`, minimum ? (under ? S.wholesale.away(bhd(minimum.remaining_bhd)) : S.wholesale.ready) : ''].filter(Boolean).join(' · ')
  const [settled, setSettled] = useState(spoken)
  if (!quoting && settled !== spoken) setSettled(spoken)
  const RestockIcon = NAV_ICONS.restock
  const go = (to: string) => {
    closeCart()
    navigate(to)
  }

  return (
    <section className={cn('flex flex-col overflow-hidden bg-surface', inDrawer ? 'h-full' : 'max-h-[72dvh] shrink-0 rounded-xl border border-line shadow-1')} aria-label={S.cart.mini}>
      <header className={cn('flex items-center gap-2.5 border-b border-line-2 px-4 py-3', inDrawer && 'pe-14')}>
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-sm bg-plum-wash text-plum" aria-hidden="true">
          <RestockIcon size={16} />
        </span>
        <h2 className="font-display text-md font-bold text-ink">{S.cart.mini}</h2>
        {items > 0 && <span className="ms-auto text-xs text-ink-2 tnum">{S.cart.summary(items, units)}</span>}
      </header>

      {lines.length === 0 ? (
        <>
          <div className="px-4 pb-6 pt-7 text-center">
            <span className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-plum-wash text-plum ring-1 ring-inset ring-plum/10" aria-hidden="true">
              <RestockIcon size={20} strokeWidth={1.75} />
            </span>
            <p className="mt-3 font-display text-base font-bold text-ink">{S.cart.emptyMini}</p>
            <p className="mx-auto mt-1 max-w-[16rem] text-sm leading-snug text-ink-2">{S.cart.emptyMiniHint}</p>
            <div className="mt-4 grid gap-2">
              <Button variant="secondary" icon={<ClipboardList size={15} aria-hidden="true" />} onClick={() => go('/quick')}>
                {S.restock.paste}
              </Button>
              {hasLast && (
                <Button variant="secondary" icon={<RotateCcw size={15} aria-hidden="true" />} onClick={() => go('/quick?load=last')}>
                  {S.restock.again}
                </Button>
              )}
            </div>
          </div>
          {!inDrawer && <PopularRows limit={3} />}
          {inDrawer && (
            <div className="px-4 pb-4">
              <LinkButton to="/shop" onClick={closeCart} variant="ghost" full>
                {S.cart.keepShopping}
              </LinkButton>
            </div>
          )}
        </>
      ) : (
        <>
          {/* one scroll body: the lines, then the wholesale state — the footer (total + CTA) never gets squeezed out */}
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            <ul className="divide-y divide-line-2 px-4">
              {lines.map((line) => {
                const item = itemsByCode.get(line.item_code)
                const q = quoteLines.get(line.item_code)
                const dead = Boolean(q?.unavailable)
                const name = item ? productName(item) : line.item_code
                return (
                  <li key={line.item_code} className="flex items-center gap-3 py-2.5">
                    <button type="button" onClick={() => openProduct(line.item_code, 'minicart')} className="h-11 w-11 shrink-0 overflow-hidden rounded-sm border border-line-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70" aria-label={name}>
                      <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={44} imgClassName="p-1" iconSize={16} showCaption={false} />
                    </button>
                    <div className="min-w-0 flex-1">
                      {/* two lines, not an ellipsis: in this catalog the part that tells two SKUs
                          apart is at the END of the name — "(2USB Port)" vs "(USB + Type-C Port)",
                          "1Mtr" vs "2Mtr" — and this is the panel where the merchant checks what
                          they are about to order */}
                      <div className="line-clamp-2 text-sm font-semibold leading-snug text-ink">{name}</div>
                      <div className={cn('text-xs tnum', dead ? 'text-bad' : 'text-ink-2')}>{dead ? q?.blocked_reason || S.card.soldOut : q ? `${money(q.unit_price_bhd)} ${S.cart.each}` : line.item_code}</div>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <span className="text-sm font-semibold tnum text-ink">{dead ? '—' : bhd(q?.line_total_bhd ?? (Number(item?.price_bhd) || 0) * line.qty)}</span>
                      {item && !dead ? (
                        <Stepper value={line.qty} step={stepOf(item)} min={minQtyOf(item)} size="xs" label={name} onChange={(n) => setQty(item, n)} onRemove={() => remove(line.item_code)} />
                      ) : (
                        <button type="button" onClick={() => remove(line.item_code)} className="text-xs font-semibold text-bad hover:underline">
                          {S.cart.remove}
                        </button>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
            <WholesaleState variant="compact" className="mx-4 mb-3 mt-1" />
          </div>
          <footer className="border-t border-line-2 px-4 pb-4 pt-3">
            {quote?.progress?.label && (
              <div className="mb-3">
                <ProgressBar progress={quote.progress} compact />
              </div>
            )}
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm text-ink-2">{S.cart.total}</span>
              <span className="min-w-0 text-end">
                <span key={total} className="block font-display text-xl font-extrabold tnum text-ink anim-fade-in">
                  {bhd(total)}
                </span>
              </span>
            </div>
            <p role="status" aria-atomic="true" className="sr-only">
              {settled}
            </p>
            {under && minimum ? (
              <div className="text-end text-xs font-semibold tnum text-plum">{S.wholesale.toGo(bhd(minimum.remaining_bhd))}</div>
            ) : quoting ? (
              <div className="mt-0.5 text-end text-2xs text-ink-3">{S.cart.updating}</div>
            ) : null}
            {under ? (
              <Button size="lg" full className="mt-3" onClick={() => go('/cart')} icon={<ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />}>
                {S.cart.review}
              </Button>
            ) : (
              <>
                <Button size="lg" full className="mt-3" disabled={quote?.can_submit === false} onClick={() => go('/checkout')} icon={<ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />}>
                  {S.cart.place}
                </Button>
                <Link to="/cart" onClick={closeCart} className="mt-1 flex h-10 items-center justify-center rounded-sm text-sm font-semibold text-plum hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                  {S.cart.full}
                </Link>
              </>
            )}
          </footer>
        </>
      )}
    </section>
  )
}
