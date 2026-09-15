import { useState } from 'react'
import { Plus, Share2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import { Stepper } from '@/components/ui/stepper'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { ProductImage } from './ProductImage'
import { badgeMeta, bhd, minQtyOf, money, stepOf, stockMeta } from './shared'

export interface ProductSheetProps {
  item: ShopItem | null
  token: string
  referralCode?: string
  allowBackorder: boolean
  showCompare: boolean
  qty: number
  pairs: ShopItem[]
  onAdd: (item: ShopItem) => void
  onSetQty: (item: ShopItem, qty: number) => void
  onRemove: (item: ShopItem) => void
  onOpenItem: (code: string) => void
  onClose: () => void
}

export function ProductSheet({
  item,
  token,
  referralCode,
  allowBackorder,
  showCompare,
  qty,
  pairs,
  onAdd,
  onSetQty,
  onRemove,
  onOpenItem,
  onClose,
}: ProductSheetProps) {
  const toast = useToast()
  // Keyed to the product: a new product always opens on its product shot, never
  // on whatever the previous one was toggled to.
  const [photo, setPhoto] = useState<{ code?: string; view: 'product' | 'package' }>({ view: 'product' })
  const view = photo.code === item?.item_code ? photo.view : 'product'
  const setView = (v: 'product' | 'package') => setPhoto({ code: item?.item_code, view: v })

  if (!item) return null

  const name = item.display_name || item.item_code
  const out = item.stock_status === 'out_of_stock'
  const stock = stockMeta(item.stock_status)
  const step = stepOf(item)
  const min = minQtyOf(item)
  const canOrder = !out || allowBackorder
  const hasPackage = Boolean(item.package_image_url)

  const compare =
    showCompare && item.compare_at_bhd != null && item.price_bhd != null && item.compare_at_bhd > item.price_bhd
      ? Number(item.compare_at_bhd)
      : null
  const savePct =
    compare != null ? Number(item.save_pct) || Math.round(((compare - Number(item.price_bhd)) / compare) * 100) : null

  const shareUrl = (() => {
    const base = `${window.location.origin}/c/${encodeURIComponent(token)}`
    const qs = new URLSearchParams()
    if (referralCode) qs.set('ref', referralCode)
    qs.set('item', item.item_code)
    return `${base}?${qs.toString()}`
  })()

  const share = async () => {
    const text = `${name}${item.price_bhd != null ? ` — ${bhd(item.price_bhd)}` : ''}`
    try {
      if (typeof navigator !== 'undefined' && navigator.share) {
        await navigator.share({ title: `YQ Bahrain — ${name}`, text, url: shareUrl })
        return
      }
      await navigator.clipboard.writeText(shareUrl)
      toast('Link copied — paste it into WhatsApp', 'success')
    } catch {
      /* the customer dismissed the share sheet, or the clipboard is blocked */
    }
  }

  const tiers = item.tiers || []

  return (
    <Sheet
      open
      onClose={onClose}
      variant="dialog"
      title={item.item_code}
      subtitle={[item.category, item.brand].filter(Boolean).join(' · ') || undefined}
      footer={
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="font-display text-[17px] font-extrabold leading-none tabular-nums text-[#6d28d9]">
              {item.price_bhd != null ? bhd(item.price_bhd) : 'Price on request'}
            </div>
            <div className="mt-1 text-[10.5px] text-[#6b6480]">
              {min > 1 ? `Minimum ${min} pcs` : 'Trade price per piece'}
            </div>
          </div>
          {qty > 0 ? (
            <Stepper
              value={qty}
              step={step}
              min={min}
              label={item.item_code}
              onChange={(n) => onSetQty(item, n)}
              onRemove={() => onRemove(item)}
            />
          ) : (
            <button
              type="button"
              onClick={() => onAdd(item)}
              disabled={!canOrder}
              className={cn(
                'flex h-12 min-w-[8.5rem] items-center justify-center gap-1.5 rounded-xl px-5 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] focus-visible:ring-offset-2',
                canOrder
                  ? out
                    ? 'border border-[#e4e0ee] bg-white text-[#1a1430] hover:bg-[#f7f5fb]'
                    : 'bg-[#6d28d9] text-white hover:bg-[#5b21b6]'
                  : 'cursor-not-allowed border border-[#ece9f3] bg-[#f7f6fa] text-[#a8a2bb]',
              )}
            >
              {canOrder ? (
                <>
                  <Plus size={16} aria-hidden="true" /> {out ? 'Backorder' : 'Add to order'}
                </>
              ) : (
                'Out of stock'
              )}
            </button>
          )}
        </div>
      }
    >
      <div className="px-4 py-4 sm:px-5">
        <div className="sm:flex sm:gap-5">
          <div className="sm:w-[16rem] sm:shrink-0">
            <div className="overflow-hidden rounded-2xl border border-[#ece9f3] bg-white">
              <ProductImage
                key={view}
                srcs={
                  view === 'package'
                    ? [item.package_image_url, item.product_image_url, item.thumb_url]
                    : [item.product_image_url, item.thumb_url, item.package_image_url]
                }
                alt={`${name} — ${view === 'package' ? 'package' : 'product'} photo`}
                width={512}
                height={512}
                eager
                className="aspect-square w-full"
                imgClassName="p-4"
                iconSize={48}
              />
            </div>
            {hasPackage && (
              <div className="mt-2 flex gap-1.5" role="group" aria-label="Photo type">
                {(['product', 'package'] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setView(v)}
                    aria-pressed={view === v}
                    className={cn(
                      'h-9 flex-1 rounded-lg border text-[12px] font-medium capitalize transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]',
                      view === v
                        ? 'border-[#6d28d9] bg-[#f1ecfb] text-[#6d28d9]'
                        : 'border-[#e4e0ee] bg-white text-[#6b6480] hover:bg-[#f7f5fb]',
                    )}
                  >
                    {v}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="mt-4 min-w-0 flex-1 sm:mt-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone={stock.tone}>{stock.label}</Badge>
              {(item.badges || []).map((b) => {
                const meta = badgeMeta(b)
                return (
                  <Badge key={b} tone={meta.tone}>
                    {meta.label}
                  </Badge>
                )
              })}
            </div>
            {out && allowBackorder && (
              <p className="mt-2 rounded-xl bg-[#f7f6fa] px-3 py-2 text-[11.5px] leading-snug text-[#6b6480]">
                Out of stock — order now and your salesman will confirm the ETA.
              </p>
            )}

            <div className="mt-3 flex items-end gap-2.5">
              <div className="font-display text-[26px] font-extrabold leading-none tabular-nums text-[#6d28d9]">
                {item.price_bhd != null ? bhd(item.price_bhd) : 'Price on request'}
              </div>
              {compare != null && (
                <div className="flex items-center gap-2 pb-0.5">
                  <span className="text-[12px] tabular-nums text-[#6b6480] line-through">Retail BHD {money(compare)}</span>
                  {savePct != null && savePct > 0 && <Badge tone="ink">Save {savePct}%</Badge>}
                </div>
              )}
            </div>
            <div className="mt-1 text-[11.5px] text-[#6b6480]">
              Trade price per piece{min > 1 ? ` · minimum ${min} pcs` : ''}
              {step > 1 ? ` · sold in packs of ${step}` : ''}
            </div>
            {item.social_proof && <p className="mt-1.5 text-[11.5px] text-[#6b6480]">{item.social_proof}</p>}

            {tiers.length > 0 && (
              <div className="mt-4 overflow-hidden rounded-xl border border-[#ece9f3]">
                <table className="w-full text-left text-[12px]">
                  <caption className="sr-only">Quantity price breaks</caption>
                  <thead>
                    <tr className="bg-[#faf9fc] text-[10.5px] uppercase tracking-wide text-[#6b6480]">
                      <th scope="col" className="px-3 py-2 font-semibold">
                        Quantity
                      </th>
                      <th scope="col" className="px-3 py-2 text-right font-semibold">
                        Price each
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {tiers.map((t) => (
                      <tr key={t.min_qty} className="border-t border-[#f2f0f7]">
                        <th scope="row" className="px-3 py-2 font-medium text-[#1a1430]">
                          {t.min_qty}+ pcs
                        </th>
                        <td className="px-3 py-2 text-right font-semibold tabular-nums text-[#6d28d9]">
                          {bhd(t.unit_price_bhd)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {item.spec && (
              <div className="mt-4">
                <h3 className="font-display text-[12px] font-bold uppercase tracking-wide text-[#1a1430]">Details</h3>
                <p className="mt-1.5 whitespace-pre-line text-[13px] leading-relaxed text-[#4a4360]">{item.spec}</p>
              </div>
            )}

            <button
              type="button"
              onClick={share}
              className="mt-4 inline-flex h-11 items-center gap-2 rounded-xl border border-[#e4e0ee] bg-white px-4 text-[13px] font-semibold text-[#1a1430] transition hover:bg-[#f7f5fb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
            >
              <Share2 size={15} aria-hidden="true" /> Share this product
            </button>
          </div>
        </div>

        {pairs.length > 0 && (
          <section className="mt-6 border-t border-[#f2f0f7] pt-4">
            <h3 className="font-display text-[13px] font-bold text-[#1a1430]">Frequently bought together</h3>
            <div className="-mx-4 mt-3 flex gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0">
              {pairs.map((p) => (
                <button
                  key={p.item_code}
                  type="button"
                  onClick={() => onOpenItem(p.item_code)}
                  className="w-[8.5rem] shrink-0 overflow-hidden rounded-xl border border-[#ece9f3] bg-white text-left transition hover:shadow-[0_8px_24px_-14px_rgba(24,16,48,.3)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
                >
                  <ProductImage
                    srcs={[p.thumb_url, p.product_image_url]}
                    alt={p.display_name || p.item_code}
                    width={160}
                    height={160}
                    className="aspect-square w-full"
                    imgClassName="p-2"
                    iconSize={26}
                    showCaption={false}
                  />
                  <div className="border-t border-[#f2f0f7] p-2">
                    <div className="truncate font-display text-[12px] font-bold text-[#1a1430]">{p.item_code}</div>
                    <div className="mt-0.5 text-[11.5px] font-semibold tabular-nums text-[#6d28d9]">
                      {p.price_bhd != null ? bhd(p.price_bhd) : '—'}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </Sheet>
  )
}
