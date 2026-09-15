import { Link } from 'react-router-dom'
import { ArrowRight, CheckCircle2, ClipboardList, Copy, Mail, MessageCircle, Receipt } from 'lucide-react'
import { useToast } from '@/components/Toast'
import { Logo } from '@/components/Logo'
import { cn } from '@/lib/utils'
import type { OrderResponse } from '@/lib/shopApi'
import { bhd, firstName, money, RING } from './shared'

export interface OrderSuccessProps {
  order: OrderResponse
  mode?: 'public' | 'salesman'
  /** Salesman mode: who the order was placed for, straight from the form. */
  customer?: { name: string; shop: string } | null
  onContinue: () => void
}

/**
 * The last screen of the funnel — and it only ever offers ONE obvious next action.
 *
 * Public: the customer sends the order to his salesman.
 * Salesman: he sends the confirmation to the shop, on his own WhatsApp, from his
 * own phone. The order is already in the database either way (docs/SHOP.md); this
 * screen exists to start the conversation, not to save the order.
 */
export function OrderSuccess({ order, mode = 'public', customer, onContinue }: OrderSuccessProps) {
  const toast = useToast()
  const staff = mode === 'salesman'
  const totals = order.totals || null
  const lines = totals?.lines || []
  const salesman = order.salesman?.name || 'your salesman'
  const statusUrl = order.status_url || (order.token ? `${window.location.origin}/o/${order.token}` : '')

  const who = [customer?.name, customer?.shop].filter(Boolean).join(' · ')
  const first = firstName(customer?.name) || 'the shop'

  const summary = [
    `YQ Bahrain order ${order.order_no}`,
    ...(staff && who ? [who] : []),
    '',
    ...lines.map((l) => `${l.qty} x ${l.item_code} @ ${money(l.unit_price_bhd)} = ${money(l.line_total_bhd)}`),
    '',
    `Subtotal: ${bhd(totals?.subtotal_bhd)}`,
    ...(Number(totals?.discount_bhd) > 0 ? [`Discount: -${bhd(totals?.discount_bhd)}`] : []),
    ...(Number(totals?.delivery_bhd) > 0 ? [`Delivery: ${bhd(totals?.delivery_bhd)}`] : []),
    `Total: ${bhd(totals?.total_bhd)}`,
    ...(statusUrl ? ['', `Track: ${statusUrl}`] : []),
  ].join('\n')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(summary)
      toast('Order summary copied', 'success')
    } catch {
      toast('Could not copy — please select the text manually', 'error')
    }
  }

  const secondary = cn(
    'flex h-11 items-center justify-center gap-2 rounded-xl border border-[#e4e0ee] bg-white text-[13px] font-semibold text-[#1a1430] transition duration-150 ease-out hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
    RING,
  )

  return (
    <div className={cn(staff ? 'bg-transparent' : 'min-h-screen bg-[#faf9fc]')}>
      <div className="mx-auto max-w-lg px-4 py-8 sm:py-10">
        <div className="rounded-[20px] border border-[#ece9f3] bg-white p-6 text-center shadow-[0_12px_40px_-24px_rgba(24,16,48,.35)]">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-[#e8f7ee]">
            <CheckCircle2 size={26} className="text-[#137a48]" aria-hidden="true" />
          </div>
          <h1 className="mt-4 font-display text-[22px] font-bold tracking-[-0.02em] text-[#1a1430]">
            {staff ? 'Order placed' : 'Order received'}
          </h1>
          <p className="mt-1 text-[12.5px] leading-snug text-[#6b6480]">
            {staff ? who || 'Saved to Shop Orders.' : `${salesman} will confirm availability and delivery with you shortly.`}
          </p>
          <div className="mt-5 rounded-[16px] bg-[#f3eefc] px-4 py-3">
            <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[#6d28d9]">Order number</div>
            <div className="mt-0.5 font-display text-[26px] font-extrabold tracking-[-0.02em] tabular-nums text-[#1a1430]">
              {order.order_no}
            </div>
          </div>

          {order.has_backorder && (
            <p className="mt-3 rounded-xl bg-[#fdf3e3] px-3 py-2 text-left text-[11.5px] leading-snug text-[#96600d]">
              {staff
                ? 'Some lines are out of stock right now and are marked as backorder — confirm the ETA before you promise a delivery day.'
                : `Some lines are out of stock right now and are marked as backorder — ${salesman} will confirm the ETA before anything is shipped.`}
            </p>
          )}

          {order.whatsapp_url && (
            <a
              href={order.whatsapp_url}
              target="_blank"
              rel="noreferrer"
              className={cn(
                'mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#25D366] px-4 text-center text-[14px] font-semibold text-white transition duration-150 ease-out hover:bg-[#1eb356] active:scale-[.995]',
                RING,
              )}
            >
              <MessageCircle size={17} className="shrink-0" aria-hidden="true" />
              {staff ? `Send confirmation to ${first} on WhatsApp` : `Send order to ${salesman} on WhatsApp`}
            </a>
          )}

          {!staff && order.email_url && (
            <a
              href={order.email_url}
              className={cn(
                'mt-2 flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-[#d9d2ee] bg-white text-[13.5px] font-semibold text-[#1a1430] transition duration-150 ease-out hover:bg-[#f7f5fb]',
                RING,
              )}
            >
              <Mail size={16} aria-hidden="true" /> Email the order to {salesman}
            </a>
          )}

          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <button type="button" onClick={copy} className={secondary}>
              <Copy size={15} aria-hidden="true" /> {staff ? 'Copy summary' : 'Copy order summary'}
            </button>
            {staff ? (
              <Link to="/shop-orders" className={secondary}>
                <ClipboardList size={15} aria-hidden="true" /> Open in Orders
              </Link>
            ) : (
              statusUrl && (
                <a href={statusUrl} className={secondary}>
                  <Receipt size={15} aria-hidden="true" /> Track this order
                </a>
              )
            )}
          </div>
        </div>

        {lines.length > 0 && (
          <div className="mt-4 overflow-hidden rounded-[20px] border border-[#ece9f3] bg-white">
            <h2 className="border-b border-[#f4f2f9] px-4 py-3 font-display text-[13px] font-bold text-[#1a1430]">
              {staff ? 'What they ordered' : 'What you ordered'}
            </h2>
            <ul className="divide-y divide-[#f4f2f9]">
              {lines.map((l) => (
                <li key={l.item_code} className="flex items-baseline justify-between gap-3 px-4 py-2.5 text-[12.5px]">
                  <span className="min-w-0">
                    <b className="font-display font-bold text-[#1a1430]">{l.item_code}</b>
                    <span className="ml-1.5 tabular-nums text-[#6b6480]">
                      {l.qty} × {money(l.unit_price_bhd)}
                    </span>
                    {l.backorder && <span className="ml-1.5 text-[11px] text-[#96600d]">backorder</span>}
                  </span>
                  <span className="shrink-0 font-semibold tabular-nums text-[#1a1430]">{bhd(l.line_total_bhd)}</span>
                </li>
              ))}
            </ul>
            <div className="flex items-baseline justify-between border-t border-[#f4f2f9] px-4 py-3">
              <span className="text-[12.5px] font-semibold text-[#1a1430]">Total</span>
              <span className="font-display text-[17px] font-extrabold tracking-[-0.015em] tabular-nums text-[#1a1430]">
                {bhd(totals?.total_bhd)}
              </span>
            </div>
          </div>
        )}

        <button
          type="button"
          onClick={onContinue}
          className={cn(
            'mt-4 flex h-11 w-full items-center justify-center gap-1.5 rounded-xl text-[13px] font-semibold text-[#6d28d9] transition duration-150 ease-out hover:bg-[#f3eefc]',
            RING,
          )}
        >
          {staff ? 'Continue' : 'Continue shopping'} <ArrowRight size={15} aria-hidden="true" />
        </button>

        {!staff && (
          <div className="mt-8 flex items-center justify-center gap-2 text-[11px] text-[#6b6480]">
            <Logo className="h-6 w-6 rounded-md" />
            YQ Bahrain W.L.L
          </div>
        )}
      </div>
    </div>
  )
}
