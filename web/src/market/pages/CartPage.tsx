import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Loader2, MessageCircle, Tag, Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Stepper } from '@/components/ui/stepper'
import { cn } from '@/lib/utils'
import type { Quote, ShopItem } from '@/lib/shopApi'
import { ProductImage } from '@/pages/shop/ProductImage'
import { bhd, FIELD, minQtyOf, money, RING, stepOf } from '@/pages/shop/shared'
import { useMarket } from '../MarketContext'
import { BTN_PRIMARY, BTN_SECONDARY, EmptyState, ProgressBar, QtySheet, Rail } from '../components/Bits'
import { BottomNav, Page, SAFE, TopBar } from '../components/Chrome'
import { MarketCard, tierNudge } from '../components/MarketCard'
import { track } from '../lib/events'
import { S } from '../strings'

type QLine = NonNullable<Quote['lines']>[number]

/** The cart as a page (plan §J): lines, tier progress, the free-delivery bar, "Complete your order", note, coupon, totals. */
export default function CartPage() {
  const navigate = useNavigate()
  const m = useMarket()
  const { cart, itemsByCode, quote, quoting, quoteError, coupon, setCoupon, note, setNote, rep } = m
  const [couponDraft, setCouponDraft] = useState(coupon)
  const [couponOpen, setCouponOpen] = useState(Boolean(coupon))
  const [keypad, setKeypad] = useState<ShopItem | null>(null)

  useEffect(() => {
    document.title = `${S.cart.title} · ${S.brand}`
    track('cart', { meta: { count: cart.lines.length } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const quoteLines = useMemo(() => {
    const map = new Map<string, QLine>()
    for (const l of quote?.lines || []) map.set(l.item_code, l)
    return map
  }, [quote])

  const complete = useMemo(() => {
    const inCart = new Set(cart.lines.map((l) => l.item_code))
    const out: ShopItem[] = []
    for (const l of cart.lines) for (const p of m.pairsFor(l.item_code)) if (!inCart.has(p.item_code) && !out.includes(p) && p.stock_status !== 'out_of_stock') out.push(p)
    return out.slice(0, 8)
  }, [cart.lines, m])

  const blocked = quote?.can_submit === false ? quote.block_reason || 'This order cannot be sent yet.' : ''
  const canCheckout = cart.lines.length > 0 && !quoting && quote?.can_submit !== false
  const first = rep?.first_name || ''

  const askUrl = useMemo(() => {
    if (!rep?.whatsapp_url) return null
    const text = [`Hello ${first}, a question about my order:`, ...cart.lines.map((l) => `• ${l.qty} × ${l.item_code}`)].join('\n')
    return `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(text)}`
  }, [rep, first, cart.lines])

  return (
    <Page className="pb-40">
      <TopBar back title={`${S.cart.title}${cart.items ? ` · ${cart.items} ${cart.items === 1 ? 'product' : 'products'}` : ''}`} />
      <main className="mx-auto max-w-3xl px-4 pt-2">
        {cart.lines.length === 0 ? (
          <EmptyState title={S.cart.empty} hint={S.cart.emptyHint} action={<button type="button" onClick={() => navigate('/')} className={BTN_SECONDARY}>{S.cart.browse}</button>} />
        ) : (
          <>
            {quote?.progress?.label && <ProgressBar progress={quote.progress} />}

            <ul className="mt-3 divide-y divide-[#f4f2f9] overflow-hidden rounded-[20px] border border-[#ece9f3] bg-white px-4">
              {cart.lines.map((line) => {
                const item = itemsByCode.get(line.item_code)
                const q = quoteLines.get(line.item_code)
                const dead = Boolean(q?.unavailable)
                const fixable = dead && Boolean(item) && Number(q?.moq || 1) > line.qty
                const nudge = item ? tierNudge(item, line.qty) : null
                return (
                  <li key={line.item_code} className="flex gap-3 py-3.5">
                    <button type="button" onClick={() => navigate(`/p/${encodeURIComponent(line.item_code)}`)} className={cn('h-[68px] w-[68px] shrink-0 overflow-hidden rounded-[14px] border border-[#ece9f3]', RING)} aria-label={`View ${item?.display_name || line.item_code}`}>
                      <ProductImage srcs={[item?.thumb_url, item?.product_image_url]} alt="" width={68} height={68} className="h-full w-full" imgClassName="p-1.5" iconSize={20} showCaption={false} />
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="line-clamp-2 font-display text-[13px] font-bold leading-tight text-[#1a1430]">{item?.display_name || line.item_code}</div>
                          <div className="mt-0.5 text-[11px] text-[#6b6480]">{line.item_code}{q && !dead ? ` · ${money(q.unit_price_bhd)} ${S.cart.each}` : ''}</div>
                        </div>
                        <div className="shrink-0 text-right font-display text-[13.5px] font-bold tabular-nums text-[#1a1430]">{dead ? '—' : bhd(q?.line_total_bhd ?? (Number(item?.price_bhd) || 0) * line.qty)}</div>
                      </div>
                      {dead && q?.blocked_reason && <p className="mt-1.5 flex items-center gap-1.5 text-[11.5px] font-medium text-[#9f1239]"><AlertTriangle size={12} aria-hidden="true" /> {q.blocked_reason}</p>}
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        {dead && !fixable ? (
                          <button type="button" onClick={() => m.remove(line.item_code)} className={cn('inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#f3c9d2] bg-[#fdecef] px-3.5 text-[12px] font-semibold text-[#9f1239]', RING)}><Trash2 size={13} aria-hidden="true" /> {S.cart.remove}</button>
                        ) : (
                          <Stepper value={line.qty} step={stepOf(item)} min={minQtyOf(item)} size="sm" label={item?.display_name || line.item_code} onChange={(n) => item && m.setQty(item, n)} onRemove={() => m.remove(line.item_code)} onValueClick={item ? () => setKeypad(item) : undefined} />
                        )}
                        {q?.backorder && <Badge tone="amber">{S.card.backorder}</Badge>}
                        {q?.applied?.length ? <Badge tone="green">{q.applied[0]?.name || 'Discount'}</Badge> : null}
                      </div>
                      {nudge && !dead && <p className="mt-1.5 text-[11px] font-medium text-[#6d28d9]">{nudge}</p>}
                      {q?.warning && <p className="mt-1.5 text-[11px] text-[#96600d]">{q.warning}</p>}
                    </div>
                  </li>
                )
              })}
            </ul>

            {complete.length > 0 && (
              <Rail id="complete" title={S.rails.complete}>
                {complete.map((it) => (
                  <MarketCard key={it.item_code} item={it} compact qty={cart.qtyOf(it.item_code)} defaultQty={m.defaultQty(it)} allowBackorder={m.allowBackorder} showCompare={m.showCompare} publicTiers={m.publicTiers} rep={rep}
                    onOpen={() => { track('reco_click', { item_code: it.item_code, meta: { rail: 'complete' } }); navigate(`/p/${encodeURIComponent(it.item_code)}`) }}
                    onAdd={() => m.add(it, undefined, 'complete')} onSetQty={(n) => m.setQty(it, n)} onRemove={() => m.remove(it.item_code)} onKeypad={() => setKeypad(it)} />
                ))}
              </Rail>
            )}

            <div className="mt-5">
              <label htmlFor="yq-note" className="mb-1.5 block text-[11.5px] font-semibold text-[#1a1430]">{first ? `Note for ${first}` : S.cart.note}</label>
              <textarea id="yq-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder={S.cart.notePlaceholder} className={cn(FIELD, 'h-auto resize-none py-2.5 text-[16px] leading-snug')} />
            </div>

            <div className="mt-4">
              {!couponOpen ? (
                <button type="button" onClick={() => setCouponOpen(true)} className={cn('inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-[#6d28d9] underline-offset-2 hover:underline', RING)}><Tag size={13} aria-hidden="true" /> {S.cart.coupon}</button>
              ) : (
                <div className="flex gap-2">
                  <input value={couponDraft} onChange={(e) => setCouponDraft(e.target.value.toUpperCase())} placeholder={S.cart.couponPlaceholder} aria-label={S.cart.coupon} autoComplete="off" className={cn(FIELD, 'flex-1 text-[16px] uppercase')} />
                  <button type="button" onClick={() => setCoupon(couponDraft.trim())} className={BTN_SECONDARY}>{S.cart.apply}</button>
                </div>
              )}
              {quote?.coupon?.message && <p className={cn('mt-1.5 text-[11.5px] font-medium', quote.coupon.valid === false ? 'text-[#9f1239]' : 'text-[#137a48]')}>{quote.coupon.message}</p>}
            </div>

            {(quote?.warnings || []).length > 0 && (
              <ul className="mt-4 space-y-1.5">{(quote?.warnings || []).map((w, i) => <li key={i} className="flex gap-2 rounded-xl bg-[#fdf3e3] px-3 py-2 text-[11.5px] leading-snug text-[#96600d]"><AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden="true" /><span>{w}</span></li>)}</ul>
            )}
            {quoteError && <p className="mt-4 rounded-xl bg-[#fdecef] px-3 py-2 text-[11.5px] text-[#9f1239]">{quoteError}</p>}

            <dl className="mt-4 space-y-2 rounded-[16px] border border-[#ece9f3] bg-white p-4 text-[12.5px]">
              <div className="flex justify-between"><dt className="text-[#6b6480]">{S.cart.subtotal}</dt><dd className="tabular-nums">{bhd(quote?.subtotal_bhd)}</dd></div>
              {(quote?.discounts || []).map((d, i) => <div key={d.rule_id ?? i} className="flex justify-between"><dt className="truncate pr-2 text-[#137a48]">{d.name || 'Discount'}</dt><dd className="shrink-0 tabular-nums text-[#137a48]">−{bhd(d.amount_bhd)}</dd></div>)}
              <div className="flex justify-between"><dt className="text-[#6b6480]">{S.cart.delivery}</dt><dd className="tabular-nums">{Number(quote?.delivery_bhd) > 0 ? bhd(quote?.delivery_bhd) : S.cart.free}</dd></div>
              <div className="flex justify-between border-t border-[#f4f2f9] pt-2.5"><dt className="font-semibold">{S.cart.total}</dt><dd className="font-display text-[15px] font-extrabold tabular-nums">{bhd(quote?.total_bhd)}</dd></div>
              {quoting && <div className="flex items-center gap-1.5 pt-0.5 text-[10.5px] text-[#6b6480]"><Loader2 size={11} className="animate-spin" aria-hidden="true" /> {S.cart.updating}</div>}
            </dl>

            {askUrl && (
              <a href={askUrl} target="_blank" rel="noreferrer" className={cn(BTN_SECONDARY, 'mt-4 w-full')}>
                <MessageCircle size={15} aria-hidden="true" /> {S.cart.ask(first)}
              </a>
            )}
          </>
        )}
      </main>

      {cart.lines.length > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-[#ece9f3] bg-white/95 px-4 pt-3 backdrop-blur-md md:bottom-0" style={{ paddingBottom: `calc(4.25rem + ${SAFE} + 0.75rem)` }}>
          <div className="mx-auto max-w-3xl">
            {blocked && <p className="mb-2 text-[11.5px] font-medium text-[#9f1239]">{blocked}</p>}
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-[11.5px] text-[#6b6480]">{S.cart.total}</div>
                <div className="font-display text-[19px] font-extrabold leading-tight tabular-nums text-[#1a1430]">{bhd(quote?.total_bhd)}</div>
              </div>
              <button type="button" disabled={!canCheckout} onClick={() => navigate('/checkout')} className={cn(BTN_PRIMARY, 'shrink-0 px-7')}>{S.cart.place}</button>
            </div>
            <p className="mt-1.5 text-[10.5px] text-[#6b6480]">{first ? S.cart.placeHint(first) : S.cart.placeHintGeneric}</p>
          </div>
        </div>
      )}
      <BottomNav />
      {keypad && <QtySheet item={keypad} value={cart.qtyOf(keypad.item_code)} onApply={(n) => m.setQty(keypad, n)} onRemove={() => m.remove(keypad.item_code)} onClose={() => setKeypad(null)} />}
    </Page>
  )
}
