import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Check, CheckCircle2, Copy, Download, Mail, MessageCircle, RotateCcw, Share2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import type { MarketOrderResponse, OrderStatusPayload } from '@/lib/shopApi'
import { bhd, fmtDateTime, money, RING } from '@/pages/shop/shared'
import { useMarket } from '../MarketContext'
import { BTN_PRIMARY, BTN_SECONDARY, EmptyState } from '../components/Bits'
import { BottomNav, Page, TopBar } from '../components/Chrome'
import { forgetOrder, rememberQty } from '../lib/device'
import { track } from '../lib/events'
import { canPromptInstall, isIos, isStandalone, onInstallChange, promptInstall } from '../lib/install'
import { cancelOrder, getOrder } from '../lib/marketApi'
import { S } from '../strings'

const POLL_MS = 30000

/** /o/{token} — tracking (plan §K): the five stages, changes, the rep, cancel while Received, reorder. */
export default function TrackingPage() {
  const { token = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const toast = useToast()
  const m = useMarket()
  const placed = (location.state as { placed?: MarketOrderResponse } | null)?.placed || null
  const [data, setData] = useState<OrderStatusPayload | null>(null)
  const [err, setErr] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [installable, setInstallable] = useState(() => canPromptInstall())

  const load = useCallback(() => {
    if (!token) return
    getOrder(token)
      .then((d) => { setData(d); setErr(false) })
      .catch(() => { if (!data) setErr(true) })
  }, [token, data])

  useEffect(() => { load() }, [token]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const live = () => document.visibilityState === 'visible'
    const id = window.setInterval(() => live() && data && !data.cancelled && data.status !== 'delivered' && load(), POLL_MS)
    const onVis = () => live() && load()
    document.addEventListener('visibilitychange', onVis)
    return () => { window.clearInterval(id); document.removeEventListener('visibilitychange', onVis) }
  }, [load, data])
  useEffect(() => onInstallChange(() => setInstallable(canPromptInstall())), [])
  useEffect(() => {
    document.title = data?.order_no ? `Order ${data.order_no} · ${S.brand}` : `${S.track.title} · ${S.brand}`
  }, [data])

  const first = data?.salesman?.first_name || placed?.salesman?.first_name || ''
  const lines = data?.lines || []
  const total = data?.total_confirmed_bhd ?? data?.total_bhd

  const doCancel = async () => {
    setBusy(true)
    try {
      const r = await cancelOrder(token, reason.trim())
      setData(r.order)
      setCancelOpen(false)
      toast(S.track.cancelled, 'info')
    } catch {
      toast('Could not cancel — please message your representative.', 'error')
    } finally {
      setBusy(false)
    }
  }

  const reorder = () => {
    let n = 0
    for (const ln of lines) {
      if ((ln.line_status || 'ok') === 'removed') continue
      const it = m.itemsByCode.get(ln.item_code)
      const qty = Number(ln.qty_confirmed ?? ln.qty) || 1
      if (it) { m.add(it, qty, 'reorder'); rememberQty(ln.item_code, qty); n++ }
    }
    track('reorder', { meta: { count: n } })
    navigate('/cart')
  }

  const copySummary = async () => {
    const text = [`YQ order ${data?.order_no}`, ...lines.map((l) => `${l.qty_confirmed ?? l.qty} x ${l.item_code}`), `Total ${bhd(total)}`, window.location.href].join('\n')
    try { await navigator.clipboard.writeText(text); toast('Copied', 'success') } catch { toast('Could not copy', 'error') }
  }

  const install = async () => {
    const r = await promptInstall()
    if (r === 'accepted') track('install')
    setInstallable(canPromptInstall())
  }

  if (err || !token) {
    return (
      <Page>
        <TopBar back title={S.track.title} />
        <main className="mx-auto max-w-2xl px-4 pt-2">
          <EmptyState title={S.track.notFound} hint={S.track.notFoundHint} action={<Link to="/orders" className={BTN_SECONDARY}>{S.orders.title}</Link>} />
        </main>
        <BottomNav />
      </Page>
    )
  }

  return (
    <Page>
      <TopBar back title={data?.order_no ? `Order ${data.order_no}` : S.track.title} />
      <main className="mx-auto max-w-2xl px-4 pt-2">
        {placed && (
          <section className="rounded-[22px] border border-[#ece9f3] bg-white p-6 text-center shadow-[0_12px_40px_-24px_rgba(24,16,48,.35)]">
            <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-[#e8f7ee]"><CheckCircle2 size={26} className="text-[#137a48]" aria-hidden="true" /></div>
            <h1 className="mt-4 font-display text-[22px] font-bold tracking-[-0.02em] text-[#1a1430]">{S.placed.title}</h1>
            <p className="mt-1 text-[12.5px] leading-snug text-[#6b6480]">{placed.duplicate ? S.placed.duplicate : placed.assigned && first ? S.placed.sentTo(first) : S.placed.unassigned}</p>
            <div className="mt-5 rounded-[16px] bg-[#f3eefc] px-4 py-3">
              <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[#6d28d9]">{S.placed.number}</div>
              <div className="mt-0.5 font-display text-[26px] font-extrabold tracking-[-0.02em] tabular-nums text-[#1a1430]">{placed.order_no}</div>
            </div>
            <ol className="mt-4 space-y-1 text-left text-[12px] text-[#4a4360]">
              {S.placed.next.map((t, i) => <li key={t} className="flex gap-2"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#f3eefc] text-[10.5px] font-bold text-[#6d28d9]">{i + 1}</span>{t}</li>)}
            </ol>
            {placed.whatsapp_url && (
              <a href={placed.whatsapp_url} target="_blank" rel="noreferrer" className={cn('mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#25D366] text-[14px] font-semibold text-white hover:bg-[#1eb356]', RING)}>
                <MessageCircle size={17} aria-hidden="true" /> {first ? S.placed.whatsapp(first) : 'Send on WhatsApp'}
              </a>
            )}
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {placed.email_url && <a href={placed.email_url} className={BTN_SECONDARY}><Mail size={15} aria-hidden="true" /> {S.placed.email(first || 'YQ')}</a>}
              <button type="button" onClick={copySummary} className={BTN_SECONDARY}><Copy size={15} aria-hidden="true" /> Copy summary</button>
            </div>
            {!isStandalone() && (installable || isIos()) && (
              <button type="button" onClick={installable ? install : () => toast('In Safari: Share → Add to Home Screen', 'info')} className={cn(BTN_SECONDARY, 'mt-2 w-full')}>
                <Download size={15} aria-hidden="true" /> {S.placed.install}
              </button>
            )}
            <Link to={m.rep ? `/${m.rep.slug}` : '/'} className={cn('mt-3 inline-flex h-10 items-center gap-1 text-[13px] font-semibold text-[#6d28d9]', RING)}>{S.placed.continue}</Link>
          </section>
        )}

        {!data ? (
          <div className="mt-4 space-y-3"><div className="h-40 animate-pulse rounded-[20px] bg-[#f0eef6]" /><div className="h-32 animate-pulse rounded-[20px] bg-[#f0eef6]" /></div>
        ) : (
          <>
            <section className={cn('rounded-[20px] border border-[#ece9f3] bg-white p-5', placed && 'mt-4')}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="text-[10.5px] font-semibold uppercase tracking-wide text-[#6b6480]">{S.placed.number}</div>
                  <h2 className="font-display text-[22px] font-extrabold tabular-nums tracking-tight text-[#1a1430]">{data.order_no}</h2>
                  {data.created_at && <div className="mt-0.5 text-[11.5px] text-[#6b6480]">{S.track.placed} {fmtDateTime(data.created_at)}</div>}
                </div>
                <Badge tone={data.cancelled ? 'rose' : data.status === 'delivered' ? 'green' : 'accent'}>{data.status_label || data.status}</Badge>
              </div>

              {data.cancelled ? (
                <p className="mt-4 rounded-xl bg-[#fdecef] px-3 py-2.5 text-[12px] leading-snug text-[#9f1239]">{S.track.cancelled} {S.track.cancelledHint}</p>
              ) : (
                <ol className="mt-5 space-y-0" aria-label="Order progress">
                  {(data.steps || []).map((s, i, arr) => (
                    <li key={s.status} className="flex gap-3">
                      <div className="flex flex-col items-center">
                        <span aria-current={s.current ? 'step' : undefined} className={cn('grid h-7 w-7 shrink-0 place-items-center rounded-full border-2 text-[11px] font-bold', s.done ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : s.current ? 'border-[#6d28d9] bg-white text-[#6d28d9]' : 'border-[#e9e5f3] bg-white text-[#a8a2bb]')}>
                          {s.done ? <Check size={14} aria-hidden="true" /> : i + 1}
                        </span>
                        {i < arr.length - 1 && <span className={cn('my-1 h-6 w-0.5', s.done ? 'bg-[#6d28d9]' : 'bg-[#e9e5f3]')} />}
                      </div>
                      <div className="min-w-0 pb-2">
                        <div className={cn('text-[13px] font-semibold', s.done || s.current ? 'text-[#1a1430]' : 'text-[#a8a2bb]')}>{s.label}</div>
                        {s.at && <div className="text-[11px] text-[#6b6480]">{fmtDateTime(s.at)}</div>}
                        {s.status === 'confirmed' && data.expected_delivery && (s.done || s.current) && <div className="text-[11.5px] text-[#6d28d9]">{S.track.expected}: {data.expected_delivery}</div>}
                      </div>
                    </li>
                  ))}
                </ol>
              )}

              {data.has_changes && (
                <div className="mt-3 rounded-[14px] bg-[#fdf3e3] px-3.5 py-3 text-[12px] text-[#96600d]">
                  <div className="font-semibold">{S.track.changes}</div>
                  <ul className="mt-1 space-y-0.5">
                    {lines.filter((l) => (l.line_status || 'ok') === 'removed' || (l.qty_confirmed != null && l.qty_confirmed !== l.qty)).map((l) => (
                      <li key={l.item_code} className="tabular-nums">{l.item_code}: {(l.line_status || 'ok') === 'removed' ? S.track.removedLine : `${l.qty} → ${l.qty_confirmed}`}</li>
                    ))}
                  </ul>
                </div>
              )}

              {data.salesman && (data.salesman.whatsapp_url || data.salesman.email_url) && (
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  {data.salesman.whatsapp_url && <a href={data.salesman.whatsapp_url} target="_blank" rel="noreferrer" className={cn('flex h-12 items-center justify-center gap-2 rounded-xl bg-[#25D366] text-[13.5px] font-semibold text-white hover:bg-[#1eb356]', RING)}><MessageCircle size={16} aria-hidden="true" /> {S.track.message(data.salesman.first_name || data.salesman.name || 'YQ')}</a>}
                  {data.salesman.email_url && <a href={data.salesman.email_url} className={BTN_SECONDARY}><Mail size={15} aria-hidden="true" /> {S.placed.email(data.salesman.first_name || 'YQ')}</a>}
                </div>
              )}

              <div className="mt-3 flex flex-wrap gap-2">
                {lines.length > 0 && <button type="button" onClick={reorder} className={BTN_SECONDARY}><RotateCcw size={15} aria-hidden="true" /> {S.track.reorder}</button>}
                <button type="button" onClick={() => navigator.share?.({ title: `Order ${data.order_no}`, url: window.location.href }).catch(() => {}) ?? copySummary()} className={BTN_SECONDARY}><Share2 size={15} aria-hidden="true" /> Share</button>
                {data.can_cancel && !cancelOpen && <button type="button" onClick={() => setCancelOpen(true)} className={cn(BTN_SECONDARY, 'text-[#9f1239]')}>{S.track.cancel}</button>}
              </div>
              {cancelOpen && (
                <div className="mt-3 rounded-[14px] border border-[#f3c9d2] bg-[#fdecef] p-3.5">
                  <p className="text-[12.5px] font-medium text-[#9f1239]">{S.track.cancelConfirm}</p>
                  <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={S.track.cancelWhy} className="mt-2 h-11 w-full rounded-xl border border-[#f3c9d2] bg-white px-3 text-[16px] outline-none" />
                  <div className="mt-2 flex gap-2">
                    <button type="button" disabled={busy} onClick={doCancel} className={cn(BTN_PRIMARY, 'h-11 bg-[#9f1239] hover:bg-[#881337]')}>{S.track.cancel}</button>
                    <button type="button" onClick={() => setCancelOpen(false)} className={BTN_SECONDARY}>Keep order</button>
                  </div>
                </div>
              )}
            </section>

            {lines.length > 0 && (
              <section className="mt-4 overflow-hidden rounded-[20px] border border-[#ece9f3] bg-white">
                <h2 className="border-b border-[#f2f0f7] px-4 py-3 font-display text-[13px] font-bold">{S.track.items}</h2>
                <ul className="divide-y divide-[#f2f0f7]">
                  {lines.map((l) => {
                    const removed = (l.line_status || 'ok') === 'removed'
                    const qty = l.qty_confirmed ?? l.qty
                    return (
                      <li key={l.item_code} className={cn('flex items-baseline justify-between gap-3 px-4 py-2.5 text-[12.5px]', removed && 'opacity-50 line-through')}>
                        <span className="min-w-0"><b className="font-display font-bold">{l.display_name || l.item_code}</b><span className="ml-1.5 tabular-nums text-[#6b6480]">{qty} × {money(l.unit_price_bhd)}</span>{l.backorder && !removed && <span className="ml-1.5 text-[11px] text-[#96600d]">backorder</span>}</span>
                        <span className="shrink-0 font-semibold tabular-nums">{bhd(Number(l.unit_price_bhd || 0) * Number(qty || 0))}</span>
                      </li>
                    )
                  })}
                </ul>
                <dl className="space-y-1.5 border-t border-[#f2f0f7] px-4 py-3 text-[12.5px]">
                  {Number(data.discount_bhd) > 0 && <div className="flex justify-between"><dt className="text-[#137a48]">Discount</dt><dd className="tabular-nums text-[#137a48]">−{bhd(data.discount_bhd)}</dd></div>}
                  <div className="flex justify-between"><dt className="text-[#6b6480]">{S.cart.delivery}</dt><dd className="tabular-nums">{Number(data.delivery_bhd) > 0 ? bhd(data.delivery_bhd) : S.cart.free}</dd></div>
                  <div className="flex justify-between border-t border-[#f2f0f7] pt-2"><dt className="font-semibold">{S.cart.total}{data.total_confirmed_bhd != null && data.total_confirmed_bhd !== data.total_bhd ? ' (confirmed)' : ''}</dt><dd className="font-display text-[16px] font-extrabold tabular-nums">{bhd(total)}</dd></div>
                </dl>
              </section>
            )}

            {(data.customer?.shop || data.customer?.area) && (
              <section className="mt-4 rounded-[20px] border border-[#ece9f3] bg-white p-4">
                <h2 className="font-display text-[13px] font-bold">{S.track.deliveringTo}</h2>
                <p className="mt-1 text-[12.5px] text-[#4a4360]">{[data.customer?.name, data.customer?.shop, data.customer?.area].filter(Boolean).join(' · ')}</p>
              </section>
            )}

            {data.cancelled && (
              <button type="button" onClick={() => { forgetOrder(token); m.refreshMyOrders(); navigate('/orders') }} className={cn(BTN_SECONDARY, 'mt-4 w-full')}>Remove from my orders</button>
            )}
          </>
        )}
      </main>
      <BottomNav />
    </Page>
  )
}
