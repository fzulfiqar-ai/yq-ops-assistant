import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, CircleAlert, Lightbulb, MessageCircle, ShoppingCart, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { ltrText } from '../i18n'
import { bhd, isOut, minQtyOf, normalizeQty, productName, stepOf, unitAt } from '../lib/format'
import { useMarket } from '../MarketContext'
import { AnchorButton, Button } from '../ui/Button'
import { Ltr } from '../ui/Ltr'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'
import { whatsappWith } from './handoff'
import type { Msg, Row } from './store'
import { A } from './strings'

/**
 * The order-list review: one row per line the merchant wrote, said or photographed. What the
 * catalog made of each line is shown, never trusted: the quantity, the item and its alternatives
 * are theirs to change, a line can be taken out, and nothing reaches the cart until "Add N items".
 */

const BADGE: Record<Row['status'], { cls: string; label: string }> = {
  matched: { cls: 'bg-fresh-soft text-fresh-ink', label: A.statusMatched },
  pick: { cls: 'bg-deal-soft text-deal-ink', label: A.statusCheck },
  missing: { cls: 'bg-bad-soft text-bad', label: A.statusMissing },
}

function LineRow({
  row,
  item,
  alts,
  blocked,
  soldOutLabel,
  onChange,
  onRemove,
  onStock,
}: {
  row: Row
  item: ShopItem | undefined
  alts: ShopItem[]
  blocked: boolean
  soldOutLabel: string
  onChange: (p: Partial<Row>) => void
  onRemove: () => void
  onStock: () => void
}) {
  const badge = BADGE[row.status]
  const name = item ? productName(item) : ''
  const unit = item ? unitAt(item, row.qty) : null
  return (
    <li className="px-3.5 py-3">
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 pt-1 text-xs text-ink-2">
          {A.youWrote}: <span dir="auto" className="font-semibold text-ink-2 break-words">{row.text}</span>
        </p>
        <span className={cn('mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-2xs font-semibold', badge.cls)}>
          {row.status === 'matched' ? <Check size={11} aria-hidden="true" /> : <CircleAlert size={11} aria-hidden="true" />}
          {badge.label}
        </span>
        <button
          type="button"
          aria-label={A.removeLine}
          title={A.removeLine}
          onClick={onRemove}
          className="-me-2 -mt-1.5 grid h-11 w-11 shrink-0 place-items-center rounded-sm text-ink-3 hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      {item ? (
        <div className="mt-1.5 flex items-start gap-3">
          <div className="h-14 w-14 shrink-0 overflow-hidden rounded-md border border-line bg-white">
            <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={56} className="h-full w-full bg-transparent" iconSize={18} showCaption={false} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-2xs font-semibold text-ink-2">
              {item.brand ? `${item.brand} · ` : ''}
              <Ltr>{item.item_code}</Ltr>
            </p>
            <p className="line-clamp-2 text-sm font-semibold leading-snug text-ink rtl:text-right" {...ltrText}>
              {name}
            </p>
            <p className="mt-0.5 text-sm font-bold text-ink">
              {unit != null ? bhd(unit) : ''}
              <span className="ms-1 text-xs font-normal text-ink-2">{unit != null ? A.perPc : ''}</span>
            </p>
            {blocked ? (
              <p className="mt-1.5 text-xs font-semibold text-bad">{A.soldOutLine}</p>
            ) : (
              <div className="mt-2">
                <Stepper
                  size="sm"
                  value={row.qty}
                  step={stepOf(item)}
                  min={minQtyOf(item)}
                  max={9999}
                  label={A.qtyOf(name)}
                  onChange={(q) => onChange({ qty: normalizeQty(item, q) })}
                  onRemove={onRemove}
                />
              </div>
            )}
            {blocked && soldOutLabel ? <p className="text-2xs text-ink-3">{soldOutLabel}</p> : null}
          </div>
        </div>
      ) : (
        <div className="mt-2">
          {row.asked ? (
            <p className="text-sm font-semibold text-ok">{A.askStockDone}</p>
          ) : (
            <Button size="sm" variant="secondary" icon={<Lightbulb size={15} aria-hidden="true" />} onClick={onStock}>
              {A.askStock}
            </Button>
          )}
        </div>
      )}

      {alts.length ? (
        <div className="mt-2.5">
          <p className="text-2xs font-semibold text-ink-3">{item ? A.otherOptions : A.closest}</p>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {alts.map((a) => (
              <button
                key={a.item_code}
                type="button"
                onClick={() => onChange({ code: a.item_code, qty: normalizeQty(a, row.qty), alts: [...(item ? [item.item_code] : []), ...row.alts.filter((c) => c !== a.item_code)].slice(0, 3), status: 'pick', asked: false })}
                className="inline-flex min-h-11 max-w-full items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-start text-xs font-semibold text-ink hover:border-plum/40 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
              >
                <Ltr>{a.item_code}</Ltr>
                <span className="truncate font-normal text-ink-2" {...ltrText}>
                  {productName(a)}
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </li>
  )
}

export function ListReview({ m, onPatch, onStock }: { m: Msg; onPatch: (id: string, p: Partial<Msg>) => void; onStock: (text: string) => Promise<boolean> }) {
  const { itemsByCode, addMany, allowBackorder, soldOutLabel, rep } = useMarket()
  const toast = useToast()
  const navigate = useNavigate()
  const [failed, setFailed] = useState(false)
  const rows = m.rows || []

  const setRow = (i: number, p: Partial<Row>) => onPatch(m.id, { rows: rows.map((r, k) => (k === i ? { ...r, ...p } : r)) })
  const blockedOf = (it: ShopItem | undefined) => Boolean(it && isOut(it) && !allowBackorder)

  const visible = rows.map((r, i) => ({ r, i })).filter(({ r }) => !r.removed)
  const addable = visible.filter(({ r }) => r.code && itemsByCode.get(r.code) && !blockedOf(itemsByCode.get(r.code)))
  const missing = visible.filter(({ r }) => !r.code)
  const waUrl = m.handoff?.whatsapp_url || rep?.whatsapp_url || null

  const addAll = () => {
    const entries = addable.map(({ r }) => ({ item: itemsByCode.get(r.code as string) as ShopItem, qty: r.qty }))
    const n = addMany(entries, 'assist')
    onPatch(m.id, { added: true })
    if (n > 0) toast(A.added(n), { kind: 'success', action: { label: A.viewCart, onClick: () => navigate('/cart') } })
    else toast(A.addedNone, 'info')
  }

  return (
    <section aria-label={A.reviewTitle} className="overflow-hidden rounded-lg border border-line bg-surface">
      <h3 className="border-b border-line-2 bg-plum-wash px-3.5 py-2 font-display text-sm font-bold text-plum-ink">{A.reviewTitle}</h3>
      <ul className="divide-y divide-line-2">
        {visible.map(({ r, i }) => {
          const item = r.code ? itemsByCode.get(r.code) : undefined
          return (
            <LineRow
              key={`${i}-${r.text}`}
              row={r}
              item={item}
              alts={r.alts.map((c) => itemsByCode.get(c)).filter((x): x is ShopItem => Boolean(x))}
              blocked={blockedOf(item)}
              soldOutLabel={blockedOf(item) ? soldOutLabel : ''}
              onChange={(p) => setRow(i, p)}
              onRemove={() => setRow(i, { removed: true })}
              onStock={async () => {
                setFailed(false)
                if (await onStock(r.text)) setRow(i, { asked: true })
                else setFailed(true)
              }}
            />
          )
        })}
      </ul>
      {failed ? <p className="px-3.5 pb-2 text-xs text-bad">{A.errFeedback}</p> : null}
      <div className="flex flex-col gap-2 border-t border-line-2 bg-surface px-3.5 py-3">
        <Button
          full
          variant={m.added ? 'secondary' : 'primary'}
          disabled={Boolean(m.added) || addable.length === 0}
          icon={m.added ? <Check size={16} aria-hidden="true" /> : <ShoppingCart size={16} aria-hidden="true" />}
          onClick={addAll}
        >
          {m.added ? A.addedBtn : addable.length ? A.addItems(addable.length) : A.nothingToAdd}
        </Button>
        {missing.length ? (
          waUrl ? (
            <AnchorButton
              variant="wa"
              href={whatsappWith(waUrl, missing.map(({ r }) => r.text).join('; '), [])}
              target="_blank"
              rel="noreferrer"
              icon={<MessageCircle size={16} aria-hidden="true" />}
              className="self-start"
            >
              {A.tellRep}
            </AnchorButton>
          ) : (
            <p className="text-xs text-ink-2">{A.repLater}</p>
          )
        ) : null}
      </div>
    </section>
  )
}
