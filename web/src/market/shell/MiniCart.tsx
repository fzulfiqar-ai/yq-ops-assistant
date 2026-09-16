import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, RotateCcw, ShoppingBag, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ProgressBar } from '../components/ProgressBar'
import { useMarket, useOrder } from '../MarketContext'
import { rememberedOrders } from '../lib/device'
import { bhd, minQtyOf, money, stepOf, productName } from '../lib/format'
import { useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button, LinkButton } from '../ui/Button'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'
import { useShell } from './ShellContext'

/**
 * The running order, always in view on wide screens (aside) and one click away below that
 * (drawer). Lines with tiny steppers, the free-delivery progress, live server totals, Place order.
 * Empty: two ways to fill it fast — Quick order and the last order.
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
  const go = (to: string) => {
    closeCart()
    navigate(to)
  }

  return (
    <section className={cn('flex flex-col overflow-hidden bg-surface', inDrawer ? 'h-full' : 'max-h-[calc(100dvh-var(--m-header-h)-32px)] rounded-xl border border-line shadow-1')} aria-label={S.cart.mini}>
      <header className="flex items-center gap-2 border-b border-line-2 px-4 py-3">
        <ShoppingBag size={18} className="text-plum" aria-hidden="true" />
        <h2 className="font-display text-md font-bold text-ink">{S.cart.mini}</h2>
        {items > 0 && <span className="ms-auto text-xs text-ink-2 tnum">{S.cart.summary(items, units)}</span>}
      </header>

      {lines.length === 0 ? (
        <div className="px-4 py-8 text-center">
          <p className="font-display text-base font-bold text-ink">{S.cart.emptyMini}</p>
          <p className="mt-1 text-sm text-ink-2">{S.cart.emptyMiniHint}</p>
          <div className="mt-4 grid gap-2">
            <Button variant="secondary" icon={<Zap size={15} aria-hidden="true" />} onClick={() => go('/quick')}>
              {S.nav.quick}
            </Button>
            {hasLast && (
              <Button variant="secondary" icon={<RotateCcw size={15} aria-hidden="true" />} onClick={() => go('/quick?load=last')}>
                {S.rails.regulars}
              </Button>
            )}
          </div>
        </div>
      ) : (
        <>
          <ul className="min-h-0 flex-1 divide-y divide-line-2 overflow-y-auto overscroll-contain px-4">
            {lines.map((line) => {
              const item = itemsByCode.get(line.item_code)
              const q = quoteLines.get(line.item_code)
              const dead = Boolean(q?.unavailable)
              const name = (item ? productName(item) : line.item_code)
              return (
                <li key={line.item_code} className="flex items-center gap-3 py-2.5">
                  <button type="button" onClick={() => openProduct(line.item_code, 'minicart')} className="h-11 w-11 shrink-0 overflow-hidden rounded-sm border border-line-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70" aria-label={name}>
                    <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={44} imgClassName="p-1" iconSize={16} showCaption={false} />
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-ink">{name}</div>
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
          <footer className="border-t border-line-2 px-4 pb-4 pt-3">
            {quote?.progress?.label && (
              <div className="mb-3">
                <ProgressBar progress={quote.progress} compact />
              </div>
            )}
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-ink-2">{S.cart.total}</span>
              <span key={total} className="font-display text-xl font-extrabold tnum text-ink anim-fade-in">
                {bhd(total)}
              </span>
            </div>
            {quoting && <div className="mt-0.5 text-2xs text-ink-3">{S.cart.updating}</div>}
            <Button size="lg" full className="mt-3" disabled={quote?.can_submit === false} onClick={() => go('/checkout')} icon={<ArrowRight size={16} aria-hidden="true" />}>
              {S.cart.place}
            </Button>
            <Link to="/cart" onClick={closeCart} className="mt-2 block text-center text-sm font-semibold text-plum hover:underline">
              {S.cart.full}
            </Link>
          </footer>
        </>
      )}
      {inDrawer && lines.length > 0 ? null : inDrawer ? <div className="px-4 pb-4"><LinkButton to="/" onClick={closeCart} variant="ghost" full>{S.cart.keepShopping}</LinkButton></div> : null}
    </section>
  )
}
