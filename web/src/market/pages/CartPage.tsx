import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Boxes, ClipboardList, Loader2, MessageCircle, Plus, RotateCcw, ShieldCheck, Store, Tag, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Quote, ShopItem } from '@/lib/shopApi'
import { MarketCard } from '../components/MarketCard'
import { ProgressBar } from '../components/ProgressBar'
import { QtySheet } from '../components/QtySheet'
import { Rail } from '../components/Rail'
import { SmallOrderSheet } from '../components/SmallOrderSheet'
import { SoldOutRows } from '../components/SoldOut'
import { SmallRequestButton, WholesaleFillers, WholesaleState } from '../components/WholesaleState'
import { useRecentOrders } from '../hooks/useRecentOrders'
import { useMarket, useOrder } from '../MarketContext'
import { rememberedOrders } from '../lib/device'
import { track } from '../lib/events'
import { bhd, fmtDateShort, minQtyOf, money, nextTier, productName, stepOf, unitAt, variantOf } from '../lib/format'
import { bestSellers, orderLines, regularStock, splitReorder, type RegularLine } from '../lib/home'
import { PageBar, useHideNav, usePageTitle, useShell } from '../shell/ShellContext'
import { useCartCounts, useCartLines } from '../store/cart'
import { useSaved } from '../store/saved'
import { S } from '../strings'
import { AnchorButton, Button, LinkButton } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { Input, Label, Textarea } from '../ui/Field'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'

type QLine = NonNullable<Quote['lines']>[number]

/**
 * /cart — "Your restock" (plan D2 + D6, the Restock tab).
 *
 * Phone: the wholesale state on top (how far to the minimum, one-tap gap fillers, Keep restocking /
 * Request a small order), the free-delivery bar under it, the lines, note + coupon, totals, then
 * ways to add more: Order again (the last order), Your regular stock, Saved items, Paste a list.
 * The page bar follows the minimum: under it → total + "BHD 7.000 to go" + Keep restocking (in every
 * small-order mode, block included, which also shows the blocked reason); at it (or with no minimum)
 * → Place wholesale order. Under it the secondary follows the mode: request → "Request a small
 * order"; allow → Place wholesale order, because such an order goes through like any other; block →
 * none.
 * Desktop: lines and suggestions left; a sticky column right with the wholesale state, the totals
 * and the CTA. Empty: a shelf with a gap, three ways to start, and Restock essentials.
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
  const savedCodes = useSaved()
  const [couponDraft, setCouponDraft] = useState(coupon)
  const [couponOpen, setCouponOpen] = useState(Boolean(coupon))
  const [keypad, setKeypad] = useState<ShopItem | null>(null)
  const [smallOpen, setSmallOpen] = useState(false)
  const hasOrders = rememberedOrders().length > 0
  usePageTitle(items ? `${S.restock.title} · ${items}` : S.restock.title, true, `${S.restock.title} · ${S.brand}`)
  // an empty Restock tab keeps the nav; a restock in progress is a focused flow with its own bar
  useHideNav(lines.length > 0)

  useEffect(() => {
    track('cart', { meta: { count: lines.length } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ── ways to add more ── */
  const recent = useRecentOrders(hasOrders, 3)
  const inCart = useMemo(() => new Set(lines.map((l) => l.item_code)), [lines])
  const lastOrder = recent[0] || null
  // the last order, split by what one tap can add today (backorder respected) — the sold-out rest is
  // shown on the card with "Tell me when back", never dropped in silence
  const againSplit = useMemo(() => splitReorder(orderLines(lastOrder, itemsByCode).filter((l) => !inCart.has(l.item.item_code)), m.allowBackorder), [lastOrder, itemsByCode, inCart, m.allowBackorder])
  const againLines = againSplit.add
  // with lines, the Order again card shows the last order, so the regulars rail skips those codes;
  // an empty restock shows Order again as a button only, so its regulars rail keeps them
  const regulars = useMemo(() => regularStock(recent, itemsByCode, new Set(inCart.size ? [...inCart, ...againLines.map((l) => l.item.item_code)] : [])), [recent, itemsByCode, inCart, againLines])
  const showRegulars = regulars.length >= 2
  const saved = useMemo(() => {
    const shown = new Set(showRegulars ? regulars.map((r) => r.item.item_code) : [])
    return savedCodes.map((c) => itemsByCode.get(c)).filter((i): i is ShopItem => Boolean(i) && !inCart.has(i!.item_code) && !shown.has(i!.item_code))
  }, [savedCodes, itemsByCode, inCart, regulars, showRegulars])
  const essentials = useMemo(() => bestSellers(m.items).filter((it) => !inCart.has(it.item_code)), [m.items, inCart])
  const together = useMemo(() => {
    const out: ShopItem[] = []
    for (const l of lines) for (const p of m.pairsFor(l.item_code)) if (!inCart.has(p.item_code) && !out.includes(p) && p.stock_status !== 'out_of_stock') out.push(p)
    return out.slice(0, 8)
  }, [lines, m, inCart])
  // "Often ordered together" must stay true, so it is never padded — with fewer than a row's worth
  // of real pairs (one 170 px card alone in a 900 px column) the essentials rail takes the slot.
  const pairsRail = together.length >= 3

  /* ── the order ── */
  const quoteLines = useMemo(() => new Map<string, QLine>((quote?.lines || []).map((l) => [l.item_code, l])), [quote])
  const minimum = quote?.minimum || null
  const under = Boolean(minimum && !minimum.met && Number(minimum.remaining_bhd) > 0)
  // under the minimum the primary is always "Keep restocking" — block mode too, where the old
  // disabled "Place wholesale order" left the page with no action at all (plan D2 / contract §7)
  const keepMode = under
  // the secondary under it follows the shop's mode: request → the small-order request (sheet +
  // request copy at checkout); allow → the order goes through like any other, so plain checkout;
  // block → nothing, only the reason.
  const requestSmall = under && minimum?.mode === 'request'
  const allowUnder = under && minimum?.mode === 'allow'
  const blocked = quote?.can_submit === false ? quote.block_reason || S.cart.blocked : ''
  const canCheckout = lines.length > 0 && !quoting && quote?.can_submit !== false
  const first = rep?.first_name || ''
  const askUrl = useMemo(() => {
    if (!rep?.whatsapp_url) return null
    const text = [S.cart.askMessage(first), ...lines.map((l) => `• ${l.qty} × ${l.item_code}`)].join('\n')
    return `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(text)}`
  }, [rep, first, lines])
  const desktop = viewport === 'desktop' || viewport === 'wide'
  const estimate = lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate
  const openSmall = () => setSmallOpen(true)
  // The page's one spoken line, updated only when a quote settles: the estimate lands first and the
  // priced quote right after, so a live total would announce twice per stepper tap. WholesaleState
  // is silent on every surface unless it is asked to speak (`announce`), so the other number that
  // moves under the finger — how far this restock is from the minimum — is said in the same sentence.
  const spoken = [`${S.cart.total} ${bhd(total)}`, minimum ? (under ? S.wholesale.away(bhd(minimum.remaining_bhd)) : S.wholesale.ready) : ''].filter(Boolean).join(' · ')
  const [settled, setSettled] = useState(spoken)
  if (!quoting && settled !== spoken) setSettled(spoken)

  const removeWithUndo = (item: ShopItem | undefined, code: string, qty: number) => {
    m.remove(code)
    toast(S.card.removed(item ? productName(item) : code), { kind: 'info', action: item ? { label: S.card.undo, onClick: () => m.setQty(item, qty) } : undefined })
  }

  const smallSheet = (
    <SmallOrderSheet
      open={smallOpen}
      onClose={() => setSmallOpen(false)}
      onContinue={() => {
        setSmallOpen(false)
        navigate('/checkout')
      }}
    />
  )

  /* ───────────────────────── empty ───────────────────────── */
  if (lines.length === 0) {
    return (
      <div className="px-gutter pb-6 lg:px-0">
        <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.restock.title}</h1>
        <section className="mt-3 overflow-hidden rounded-xl border border-line bg-surface px-5 pb-6 pt-8 text-center shadow-1 lg:mt-4 lg:py-12" aria-labelledby="restock-empty">
          <EmptyShelf />
          <h2 id="restock-empty" className="mt-6 text-balance font-display text-xl font-bold text-ink">
            {S.restock.empty}
          </h2>
          <p className="mx-auto mt-1.5 max-w-xs text-balance text-sm leading-snug text-ink-2">{S.restock.emptyHint}</p>
          <div className="mx-auto mt-6 grid max-w-sm gap-2 sm:flex sm:max-w-none sm:flex-wrap sm:justify-center">
            <LinkButton to="/shop" variant="primary" size="lg" icon={<Store size={16} aria-hidden="true" />}>
              {S.restock.browse}
            </LinkButton>
            {hasOrders && (
              <LinkButton to="/quick?load=last" variant="secondary" size="lg" icon={<RotateCcw size={16} aria-hidden="true" />}>
                {S.restock.again}
              </LinkButton>
            )}
            <LinkButton to="/quick" variant="secondary" size="lg" icon={<ClipboardList size={16} aria-hidden="true" />}>
              {S.restock.paste}
            </LinkButton>
          </div>
        </section>
        {showRegulars && (
          <Rail id="restock-regular" title={S.restock.regular} seeAllTo="/quick?load=regular">
            {regulars.map((r) => (
              <MarketCard key={r.item.item_code} item={r.item} variant="compact" from="restock_regular" presetQty={r.qty} />
            ))}
          </Rail>
        )}
        {essentials.length > 0 && (
          <Rail id="restock-essentials" title={S.restock.essentials} seeAllTo="/shop?f=best">
            {essentials.map((it) => (
              <MarketCard key={it.item_code} item={it} variant="compact" from="restock_essentials" />
            ))}
          </Rail>
        )}
        {saved.length > 0 && (
          <Rail id="restock-saved" title={S.restock.saved} seeAllTo="/shop?f=saved">
            {saved.map((it) => (
              <MarketCard key={it.item_code} item={it} variant="compact" from="restock_saved" />
            ))}
          </Rail>
        )}
      </div>
    )
  }

  /* ───────────────────────── pieces ───────────────────────── */
  const totals = (
    <>
      <dl className="space-y-2 text-sm">
        <div className="flex justify-between gap-3">
          <dt className="text-ink-2">{S.cart.subtotal}</dt>
          <dd className="tnum">{bhd(quote?.subtotal_bhd ?? estimate)}</dd>
        </div>
        {(quote?.discounts || []).map((d, i) => (
          <div key={d.rule_id ?? i} className="flex justify-between gap-3">
            <dt className="truncate text-ok">{d.name || S.cart.discount}</dt>
            <dd className="shrink-0 tnum text-ok">−{bhd(d.amount_bhd)}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-3">
          <dt className="text-ink-2">{S.cart.delivery}</dt>
          <dd className="tnum">{Number(quote?.delivery_bhd) > 0 ? bhd(quote?.delivery_bhd) : S.cart.free}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-3 border-t border-line-2 pt-2.5">
          <dt className="font-semibold">{S.cart.total}</dt>
          <dd>
            <span key={total} className="block font-display text-xl font-extrabold tnum anim-fade-in">
              {bhd(total)}
            </span>
          </dd>
        </div>
        {quoting && (
          <div className="flex items-center justify-end gap-1.5 pt-0.5 text-2xs text-ink-3">
            <Loader2 size={11} className="animate-spin" aria-hidden="true" /> {S.cart.updating}
          </div>
        )}
      </dl>
      {/* a fact about the figures above (the price book is VAT-inclusive), never a change to them */}
      <p className="mt-1.5 text-end text-2xs text-ink-2">{S.vat.note}</p>
      {/* the only live total on this surface — the visible ones (here and in the page bar) are
       * silent, so a stepper tap says the total once instead of announcing every re-render */}
      <p role="status" aria-atomic="true" className="sr-only">
        {settled}
      </p>
    </>
  )

  const progress = quote?.progress?.label ? <ProgressBar progress={quote.progress} /> : null

  return (
    <div className="px-gutter lg:px-0">
      <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.restock.title}</h1>
      <div className="lg:mt-4 lg:grid lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start lg:gap-6 2xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="min-w-0">
          {!desktop && (
            <>
              {/* the page bar carries the primary (Keep restocking / Place wholesale order); the card
                  keeps the small-order request. The gap fillers are NOT in it: with the list inside,
                  the card ran past the fold and "In this restock" opened below three products the
                  merchant had not chosen — they get their own block under the lines instead. */}
              <WholesaleState variant="page" actions="secondary" fillers={false} onRequestSmall={requestSmall ? openSmall : undefined} className="mt-3" />
              {progress && <div className="mt-3">{progress}</div>}
            </>
          )}

          {/* ── the lines ── */}
          <section aria-labelledby="restock-lines" className="mt-6 lg:mt-0">
            <div className="flex items-center justify-between gap-3">
              <h2 id="restock-lines" className="flex items-center gap-2 font-display text-lg font-bold text-ink lg:text-xl">
                {S.restock.lines}
                <span className="grid h-6 min-w-6 place-items-center rounded-full bg-plum-soft px-2 font-sans text-xs font-bold tnum text-plum-ink">{items}</span>
              </h2>
              <span className="text-xs tnum text-ink-2">{S.cart.summary(items, units)}</span>
            </div>
            <ul className="mt-2.5 divide-y divide-line-2 overflow-hidden rounded-lg border border-line bg-surface px-4">
              {lines.map((line) => {
                const item = itemsByCode.get(line.item_code)
                const q = quoteLines.get(line.item_code)
                const dead = Boolean(q?.unavailable)
                const nudge = item ? nextTier(item, line.qty) : null
                const list = q?.list_price_bhd != null && q.unit_price_bhd != null && Number(q.list_price_bhd) > Number(q.unit_price_bhd) ? Number(q.list_price_bhd) : null
                const name = item ? productName(item) : line.item_code
                const chips = item ? variantOf(item).slice(0, 3) : []
                return (
                  <li key={line.item_code} className="flex gap-3 py-3.5">
                    <button type="button" onClick={() => openProduct(line.item_code, 'cart')} className="h-16 w-16 shrink-0 overflow-hidden rounded-md border border-line-2 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70" aria-label={name}>
                      <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={64} imgClassName={cn('p-1.5', dead && 'opacity-45 saturate-50')} iconSize={20} showCaption={false} />
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="line-clamp-2 font-display text-sm font-bold leading-tight text-ink">{name}</div>
                          {chips.length > 0 && (
                            <div className="mt-1 flex max-h-5 flex-wrap gap-1 overflow-hidden">
                              {chips.map((c) => (
                                <Chip key={c} tone="spec">
                                  {c}
                                </Chip>
                              ))}
                            </div>
                          )}
                          <div className="mt-1 text-xs tnum text-ink-2">
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
                        <div key={q?.line_total_bhd ?? 'est'} className="shrink-0 text-end font-display text-sm font-bold tnum text-ink anim-fade-in">
                          {dead ? '—' : bhd(q?.line_total_bhd ?? (Number(item?.price_bhd) || 0) * line.qty)}
                        </div>
                      </div>
                      {/* a line that can no longer be ordered is a state, not an error: grey, never red —
                          red stays for real errors (the quote failing, a refused coupon, the blocked order) */}
                      {dead && q?.blocked_reason && <p className="mt-1.5 text-xs text-ink-2">{q.blocked_reason}</p>}
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        {dead && !(item && Number(q?.moq || 1) > line.qty) ? (
                          <Button variant="secondary" size="sm" className="text-ink-2" icon={<Trash2 size={13} aria-hidden="true" />} onClick={() => removeWithUndo(item, line.item_code, line.qty)}>
                            {S.cart.remove}
                          </Button>
                        ) : (
                          <Stepper value={line.qty} step={stepOf(item)} min={minQtyOf(item)} size="md" label={name} onChange={(n) => item && m.setQty(item, n)} onRemove={() => removeWithUndo(item, line.item_code, line.qty)} onValueClick={item ? () => setKeypad(item) : undefined} />
                        )}
                        {q?.backorder && <Chip tone="warn">{S.card.backorder}</Chip>}
                        {q?.applied?.length ? <Chip tone="ok">{q.applied[0]?.name || S.cart.discount}</Chip> : null}
                      </div>
                      {nudge && !dead && <p className="mt-1.5 text-xs font-medium text-plum">{S.card.nudge(nudge.min_qty - line.qty, money(nudge.unit_price_bhd))}</p>}
                      {q?.warning && <p className="mt-1.5 text-xs text-warn">{q.warning}</p>}
                    </div>
                  </li>
                )
              })}
            </ul>
          </section>

          {/* "Complete your restock with these" — the quote's gap fillers, under the merchant's own
              lines on every width. In the state card they pushed the restock off the first phone
              screen, and in the desktop column they pushed the CTA out of the sticky box. */}
          <WholesaleFillers className="mt-4" />

          {pairsRail ? (
            <Rail id="together" title={S.rails.together} max={4}>
              {together.map((it) => (
                <MarketCard key={it.item_code} item={it} variant="compact" from="complete" />
              ))}
            </Rail>
          ) : essentials.length > 0 ? (
            <Rail id="restock-essentials" title={S.restock.essentials} seeAllTo="/shop?f=best">
              {essentials.map((it) => (
                <MarketCard key={it.item_code} item={it} variant="compact" from="restock_essentials" />
              ))}
            </Rail>
          ) : null}

          <div className="mt-6">
            <Label htmlFor="yq-note">{first ? S.cart.noteFor(first) : S.cart.note}</Label>
            <Textarea id="yq-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder={S.cart.notePlaceholder} />
          </div>

          <div className="mt-4">
            {!couponOpen ? (
              <button type="button" onClick={() => setCouponOpen(true)} className="inline-flex h-10 items-center gap-1.5 rounded-sm text-sm font-semibold text-plum underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
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

          {!desktop && <div className="mt-4 rounded-lg border border-line bg-surface p-4">{totals}</div>}

          {askUrl && (
            <AnchorButton href={askUrl} target="_blank" rel="noreferrer" variant="secondary" full className="mt-4" icon={<MessageCircle size={15} aria-hidden="true" />}>
              {S.cart.ask(first)}
            </AnchorButton>
          )}

          {/* ── add more to this restock ── */}
          <section aria-labelledby="restock-more" className="mt-9 pb-2">
            <h2 id="restock-more" className="font-display text-lg font-bold text-ink lg:text-xl">
              {S.restock.more}
            </h2>
            {(againLines.length > 0 || againSplit.sold.length > 0) && <OrderAgainCard lines={againLines} sold={againSplit.sold} placedAt={lastOrder?.created_at} />}
            {showRegulars && (
              <Rail id="restock-regular" title={S.restock.regular} seeAllTo="/quick?load=regular">
                {regulars.map((r) => (
                  <MarketCard key={r.item.item_code} item={r.item} variant="compact" from="restock_regular" presetQty={r.qty} />
                ))}
              </Rail>
            )}
            {saved.length > 0 && (
              <Rail id="restock-saved" title={S.restock.saved} seeAllTo="/shop?f=saved">
                {saved.map((it) => (
                  <MarketCard key={it.item_code} item={it} variant="compact" from="restock_saved" />
                ))}
              </Rail>
            )}
            <PasteCard />
          </section>
        </div>

        {/* ── desktop: the sticky decision column. --m-sticky-h is the MEASURED sticky header
            (brand row + any category strip); the plain header height is the fallback for a page
            the shell has not measured. It carries the decision only — how far to the minimum, the
            totals, the CTA — never the gap fillers, which used to make it 875 px in a 695 px box at
            1280×800 and sliced "Keep restocking" off a sticky column the page could not scroll.
            The totals card sticks to the bottom of the box, so the CTA survives a tall quote ── */}
        {desktop && (
          <div className="sticky top-[calc(var(--m-sticky-h,var(--m-header-h))+16px)] -m-1 max-h-[calc(100dvh-var(--m-sticky-h,var(--m-header-h))-32px)] space-y-3 overflow-y-auto overscroll-contain p-1">
            <WholesaleState variant="page" actions={false} fillers={false} />
            {progress}
            <div className="sticky bottom-1 rounded-xl border border-line bg-surface p-4 shadow-1">
              {totals}
              {blocked && <p className="mt-3 text-xs font-medium text-bad">{blocked}</p>}
              {keepMode ? (
                <>
                  <Button size="lg" full className="mt-4" onClick={() => navigate('/shop')} icon={<Boxes size={16} aria-hidden="true" />}>
                    {S.wholesale.keep}
                  </Button>
                  {requestSmall && <SmallRequestButton onClick={openSmall} className="mt-1" />}
                  {allowUnder && (
                    <Button variant="secondary" full className="mt-2" disabled={!canCheckout} onClick={() => navigate('/checkout')}>
                      {S.cart.place}
                    </Button>
                  )}
                </>
              ) : (
                <Button size="lg" full className="mt-4" disabled={!canCheckout} onClick={() => navigate('/checkout')} icon={<ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />}>
                  {S.cart.place}
                </Button>
              )}
              <p className="mt-2 flex items-center gap-1.5 text-xs text-ink-2">
                <ShieldCheck size={13} className="shrink-0" aria-hidden="true" /> {first ? S.cart.placeHint(first) : S.cart.placeHintGeneric}
              </p>
            </div>
          </div>
        )}
      </div>

      {!desktop && (
        <PageBar>
          {blocked && <p className="mb-1.5 text-xs font-medium text-bad">{blocked}</p>}
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              {keepMode && minimum ? (
                <div key={minimum.remaining_bhd} className="truncate text-xs font-semibold tnum text-plum anim-fade-in">
                  {S.wholesale.toGo(bhd(minimum.remaining_bhd))}
                </div>
              ) : (
                <div className="truncate text-xs text-ink-2">{S.cart.summary(items, units)}</div>
              )}
              <span key={total} className="block truncate font-display text-lg font-extrabold leading-tight tnum text-ink anim-fade-in sm:text-xl">
                {bhd(total)}
              </span>
            </div>
            {keepMode ? (
              <Button size="lg" className="shrink-0 px-5" onClick={() => navigate('/shop')}>
                {S.wholesale.keep}
              </Button>
            ) : (
              <Button size="lg" className="shrink-0 px-5" disabled={!canCheckout} onClick={() => navigate('/checkout')}>
                {S.cart.place}
              </Button>
            )}
          </div>
          {allowUnder && (
            <Button variant="secondary" full className="mt-2" disabled={!canCheckout} onClick={() => navigate('/checkout')}>
              {S.cart.place}
            </Button>
          )}
          <p className="mt-1 truncate text-2xs text-ink-2">{first ? S.cart.placeHint(first) : S.cart.placeHintGeneric}</p>
        </PageBar>
      )}
      {keypad && <QtySheet item={keypad} value={lines.find((l) => l.item_code === keypad.item_code)?.qty || 0} onApply={(n) => m.setQty(keypad, n)} onRemove={() => m.remove(keypad.item_code)} onClose={() => setKeypad(null)} />}
      {smallSheet}
    </div>
  )
}

/* ───────────────────────── blocks ───────────────────────── */

/** The empty state's picture: a shelf of stock with one gap waiting to be filled. CSS only. */
function EmptyShelf() {
  return (
    <div aria-hidden="true" className="mx-auto w-44">
      <div className="flex h-20 items-end justify-center gap-1.5 px-2">
        <span className="h-11 w-8 rounded-t-md bg-tile-lilac ring-1 ring-inset ring-plum/10" />
        <span className="h-16 w-8 rounded-t-md bg-plum" />
        <span className="grid h-14 w-8 place-items-center rounded-t-md border-2 border-b-0 border-dashed border-plum/45 text-plum">
          <Plus size={14} strokeWidth={2.5} />
        </span>
        <span className="h-9 w-8 rounded-t-md bg-tile-apricot ring-1 ring-inset ring-deal/30" />
        <span className="h-12 w-8 rounded-t-md bg-tile-mint ring-1 ring-inset ring-fresh/20" />
      </div>
      <div className="h-2 rounded-full bg-surface-2 ring-1 ring-inset ring-line" />
    </div>
  )
}

/** The last order, one tap back into the restock (the lines that can be ordered today and are not already added), then its sold-out lines with "Tell me when back". */
function OrderAgainCard({ lines, sold, placedAt }: { lines: RegularLine[]; sold: RegularLine[]; placedAt?: string | null }) {
  const m = useMarket()
  const { openProduct } = useShell()
  const total = lines.reduce((s, l) => s + (unitAt(l.item, l.qty) ?? 0) * l.qty, 0)
  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-line bg-surface shadow-1">
      <div className="flex items-center gap-3 px-4 pt-3.5">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-sm bg-plum-soft text-plum" aria-hidden="true">
          <RotateCcw size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-base font-bold text-ink">{S.restock.again}</h3>
          <p className="truncate text-xs text-ink-2">{S.restock.againLine(lines.length + sold.length, fmtDateShort(placedAt))}</p>
        </div>
      </div>
      {lines.length > 0 && (
        <>
          <ul className="no-scrollbar mt-3 flex gap-2 overflow-x-auto px-4 pb-1">
            {lines.map((l) => {
              const name = productName(l.item)
              return (
                <li key={l.item.item_code} className="shrink-0">
                  <button type="button" onClick={() => openProduct(l.item.item_code, 'order_again')} aria-label={name} title={name} className="relative block h-14 w-14 overflow-hidden rounded-md border border-line-2 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                    <ProductImage item={l.item} alt="" sizes={SIZES_THUMB} size={56} imgClassName="p-1" iconSize={16} showCaption={false} />
                    <span className="absolute bottom-0.5 end-0.5 rounded-xs bg-ink/85 px-1 text-[10px] font-bold leading-[14px] tnum text-white">×{l.qty}</span>
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2 border-t border-line-2 p-3">
            <Button className="min-w-0 flex-1" onClick={() => m.addMany(lines, 'order_again_cart')} icon={<Plus size={16} aria-hidden="true" />}>
              <span className="min-w-0 truncate">{S.restock.addAll(lines.length, bhd(total))}</span>
            </Button>
            <LinkButton to="/quick?load=last" variant="secondary">
              {S.restock.edit}
            </LinkButton>
          </div>
        </>
      )}
      <SoldOutRows items={sold.map((l) => l.item)} className={cn('px-4 pb-1.5', !lines.length && 'mt-3')} />
    </div>
  )
}

/** Paste a list — a dashed "drop it here" tile into Quick order. */
function PasteCard() {
  return (
    <Link
      to="/quick"
      className="group mt-6 flex items-center gap-3.5 rounded-xl border border-dashed border-plum/35 bg-plum-wash px-4 py-3.5 transition duration-2 ease-m hover:border-plum/60 hover:bg-plum-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
    >
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface text-plum shadow-1 ring-1 ring-inset ring-plum/10" aria-hidden="true">
        <ClipboardList size={18} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-display text-sm font-bold text-ink">{S.restock.paste}</span>
        <span className="block text-xs leading-snug text-ink-2">{S.restock.pasteHint}</span>
      </span>
      <ArrowRight size={17} className="shrink-0 text-plum transition-transform duration-2 ease-m group-hover:translate-x-0.5 rtl:-scale-x-100" aria-hidden="true" />
    </Link>
  )
}
