import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Loader2, MessageCircle, ShieldCheck, Tag, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Quote, ShopItem } from '@/lib/shopApi'
import { MarketCard } from '../components/MarketCard'
import { MinimumBar } from '../components/MinimumBar'
import { ProgressBar } from '../components/ProgressBar'
import { QtySheet } from '../components/QtySheet'
import { Rail } from '../components/Rail'
import { EmptyState } from '../components/States'
import { useMarket, useOrder } from '../MarketContext'
import { track } from '../lib/events'
import { bhd, minQtyOf, money, nextTier, stepOf, productName } from '../lib/format'
import { PageBar, useHideNav, usePageTitle, useShell } from '../shell/ShellContext'
import { useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'
import { AnchorButton, Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { Input, Label, Textarea } from '../ui/Field'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'

type QLine = NonNullable<Quote['lines']>[number]

/**
 * /cart — the order as a page. Lines (thumb · name · code · unit after tier · stepper · total),
 * the free-delivery progress from the real quote, "Complete your order", the note, a collapsed
 * coupon, "8 products · 72 pcs" and live server totals. Sticky Place order. Desktop: lines left,
 * a sticky summary right.
 */
export default function CartPage() {
  const navigate = useNavigate()
  const m = useMarket()
  const { quote, quoting, quoteError, coupon, setCoupon, note, setNote } = useOrder()
  const { itemsByCode, rep } = m
  const { viewport, openProduct } = useShell()
  const toast = useToast()
  const lines = useCartLines()
  const { items, units } = useCartCounts()
  const [couponDraft, setCouponDraft] = useState(coupon)
  const [couponOpen, setCouponOpen] = useState(Boolean(coupon))
  const [keypad, setKeypad] = useState<ShopItem | null>(null)
  usePageTitle(items ? `${S.cart.title} · ${items}` : S.cart.title, true, `${S.cart.title} · ${S.brand}`)
  useHideNav(true)

  useEffect(() => {
    track('cart', { meta: { count: lines.length } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const quoteLines = useMemo(() => new Map<string, QLine>((quote?.lines || []).map((l) => [l.item_code, l])), [quote])
  const complete = useMemo(() => {
    const inCart = new Set(lines.map((l) => l.item_code))
    const out: ShopItem[] = []
    for (const l of lines) for (const p of m.pairsFor(l.item_code)) if (!inCart.has(p.item_code) && !out.includes(p) && p.stock_status !== 'out_of_stock') out.push(p)
    return out.slice(0, 8)
  }, [lines, m])

  const blocked = quote?.can_submit === false ? quote.block_reason || 'This order cannot be sent yet.' : ''
  const canCheckout = lines.length > 0 && !quoting && quote?.can_submit !== false
  const small = Boolean(quote?.minimum && !quote.minimum.met && quote.minimum.mode !== 'block')
  const first = rep?.first_name || ''
  const askUrl = useMemo(() => {
    if (!rep?.whatsapp_url) return null
    const text = [`Hello ${first}, a question about my order:`, ...lines.map((l) => `• ${l.qty} × ${l.item_code}`)].join('\n')
    return `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(text)}`
  }, [rep, first, lines])
  const desktop = viewport === 'desktop' || viewport === 'wide'
  const estimate = lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate

  const removeWithUndo = (item: ShopItem | undefined, code: string, qty: number) => {
    m.remove(code)
    toast(S.card.removed((item ? productName(item) : code)), { kind: 'info', action: item ? { label: S.card.undo, onClick: () => m.setQty(item, qty) } : undefined })
  }

  if (lines.length === 0) {
    return (
      <div className="px-gutter lg:px-0">
        <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.cart.title}</h1>
        <EmptyState className="mt-3" title={S.cart.empty} hint={S.cart.emptyHint} action={<Button variant="secondary" onClick={() => navigate('/')}>{S.cart.browse}</Button>} />
      </div>
    )
  }

  const summary = (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-base font-bold text-ink">{S.cart.title}</h2>
        <span className="text-xs tnum text-ink-2">{S.cart.summary(items, units)}</span>
      </div>
      <dl className="mt-3 space-y-2 text-sm">
        <div className="flex justify-between">
          <dt className="text-ink-2">{S.cart.subtotal}</dt>
          <dd className="tnum">{bhd(quote?.subtotal_bhd ?? estimate)}</dd>
        </div>
        {(quote?.discounts || []).map((d, i) => (
          <div key={d.rule_id ?? i} className="flex justify-between">
            <dt className="truncate pe-2 text-ok">{d.name || 'Discount'}</dt>
            <dd className="shrink-0 tnum text-ok">−{bhd(d.amount_bhd)}</dd>
          </div>
        ))}
        <div className="flex justify-between">
          <dt className="text-ink-2">{S.cart.delivery}</dt>
          <dd className="tnum">{Number(quote?.delivery_bhd) > 0 ? bhd(quote?.delivery_bhd) : S.cart.free}</dd>
        </div>
        <div className="flex items-baseline justify-between border-t border-line-2 pt-2.5">
          <dt className="font-semibold">{S.cart.total}</dt>
          <dd key={total} className="font-display text-xl font-extrabold tnum anim-fade-in">
            {bhd(total)}
          </dd>
        </div>
        {quoting && (
          <div className="flex items-center gap-1.5 pt-0.5 text-2xs text-ink-3">
            <Loader2 size={11} className="animate-spin" aria-hidden="true" /> {S.cart.updating}
          </div>
        )}
      </dl>
      {quote?.minimum && (
        <div className="mt-3 border-t border-line-2 pt-3">
          <MinimumBar minimum={quote.minimum} suggestions={quote.gap_suggestions || []} />
        </div>
      )}
      {desktop && (
        <>
          {blocked && <p className="mt-3 text-xs font-medium text-bad">{blocked}</p>}
          <Button size="lg" full className="mt-4" disabled={!canCheckout} onClick={() => navigate('/checkout')} icon={<ArrowRight size={16} aria-hidden="true" />}>
            {small ? S.minimum.requestCta : S.cart.place}
          </Button>
          <p className="mt-2 flex items-center gap-1.5 text-xs text-ink-2">
            <ShieldCheck size={13} aria-hidden="true" /> {first ? S.cart.placeHint(first) : S.cart.placeHintGeneric}
          </p>
        </>
      )}
    </div>
  )

  return (
    <div className="px-gutter lg:px-0">
      <div className="hidden items-baseline gap-3 lg:flex">
        <h1 className="font-display text-2xl font-bold text-ink">{S.cart.title}</h1>
        <span className="text-sm tnum text-ink-2">{S.cart.summary(items, units)}</span>
      </div>
      <div className="lg:mt-4 lg:grid lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start lg:gap-6">
        <div className="min-w-0">
          {quote?.progress?.label && <ProgressBar progress={quote.progress} />}

          <ul className="mt-3 divide-y divide-line-2 overflow-hidden rounded-lg border border-line bg-surface px-4">
            {lines.map((line) => {
              const item = itemsByCode.get(line.item_code)
              const q = quoteLines.get(line.item_code)
              const dead = Boolean(q?.unavailable)
              const nudge = item ? nextTier(item, line.qty) : null
              const list = q?.list_price_bhd != null && q.unit_price_bhd != null && Number(q.list_price_bhd) > Number(q.unit_price_bhd) ? Number(q.list_price_bhd) : null
              const name = (item ? productName(item) : line.item_code)
              return (
                <li key={line.item_code} className="flex gap-3 py-3.5">
                  <button type="button" onClick={() => openProduct(line.item_code, 'cart')} className="h-16 w-16 shrink-0 overflow-hidden rounded-sm border border-line-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70" aria-label={name}>
                    <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={64} imgClassName="p-1.5" iconSize={20} showCaption={false} />
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="line-clamp-2 font-display text-sm font-bold leading-tight text-ink">{name}</div>
                        <div className="mt-0.5 text-xs tnum text-ink-2">
                          {line.item_code}
                          {q && !dead ? (
                            <>
                              {' '}
                              · {money(q.unit_price_bhd)} {S.cart.each}
                              {list != null && <s className="ms-1 text-ink-3">{money(list)}</s>}
                            </>
                          ) : null}
                        </div>
                      </div>
                      <div className="shrink-0 text-end font-display text-sm font-bold tnum text-ink">{dead ? '—' : bhd(q?.line_total_bhd ?? (Number(item?.price_bhd) || 0) * line.qty)}</div>
                    </div>
                    {dead && q?.blocked_reason && (
                      <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-bad">
                        <AlertTriangle size={12} aria-hidden="true" /> {q.blocked_reason}
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {dead && !(item && Number(q?.moq || 1) > line.qty) ? (
                        <Button variant="danger" size="sm" icon={<Trash2 size={13} aria-hidden="true" />} onClick={() => removeWithUndo(item, line.item_code, line.qty)}>
                          {S.cart.remove}
                        </Button>
                      ) : (
                        <Stepper value={line.qty} step={stepOf(item)} min={minQtyOf(item)} size="md" label={name} onChange={(n) => item && m.setQty(item, n)} onRemove={() => removeWithUndo(item, line.item_code, line.qty)} onValueClick={item ? () => setKeypad(item) : undefined} />
                      )}
                      {q?.backorder && <Chip tone="warn">{S.card.backorder}</Chip>}
                      {q?.applied?.length ? <Chip tone="ok">{q.applied[0]?.name || 'Discount'}</Chip> : null}
                    </div>
                    {nudge && !dead && <p className="mt-1.5 text-xs font-medium text-plum">{S.card.nudge(nudge.min_qty - line.qty, money(nudge.unit_price_bhd))}</p>}
                    {q?.warning && <p className="mt-1.5 text-xs text-warn">{q.warning}</p>}
                  </div>
                </li>
              )
            })}
          </ul>

          {complete.length > 0 && (
            <Rail id="complete" title={S.rails.complete} max={4}>
              {complete.map((it) => (
                <MarketCard key={it.item_code} item={it} variant="compact" from="complete" />
              ))}
            </Rail>
          )}

          <div className="mt-5">
            <Label htmlFor="yq-note">{first ? S.cart.noteFor(first) : S.cart.note}</Label>
            <Textarea id="yq-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder={S.cart.notePlaceholder} />
          </div>

          <div className="mt-4">
            {!couponOpen ? (
              <button type="button" onClick={() => setCouponOpen(true)} className="inline-flex items-center gap-1.5 text-sm font-semibold text-plum underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                <Tag size={13} aria-hidden="true" /> {S.cart.coupon}
              </button>
            ) : (
              <div className="flex gap-2">
                <Input value={couponDraft} onChange={(e) => setCouponDraft(e.target.value.toUpperCase())} placeholder={S.cart.couponPlaceholder} aria-label={S.cart.coupon} autoComplete="off" className="flex-1 uppercase" tall={false} />
                <Button variant="secondary" onClick={() => setCoupon(couponDraft.trim())}>
                  {S.cart.apply}
                </Button>
              </div>
            )}
            {quote?.coupon?.message && <p className={cn('mt-1.5 text-xs font-medium', quote.coupon.valid === false ? 'text-bad' : 'text-ok')}>{quote.coupon.message}</p>}
          </div>

          {(quote?.warnings || []).length > 0 && (
            <ul className="mt-4 space-y-1.5">
              {(quote?.warnings || []).map((w, i) => (
                <li key={i} className="flex gap-2 rounded-sm bg-warn-soft px-3 py-2 text-xs leading-snug text-warn">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
                  <span>{w}</span>
                </li>
              ))}
            </ul>
          )}
          {quoteError && <p className="mt-4 rounded-sm bg-bad-soft px-3 py-2 text-xs text-bad">{quoteError}</p>}

          <div className="mt-4 lg:hidden">{summary}</div>

          {askUrl && (
            <AnchorButton href={askUrl} target="_blank" rel="noreferrer" variant="secondary" full className="mt-4" icon={<MessageCircle size={15} aria-hidden="true" />}>
              {S.cart.ask(first)}
            </AnchorButton>
          )}
        </div>

        <div className="hidden lg:sticky lg:top-[calc(var(--m-header-h)+16px)] lg:block">{summary}</div>
      </div>

      {!desktop && (
        <PageBar>
          {blocked && <p className="mb-1.5 text-xs font-medium text-bad">{blocked}</p>}
          {quote?.minimum && !quote.minimum.met && !blocked && (
            <p className="mb-1.5 text-xs font-semibold text-plum">{S.minimum.more(bhd(quote.minimum.remaining_bhd))}</p>
          )}
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-xs text-ink-2">{S.cart.summary(items, units)}</div>
              <div key={total} className="font-display text-xl font-extrabold leading-tight tnum text-ink anim-fade-in">
                {bhd(total)}
              </div>
            </div>
            <Button size="lg" className="shrink-0 px-6" disabled={!canCheckout} onClick={() => navigate('/checkout')} icon={<ArrowRight size={16} aria-hidden="true" />}>
              {small ? S.minimum.requestCta : S.cart.place}
            </Button>
          </div>
          <p className="mt-1 text-2xs text-ink-2">{first ? S.cart.placeHint(first) : S.cart.placeHintGeneric}</p>
        </PageBar>
      )}
      {keypad && <QtySheet item={keypad} value={lines.find((l) => l.item_code === keypad.item_code)?.qty || 0} onApply={(n) => m.setQty(keypad, n)} onRemove={() => m.remove(keypad.item_code)} onClose={() => setKeypad(null)} />}
    </div>
  )
}
