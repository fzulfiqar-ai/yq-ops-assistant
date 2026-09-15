import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Search, Copy, ExternalLink, Link2, MessageCircle, X, Check, Loader2,
  Phone, Mail, PackageX, ChevronRight, ChevronDown,
} from 'lucide-react'
import { apiGet, apiPost, ApiError, API_BASE } from '@/lib/api'
import { getSessionSafe } from '@/lib/supabase'
import { useAuth } from '@/lib/auth'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd, num } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import { DataTable, Stat, type Column } from '@/components/DataTable'

// ── types (kept close to docs/SHOP.md — fields we're not certain about stay optional) ──

interface ShopOrderRow {
  id: number
  order_no: string
  status: string
  customer_name?: string | null
  customer_phone?: string | null
  customer_shop?: string | null
  customer_area?: string | null
  salesman_name?: string | null
  total_bhd?: number | null
  items_count?: number | null
  units_count?: number | null
  has_backorder?: boolean
  created_at?: string | null
  source?: string | null
  referral_code?: string | null
  placed_by?: string | null
}
type StatusCounts = Partial<Record<'new' | 'confirmed' | 'packed' | 'delivered' | 'cancelled', number>>
interface ShopOrdersResp { orders: ShopOrderRow[]; count: number; counts?: StatusCounts }

interface OrderLine {
  item_code: string
  display_name?: string | null
  qty: number
  unit_price_bhd?: number | null
  line_total_bhd?: number | null
  stock_status?: 'in_stock' | 'low_stock' | 'out_of_stock' | null
  backorder?: boolean
}
interface OrderEvent { ts: string; event: string; note?: string | null }
interface OrderDetail extends ShopOrderRow {
  customer_email?: string | null
  note?: string | null
  subtotal_bhd?: number | null
  discount_bhd?: number | null
  delivery_bhd?: number | null
  coupon_code?: string | null
  coupon?: { code?: string | null; message?: string | null } | null
  lines: OrderLine[]
  events: OrderEvent[]
  notify_result?: Record<string, unknown> | null
  whatsapp_url?: string | null
  next_statuses?: string[] | null
}

interface ShopMeSalesman {
  id: number
  name: string
  [k: string]: unknown
}
interface ShopMe {
  salesman: ShopMeSalesman | null
  link?: string | null
  qr_url?: string | null
  kpis?: { orders_7d?: number; orders_30d?: number; value_30d_bhd?: number; customers_30d?: number } | null
  focus?: { revenue_90d_bhd?: number; target_bhd?: number } | null
}

const STATUSES = ['new', 'confirmed', 'packed', 'delivered', 'cancelled'] as const
type StatusFilter = 'all' | (typeof STATUSES)[number]

/**
 * Three buckets, not five statuses. Standing in a shop the only questions are
 * "what is waiting for me", "what am I working on", "what is finished".
 */
const BUCKETS = [
  { key: 'new', label: 'New', param: 'new', of: (c: StatusCounts) => c.new },
  { key: 'progress', label: 'In progress', param: 'confirmed,packed', of: (c: StatusCounts) => (c.confirmed ?? 0) + (c.packed ?? 0) },
  { key: 'done', label: 'Done', param: 'delivered,cancelled', of: (c: StatusCounts) => (c.delivered ?? 0) + (c.cancelled ?? 0) },
] as const
type BucketKey = (typeof BUCKETS)[number]['key']

const STATUS_TONE: Record<string, BadgeTone> = {
  new: 'ink',
  confirmed: 'amber',
  packed: 'amber',
  delivered: 'green',
  cancelled: 'grey',
}
function StatusPill({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] || 'grey'} className="uppercase tracking-wide">{status}</Badge>
}

const STOCK_LABEL: Record<string, string> = { in_stock: 'In stock', low_stock: 'Only a few left', out_of_stock: 'Out of stock' }
const STOCK_TONE: Record<string, BadgeTone> = { in_stock: 'green', low_stock: 'amber', out_of_stock: 'rose' }
function StockPill({ status }: { status: string }) {
  return <Badge tone={STOCK_TONE[status] || 'grey'}>{STOCK_LABEL[status] || status}</Badge>
}

function fmtDateTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return String(iso)
  }
}

/** "12 min ago" / "Yesterday" — how a person actually reads an order's age. */
function relTime(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const t = d.getTime()
  if (!Number.isFinite(t)) return ''
  const mins = Math.floor((Date.now() - t) / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins} min ago`
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((midnight(new Date()) - midnight(d)) / 86_400_000)
  if (days <= 0) {
    const h = Math.floor(mins / 60)
    return h <= 1 ? '1 hour ago' : `${h} hours ago`
  }
  if (days === 1) return 'Yesterday'
  if (days < 7) return `${days} days ago`
  try {
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
  } catch {
    return ''
  }
}

function eventLabel(event: string): string {
  if (event === 'created') return 'Order created'
  if (event.startsWith('status:')) return `Marked ${event.slice(7)}`
  return event.replace(/[_:]/g, ' ')
}

function nextStatuses(status: string): string[] {
  switch (status) {
    case 'new': return ['confirmed', 'cancelled']
    case 'confirmed': return ['packed', 'cancelled']
    case 'packed': return ['delivered']
    default: return []
  }
}

/** Fetch a protected binary endpoint (the QR PNG needs the bearer token) and hand back an
 *  object URL — plain <img src> can't carry an Authorization header. */
function useAuthedBlob(url: string | null | undefined) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    let active = true
    let created: string | null = null
    // Every setState call below lives inside this async callback (not the effect's
    // synchronous body) so React doesn't schedule an extra render on commit.
    ;(async () => {
      setBlobUrl(null)
      if (!url) return
      setLoading(true)
      try {
        const session = await getSessionSafe()
        const token = session?.access_token
        const res = await fetch(`${API_BASE}${url}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
        if (!res.ok) throw new Error(String(res.status))
        const blob = await res.blob()
        if (!active) return
        created = URL.createObjectURL(blob)
        setBlobUrl(created)
      } catch {
        if (active) setBlobUrl(null)
      } finally {
        if (active) setLoading(false)
      }
    })()
    return () => {
      active = false
      if (created) URL.revokeObjectURL(created)
    }
  }, [url])
  return { blobUrl, loading }
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums font-medium">{value}</span>
    </div>
  )
}

function NotifyResult({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data)
  if (!entries.length) return <p className="text-[12px] text-muted-foreground">No notifications sent.</p>
  return (
    <div className="space-y-1 rounded-xl border p-3">
      {entries.map(([channel, v]) => {
        const obj = v && typeof v === 'object' ? (v as Record<string, unknown>) : null
        const sent = obj ? Boolean(obj.sent) : Boolean(v)
        const reason = obj && typeof obj.reason === 'string' ? obj.reason : undefined
        return (
          <div key={channel} className="flex items-center justify-between text-[12px]">
            <span className="capitalize text-muted-foreground">{channel}</span>
            <span className={sent ? 'font-medium text-emerald-600' : 'text-muted-foreground'}>{sent ? 'Sent' : reason || 'Not sent'}</span>
          </div>
        )
      })}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// Desk view (admin) — the wide table stays; it is how the office reads the day.
// ══════════════════════════════════════════════════════════════════════════════

function MyLinkCard({
  me, isAdmin, companyKpis,
}: {
  me?: ShopMe
  isAdmin: boolean
  companyKpis: { orders_7d: number; orders_30d: number; value_30d_bhd: number; customers_30d: number } | null
}) {
  const toast = useToast()
  const salesman = me?.salesman ?? null
  const { blobUrl: qrUrl } = useAuthedBlob(salesman ? me?.qr_url : null)

  if (!salesman) {
    return (
      <Card className="mb-5 p-5">
        <div className="flex items-center gap-2 font-display text-base font-semibold">
          <Link2 size={18} className="text-primary" /> My link
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          {isAdmin
            ? <>Link your login on the <b>Salesmen</b> page to get a personal link and QR code.</>
            : <>Ask an admin to link your login on the Salesmen page to get your personal referral link and QR code.</>}
        </p>
        {isAdmin && companyKpis && (
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Orders (7d)" value={num(companyKpis.orders_7d)} />
            <Stat label="Orders (30d)" value={num(companyKpis.orders_30d)} />
            <Stat label="Value (30d)" value={bhd(companyKpis.value_30d_bhd, 3)} tone="violet" />
            <Stat label="Customers (30d)" value={num(companyKpis.customers_30d)} />
          </div>
        )}
      </Card>
    )
  }

  const link = me?.link || ''
  const kpis = me?.kpis
  const focus = me?.focus
  const pct = focus?.target_bhd ? Math.min(100, Math.round(((focus.revenue_90d_bhd || 0) / focus.target_bhd) * 100)) : null

  return (
    <Card className="mb-5 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex items-center gap-2 font-display text-base font-semibold">
            <Link2 size={18} className="text-primary" /> My link
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input readOnly value={link} onFocus={(e) => e.currentTarget.select()} className="max-w-md" />
            <Button variant="outline" size="sm" type="button"
              onClick={() => { if (link) { navigator.clipboard?.writeText(link); toast('Link copied.', 'success') } }}>
              <Copy size={14} /> Copy
            </Button>
            {link && (
              <a href={link} target="_blank" rel="noreferrer">
                <Button variant="outline" size="sm" type="button"><ExternalLink size={14} /> Open</Button>
              </a>
            )}
            {link && (
              <a href={`https://wa.me/?text=${encodeURIComponent(link)}`} target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-[13px] font-semibold text-white transition hover:bg-emerald-700">
                <MessageCircle size={14} /> Share on WhatsApp
              </a>
            )}
          </div>
        </div>
        {qrUrl && <img src={qrUrl} alt="Referral QR code" className="h-24 w-24 shrink-0 rounded-xl border bg-white p-1.5" />}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Orders (7d)" value={num(kpis?.orders_7d)} />
        <Stat label="Orders (30d)" value={num(kpis?.orders_30d)} />
        <Stat label="Value (30d)" value={bhd(kpis?.value_30d_bhd, 3)} tone="violet" />
        <Stat label="Customers (30d)" value={num(kpis?.customers_30d)} />
      </div>
      {focus && (
        <div className="mt-4 border-t pt-3">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-x-3 text-[12px] text-muted-foreground">
            <span>90-day revenue vs target</span>
            <span className="tabular-nums">
              {bhd(focus.revenue_90d_bhd, 3)} of {bhd(focus.target_bhd, 3)}{pct != null ? ` · ${pct}%` : ''}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-secondary">
            <div className="h-full rounded-full bg-primary" style={{ width: `${pct ?? 0}%` }} />
          </div>
        </div>
      )}
    </Card>
  )
}

function OrderDrawer({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['shop-order', id], queryFn: () => apiGet<OrderDetail>(`/shop/orders/${id}`) })
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  async function setStatus(next: string) {
    setBusy(next)
    try {
      await apiPost(`/shop/orders/${id}/status`, { status: next, note: note.trim() || undefined })
      toast(next === 'cancelled' ? 'Order cancelled.' : `Order marked ${next}.`, 'success')
      setNote('')
      qc.invalidateQueries({ queryKey: ['shop-order', id] })
      onChanged()
    } catch (e) {
      toast(e instanceof ApiError ? e.body.slice(0, 160) : 'Could not update the order.', 'error')
    } finally {
      setBusy(null)
    }
  }

  const actions = data ? (data.next_statuses?.length ? data.next_statuses : nextStatuses(data.status)) : []
  const coupon = data?.coupon_code || data?.coupon?.code || null

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="flex h-full w-full flex-col overflow-y-auto bg-card shadow-lift sm:max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-5 py-4">
          <div>
            <div className="font-display text-base font-semibold">{data?.order_no || 'Order'}</div>
            {data && <div className="mt-1"><StatusPill status={data.status} /></div>}
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={18} /></button>
        </div>

        {isLoading || !data ? (
          <div className="space-y-2 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
        ) : (
          <div className="flex-1 space-y-5 p-5">
            <section>
              <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Customer</div>
              <div className="rounded-xl border p-3 text-sm">
                <div className="font-semibold">{data.customer_name || '—'}</div>
                {data.customer_shop && <div className="text-muted-foreground">{data.customer_shop}</div>}
                {data.customer_area && <div className="text-muted-foreground">{data.customer_area}</div>}
                <div className="mt-2 flex flex-wrap gap-2">
                  {data.customer_phone && (
                    <a href={`tel:${data.customer_phone}`}
                      className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] font-medium transition hover:border-primary/40">
                      <Phone size={12} /> {data.customer_phone}
                    </a>
                  )}
                  {data.customer_email && (
                    <a href={`mailto:${data.customer_email}`}
                      className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] font-medium transition hover:border-primary/40">
                      <Mail size={12} /> {data.customer_email}
                    </a>
                  )}
                  {data.whatsapp_url && (
                    <a href={data.whatsapp_url} target="_blank" rel="noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-[12px] font-semibold text-white transition hover:bg-emerald-700">
                      <MessageCircle size={12} /> WhatsApp customer
                    </a>
                  )}
                </div>
              </div>
            </section>

            <section>
              <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Items</div>
              <div className="overflow-hidden rounded-xl border">
                <table className="w-full text-[13px]">
                  <thead className="bg-secondary/50 text-[11px] uppercase text-muted-foreground">
                    <tr>
                      <th className="px-2.5 py-1.5 text-left">Item</th>
                      <th className="px-2.5 py-1.5 text-right">Qty</th>
                      <th className="px-2.5 py-1.5 text-right">Unit</th>
                      <th className="px-2.5 py-1.5 text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.lines || []).map((l, i) => (
                      <tr key={i} className="border-t align-top">
                        <td className="px-2.5 py-1.5">
                          <div className="font-medium">{l.item_code}</div>
                          {l.display_name && l.display_name !== l.item_code && (
                            <div className="text-[11px] text-muted-foreground">{l.display_name}</div>
                          )}
                          <div className="mt-1 flex flex-wrap items-center gap-1.5">
                            {l.stock_status && <StockPill status={l.stock_status} />}
                            {l.backorder && <span className="text-[10px] font-semibold uppercase text-amber-600">Backorder</span>}
                          </div>
                        </td>
                        <td className="px-2.5 py-1.5 text-right tabular-nums">{l.qty}</td>
                        <td className="px-2.5 py-1.5 text-right tabular-nums">{bhd(l.unit_price_bhd, 3)}</td>
                        <td className="px-2.5 py-1.5 text-right tabular-nums font-medium">{bhd(l.line_total_bhd, 3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="space-y-1 rounded-xl border p-3 text-sm">
              <Row label="Subtotal" value={bhd(data.subtotal_bhd, 3)} />
              {!!data.discount_bhd && <Row label="Discount" value={`− ${bhd(data.discount_bhd, 3)}`} />}
              <Row label="Delivery" value={bhd(data.delivery_bhd, 3)} />
              {coupon && <Row label="Coupon" value={coupon} />}
              <div className="mt-1 flex items-baseline justify-between border-t pt-1.5 text-base font-bold">
                <span>Total</span><span className="tabular-nums text-primary">{bhd(data.total_bhd, 3)}</span>
              </div>
            </section>

            {data.note && (
              <section>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Note</div>
                <p className="rounded-xl border bg-secondary/30 p-3 text-sm">{data.note}</p>
              </section>
            )}

            {!!data.events?.length && (
              <section>
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Timeline</div>
                <ul className="space-y-2">
                  {data.events.map((e, i) => (
                    <li key={i} className="flex gap-2 text-[12px]">
                      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                      <div>
                        <div className="font-medium">{eventLabel(e.event)}</div>
                        <div className="text-muted-foreground">{fmtDateTime(e.ts)}{e.note ? ` · ${e.note}` : ''}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {data.notify_result && (
              <section>
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Notifications</div>
                <NotifyResult data={data.notify_result} />
              </section>
            )}
          </div>
        )}

        {data && actions.length > 0 && (
          <div className="sticky bottom-0 space-y-2 border-t bg-card p-4">
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional note…" />
            <div className="flex flex-wrap gap-2">
              {actions.map((a) => (
                <Button key={a} size="sm" variant={a === 'cancelled' ? 'destructive' : 'default'}
                  onClick={() => setStatus(a)} disabled={busy !== null}>
                  {busy === a ? <Loader2 className="animate-spin" size={14} /> : <Check size={14} />}
                  {a === 'cancelled' ? 'Cancel order' : `Mark ${a}`}
                </Button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function DeskOrders({
  meData, rows, isLoading, companyKpis, needsCompanyKpi, status, setStatus, qRaw, setQRaw, onRefresh,
}: {
  meData?: ShopMe
  rows: ShopOrderRow[]
  isLoading: boolean
  companyKpis: { orders_7d: number; orders_30d: number; value_30d_bhd: number; customers_30d: number } | null
  needsCompanyKpi: boolean
  status: StatusFilter
  setStatus: (s: StatusFilter) => void
  qRaw: string
  setQRaw: (v: string) => void
  onRefresh: () => void
}) {
  const [openId, setOpenId] = useState<number | null>(null)
  const openRow = (r: ShopOrderRow) => setOpenId(r.id)

  const cols: Column<ShopOrderRow>[] = [
    { key: 'order_no', label: 'Order', render: (_, r) => (
        <button type="button" onClick={() => openRow(r)} className="font-semibold text-foreground hover:text-primary hover:underline">
          {r.order_no}
        </button>
      ) },
    { key: 'created_at', label: 'Created', render: (_, r) => fmtDateTime(r.created_at) },
    { key: 'customer_name', label: 'Customer', render: (_, r) => (
        <div>
          <div className="font-medium">{r.customer_name || '—'}</div>
          {r.customer_shop && <div className="text-[11px] text-muted-foreground">{r.customer_shop}</div>}
        </div>
      ) },
    { key: 'customer_area', label: 'Area', render: (_, r) => r.customer_area || '—' },
    { key: 'salesman_name', label: 'Salesman', render: (_, r) => r.salesman_name || '—' },
    { key: 'items_count', label: 'Items / Units', align: 'right', render: (_, r) => `${num(r.items_count)} / ${num(r.units_count)}` },
    { key: 'total_bhd', label: 'Total', align: 'right', render: (_, r) => bhd(r.total_bhd, 3) },
    { key: 'status', label: 'Status', render: (_, r) => <StatusPill status={r.status} /> },
    { key: 'has_backorder', label: 'Backorder', render: (_, r) => (
        r.has_backorder
          ? <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-600"><PackageX size={12} /> Backorder</span>
          : null
      ) },
    { key: 'id', label: '', align: 'right', render: (_, r) => (
        <button type="button" onClick={() => openRow(r)}
          className="inline-flex items-center gap-0.5 text-[12px] font-medium text-primary hover:underline">
          View <ChevronRight size={14} />
        </button>
      ) },
  ]

  return (
    <div>
      <PageHeader title="Shop Orders" subtitle="Orders placed from your shared catalog link" />

      <MyLinkCard me={meData} isAdmin companyKpis={needsCompanyKpi ? companyKpis : null} />

      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {(['all', ...STATUSES] as StatusFilter[]).map((s) => (
            <button key={s} onClick={() => setStatus(s)}
              className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium capitalize transition duration-150 motion-reduce:transition-none',
                status === s ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
              {s}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2 rounded-lg border bg-card px-3 shadow-sm focus-within:border-primary/40">
          <Search size={15} className="text-muted-foreground" />
          <input value={qRaw} onChange={(e) => setQRaw(e.target.value)} placeholder="Search order no, customer, shop, phone…"
            className="h-9 w-64 bg-transparent text-sm outline-none" />
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : (
        <DataTable rows={rows} cols={cols} searchable={false} exportName="yq-shop-orders"
          empty="No orders yet — customers order from your shared catalog link (see “My link” above) and orders will appear here automatically." />
      )}

      {openId != null && (
        <OrderDrawer id={openId} onClose={() => setOpenId(null)} onChanged={onRefresh} />
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// Field view (phone) — one thumb, a shop owner waiting, no table in sight.
// ══════════════════════════════════════════════════════════════════════════════

const INK = 'text-[#1a1430]'
const MUTED = 'text-[#6b6480]'
const HAIRLINE = 'border-[#ece9f3]'

function FieldLinkCard({ me, open, onToggle }: { me?: ShopMe; open: boolean; onToggle: () => void }) {
  const toast = useToast()
  const salesman = me?.salesman ?? null
  const link = me?.link || ''
  // Only pay for the QR once the card is actually opened.
  const { blobUrl: qrUrl } = useAuthedBlob(salesman && open ? me?.qr_url : null)
  const kpis = me?.kpis
  const focus = me?.focus
  const pct = focus?.target_bhd ? Math.min(100, Math.round(((focus.revenue_90d_bhd || 0) / focus.target_bhd) * 100)) : null

  if (!salesman) {
    return (
      <div className={cn('rounded-[20px] border bg-white p-4', HAIRLINE)}>
        <div className={cn('font-display text-[14px] font-bold', INK)}>No personal link yet</div>
        <p className={cn('mt-1 text-[12.5px] leading-snug', MUTED)}>
          Ask an admin to link your login on the Salesmen page — your own catalog link and QR code then appear here.
        </p>
      </div>
    )
  }

  return (
    <div className={cn('overflow-hidden rounded-[20px] border bg-white', HAIRLINE)}>
      <div className="flex items-center gap-1 pr-2">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2.5 px-4 py-3.5 text-left"
        >
          <Link2 size={17} className="shrink-0 text-[#6d28d9]" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className={cn('block font-display text-[14px] font-bold leading-tight', INK)}>My link</span>
            <span className={cn('block truncate text-[11.5px]', MUTED)}>{link.replace(/^https?:\/\//, '')}</span>
          </span>
          <ChevronDown
            size={18}
            aria-hidden="true"
            className={cn('shrink-0 transition-transform duration-200 motion-reduce:transition-none', MUTED, open && 'rotate-180')}
          />
        </button>
        <button
          type="button"
          onClick={() => { if (link) { navigator.clipboard?.writeText(link); toast('Link copied.', 'success') } }}
          className={cn('grid h-11 w-11 shrink-0 place-items-center rounded-xl transition-colors duration-150 hover:bg-[#f4f2f9] motion-reduce:transition-none', MUTED)}
          aria-label="Copy my link"
        >
          <Copy size={17} />
        </button>
      </div>

      {open && (
        <div className={cn('border-t px-4 pb-4 pt-3.5', HAIRLINE)}>
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1 space-y-2">
              <a
                href={link ? `https://wa.me/?text=${encodeURIComponent(link)}` : undefined}
                target="_blank"
                rel="noreferrer"
                className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#25d366] text-[13.5px] font-semibold text-[#08331b] transition-opacity duration-150 hover:opacity-90 motion-reduce:transition-none"
              >
                <MessageCircle size={16} aria-hidden="true" /> Share on WhatsApp
              </a>
              <a
                href={link || undefined}
                target="_blank"
                rel="noreferrer"
                className={cn('flex h-11 w-full items-center justify-center gap-2 rounded-xl border text-[13.5px] font-semibold transition-colors duration-150 hover:bg-[#faf9fc] motion-reduce:transition-none', HAIRLINE, INK)}
              >
                <ExternalLink size={16} aria-hidden="true" /> Open my catalog
              </a>
            </div>
            {qrUrl && (
              <img src={qrUrl} alt="My referral QR code" className={cn('h-[5.5rem] w-[5.5rem] shrink-0 rounded-xl border bg-white p-1.5', HAIRLINE)} />
            )}
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-2">
            {[
              { k: 'Orders · 7 days', v: num(kpis?.orders_7d) },
              { k: 'Orders · 30 days', v: num(kpis?.orders_30d) },
              { k: 'Value · 30 days', v: bhd(kpis?.value_30d_bhd, 3) },
              { k: 'Customers · 30 days', v: num(kpis?.customers_30d) },
            ].map((s) => (
              <div key={s.k} className="rounded-xl bg-[#faf9fc] px-3 py-2.5">
                <dt className={cn('text-[10.5px] font-semibold uppercase tracking-wide', MUTED)}>{s.k}</dt>
                <dd className={cn('mt-0.5 font-display text-[15px] font-bold tabular-nums', INK)}>{s.v}</dd>
              </div>
            ))}
          </dl>

          {focus && (
            <div className={cn('mt-3.5 border-t pt-3', HAIRLINE)}>
              <div className={cn('mb-1.5 flex items-baseline justify-between gap-2 text-[11.5px]', MUTED)}>
                <span>90-day revenue vs target</span>
                <span className="tabular-nums">{pct != null ? `${pct}%` : '—'}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-[#ece9f3]">
                <div className="h-full rounded-full bg-[#6d28d9]" style={{ width: `${pct ?? 0}%` }} />
              </div>
              <div className={cn('mt-1.5 text-[11.5px] tabular-nums', MUTED)}>
                {bhd(focus.revenue_90d_bhd, 3)} of {bhd(focus.target_bhd, 3)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function OrderCard({ row, onOpen }: { row: ShopOrderRow; onOpen: () => void }) {
  const title = row.customer_shop || row.customer_name || 'Order'
  const sub = [row.customer_shop ? row.customer_name : null, row.customer_area].filter(Boolean).join(' · ')
  const age = relTime(row.created_at)
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        'w-full rounded-[20px] border bg-white p-4 text-left transition-colors duration-150 hover:border-[#d9d3ea]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] motion-reduce:transition-none',
        HAIRLINE,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className={cn('truncate font-display text-[15px] font-bold leading-tight', INK)}>{title}</div>
          {sub && <div className={cn('mt-0.5 truncate text-[12px]', MUTED)}>{sub}</div>}
        </div>
        <div className="shrink-0 text-right">
          <div className={cn('font-display text-[15px] font-bold tabular-nums', INK)}>{bhd(row.total_bhd, 3)}</div>
          <div className={cn('mt-0.5 text-[11px] tabular-nums', MUTED)}>
            {num(row.items_count)} items · {num(row.units_count)} units
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <StatusPill status={row.status} />
        {row.source === 'salesman' && <Badge tone="accent">You placed</Badge>}
        {row.has_backorder && <Badge tone="amber">Backorder</Badge>}
        <span className={cn('ml-auto shrink-0 text-[11px]', MUTED)}>
          {row.order_no}{age ? ` · ${age}` : ''}
        </span>
      </div>
    </button>
  )
}

function FieldOrderSheet({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['shop-order', id], queryFn: () => apiGet<OrderDetail>(`/shop/orders/${id}`) })
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)

  async function setStatus(next: string) {
    setBusy(next)
    try {
      await apiPost(`/shop/orders/${id}/status`, { status: next })
      toast(next === 'cancelled' ? 'Order cancelled.' : `Order marked ${next}.`, 'success')
      setConfirmCancel(false)
      qc.invalidateQueries({ queryKey: ['shop-order', id] })
      onChanged()
    } catch (e) {
      toast(e instanceof ApiError ? e.body.slice(0, 160) : 'Could not update the order.', 'error')
    } finally {
      setBusy(null)
    }
  }

  const allowed = data ? (data.next_statuses?.length ? data.next_statuses : nextStatuses(data.status)) : []
  const forward = allowed.find((s) => s !== 'cancelled') || null
  const canCancel = allowed.includes('cancelled')
  const coupon = data?.coupon_code || data?.coupon?.code || null

  const footer = !data ? null : (
    <div className="space-y-2">
      {forward && (
        <button
          type="button"
          onClick={() => setStatus(forward)}
          disabled={busy !== null}
          className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#6d28d9] text-[15px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 disabled:opacity-60 motion-reduce:transition-none"
        >
          {busy === forward ? <Loader2 className="animate-spin" size={17} /> : <Check size={17} aria-hidden="true" />}
          Mark {forward}
        </button>
      )}
      {canCancel && !confirmCancel && (
        <button
          type="button"
          onClick={() => setConfirmCancel(true)}
          disabled={busy !== null}
          className={cn('h-11 w-full rounded-xl text-[13px] font-semibold transition-colors duration-150 hover:bg-[#f4f2f9] motion-reduce:transition-none', MUTED)}
        >
          Cancel this order
        </button>
      )}
      {canCancel && confirmCancel && (
        <div className={cn('rounded-xl border p-3', HAIRLINE)}>
          <p className={cn('text-[12.5px] leading-snug', INK)}>Cancel {data.order_no}? The customer sees it as cancelled.</p>
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              onClick={() => setConfirmCancel(false)}
              className={cn('h-11 flex-1 rounded-xl border text-[13px] font-semibold transition-colors duration-150 hover:bg-[#faf9fc] motion-reduce:transition-none', HAIRLINE, INK)}
            >
              Keep it
            </button>
            <button
              type="button"
              onClick={() => setStatus('cancelled')}
              disabled={busy !== null}
              className="flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl bg-[#9f1239] text-[13px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 disabled:opacity-60 motion-reduce:transition-none"
            >
              {busy === 'cancelled' ? <Loader2 className="animate-spin" size={15} /> : null}
              Yes, cancel
            </button>
          </div>
        </div>
      )}
      {!forward && !canCancel && (
        <p className={cn('py-1 text-center text-[12.5px]', MUTED)}>This order is closed — nothing left to do.</p>
      )}
    </div>
  )

  return (
    <Sheet
      open
      onClose={onClose}
      title={data?.order_no || 'Order'}
      subtitle={data ? [data.customer_shop || data.customer_name, relTime(data.created_at)].filter(Boolean).join(' · ') : undefined}
      footer={footer}
    >
      {isLoading || !data ? (
        <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div>
      ) : (
        <div className="space-y-4 p-4">
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusPill status={data.status} />
            {data.source === 'salesman' && <Badge tone="accent">You placed</Badge>}
            {data.has_backorder && <Badge tone="amber">Backorder</Badge>}
          </div>

          <section className={cn('rounded-xl border p-3.5', HAIRLINE)}>
            <div className={cn('font-display text-[14px] font-bold leading-tight', INK)}>
              {data.customer_shop || data.customer_name || '—'}
            </div>
            <div className={cn('mt-0.5 text-[12px]', MUTED)}>
              {[data.customer_shop ? data.customer_name : null, data.customer_area].filter(Boolean).join(' · ') || '—'}
            </div>
            <div className="mt-3 flex gap-2">
              {data.customer_phone && (
                <a
                  href={`tel:${data.customer_phone}`}
                  className={cn('flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl border text-[13px] font-semibold transition-colors duration-150 hover:bg-[#faf9fc] motion-reduce:transition-none', HAIRLINE, INK)}
                >
                  <Phone size={15} aria-hidden="true" /> Call
                </a>
              )}
              {data.whatsapp_url && (
                <a
                  href={data.whatsapp_url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl bg-[#25d366] text-[13px] font-semibold text-[#08331b] transition-opacity duration-150 hover:opacity-90 motion-reduce:transition-none"
                >
                  <MessageCircle size={15} aria-hidden="true" /> WhatsApp
                </a>
              )}
            </div>
          </section>

          <section>
            <h3 className={cn('mb-1.5 text-[10.5px] font-semibold uppercase tracking-wide', MUTED)}>Items</h3>
            <ul className={cn('divide-y overflow-hidden rounded-xl border', HAIRLINE)}>
              {(data.lines || []).map((l, i) => (
                <li key={i} className="flex items-start justify-between gap-3 p-3">
                  <div className="min-w-0">
                    <div className={cn('text-[13px] font-semibold', INK)}>{l.item_code}</div>
                    {l.display_name && l.display_name !== l.item_code && (
                      <div className={cn('truncate text-[11.5px]', MUTED)}>{l.display_name}</div>
                    )}
                    <div className={cn('mt-0.5 text-[11.5px] tabular-nums', MUTED)}>
                      {l.qty} × {bhd(l.unit_price_bhd, 3)}
                    </div>
                    {(l.stock_status || l.backorder) && (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {l.stock_status && <StockPill status={l.stock_status} />}
                        {l.backorder && <Badge tone="amber">Backorder</Badge>}
                      </div>
                    )}
                  </div>
                  <div className={cn('shrink-0 font-display text-[13.5px] font-bold tabular-nums', INK)}>
                    {bhd(l.line_total_bhd, 3)}
                  </div>
                </li>
              ))}
            </ul>
          </section>

          <section className={cn('space-y-1.5 rounded-xl border p-3.5 text-[12.5px]', HAIRLINE)}>
            <div className="flex items-baseline justify-between">
              <span className={MUTED}>Subtotal</span>
              <span className={cn('font-medium tabular-nums', INK)}>{bhd(data.subtotal_bhd, 3)}</span>
            </div>
            {!!data.discount_bhd && (
              <div className="flex items-baseline justify-between">
                <span className={MUTED}>Discount</span>
                <span className="font-medium tabular-nums text-[#137a48]">− {bhd(data.discount_bhd, 3)}</span>
              </div>
            )}
            <div className="flex items-baseline justify-between">
              <span className={MUTED}>Delivery</span>
              <span className={cn('font-medium tabular-nums', INK)}>{bhd(data.delivery_bhd, 3)}</span>
            </div>
            {coupon && (
              <div className="flex items-baseline justify-between">
                <span className={MUTED}>Coupon</span>
                <span className={cn('font-medium', INK)}>{coupon}</span>
              </div>
            )}
            <div className={cn('mt-1 flex items-baseline justify-between border-t pt-2', HAIRLINE)}>
              <span className={cn('font-display text-[14px] font-bold', INK)}>Total</span>
              <span className={cn('font-display text-[17px] font-bold tabular-nums', INK)}>{bhd(data.total_bhd, 3)}</span>
            </div>
          </section>

          {data.note && (
            <section>
              <h3 className={cn('mb-1.5 text-[10.5px] font-semibold uppercase tracking-wide', MUTED)}>Note</h3>
              <p className={cn('rounded-xl bg-[#faf9fc] p-3 text-[12.5px] leading-snug', INK)}>{data.note}</p>
            </section>
          )}

          {!!data.events?.length && (
            <section>
              <h3 className={cn('mb-1.5 text-[10.5px] font-semibold uppercase tracking-wide', MUTED)}>Timeline</h3>
              <ul className="space-y-2.5">
                {data.events.map((e, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="mt-[0.4rem] h-1.5 w-1.5 shrink-0 rounded-full bg-[#6d28d9]" aria-hidden="true" />
                    <div className="min-w-0">
                      <div className={cn('text-[12.5px] font-semibold', INK)}>{eventLabel(e.event)}</div>
                      <div className={cn('text-[11.5px]', MUTED)}>{fmtDateTime(e.ts)}{e.note ? ` · ${e.note}` : ''}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </Sheet>
  )
}

function FieldOrders({
  meData, rows, isLoading, isError, counts, bucket, setBucket, qRaw, setQRaw, onRefresh,
}: {
  meData?: ShopMe
  rows: ShopOrderRow[]
  isLoading: boolean
  isError: boolean
  counts: StatusCounts
  bucket: BucketKey
  setBucket: (b: BucketKey) => void
  qRaw: string
  setQRaw: (v: string) => void
  onRefresh: () => void
}) {
  const [linkOpen, setLinkOpen] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)
  const name = meData?.salesman?.name

  return (
    <div className="mx-auto max-w-2xl space-y-4 px-4 py-4">
      <header>
        <h1 className={cn('font-display text-[22px] font-bold leading-tight tracking-tight', INK)}>Orders</h1>
        <p className={cn('mt-0.5 text-[12.5px]', MUTED)}>
          {name ? `${name} · everything from your link` : 'Everything placed through your link'}
        </p>
      </header>

      <FieldLinkCard me={meData} open={linkOpen} onToggle={() => setLinkOpen((o) => !o)} />

      <div className={cn('flex h-12 items-center gap-2 rounded-2xl border bg-white px-3.5 focus-within:border-[#6d28d9]', HAIRLINE)}>
        <Search size={16} className={MUTED} aria-hidden="true" />
        <input
          value={qRaw}
          onChange={(e) => setQRaw(e.target.value)}
          placeholder="Search order, shop, phone…"
          aria-label="Search orders"
          className={cn('h-full w-full bg-transparent text-[14px] outline-none placeholder:text-[#9a93ad]', INK)}
        />
        {qRaw && (
          <button type="button" onClick={() => setQRaw('')} aria-label="Clear search" className={cn('shrink-0 p-1', MUTED)}>
            <X size={15} />
          </button>
        )}
      </div>

      <div role="tablist" aria-label="Order stage" className={cn('flex gap-1 rounded-2xl border bg-white p-1', HAIRLINE)}>
        {BUCKETS.map((b) => {
          const on = bucket === b.key
          const n = b.of(counts)
          return (
            <button
              key={b.key}
              role="tab"
              aria-selected={on}
              type="button"
              onClick={() => setBucket(b.key)}
              className={cn(
                'flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl text-[13px] font-semibold transition-colors duration-150 motion-reduce:transition-none',
                on ? 'bg-[#6d28d9] text-white' : cn(MUTED, 'hover:bg-[#faf9fc]'),
              )}
            >
              {b.label}
              {n != null && <span className={cn('tabular-nums', on ? 'text-white/70' : 'text-[#9a93ad]')}>{n}</span>}
            </button>
          )
        })}
      </div>

      {isLoading ? (
        <div className="space-y-2.5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-[6.5rem] rounded-[20px]" />)}</div>
      ) : isError ? (
        <div className={cn('rounded-[20px] border bg-white px-4 py-10 text-center', HAIRLINE)}>
          <p className={cn('font-display text-[15px] font-bold', INK)}>Couldn't load your orders</p>
          <p className={cn('mt-1 text-[12.5px]', MUTED)}>You're offline, or the server is still waking up.</p>
          <button
            type="button"
            onClick={onRefresh}
            className="mt-4 h-11 rounded-xl bg-[#6d28d9] px-5 text-[13.5px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 motion-reduce:transition-none"
          >
            Try again
          </button>
        </div>
      ) : rows.length === 0 ? (
        <div className={cn('rounded-[20px] border bg-white px-4 py-10 text-center', HAIRLINE)}>
          {bucket === 'new' && !qRaw ? (
            <>
              <p className={cn('font-display text-[15px] font-bold', INK)}>No new orders yet — share your link</p>
              <p className={cn('mx-auto mt-1 max-w-[22rem] text-[12.5px] leading-snug', MUTED)}>
                Every order a shop places from your link lands here the moment they send it.
              </p>
              <button
                type="button"
                onClick={() => setLinkOpen(true)}
                className="mt-4 h-11 rounded-xl bg-[#6d28d9] px-5 text-[13.5px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 motion-reduce:transition-none"
              >
                Open my link
              </button>
            </>
          ) : (
            <>
              <p className={cn('font-display text-[15px] font-bold', INK)}>
                {qRaw ? 'Nothing matches that search' : 'Nothing here yet'}
              </p>
              <p className={cn('mt-1 text-[12.5px]', MUTED)}>
                {qRaw ? 'Try the order number, the shop name or a phone number.' : 'Orders arrive in this stage as you work through them.'}
              </p>
            </>
          )}
        </div>
      ) : (
        <ul className="space-y-2.5">
          {rows.map((r) => (
            <li key={r.id}>
              <OrderCard row={r} onOpen={() => setOpenId(r.id)} />
            </li>
          ))}
        </ul>
      )}

      {openId != null && (
        <FieldOrderSheet id={openId} onClose={() => setOpenId(null)} onChanged={onRefresh} />
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════

export default function ShopOrders() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const isAdmin = me?.role === 'admin'
  const [status, setStatus] = useState<StatusFilter>('all')
  const [bucket, setBucket] = useState<BucketKey>('new')
  const [qRaw, setQRaw] = useState('')
  const [q, setQ] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setQ(qRaw.trim()), 300)
    return () => clearTimeout(t)
  }, [qRaw])

  const meQuery = useQuery({ queryKey: ['shop-me'], queryFn: () => apiGet<ShopMe>('/shop/me') })

  // The desk filters by one status, the field by a bucket of statuses (the API takes a
  // comma list) — both collapse to a single `status` query param.
  const statusParam = isAdmin
    ? (status === 'all' ? '' : status)
    : (BUCKETS.find((b) => b.key === bucket)?.param ?? 'new')

  const ordersQuery = useQuery({
    queryKey: ['shop-orders', statusParam, q],
    queryFn: () => {
      const params = new URLSearchParams({ limit: '100' })
      if (statusParam) params.set('status', statusParam)
      if (q) params.set('q', q)
      return apiGet<ShopOrdersResp>(`/shop/orders?${params.toString()}`)
    },
  })

  const needsCompanyKpi = !!isAdmin && !!meQuery.data && meQuery.data.salesman == null
  const companyKpiQuery = useQuery({
    queryKey: ['shop-orders-kpi-fallback'],
    queryFn: () => apiGet<ShopOrdersResp>('/shop/orders?limit=200'),
    enabled: needsCompanyKpi,
  })
  const companyKpis = useMemo(() => {
    if (!companyKpiQuery.data) return null
    const rows = companyKpiQuery.data.orders || []
    // Use the query's own fetch timestamp rather than Date.now() — a pure function of
    // already-committed state, so this useMemo stays a pure computation during render.
    const now = companyKpiQuery.dataUpdatedAt
    const within = (iso: string | null | undefined, days: number) => (iso ? now - new Date(iso).getTime() <= days * 864e5 : false)
    const r7 = rows.filter((r) => within(r.created_at, 7))
    const r30 = rows.filter((r) => within(r.created_at, 30))
    const custKey = (r: ShopOrderRow) => r.customer_phone || `${r.customer_name || ''}|${r.customer_shop || ''}`
    return {
      orders_7d: r7.length,
      orders_30d: r30.length,
      value_30d_bhd: r30.reduce((s, r) => s + Number(r.total_bhd || 0), 0),
      customers_30d: new Set(r30.map(custKey)).size,
    }
  }, [companyKpiQuery.data, companyKpiQuery.dataUpdatedAt])

  const rows = ordersQuery.data?.orders || []
  const refreshList = () => {
    qc.invalidateQueries({ queryKey: ['shop-orders'] })
    qc.invalidateQueries({ queryKey: ['shop-orders-new-count'] })
    qc.invalidateQueries({ queryKey: ['shop-orders-kpi-fallback'] })
  }

  if (!isAdmin) {
    return (
      <FieldOrders
        meData={meQuery.data}
        rows={rows}
        isLoading={ordersQuery.isLoading}
        isError={ordersQuery.isError}
        counts={ordersQuery.data?.counts || {}}
        bucket={bucket}
        setBucket={setBucket}
        qRaw={qRaw}
        setQRaw={setQRaw}
        onRefresh={refreshList}
      />
    )
  }

  return (
    <DeskOrders
      meData={meQuery.data}
      rows={rows}
      isLoading={ordersQuery.isLoading}
      companyKpis={companyKpis}
      needsCompanyKpi={needsCompanyKpi}
      status={status}
      setStatus={setStatus}
      qRaw={qRaw}
      setQRaw={setQRaw}
      onRefresh={refreshList}
    />
  )
}
