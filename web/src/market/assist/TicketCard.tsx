import { useMemo, useRef, useState } from 'react'
import { Camera, Check, ChevronLeft, LifeBuoy, MessageCircle, Package, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getOrderStatus, type OrderStatusLine, type ShopItem } from '@/lib/shopApi'
import { ltrText } from '../i18n'
import { fmtDateShort, productName } from '../lib/format'
import { searchItems } from '../lib/search'
import { useMarket, useMarketSearch } from '../MarketContext'
import { AnchorButton, Button } from '../ui/Button'
import { Ltr } from '../ui/Ltr'
import { Stepper } from '../ui/Stepper'
import type { TicketReceipt } from './assistApi'
import { downscale } from './media'
import { A } from './strings'
import { newId, type Msg } from './store'

/**
 * The ticket card (YQ Assist v2): a damaged, wrong or missing item, something not working, a delivery problem
 * or a return, reported in four short steps that run here, then one request (app/market_support.py). An order is
 * named by the token this phone holds and its lines by their position; a report without an order (bought on a
 * salesman's visit) picks catalogue items. The merchant is promised the next step only (owner, 29-Oct-2026).
 */

const AFTER_DELIVERY = ['damaged', 'wrong_item', 'missing', 'not_working', 'return', 'other']
const BEFORE_DELIVERY = ['delivery', 'other']
const NO_ORDER = ['damaged', 'wrong_item', 'missing', 'not_working', 'return', 'delivery', 'other']
const NEEDS_ITEMS = new Set(['damaged', 'wrong_item', 'missing', 'not_working', 'return'])
const MAX_PHOTOS = 3

interface Pick {
  index: number | null
  code: string
  name: string
  qty: number
  max: number
}

function lineMax(l: OrderStatusLine): number {
  return Math.max(0, Number(l.qty_delivered ?? l.qty_confirmed ?? l.qty) || 0)
}

export function Receipt({ r }: { r: TicketReceipt }) {
  const name = r.rep?.name || null
  const wa = r.rep?.whatsapp_url ? r.rep.whatsapp_url.replace(/([?&])text=[^&]*/, '$1').replace(/[?&]$/, '') : null
  const href = wa ? `${wa}${wa.includes('?') ? '&' : '?'}text=${encodeURIComponent(A.t.waText(r.ref))}` : null
  return (
    <section aria-label={A.t.receipt(r.ref)} className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex items-center gap-2.5 border-b border-line-2 bg-ok-soft px-3.5 py-2.5">
        <span className="grid h-7 w-7 place-items-center rounded-full bg-ok text-white">
          <Check size={15} aria-hidden="true" />
        </span>
        <h3 className="font-display text-sm font-bold text-ink">
          <bdi dir="ltr">{A.t.receipt(r.ref)}</bdi>
        </h3>
        <span className="ms-auto rounded-full bg-surface px-2 py-0.5 text-2xs font-semibold text-ok">{A.ticketStatus[r.status] || r.status}</span>
      </div>
      <div className="flex flex-col gap-2.5 px-3.5 py-3">
        <p className="text-sm text-ink">{r.kind === 'callback' || r.kind === 'account' ? A.callBackDone(r.ref, name) : A.t.receiptLine(name)}</p>
        {href ? (
          <AnchorButton variant="wa" size="sm" href={href} target="_blank" rel="noreferrer" icon={<MessageCircle size={15} aria-hidden="true" />} className="self-start">
            {A.t.tellRep(name)}
          </AnchorButton>
        ) : null}
      </div>
    </section>
  )
}

export function TicketCard({ m, onSubmit }: { m: Msg; onSubmit: (body: { kind: string; order_token?: string | null; lines: { index?: number | null; item_code: string; qty: number }[]; note?: string; photos?: string[]; client_ticket_id: string }) => Promise<TicketReceipt | string> }) {
  const { items, itemsByCode } = useMarket()
  const { ensureIndex } = useMarketSearch()
  const card = m.ticket
  const orders = card?.orders || []
  const [issue, setIssue] = useState<string>(card?.issue || '')
  const [order, setOrder] = useState<string | null | undefined>(orders.length ? undefined : null) // undefined = not chosen
  const [orderLines, setOrderLines] = useState<OrderStatusLine[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [picks, setPicks] = useState<Pick[]>([])
  const [q, setQ] = useState('')
  const [found, setFound] = useState<ShopItem[]>([])
  const [photos, setPhotos] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const clientId = useRef(newId())
  const fileRef = useRef<HTMLInputElement>(null)

  const [itemsDone, setItemsDone] = useState(false)
  const needsItems = NEEDS_ITEMS.has(issue)
  const step = !issue ? 'issue' : order === undefined ? 'order' : needsItems && !itemsDone ? 'items' : 'details'
  // an order that cannot take this problem is shown, greyed (damage before delivery, a late delivery after it)
  const fits = useMemo(
    () => (status: string) => (status === 'delivered' ? AFTER_DELIVERY : status === 'cancelled' ? ['other'] : BEFORE_DELIVERY).includes(issue),
    [issue],
  )
  const back = () => {
    if (step === 'details' && needsItems) setItemsDone(false)
    else if ((step === 'details' || step === 'items') && orders.length) setOrder(undefined)
    else setIssue('')
  }

  const pickOrder = async (token: string | null) => {
    setOrder(token)
    setPicks([])
    setOrderLines(null)
    setError('')
    if (!token) return
    setLoading(true)
    try {
      const o = await getOrderStatus(token)
      setOrderLines(o.lines || [])
    } catch {
      setError(A.t.orderFailed)
    } finally {
      setLoading(false)
    }
  }

  const search = async (text: string) => {
    setQ(text)
    if (text.trim().length < 2) return setFound([])
    const index = await ensureIndex().catch(() => null)
    setFound(index ? searchItems(index, items, text, 5) : [])
  }

  const togglePick = (p: Pick) =>
    setPicks((all) => (all.some((x) => x.code === p.code && x.index === p.index) ? all.filter((x) => !(x.code === p.code && x.index === p.index)) : [...all, p]))

  const addPhotos = async (files: FileList | null) => {
    if (!files) return
    const room = MAX_PHOTOS - photos.length
    const next: string[] = []
    for (const f of Array.from(files).slice(0, room)) {
      try {
        next.push(await downscale(f))
      } catch {
        setError(A.errPhoto)
      }
    }
    setPhotos((p) => [...p, ...next].slice(0, MAX_PHOTOS))
  }

  const send = async () => {
    if (needsItems && !picks.length) return setError(A.t.pickItem)
    setSending(true)
    setError('')
    const res = await onSubmit({
      kind: issue,
      order_token: order || null,
      lines: picks.map((p) => ({ index: p.index, item_code: p.code, qty: p.qty })),
      note: note.trim() || undefined,
      photos,
      client_ticket_id: clientId.current,
    })
    setSending(false)
    if (typeof res === 'string') setError(res)
  }

  if (!card) return null
  if (m.receipt) return <Receipt r={m.receipt} />

  return (
    <section aria-label={A.t.title} className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex items-center gap-2 border-b border-line-2 bg-plum-wash px-3.5 py-2.5">
        <LifeBuoy size={16} className="text-plum" aria-hidden="true" />
        <h3 className="font-display text-sm font-bold text-plum-ink">{A.t.title}</h3>
        {step !== 'issue' ? (
          <button
            type="button"
            onClick={back}
            className="ms-auto inline-flex min-h-9 items-center gap-1 rounded-full px-2.5 text-xs font-semibold text-plum hover:bg-plum-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
          >
            <ChevronLeft size={14} className="rtl:-scale-x-100" aria-hidden="true" />
            {A.t.back}
          </button>
        ) : null}
      </div>

      <div className="flex flex-col gap-3 px-3.5 py-3">
        {step === 'issue' ? (
          <>
            <p className="text-sm font-semibold text-ink">{A.t.whatWrong}</p>
            <div className="flex flex-wrap gap-2">
              {NO_ORDER.map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setIssue(k)}
                  className="min-h-11 rounded-full border border-line bg-surface px-3.5 text-sm font-semibold text-ink hover:border-plum/40 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
                >
                  {A.ticketKind[k]}
                </button>
              ))}
            </div>
          </>
        ) : null}

        {step === 'order' ? (
          <>
            <p className="text-sm font-semibold text-ink">{A.t.whichOrder}</p>
            <div className="overflow-hidden rounded-md border border-line" role="list">
              {orders.map((o, i) => (
                <button
                  key={o.token}
                  type="button"
                  role="listitem"
                  disabled={!fits(o.status)}
                  onClick={() => void pickOrder(o.token)}
                  className={cn('flex min-h-[52px] w-full items-center gap-3 px-3 text-start hover:bg-plum-wash disabled:opacity-45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70', i > 0 && 'border-t border-line-2')}
                >
                  <Package size={16} className="text-plum" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <bdi dir="ltr" className="font-display text-sm font-bold text-ink">{o.order_no}</bdi>
                    <span className="block text-xs text-ink-2">{[o.status_label, fmtDateShort(o.created_at)].filter(Boolean).join(' · ')}</span>
                  </span>
                </button>
              ))}
              <button
                type="button"
                onClick={() => void pickOrder(null)}
                className="flex min-h-[52px] w-full items-center gap-3 border-t border-line-2 px-3 text-start text-sm font-semibold text-ink hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70"
              >
                {A.t.noOrder}
              </button>
            </div>
          </>
        ) : null}

        {step === 'items' ? (
          <>
            <p className="text-sm font-semibold text-ink">{A.t.items}</p>
            {loading ? <p className="assist-stage text-xs text-ink-2">{A.t.loadingOrder}</p> : null}
            {order && orderLines ? (
              <ul className="divide-y divide-line-2 overflow-hidden rounded-md border border-line">
                {orderLines.map((l, i) => {
                  const max = lineMax(l)
                  const it = itemsByCode.get(l.item_code)
                  const on = picks.some((p) => p.index === i)
                  if (max < 1) return null
                  return (
                    <li key={`${i}-${l.item_code}`}>
                      <button
                        type="button"
                        aria-pressed={on}
                        onClick={() => togglePick({ index: i, code: l.item_code, name: it ? productName(it) : l.display_name || l.item_code, qty: 1, max })}
                        className="flex min-h-[52px] w-full items-center gap-3 px-3 text-start hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70"
                      >
                        <span className={cn('grid h-5 w-5 shrink-0 place-items-center rounded border', on ? 'border-plum bg-plum text-white' : 'border-line')}>{on ? <Check size={13} aria-hidden="true" /> : null}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-semibold text-ink" {...ltrText}>{it ? productName(it) : l.display_name || l.item_code}</span>
                          <span className="block text-xs text-ink-2"><Ltr>{l.item_code}</Ltr> · {A.t.upTo(max)}</span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            ) : null}
            {order === null ? (
              <>
                <input
                  value={q}
                  onChange={(e) => void search(e.target.value)}
                  placeholder={A.t.findItem}
                  aria-label={A.t.findItem}
                  className="min-h-11 w-full rounded-md border border-line bg-surface px-3 text-[16px] text-ink placeholder:text-ink-3 focus-visible:border-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40"
                />
                {found.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {found.map((it) => (
                      <button
                        key={it.item_code}
                        type="button"
                        onClick={() => {
                          togglePick({ index: null, code: it.item_code, name: productName(it), qty: 1, max: 999 })
                          setQ('')
                          setFound([])
                        }}
                        className="inline-flex min-h-11 max-w-full items-center gap-1.5 rounded-full border border-line px-3 text-xs font-semibold text-ink hover:bg-plum-wash"
                      >
                        <Ltr>{it.item_code}</Ltr>
                        <span className="truncate font-normal text-ink-2" {...ltrText}>{productName(it)}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </>
            ) : null}
          </>
        ) : null}

        {step === 'details' || picks.length ? (
          <>
            {picks.length ? (
              <ul className="flex flex-col gap-2">
                {picks.map((p) => (
                  <li key={`${p.index}-${p.code}`} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink" {...ltrText}>{p.name}</span>
                    <Stepper size="sm" value={p.qty} step={1} min={1} max={p.max} label={p.name} onChange={(v) => setPicks((all) => all.map((x) => (x === p ? { ...x, qty: v } : x)))} onRemove={() => togglePick(p)} />
                  </li>
                ))}
              </ul>
            ) : null}
            {step === 'details' ? (
              <>
                <div>
                  <p className="mb-1.5 text-xs font-semibold text-ink-2">{A.t.photo}</p>
                  <div className="flex flex-wrap items-center gap-2">
                    {photos.map((src, i) => (
                      <span key={i} className="relative">
                        <img src={src} alt="" className="h-16 w-16 rounded-md object-contain ring-1 ring-line" />
                        <button type="button" aria-label={A.t.cancel} onClick={() => setPhotos((ps) => ps.filter((_, k) => k !== i))} className="absolute -end-2 -top-2 grid h-7 w-7 place-items-center rounded-full bg-ink text-white">
                          <X size={13} aria-hidden="true" />
                        </button>
                      </span>
                    ))}
                    {photos.length < MAX_PHOTOS ? (
                      <button type="button" onClick={() => fileRef.current?.click()} aria-label={A.t.photo} className="grid h-16 w-16 place-items-center rounded-md border border-dashed border-line text-plum hover:bg-plum-wash">
                        <Camera size={18} aria-hidden="true" />
                      </button>
                    ) : null}
                    <input ref={fileRef} type="file" accept="image/*" multiple hidden tabIndex={-1} onChange={(e) => { void addPhotos(e.target.files); e.target.value = '' }} />
                  </div>
                </div>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value.slice(0, 500))}
                  rows={2}
                  placeholder={A.t.note}
                  aria-label={A.t.note}
                  className="w-full resize-none rounded-md border border-line bg-surface p-3 text-[16px] text-ink placeholder:text-ink-3 focus-visible:border-plum focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/40"
                />
                <Button full loading={sending} onClick={() => void send()} icon={<LifeBuoy size={16} aria-hidden="true" />}>
                  {sending ? A.t.sending : A.t.send}
                </Button>
              </>
            ) : (
              <Button size="sm" className="self-start" disabled={!picks.length} onClick={() => setItemsDone(true)}>
                {A.t.next}
              </Button>
            )}
          </>
        ) : null}

        {error ? <p role="status" className="rounded-md bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p> : null}
        {step === 'items' && order === null && !orders.length ? <p className="text-xs text-ink-2">{A.t.noOrders}</p> : null}
      </div>
    </section>
  )
}
