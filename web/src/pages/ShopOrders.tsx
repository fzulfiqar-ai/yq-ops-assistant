import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Search, Copy, ExternalLink, Link2, MessageCircle, X, Check, Loader2,
  Phone, Mail, PackageX, ChevronRight,
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
}
interface ShopOrdersResp { orders: ShopOrderRow[]; count: number }

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

const STATUS_STYLE: Record<string, string> = {
  new: 'bg-violet-100 text-violet-700',
  confirmed: 'bg-blue-100 text-blue-700',
  packed: 'bg-amber-100 text-amber-700',
  delivered: 'bg-emerald-100 text-emerald-700',
  cancelled: 'bg-rose-100 text-rose-700',
}
function StatusPill({ status }: { status: string }) {
  return (
    <span className={cn('inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase', STATUS_STYLE[status] || 'bg-secondary text-muted-foreground')}>
      {status}
    </span>
  )
}

const STOCK_LABEL: Record<string, string> = { in_stock: 'In stock', low_stock: 'Only a few left', out_of_stock: 'Out of stock' }
const STOCK_STYLE: Record<string, string> = {
  in_stock: 'bg-emerald-100 text-emerald-700',
  low_stock: 'bg-amber-100 text-amber-700',
  out_of_stock: 'bg-rose-100 text-rose-700',
}
function StockPill({ status }: { status: string }) {
  return (
    <span className={cn('inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase', STOCK_STYLE[status] || 'bg-secondary text-muted-foreground')}>
      {STOCK_LABEL[status] || status}
    </span>
  )
}

function fmtDateTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return String(iso)
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

  const actions = data ? nextStatuses(data.status) : []
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

export default function ShopOrders() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const isAdmin = me?.role === 'admin'
  const [status, setStatus] = useState<StatusFilter>('all')
  const [qRaw, setQRaw] = useState('')
  const [q, setQ] = useState('')
  const [openId, setOpenId] = useState<number | null>(null)

  useEffect(() => {
    const t = setTimeout(() => setQ(qRaw.trim()), 300)
    return () => clearTimeout(t)
  }, [qRaw])

  const meQuery = useQuery({ queryKey: ['shop-me'], queryFn: () => apiGet<ShopMe>('/shop/me') })

  const ordersQuery = useQuery({
    queryKey: ['shop-orders', status, q],
    queryFn: () => {
      const params = new URLSearchParams({ limit: '100' })
      if (status !== 'all') params.set('status', status)
      if (q) params.set('q', q)
      return apiGet<ShopOrdersResp>(`/shop/orders?${params.toString()}`)
    },
  })

  const needsCompanyKpi = !!meQuery.data && meQuery.data.salesman == null
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
  const openRow = (r: ShopOrderRow) => setOpenId(r.id)
  const refreshList = () => {
    qc.invalidateQueries({ queryKey: ['shop-orders'] })
    qc.invalidateQueries({ queryKey: ['shop-orders-kpi-fallback'] })
  }

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

      <MyLinkCard me={meQuery.data} isAdmin={!!isAdmin} companyKpis={needsCompanyKpi ? companyKpis : null} />

      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {(['all', ...STATUSES] as StatusFilter[]).map((s) => (
            <button key={s} onClick={() => setStatus(s)}
              className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium capitalize transition',
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

      {ordersQuery.isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : (
        <DataTable rows={rows} cols={cols} searchable={false} exportName="yq-shop-orders"
          empty="No orders yet — customers order from your shared catalog link (see “My link” above) and orders will appear here automatically." />
      )}

      {openId != null && (
        <OrderDrawer id={openId} onClose={() => setOpenId(null)} onChanged={refreshList} />
      )}
    </div>
  )
}
