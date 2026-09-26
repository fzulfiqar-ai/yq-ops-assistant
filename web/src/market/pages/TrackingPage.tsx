import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Check, Copy, Download, Mail, MessageCircle, RotateCcw, Share2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { MarketOrderResponse, OrderStatusPayload } from '@/lib/shopApi'
import { CheckMark } from '../components/CheckMark'
import { SoldOutRows } from '../components/SoldOut'
import { EmptyState } from '../components/States'
import { useMarket, useOrder } from '../MarketContext'
import { forgetOrder } from '../lib/device'
import { splitReorder } from '../lib/home'
import { track } from '../lib/events'
import { bhd, fmtDateTime, money } from '../lib/format'
import { canPromptInstall, isIos, isStandalone, onInstallChange, promptInstall } from '../lib/install'
import { cancelOrder, getOrder } from '../lib/marketApi'
import { usePageTitle } from '../shell/ShellContext'
import { S } from '../strings'
import { AnchorButton, Button, LinkButton } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { Input } from '../ui/Field'
import { Skeleton } from '../ui/Skeleton'
import { useToast } from '../ui/Toast'

const POLL_MS = 30000

/** /o/{token} — confirmation (when just placed) and live tracking: the five stages, changes, the rep, cancel while Received, reorder. */
export default function TrackingPage() {
  const { token = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const toast = useToast()
  const m = useMarket()
  const { refreshMyOrders } = useOrder()
  const placed = (location.state as { placed?: MarketOrderResponse } | null)?.placed || null
  const [data, setData] = useState<OrderStatusPayload | null>(null)
  const [err, setErr] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [installable, setInstallable] = useState(() => canPromptInstall())
  usePageTitle(data?.order_no ? S.track.order(data.order_no) : S.track.title, true, data?.order_no ? `${S.track.order(data.order_no)} · ${S.brand}` : `${S.track.title} · ${S.brand}`)

  const load = useCallback(() => {
    if (!token) return
    getOrder(token)
      .then((d) => {
        setData(d)
        setErr(false)
      })
      .catch(() => setData((cur) => (cur ? cur : (setErr(true), null))))
  }, [token])

  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    const live = () => document.visibilityState === 'visible'
    const id = window.setInterval(() => live() && data && !data.cancelled && data.status !== 'delivered' && load(), POLL_MS)
    const onVis = () => live() && load()
    document.addEventListener('visibilitychange', onVis)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [load, data])
  useEffect(() => onInstallChange(() => setInstallable(canPromptInstall())), [])

  const first = data?.salesman?.first_name || placed?.salesman?.first_name || ''
  // the placed screen speaks the order's kind: a small order request is confirmed case by case
  const isSmall = (placed?.order_kind ?? data?.order_kind) === 'small'
  const lines = data?.lines || []
  const total = data?.total_confirmed_bhd ?? data?.total_bhd
  /*
   * The confirmation's one figure — what was ordered and what it comes to. A shop owner wants the
   * amount straight after the order number, and on a phone the line items sit a long scroll below
   * the steps and the share card. Every number here is the payload's: the placed response carries
   * its own quote (there before the status call returns), the status payload replaces it.
   */
  const live = lines.filter((l) => (l.line_status || 'ok') !== 'removed')
  const heroItems = live.length || Number(placed?.totals?.items) || 0
  const heroUnits = live.length ? live.reduce((n, l) => n + (Number(l.qty_confirmed ?? l.qty) || 0), 0) : Number(placed?.totals?.units) || 0
  const heroTotal = total ?? placed?.totals?.total_bhd ?? null
  /*
   * Desktop: the second column holds the items, the delivery address and the cancelled-order
   * action. When the order has none of those there is nothing to put beside the hero, so the page
   * stops being a two-column grid instead of leaving half the screen blank. While the status is
   * still loading the grid stays, so the ordinary order (which always has lines) never shifts.
   */
  const hasAside = !data || lines.length > 0 || Boolean(data.customer?.shop) || Boolean(data.customer?.area) || Boolean(data.cancelled)

  const doCancel = async () => {
    setBusy(true)
    try {
      const r = await cancelOrder(token, reason.trim())
      setData(r.order)
      setCancelOpen(false)
      toast(S.track.cancelled, 'info')
    } catch {
      toast(S.track.cancelFailed, 'error')
    } finally {
      setBusy(false)
    }
  }
  /* Order again follows the shop's backorder setting (m.addMany leaves sold-out lines out while it is
   * off, and says how many). Those lines are not dropped in silence: they are listed under the button,
   * greyed, each with "Tell me when back" — and when nothing at all could be added the page stays put
   * instead of opening an empty restock. */
  const reorderSold = useMemo(() => {
    const again = (data?.lines || []).filter((ln) => (ln.line_status || 'ok') !== 'removed').flatMap((ln) => {
      const item = m.itemsByCode.get(ln.item_code)
      return item ? [{ item, qty: 1 }] : []
    })
    return splitReorder(again, m.allowBackorder).sold.map((l) => l.item)
  }, [data, m.itemsByCode, m.allowBackorder])
  const reorder = () => {
    const entries = lines
      .filter((ln) => (ln.line_status || 'ok') !== 'removed')
      .map((ln) => ({ item: m.itemsByCode.get(ln.item_code)!, qty: Number(ln.qty_confirmed ?? ln.qty) || 1 }))
      .filter((e) => e.item)
    const added = m.addMany(entries, 'reorder')
    if (added > 0) navigate('/cart')
  }
  const copySummary = async () => {
    const text = [`YQ ${S.track.order(data?.order_no || '')}`, ...lines.map((l) => `${l.qty_confirmed ?? l.qty} x ${l.item_code}`), `${S.cart.total} ${bhd(total)}`, window.location.href].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      toast(S.placed.copied, 'success')
    } catch {
      toast(S.track.copyFailed, 'error')
    }
  }
  const install = async () => {
    const r = await promptInstall()
    if (r === 'accepted') track('install')
    setInstallable(canPromptInstall())
  }

  if (err || !token) {
    return (
      <div className="px-gutter lg:px-0">
        <EmptyState title={S.track.notFound} hint={S.track.notFoundHint} action={<LinkButton to="/orders">{S.orders.title}</LinkButton>} />
      </div>
    )
  }

  return (
    <div className="px-gutter lg:px-0">
      <div className={cn('mx-auto max-w-2xl', hasAside && 'lg:mx-0 lg:grid lg:max-w-none lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:items-start lg:gap-6')}>
        <div>
          {placed && (
            <section className="rounded-xl border border-line bg-surface p-6 text-center shadow-2">
              <div className="mx-auto w-14">
                <CheckMark size={56} />
              </div>
              <h1 className="mt-4 text-balance font-display text-xl font-bold text-ink">{isSmall ? S.small.received : S.placed.title}</h1>
              <p className="mx-auto mt-1 max-w-sm text-balance text-sm leading-snug text-ink-2">
                {placed.duplicate ? S.placed.duplicate : isSmall ? S.small.receivedHint : placed.assigned && first ? S.placed.sentTo(first) : S.placed.unassigned}
              </p>
              {isSmall && !placed.duplicate && placed.assigned && first && <p className="mt-1 text-xs font-medium text-plum-ink">{S.placed.sentTo(first)}</p>}
              <div className="mt-5 rounded-md bg-plum-soft px-4 py-3">
                <div className="text-2xs font-semibold uppercase tracking-[0.1em] text-plum">{S.placed.number}</div>
                <div className="mt-0.5 font-display text-2xl font-extrabold tnum text-ink">{placed.order_no}</div>
                {heroItems > 0 && heroTotal != null && (
                  <div className="mt-1 border-t border-plum/10 pt-1.5 text-xs tnum text-plum-ink">
                    {S.cart.summary(heroItems, heroUnits)} <span aria-hidden="true">·</span> <b className="font-semibold">{bhd(heroTotal)}</b>
                  </div>
                )}
              </div>
              <ol className="mt-4 space-y-1.5 text-start text-sm text-ink-2">
                {S.placed.next.map((t, i) => (
                  <li key={t} className="flex gap-2">
                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-plum-soft text-2xs font-bold text-plum-ink">{i + 1}</span>
                    {t}
                  </li>
                ))}
              </ol>
              {placed.whatsapp_url && (
                <AnchorButton href={placed.whatsapp_url} target="_blank" rel="noreferrer" variant="wa" size="lg" full className="mt-5" icon={<MessageCircle size={17} aria-hidden="true" />}>
                  {first ? S.placed.whatsapp(first) : S.track.sendWhatsapp}
                </AnchorButton>
              )}
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {placed.email_url && (
                  <AnchorButton href={placed.email_url} variant="secondary" icon={<Mail size={15} aria-hidden="true" />}>
                    {S.placed.email(first || 'YQ')}
                  </AnchorButton>
                )}
                <Button variant="secondary" onClick={copySummary} icon={<Copy size={15} aria-hidden="true" />}>
                  {S.placed.copy}
                </Button>
              </div>
              {!isStandalone() && (installable || isIos()) && (
                <Button variant="secondary" full className="mt-2" onClick={installable ? install : () => toast(S.me.installIos, 'info')} icon={<Download size={15} aria-hidden="true" />}>
                  {S.placed.install}
                </Button>
              )}
              <Link to={m.rep ? `/${m.rep.slug}` : '/'} className="hit relative mt-3 inline-flex h-10 items-center text-sm font-semibold text-plum hover:underline">
                {S.placed.continue}
              </Link>
              <p className="mt-3 border-t border-line-2 pt-3 font-display text-xs font-semibold tracking-[-0.01em] text-ink-3">{S.tagline}</p>
            </section>
          )}

          {!data ? (
            <div className={cn('space-y-3', placed && 'mt-4')}>
              <Skeleton className="h-44 w-full rounded-lg" />
              <Skeleton className="h-32 w-full rounded-lg" />
            </div>
          ) : (
            <section className={cn('rounded-lg border border-line bg-surface p-5', placed && 'mt-4')}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                {/* one order number per screen: straight after placing, the hero above owns it (and
                    the small-order sentence), so the tracking card leads with when it was placed */}
                <div>
                  {!placed && (
                    <>
                      <div className="text-2xs font-semibold uppercase tracking-wide text-ink-2">{S.placed.number}</div>
                      <h2 className="font-display text-xl font-extrabold tnum text-ink">{data.order_no}</h2>
                    </>
                  )}
                  {data.created_at && (
                    <div className={cn(placed ? 'font-display text-base font-bold text-ink' : 'mt-0.5 text-xs text-ink-2')}>
                      {S.track.placed} {fmtDateTime(data.created_at)}
                    </div>
                  )}
                  {!placed && isSmall && data.status === 'new' && <div className="mt-1.5 max-w-prose text-xs leading-snug text-plum-ink">{S.minimum.requested}</div>}
                </div>
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  {isSmall && (
                    <Chip tone="grey" size="md">
                      {S.small.badge}
                    </Chip>
                  )}
                  <Chip tone={data.cancelled ? 'bad' : data.status === 'delivered' ? 'ok' : 'plum'} size="md">
                    {data.status_label || data.status}
                  </Chip>
                </div>
              </div>

              {data.cancelled ? (
                <p className="mt-4 rounded-sm bg-bad-soft px-3 py-2.5 text-xs leading-snug text-bad">
                  {S.track.cancelled} {S.track.cancelledHint}
                </p>
              ) : (
                <ol className="mt-5" aria-label={S.track.progress}>
                  {(data.steps || []).map((s, i, arr) => (
                    <li key={s.status} className="flex gap-3">
                      <div className="flex flex-col items-center">
                        <span aria-current={s.current ? 'step' : undefined} className={cn('grid h-7 w-7 shrink-0 place-items-center rounded-full border-2 text-xs font-bold', s.done ? 'border-plum bg-plum text-white' : s.current ? 'border-plum bg-surface text-plum' : 'border-line bg-surface text-ink-3')}>
                          {s.done ? <Check size={14} aria-hidden="true" /> : i + 1}
                        </span>
                        {i < arr.length - 1 && <span className={cn('my-1 h-6 w-0.5', s.done ? 'bg-plum' : 'bg-line')} />}
                      </div>
                      <div className="min-w-0 pb-2">
                        <div className={cn('text-sm font-semibold', s.done || s.current ? 'text-ink' : 'text-ink-3')}>{s.label}</div>
                        {s.at && <div className="text-xs text-ink-2">{fmtDateTime(s.at)}</div>}
                        {s.current && data.expected_delivery && (
                          <div className="mt-0.5 inline-flex rounded-xs bg-plum-soft px-2 py-0.5 text-xs font-medium text-plum-ink">
                            {S.track.expected}: {data.expected_delivery}
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              )}

              {data.has_changes && (
                <div className="mt-3 rounded-md bg-warn-soft px-3.5 py-3 text-xs text-warn">
                  <div className="font-semibold">{S.track.changes}</div>
                  <ul className="mt-1 space-y-0.5">
                    {lines
                      .filter((l) => (l.line_status || 'ok') === 'removed' || (l.qty_confirmed != null && l.qty_confirmed !== l.qty))
                      .map((l) => (
                        <li key={l.item_code} className="tnum">
                          {l.item_code}: {(l.line_status || 'ok') === 'removed' ? S.track.removedLine : `${l.qty} → ${l.qty_confirmed}`}
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {data.salesman && (data.salesman.whatsapp_url || data.salesman.email_url) && (
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  {data.salesman.whatsapp_url && (
                    <AnchorButton href={`${data.salesman.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(S.track.aboutOrder(data.salesman.first_name || '', data.order_no))}`} target="_blank" rel="noreferrer" variant="wa" icon={<MessageCircle size={16} aria-hidden="true" />}>
                      {S.track.message(data.salesman.first_name || data.salesman.name || 'YQ')}
                    </AnchorButton>
                  )}
                  {data.salesman.email_url && (
                    <AnchorButton href={data.salesman.email_url} variant="secondary" icon={<Mail size={15} aria-hidden="true" />}>
                      {S.placed.email(data.salesman.first_name || 'YQ')}
                    </AnchorButton>
                  )}
                </div>
              )}

              <div className="mt-3 flex flex-wrap gap-2">
                {lines.length > 0 && (
                  <Button variant="secondary" onClick={reorder} icon={<RotateCcw size={15} aria-hidden="true" />}>
                    {S.track.reorder}
                  </Button>
                )}
                <Button variant="secondary" onClick={() => navigator.share?.({ title: S.track.order(data.order_no), url: window.location.href }).catch(() => {}) ?? copySummary()} icon={<Share2 size={15} aria-hidden="true" />}>
                  {S.placed.share}
                </Button>
                {data.can_cancel && !cancelOpen && (
                  <Button variant="ghost" className="text-bad" onClick={() => setCancelOpen(true)}>
                    {S.track.cancel}
                  </Button>
                )}
              </div>
              {/* what Order again cannot add today (sold out, no backorder): named here, with "Tell me when
                  back" — not on the just-placed confirmation, where reordering is not the next step */}
              {!placed && lines.length > 0 && <SoldOutRows items={reorderSold} className="mt-4" />}
              {cancelOpen && (
                <div className="mt-3 rounded-md border border-bad/20 bg-bad-soft p-3.5">
                  <p className="text-sm font-medium text-bad">{S.track.cancelConfirm}</p>
                  <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={S.track.cancelWhy} className="mt-2" tall={false} />
                  <div className="mt-2 flex gap-2">
                    <Button variant="danger" disabled={busy} onClick={doCancel}>
                      {S.track.cancel}
                    </Button>
                    <Button variant="secondary" onClick={() => setCancelOpen(false)}>
                      {S.track.keep}
                    </Button>
                  </div>
                </div>
              )}
            </section>
          )}
        </div>

        <div>
          {data && lines.length > 0 && (
            <section className="mt-4 overflow-hidden rounded-lg border border-line bg-surface lg:mt-0">
              <h2 className="border-b border-line-2 px-4 py-3 font-display text-sm font-bold">{S.track.items}</h2>
              <ul className="divide-y divide-line-2">
                {lines.map((l) => {
                  const removed = (l.line_status || 'ok') === 'removed'
                  const qty = l.qty_confirmed ?? l.qty
                  return (
                    <li key={l.item_code} className={cn('flex items-baseline justify-between gap-3 px-4 py-2.5 text-sm', removed && 'opacity-50 line-through')}>
                      <span className="min-w-0">
                        <b className="font-display font-bold">{l.display_name || l.item_code}</b>
                        <span className="ms-1.5 tnum text-ink-2">
                          {qty} × {money(l.unit_price_bhd)}
                        </span>
                        {l.backorder && !removed && <span className="ms-1.5 text-xs text-warn">{S.card.backorder.toLowerCase()}</span>}
                      </span>
                      <span className="shrink-0 font-semibold tnum">{bhd(Number(l.unit_price_bhd || 0) * Number(qty || 0))}</span>
                    </li>
                  )
                })}
              </ul>
              <div className="border-t border-line-2 px-4 py-3">
                <dl className="space-y-1.5 text-sm">
                  {Number(data.discount_bhd) > 0 && (
                    <div className="flex justify-between">
                      <dt className="text-ok">{S.cart.discount}</dt>
                      <dd className="tnum text-ok">−{bhd(data.discount_bhd)}</dd>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <dt className="text-ink-2">{S.cart.delivery}</dt>
                    <dd className="tnum">{Number(data.delivery_bhd) > 0 ? bhd(data.delivery_bhd) : S.cart.free}</dd>
                  </div>
                  <div className="flex justify-between border-t border-line-2 pt-2">
                    <dt className="font-semibold">
                      {S.cart.total}
                      {data.total_confirmed_bhd != null && data.total_confirmed_bhd !== data.total_bhd ? ` ${S.track.confirmed}` : ''}
                    </dt>
                    <dd className="font-display text-md font-extrabold tnum">{bhd(total)}</dd>
                  </div>
                </dl>
                {/* a fact about the figures above (the price book is VAT-inclusive), never a change to them */}
                <p className="mt-1.5 text-end text-2xs text-ink-2">{S.vat.note}</p>
              </div>
            </section>
          )}
          {data && (data.customer?.shop || data.customer?.area) && (
            <section className="mt-4 rounded-lg border border-line bg-surface p-4">
              <h2 className="font-display text-sm font-bold">{S.track.deliveringTo}</h2>
              <p className="mt-1 text-sm text-ink-2">{[data.customer?.name, data.customer?.shop, data.customer?.area].filter(Boolean).join(' · ')}</p>
            </section>
          )}
          {data?.cancelled && (
            <Button
              variant="secondary"
              full
              className="mt-4"
              onClick={() => {
                forgetOrder(token)
                refreshMyOrders()
                navigate('/orders')
              }}
            >
              {S.track.forget}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
