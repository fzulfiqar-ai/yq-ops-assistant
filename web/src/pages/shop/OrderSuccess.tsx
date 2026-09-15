import { ArrowRight, CheckCircle2, Copy, Mail, MessageCircle, Receipt } from 'lucide-react'
import { useToast } from '@/components/Toast'
import { Logo } from '@/components/Logo'
import type { OrderResponse } from '@/lib/shopApi'
import { bhd, money } from './shared'

export interface OrderSuccessProps {
  order: OrderResponse
  onContinue: () => void
}

/** The last screen of the funnel: one obvious next action — send it to the salesman. */
export function OrderSuccess({ order, onContinue }: OrderSuccessProps) {
  const toast = useToast()
  const totals = order.totals || null
  const lines = totals?.lines || []
  const salesman = order.salesman?.name || 'your salesman'
  const statusUrl = order.status_url || (order.token ? `${window.location.origin}/o/${order.token}` : '')

  const summary = [
    `YQ Bahrain order ${order.order_no}`,
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

  return (
    <div className="min-h-screen bg-[#faf9fc]">
      <div className="mx-auto max-w-lg px-4 py-10">
        <div className="rounded-2xl border border-[#ece9f3] bg-white p-6 text-center shadow-[0_10px_40px_-20px_rgba(24,16,48,.35)]">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-[#e8f7ee]">
            <CheckCircle2 size={26} className="text-[#137a48]" aria-hidden="true" />
          </div>
          <h1 className="mt-4 font-display text-xl font-bold text-[#1a1430]">Order received</h1>
          <p className="mt-1 text-[12.5px] leading-snug text-[#6b6480]">
            {salesman} will confirm availability and delivery with you shortly.
          </p>
          <div className="mt-4 rounded-xl bg-[#f1ecfb] px-4 py-3">
            <div className="text-[10.5px] font-semibold uppercase tracking-wide text-[#6d28d9]">Order number</div>
            <div className="font-display text-2xl font-extrabold tabular-nums tracking-tight text-[#1a1430]">
              {order.order_no}
            </div>
          </div>

          {order.has_backorder && (
            <p className="mt-3 rounded-xl bg-[#fdf3e3] px-3 py-2 text-left text-[11.5px] leading-snug text-[#96600d]">
              Some lines are out of stock right now and are marked as backorder — {salesman} will confirm the ETA before
              anything is shipped.
            </p>
          )}

          {order.whatsapp_url && (
            <a
              href={order.whatsapp_url}
              target="_blank"
              rel="noreferrer"
              className="mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#25D366] text-[14px] font-semibold text-white transition hover:bg-[#1eb356] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#128C7E] focus-visible:ring-offset-2"
            >
              <MessageCircle size={17} aria-hidden="true" /> Send order to {order.salesman?.name || 'your salesman'} on
              WhatsApp
            </a>
          )}

          {order.email_url && (
            <a
              href={order.email_url}
              className="mt-2 flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-[#d9d2ee] bg-white text-[13.5px] font-semibold text-[#1a1430] transition hover:bg-[#f7f5fb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] focus-visible:ring-offset-2"
            >
              <Mail size={16} aria-hidden="true" /> Email the order to {order.salesman?.name || 'your salesman'}
            </a>
          )}

          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              onClick={copy}
              className="flex h-11 items-center justify-center gap-2 rounded-xl border border-[#e4e0ee] bg-white text-[13px] font-semibold text-[#1a1430] transition hover:bg-[#f7f5fb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
            >
              <Copy size={15} aria-hidden="true" /> Copy order summary
            </button>
            {statusUrl && (
              <a
                href={statusUrl}
                className="flex h-11 items-center justify-center gap-2 rounded-xl border border-[#e4e0ee] bg-white text-[13px] font-semibold text-[#1a1430] transition hover:bg-[#f7f5fb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
              >
                <Receipt size={15} aria-hidden="true" /> Track this order
              </a>
            )}
          </div>
        </div>

        {lines.length > 0 && (
          <div className="mt-4 overflow-hidden rounded-2xl border border-[#ece9f3] bg-white">
            <h2 className="border-b border-[#f2f0f7] px-4 py-3 font-display text-[13px] font-bold text-[#1a1430]">
              What you ordered
            </h2>
            <ul className="divide-y divide-[#f2f0f7]">
              {lines.map((l) => (
                <li key={l.item_code} className="flex items-baseline justify-between gap-3 px-4 py-2.5 text-[12.5px]">
                  <span className="min-w-0">
                    <b className="font-display font-bold text-[#1a1430]">{l.item_code}</b>
                    <span className="ml-1.5 tabular-nums text-[#6b6480]">
                      {l.qty} × {money(l.unit_price_bhd)}
                    </span>
                    {l.backorder && <span className="ml-1.5 text-[11px] text-[#96600d]">backorder</span>}
                  </span>
                  <span className="shrink-0 tabular-nums font-semibold text-[#1a1430]">{bhd(l.line_total_bhd)}</span>
                </li>
              ))}
            </ul>
            <div className="flex items-baseline justify-between border-t border-[#f2f0f7] px-4 py-3">
              <span className="text-[12.5px] font-semibold text-[#1a1430]">Total</span>
              <span className="font-display text-[16px] font-extrabold tabular-nums text-[#1a1430]">
                {bhd(totals?.total_bhd)}
              </span>
            </div>
          </div>
        )}

        <button
          type="button"
          onClick={onContinue}
          className="mt-4 flex h-11 w-full items-center justify-center gap-1.5 rounded-xl text-[13px] font-semibold text-[#6d28d9] transition hover:bg-[#f1ecfb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
        >
          Continue shopping <ArrowRight size={15} aria-hidden="true" />
        </button>

        <div className="mt-8 flex items-center justify-center gap-2 text-[11px] text-[#6b6480]">
          <Logo className="h-6 w-6 rounded-md" />
          YQ Bahrain W.L.L
        </div>
      </div>
    </div>
  )
}
