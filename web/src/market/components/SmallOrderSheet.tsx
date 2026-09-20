import { useNavigate } from 'react-router-dom'
import { ArrowRight, MessageCircle, ReceiptText, ShieldCheck } from 'lucide-react'
import { useMarket, useOrder } from '../MarketContext'
import { bhd } from '../lib/format'
import { ackSmallOrder } from '../lib/smallOrder'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Sheet } from '../ui/Sheet'
import { WholesaleRail } from './WholesaleState'

/**
 * "Request a small order" — asked once, before checkout, under the wholesale minimum.
 * Where the restock stands (the same rail), how small orders work (reviewed by the rep,
 * confirmed on WhatsApp), the handling fee only when the shop charges one, and no payment now.
 * Continue records the acknowledgement for this session (lib/smallOrder) and hands over to the
 * caller (the Restock page navigates to checkout); "Keep restocking instead" closes and browses.
 */
export function SmallOrderSheet({ open, onClose, onContinue }: { open: boolean; onClose: () => void; onContinue: () => void }) {
  const navigate = useNavigate()
  const { quote } = useOrder()
  const { settings } = useMarket()
  if (!open) return null

  const minimum = quote?.minimum || null
  const need = Math.max(0, Number(minimum?.value_bhd ?? settings.min_order_bhd ?? 0) || 0)
  const remaining = Math.max(0, Number(minimum?.remaining_bhd) || 0)
  const fee = Number(minimum?.fee_bhd) || 0
  const under = Boolean(minimum && !minimum.met && remaining > 0)
  const have = Math.max(0, need - remaining)

  return (
    <Sheet
      open
      onClose={onClose}
      title={S.small.title}
      footer={
        <div className="grid gap-1.5">
          <Button
            size="lg"
            full
            onClick={() => {
              ackSmallOrder()
              onContinue()
            }}
            icon={<ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />}
          >
            {S.small.continue}
          </Button>
          <Button
            size="lg"
            variant="ghost"
            full
            className="text-plum hover:text-plum-deep"
            onClick={() => {
              onClose()
              navigate('/shop')
            }}
          >
            {S.small.keep}
          </Button>
        </div>
      }
    >
      <div className="px-4 pb-5 pt-4 md:px-5">
        {under && (
          <div className="rounded-lg bg-plum-wash p-3.5 ring-1 ring-inset ring-plum/10">
            <p className="text-sm font-semibold leading-snug text-ink">{S.wholesale.away(bhd(remaining))}</p>
            <WholesaleRail have={have} need={need} met={false} size="sm" trackClassName="bg-surface" className="mt-3" />
            <div className="mt-2 flex items-baseline justify-between gap-3 text-2xs tnum text-ink-2">
              <span>
                <b className="font-semibold text-ink">{bhd(have)}</b> {S.wholesale.inRestock}
              </span>
              <span className="text-end">
                <b className="font-semibold text-ink">{bhd(need)}</b> {S.wholesale.minimumLabel}
              </span>
            </div>
          </div>
        )}

        <ul className="mt-4 space-y-3">
          <li className="flex gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-plum-soft text-plum" aria-hidden="true">
              <MessageCircle size={15} />
            </span>
            <p className="pt-1 text-sm leading-snug text-ink">{S.small.body(bhd(need))}</p>
          </li>
          {fee > 0 && (
            <li className="flex gap-3">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-deal-soft text-deal-ink" aria-hidden="true">
                <ReceiptText size={15} />
              </span>
              <p className="pt-1 text-sm leading-snug text-ink">{S.small.fee(bhd(fee))}</p>
            </li>
          )}
          <li className="flex gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ok-soft text-ok" aria-hidden="true">
              <ShieldCheck size={15} />
            </span>
            <p className="pt-1 text-sm leading-snug text-ink-2">{S.small.noPayment}</p>
          </li>
        </ul>
      </div>
    </Sheet>
  )
}
