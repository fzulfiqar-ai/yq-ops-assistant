import { useEffect, useMemo, useState } from 'react'
import { Check, MessageCircle, Plus, Share2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { rememberViewed } from '../lib/device'
import { track } from '../lib/events'
import { badgeMeta, bhd, cardBadges, isOut, minQtyOf, money, niceCategory, stepOf, stockMeta, priceAnchor, productDetail, productName } from '../lib/format'
import { useShell } from '../shell/ShellContext'
import { useCartQty } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { ProductImage, SIZES_HERO } from '../ui/ProductImage'
import { Sheet } from '../ui/Sheet'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'
import { MarketCard } from './MarketCard'
import { QtySheet } from './QtySheet'

/**
 * The product, over whichever page the merchant is on: a bottom sheet on phones, a dialog on
 * tablets, a right drawer on desktop. Photo (product/package), name and code, stock + badges,
 * price with compare-at, quantity rules, the tier ladder (current quantity highlighted), spec,
 * share, "Often ordered together", and a sticky footer with the one action.
 */
export default function ProductPanel({ code }: { code: string }) {
  const m = useMarket()
  const { closeProduct, viewport } = useShell()
  const toast = useToast()
  const item = m.itemsByCode.get(code) || m.items.find((i) => i.item_code.toLowerCase() === code.toLowerCase()) || null
  const qty = useCartQty(item?.item_code || '')
  const [view, setView] = useState<'product' | 'package'>('product')
  const [added, setAdded] = useState(false)
  const [keypad, setKeypad] = useState(false)
  // a new product always opens on its product shot
  const [codeSeen, setCodeSeen] = useState(code)
  if (codeSeen !== code) {
    setCodeSeen(code)
    setView('product')
  }
  useEffect(() => {
    if (!item) return
    rememberViewed(item.item_code)
    track('item', { item_code: item.item_code })
  }, [item])
  const pairs = useMemo(() => (item ? m.pairsFor(item.item_code).filter((p) => !isOut(p)) : []), [m, item])

  if (!item) {
    // catalog still loading, or an unknown code: close quietly once we know
    if (m.status === 'ready' || m.status === 'error' || m.status === 'closed') {
      return (
        <Sheet open onClose={closeProduct} title={S.states.noMatch(code)} variant={viewport === 'desktop' || viewport === 'wide' ? 'drawer' : 'sheet'}>
          <div className="px-5 py-8 text-sm text-ink-2">{S.states.noMatchHint}</div>
        </Sheet>
      )
    }
    return null
  }

  const name = productName(item)
  const out = isOut(item)
  const stock = stockMeta(item.stock_status)
  const step = stepOf(item)
  const min = minQtyOf(item)
  const canOrder = !out || m.allowBackorder
  const anchor = priceAnchor(item, m.showCompare)
  const compare = anchor?.was ?? null
  const savePct = anchor?.pct ?? null
  const tiers = m.publicTiers ? item.tiers || [] : []
  const tellUrl = out && !m.allowBackorder && m.rep?.whatsapp_url ? `${m.rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(`Hello ${m.rep.first_name || ''}, please tell me when ${item.item_code} (${name}) is back in stock.`)}` : null
  const shareUrl = `${window.location.origin}/p/${encodeURIComponent(item.item_code)}${m.rep ? `?ref=${encodeURIComponent(m.rep.slug)}` : ''}`
  const phone = viewport === 'phone'

  const add = () => {
    m.add(item, undefined, 'panel')
    setAdded(true)
    window.setTimeout(() => setAdded(false), 600)
  }
  const share = async () => {
    const text = `${name}${item.price_bhd != null ? ` — ${bhd(item.price_bhd)}` : ''}`
    try {
      if (navigator.share) {
        await navigator.share({ title: `${S.company} — ${name}`, text, url: shareUrl })
      } else {
        await navigator.clipboard.writeText(shareUrl)
        toast('Link copied — paste it into WhatsApp', 'success')
      }
      track('share', { item_code: item.item_code })
    } catch {
      /* dismissed */
    }
  }

  const footer = (
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <div className="font-display text-xl font-extrabold leading-none tnum text-plum-ink">{item.price_bhd != null ? bhd(item.price_bhd) : S.card.priceOnRequest}</div>
        <div className="mt-1 text-2xs text-ink-2">{min > 1 ? `${S.card.min(min)} pcs` : S.card.perPiece}</div>
      </div>
      {added ? (
        <div className="flex h-12 min-w-[9rem] items-center justify-center gap-2 rounded-md bg-plum px-5 text-base font-semibold text-white">
          <Check size={18} aria-hidden="true" /> {S.card.added}
        </div>
      ) : qty > 0 ? (
        <Stepper value={qty} step={step} min={min} size="lg" label={name} onChange={(n) => m.setQty(item, n)} onRemove={() => m.remove(item.item_code)} onValueClick={() => setKeypad(true)} />
      ) : tellUrl ? (
        <Button variant="secondary" size="lg" onClick={() => window.open(tellUrl, '_blank', 'noreferrer')} icon={<MessageCircle size={16} aria-hidden="true" />}>
          {S.card.tellRep(m.rep?.first_name || 'us')}
        </Button>
      ) : (
        <Button size="lg" variant={out ? 'secondary' : 'primary'} className="min-w-[9rem]" onClick={add} disabled={!canOrder} icon={<Plus size={17} aria-hidden="true" />}>
          {canOrder ? (out ? S.card.backorder : S.card.add) : S.card.soldOut}
          {canOrder && !out && m.defaultQty(item) > 1 && <span className="tnum opacity-80">· {m.defaultQty(item)}</span>}
        </Button>
      )}
    </div>
  )

  return (
    <Sheet open onClose={closeProduct} title={name} variant={phone || viewport === 'tablet' ? 'sheet' : 'drawer'} size="lg" bare footer={footer}>
      <div className="md:flex md:gap-6 md:p-5 lg:block">
        <div className="md:w-[17rem] md:shrink-0 lg:w-auto">
          <div className="relative bg-surface md:overflow-hidden md:rounded-lg md:border md:border-line-2">
            <ProductImage
              key={view}
              srcs={view === 'package' ? [item.package_image_url, item.product_image_url, item.thumb_url] : undefined}
              item={view === 'product' ? item : undefined}
              alt={`${name} — ${view}`}
              sizes={SIZES_HERO}
              size={512}
              eager
              className="mx-auto w-full max-w-[17rem] md:max-w-[26rem]"
              imgClassName="p-4 md:p-6"
              iconSize={48}
              vtName={phone ? 'product-photo' : undefined}
            />
            <div className="absolute start-3 top-3 flex flex-wrap gap-1.5">
              {cardBadges(item, 2).map((b) => (
                <Chip key={b} tone={badgeMeta(b).tone}>
                  {badgeMeta(b).label}
                </Chip>
              ))}
              {savePct != null && savePct > 0 && <Chip tone="ink">{S.card.save(savePct)}</Chip>}
            </div>
          </div>
          {item.package_image_url && (
            <div className="mt-2 flex gap-1.5 px-4 md:px-0" role="group" aria-label="Photo">
              {(['product', 'package'] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  aria-pressed={view === v}
                  className={cn('h-9 flex-1 rounded-sm border text-xs font-medium capitalize transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', view === v ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink-2 hover:bg-plum-wash')}
                >
                  {v}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1 px-4 pb-4 pt-4 md:px-0 md:pt-0 lg:pt-4">
          <div className="text-xs text-ink-2">
            {[niceCategory(item.category), item.brand].filter(Boolean).join(' · ')} · <span className="tnum">{item.item_code}</span>
          </div>
          <h2 className="mt-1 font-display text-xl font-bold leading-tight text-ink">{name}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <Chip tone={stock.tone} dot size="md">
              {stock.label}
            </Chip>
            {item.social_proof && <span className="text-xs text-ink-2">{item.social_proof}</span>}
          </div>
          {out && m.allowBackorder && <p className="mt-2.5 rounded-sm bg-warn-soft px-3 py-2 text-xs leading-snug text-warn">Sold out — order now and your representative confirms the ETA.</p>}

          <div className="mt-4 flex flex-wrap items-end gap-x-3 gap-y-1">
            <div className="font-display text-2xl font-extrabold leading-none tnum text-plum-ink">{item.price_bhd != null ? bhd(item.price_bhd) : S.card.priceOnRequest}</div>
            {compare != null && (
              <span className="pb-0.5 text-xs tnum text-ink-2">
                {anchor?.kind === 'was' ? S.card.was : S.card.retail} <s>BHD {money(compare)}</s>
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-ink-2">
            {S.card.perPiece}
            {min > 1 ? ` · ${S.card.min(min)} pcs` : ''}
            {step > 1 ? ` · ${S.card.packs(step)}` : ''}
          </div>

          {tiers.length > 0 && (
            <div className="mt-4 overflow-hidden rounded-md border border-line">
              <table className="w-full text-start text-sm">
                <caption className="sr-only">Quantity price breaks</caption>
                <thead>
                  <tr className="bg-canvas text-2xs uppercase tracking-[0.06em] text-ink-2">
                    <th scope="col" className="px-3 py-2 text-start font-semibold">
                      Quantity
                    </th>
                    <th scope="col" className="px-3 py-2 text-end font-semibold">
                      Price each
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr className={cn('border-t border-line-2', qty > 0 && !tiers.some((t) => qty >= t.min_qty) && 'bg-plum-soft')}>
                    <th scope="row" className="px-3 py-2 text-start font-medium text-ink">
                      1+ pcs
                    </th>
                    <td className="px-3 py-2 text-end font-display font-bold tnum text-ink">{bhd(item.price_bhd)}</td>
                  </tr>
                  {tiers.map((t, i) => {
                    const next = tiers[i + 1]
                    const active = qty >= t.min_qty && (!next || qty < next.min_qty)
                    return (
                      <tr key={t.min_qty} className={cn('border-t border-line-2', active && 'bg-plum-soft')}>
                        <th scope="row" className="px-3 py-2 text-start font-medium text-ink">
                          {t.min_qty}+ pcs
                        </th>
                        <td className="px-3 py-2 text-end font-display font-bold tnum text-plum-ink">{bhd(t.unit_price_bhd)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {productDetail(item) && (
            <div className="mt-5">
              <h3 className="text-2xs font-bold uppercase tracking-[0.08em] text-ink-2">Details</h3>
              <p className="mt-1.5 whitespace-pre-line text-sm leading-[1.6] text-ink-2">{productDetail(item)}</p>
            </div>
          )}

          <Button variant="secondary" className="mt-5" onClick={share} icon={<Share2 size={15} aria-hidden="true" />}>
            Share this product
          </Button>

          {pairs.length > 0 && (
            <section className="mt-6 border-t border-line-2 pt-5">
              <h3 className="font-display text-base font-bold text-ink">{S.rails.together}</h3>
              <div className="rail -mx-4 mt-3 px-4 pb-1 md:mx-0 md:px-0">
                {pairs.slice(0, 6).map((p) => (
                  <MarketCard key={p.item_code} item={p} variant="compact" from="together" className="!w-[9.5rem]" />
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
      {keypad && <QtySheet item={item} value={qty} onApply={(n) => m.setQty(item, n)} onRemove={() => m.remove(item.item_code)} onClose={() => setKeypad(false)} />}
    </Sheet>
  )
}
