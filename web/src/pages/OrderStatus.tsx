import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Check, CircleSlash, MessageCircle } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { getOrderStatus, type OrderStatusPayload } from '@/lib/shopApi'
import { bhd, fmtDateTime, money } from '@/pages/shop/shared'

/** /o/{orderToken} — the customer's own view of an order they placed from /c/{token}. */

const STEPS: { key: string; label: string }[] = [
  { key: 'new', label: 'Received' },
  { key: 'confirmed', label: 'Confirmed' },
  { key: 'packed', label: 'Packed' },
  { key: 'delivered', label: 'Delivered' },
]

const EVENT_LABEL: Record<string, string> = {
  created: 'Order received',
  'status:new': 'Order received',
  'status:confirmed': 'Confirmed by your salesman',
  'status:packed': 'Packed and ready',
  'status:delivered': 'Delivered',
  'status:cancelled': 'Cancelled',
}

function eventLabel(e?: string | null) {
  if (!e) return 'Update'
  return EVENT_LABEL[e] || e.replace(/^status:/, '').replace(/_/g, ' ')
}

export default function OrderStatus() {
  const { orderToken = '' } = useParams()
  const [data, setData] = useState<OrderStatusPayload | null>(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    if (!orderToken) return
    let alive = true
    getOrderStatus(orderToken)
      .then((d) => {
        if (alive) setData(d)
      })
      .catch(() => {
        if (alive) setErr(true)
      })
    return () => {
      alive = false
    }
  }, [orderToken])

  useEffect(() => {
    const prev = document.title
    document.title = data?.order_no ? `Order ${data.order_no} · YQ Bahrain` : 'Your order · YQ Bahrain'
    return () => {
      document.title = prev
    }
  }, [data])

  // A missing token is the same dead end as a rejected one — no state needed.
  if (err || !orderToken) {
    return (
      <div className="grid min-h-screen place-items-center bg-[#140f24] px-4 text-center text-white/80">
        <div>
          <Logo className="mx-auto h-14 w-14 rounded-2xl" />
          <p className="mt-4 text-sm">
            We could not find this order.
            <br />
            Please ask your YQ Bahrain salesman for an up-to-date link.
          </p>
        </div>
      </div>
    )
  }

  const status = String(data?.status || 'new').toLowerCase()
  const cancelled = status === 'cancelled'
  const stepIndex = Math.max(0, STEPS.findIndex((s) => s.key === status))
  const lines = data?.lines || []
  const timeline = data?.timeline || []

  return (
    <div className="min-h-screen bg-[#faf9fc] text-[#1a1430]">
      <header className="border-b border-[#ece9f3] bg-white">
        <div className="mx-auto flex max-w-2xl items-center gap-3 px-4 py-3">
          <Logo className="h-10 w-10 rounded-xl" />
          <div>
            <div className="font-display text-base font-bold leading-tight">YQ Bahrain — Your order</div>
            <div className="text-[11px] text-[#6b6480]">Mobile accessories · trade orders</div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-2xl px-4 py-5">
        {!data ? (
          <div className="space-y-3">
            <div className="h-28 animate-pulse rounded-2xl bg-[#f0eef6]" />
            <div className="h-40 animate-pulse rounded-2xl bg-[#f0eef6]" />
          </div>
        ) : (
          <>
            <section className="rounded-2xl border border-[#ece9f3] bg-white p-5 shadow-[0_1px_2px_rgba(24,16,48,.04)]">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="text-[10.5px] font-semibold uppercase tracking-wide text-[#6b6480]">Order number</div>
                  <h1 className="font-display text-2xl font-extrabold tabular-nums tracking-tight">{data.order_no}</h1>
                  {data.created_at && (
                    <div className="mt-0.5 text-[11.5px] text-[#6b6480]">Placed {fmtDateTime(data.created_at)}</div>
                  )}
                </div>
                {cancelled ? (
                  <Badge tone="rose">
                    <CircleSlash size={11} aria-hidden="true" /> Cancelled
                  </Badge>
                ) : (
                  <Badge tone="accent">{STEPS[stepIndex]?.label || 'In progress'}</Badge>
                )}
              </div>

              {cancelled ? (
                <p className="mt-4 rounded-xl bg-[#fdecef] px-3 py-2.5 text-[12px] leading-snug text-[#9f1239]">
                  This order was cancelled. Talk to your salesman if that was not expected.
                </p>
              ) : (
                <ol className="mt-5 flex items-start" aria-label="Order progress">
                  {STEPS.map((s, i) => {
                    const done = i <= stepIndex
                    return (
                      <li key={s.key} className="flex flex-1 flex-col items-center text-center">
                        <div className="flex w-full items-center">
                          <span className={cn('h-0.5 flex-1', i === 0 ? 'bg-transparent' : done ? 'bg-[#6d28d9]' : 'bg-[#e9e5f3]')} />
                          <span
                            aria-current={i === stepIndex ? 'step' : undefined}
                            className={cn(
                              'grid h-7 w-7 shrink-0 place-items-center rounded-full border-2 text-[11px] font-bold tabular-nums',
                              done ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : 'border-[#e9e5f3] bg-white text-[#a8a2bb]',
                            )}
                          >
                            {done ? <Check size={14} aria-hidden="true" /> : i + 1}
                          </span>
                          <span
                            className={cn(
                              'h-0.5 flex-1',
                              i === STEPS.length - 1 ? 'bg-transparent' : i < stepIndex ? 'bg-[#6d28d9]' : 'bg-[#e9e5f3]',
                            )}
                          />
                        </div>
                        <span className={cn('mt-1.5 text-[10.5px] font-medium', done ? 'text-[#1a1430]' : 'text-[#a8a2bb]')}>
                          {s.label}
                        </span>
                      </li>
                    )
                  })}
                </ol>
              )}

              {data.salesman?.whatsapp_url && (
                <a
                  href={data.salesman.whatsapp_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#25D366] text-[14px] font-semibold text-white transition hover:bg-[#1eb356] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#128C7E] focus-visible:ring-offset-2"
                >
                  <MessageCircle size={17} aria-hidden="true" /> Message {data.salesman.name || 'your salesman'} on WhatsApp
                </a>
              )}
              {data.salesman?.name && !data.salesman.whatsapp_url && (
                <p className="mt-4 text-[12px] text-[#6b6480]">
                  Your salesman: <b className="font-semibold text-[#1a1430]">{data.salesman.name}</b>
                </p>
              )}
            </section>

            {data.has_backorder && (
              <p className="mt-3 rounded-xl bg-[#fdf3e3] px-3 py-2.5 text-[11.5px] leading-snug text-[#96600d]">
                Some lines are on backorder — your salesman will confirm the ETA before they ship.
              </p>
            )}

            {lines.length > 0 && (
              <section className="mt-4 overflow-hidden rounded-2xl border border-[#ece9f3] bg-white">
                <h2 className="border-b border-[#f2f0f7] px-4 py-3 font-display text-[13px] font-bold">Items</h2>
                <ul className="divide-y divide-[#f2f0f7]">
                  {lines.map((l) => (
                    <li key={l.item_code} className="flex items-baseline justify-between gap-3 px-4 py-2.5 text-[12.5px]">
                      <span className="min-w-0">
                        <b className="font-display font-bold">{l.item_code}</b>
                        {l.display_name && l.display_name !== l.item_code && (
                          <span className="ml-1.5 text-[#6b6480]">{l.display_name}</span>
                        )}
                        <span className="ml-1.5 tabular-nums text-[#6b6480]">
                          {l.qty} × {money(l.unit_price_bhd)}
                        </span>
                        {l.backorder && <span className="ml-1.5 text-[11px] text-[#96600d]">backorder</span>}
                      </span>
                      <span className="shrink-0 font-semibold tabular-nums">{bhd(l.line_total_bhd)}</span>
                    </li>
                  ))}
                </ul>
                <dl className="space-y-1.5 border-t border-[#f2f0f7] px-4 py-3 text-[12.5px]">
                  <div className="flex justify-between">
                    <dt className="text-[#6b6480]">Subtotal</dt>
                    <dd className="tabular-nums">{bhd(data.subtotal_bhd)}</dd>
                  </div>
                  {Number(data.discount_bhd) > 0 && (
                    <div className="flex justify-between">
                      <dt className="text-[#137a48]">Discount</dt>
                      <dd className="tabular-nums text-[#137a48]">−{bhd(data.discount_bhd)}</dd>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <dt className="text-[#6b6480]">Delivery</dt>
                    <dd className="tabular-nums">{Number(data.delivery_bhd) > 0 ? bhd(data.delivery_bhd) : 'Free'}</dd>
                  </div>
                  <div className="flex justify-between border-t border-[#f2f0f7] pt-2">
                    <dt className="font-semibold">Total</dt>
                    <dd className="font-display text-[16px] font-extrabold tabular-nums">{bhd(data.total_bhd)}</dd>
                  </div>
                </dl>
              </section>
            )}

            {(data.customer?.name || data.customer?.shop || data.customer?.area) && (
              <section className="mt-4 rounded-2xl border border-[#ece9f3] bg-white p-4">
                <h2 className="font-display text-[13px] font-bold">Delivering to</h2>
                <p className="mt-1 text-[12.5px] leading-relaxed text-[#4a4360]">
                  {[data.customer?.name, data.customer?.shop, data.customer?.area].filter(Boolean).join(' · ')}
                </p>
              </section>
            )}

            {timeline.length > 0 && (
              <section className="mt-4 rounded-2xl border border-[#ece9f3] bg-white p-4">
                <h2 className="font-display text-[13px] font-bold">Timeline</h2>
                <ol className="mt-3 space-y-3">
                  {timeline.map((t, i) => (
                    <li key={i} className="flex gap-3">
                      <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[#6d28d9]" aria-hidden="true" />
                      <div className="min-w-0">
                        <div className="text-[12.5px] font-medium capitalize">{eventLabel(t.event)}</div>
                        <div className="text-[11px] text-[#6b6480]">{fmtDateTime(t.ts) || ''}</div>
                        {t.note && <div className="mt-0.5 text-[11.5px] text-[#6b6480]">{t.note}</div>}
                      </div>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </>
        )}

        <footer className="py-10 text-center text-[11px] text-[#6b6480]">
          YQ Bahrain W.L.L · Prices confirmed by your salesman.
        </footer>
      </main>
    </div>
  )
}
