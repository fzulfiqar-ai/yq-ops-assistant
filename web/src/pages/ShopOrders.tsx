import { useEffect, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
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
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import { DataTable, Stat, type Column } from '@/components/DataTable'
import {
  ACTION_LABEL, AssignBox, AssignmentQueue, ConfirmEditor, CustomerWhatsApp, STATUS_LABEL,
  STATUS_TONE as MARKET_STATUS_TONE,
  CancelReasonPicker, FocusExceptionsPanel, ReturnBox,
} from '@/pages/shop-ops/OrderActions'
import {
  apiDetail, attributionLabel, cancelReady, cancelReasonLabel, minimumGapText, PAYMENT_LABEL,
  shopRepLine, useFocusCandidates,
} from '@/pages/shop-ops/pipeline'

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
  order_kind?: 'standard' | 'small' | null
  minimum_gap_bhd?: number | null
  created_at?: string | null
  source?: string | null
  referral_code?: string | null
  placed_by?: string | null
  // marketplace (16-Sep-2026)
  salesman_id?: number | null
  customer_id?: number | null
  attribution_source?: string | null
  attribution_conflict?: boolean
  expected_delivery?: string | null
  total_confirmed_bhd?: number | null
  // 24-Sep-2026: list rows carry two small flags computed server-side (shop.list_orders) for the
  // "Not notified" badge; the full notify_result (channel by channel) comes with the detail only.
  notify_failed?: boolean
  notify_attempts?: number
  notify_result?: Record<string, unknown> | null
  // R3 pipeline (24-Sep-2026): payment is a recorded fact, returns are events, cancels carry a reason
  payment_status?: string | null
  focus_invoice_no?: string | null
  cancel_reason?: string | null
  cancel_reason_code?: string | null
  returned_bhd?: number | null
}
type StatusCounts = Partial<Record<'new' | 'confirmed' | 'packed' | 'out_for_delivery' | 'delivered' | 'cancelled', number>>
// min_order_bhd also arrives on the list; it is not read here — a stored gap is never paired with today's minimum (pipeline.ts)
interface ShopOrdersResp { orders: ShopOrderRow[]; count: number; counts?: StatusCounts }

interface OrderLine {
  id: number
  item_code: string
  display_name?: string | null
  qty: number
  qty_confirmed?: number | null
  line_status?: string | null
  unit_price_bhd?: number | null
  unit_price_confirmed?: number | null
  line_total_bhd?: number | null
  stock_status?: 'in_stock' | 'low_stock' | 'out_of_stock' | null
  backorder?: boolean
}
interface OrderEvent { ts: string; event: string; note?: string | null; detail?: Record<string, unknown> | null }
/** R3: the shop's recorded rep (admin assignment, else the settled first-touch rep) — admins only. */
interface OrderCustomerRep {
  id: number
  shop?: string | null
  salesman_id?: number | null
  salesman_name?: string | null
  sticky_salesman_id?: number | null
  sticky_name?: string | null
  first_ref?: string | null
}
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
  whatsapp_url?: string | null
  next_statuses?: string[] | null
  status_url?: string | null
  payment_label?: string | null
  payment_method?: string | null
  customer?: OrderCustomerRep | null
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
  focus?: { revenue_90d_bhd?: number } | null
}

const STATUSES = ['new', 'confirmed', 'packed', 'out_for_delivery', 'delivered', 'cancelled'] as const
// 'needs_action' is a computed view (item 4, R7a), not a real status: it maps to no `status`
// filter on the API call and is client-filtered afterwards (see DeskOrders).
type StatusFilter = 'all' | 'unassigned' | 'needs_action' | (typeof STATUSES)[number]

/**
 * Three buckets, not six statuses. Standing in a shop the only questions are
 * "what is waiting for me", "what am I working on", "what is finished".
 */
const BUCKETS = [
  { key: 'new', label: 'New', param: 'new', of: (c: StatusCounts) => c.new },
  { key: 'progress', label: 'In progress', param: 'confirmed,packed,out_for_delivery', of: (c: StatusCounts) => (c.confirmed ?? 0) + (c.packed ?? 0) + (c.out_for_delivery ?? 0) },
  { key: 'done', label: 'Done', param: 'delivered,cancelled', of: (c: StatusCounts) => (c.delivered ?? 0) + (c.cancelled ?? 0) },
] as const
type BucketKey = (typeof BUCKETS)[number]['key']

const STATUS_TONE: Record<string, BadgeTone> = MARKET_STATUS_TONE
/** The merchant's words for each stage (Received / Confirmed / Preparing / On the way / Delivered). */
function StatusPill({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] || 'grey'}>{STATUS_LABEL[status] || status}</Badge>
}
function actionLabel(s: string): string {
  return ACTION_LABEL[s] || `Mark ${STATUS_LABEL[s] || s}`
}

const STOCK_LABEL: Record<string, string> = { in_stock: 'In stock', low_stock: 'Only a few left', out_of_stock: 'Sold out' }
const STOCK_TONE: Record<string, BadgeTone> = { in_stock: 'green', low_stock: 'amber', out_of_stock: 'rose' }
function StockPill({ status }: { status: string }) {
  return <Badge tone={STOCK_TONE[status] || 'grey'}>{STOCK_LABEL[status] || status}</Badge>
}

/** The R3 Payment column/pills, the PaymentBox and the free-text Focus invoice inputs are gone
 *  (item 6, R7a) — this is the one thing left to say about Focus on an order: it already has a
 *  linked invoice, from either the old manual entry or a Focus-exceptions Accept. */
function InvoicedBadge({ invoiceNo }: { invoiceNo?: string | null }) {
  if (!invoiceNo) return null
  return <Badge tone="green">Invoiced in Focus: {invoiceNo}</Badge>
}

/** "2d" / "3h" / "45m" — compact enough to sit inside a filter chip. */
function ageCompact(mins: number): string {
  if (mins < 60) return `${Math.max(0, Math.round(mins))}m`
  const hours = mins / 60
  if (hours < 24) return `${Math.round(hours)}h`
  return `${Math.round(hours / 24)}d`
}

/** A Received order past the SLA setting — the same lateness the "Needs action" chip counts. */
function isLateReceived(r: { status: string; created_at?: string | null }, slaMin: number): boolean {
  if (r.status !== 'new' || !r.created_at) return false
  const t = Date.parse(r.created_at)
  return Number.isFinite(t) && (Date.now() - t) / 60000 > slaMin
}

/** Minutes since the oldest of these rows was created — null with nothing to measure. Kept as its
 *  own named function (not inlined in DeskOrders) so the impure Date.now() read stays out of a
 *  component body, same as relTime()/isLateReceived() above (react-hooks/purity). */
function oldestAgeMin(rows: { created_at?: string | null }[]): number | null {
  const times = rows.map((r) => Date.parse(r.created_at || '')).filter((t) => Number.isFinite(t))
  if (!times.length) return null
  return Math.max(0, Math.floor((Date.now() - Math.min(...times)) / 60000))
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

function eventLabel(event: string, detail?: Record<string, unknown> | null): string {
  if (event === 'created') return 'Order created'
  if (event === 'assigned') return 'Assigned'
  if (event === 'reminded') {
    if (detail?.level === 'digest') return 'In the daily office digest'
    if (detail?.owner) return 'Reminder sent · office told'
    if (detail?.rep) return 'Reminder sent to the rep'
    return 'Reminder attempted · rep not reached'
  }
  if (event === 'reminded:summary') {
    const total = Number(detail?.total || 0)
    const reached = Number(detail?.reached || 0)
    return reached > 0 ? `Reminded ${total}× — rep reached ${reached}×` : `Reminded ${total}× — rep not reached`
  }
  // R3 pipeline events
  if (event === 'payment') {
    const to = String(detail?.to || '')
    const amt = typeof detail?.amount_bhd === 'number' ? ` · ${bhd(detail.amount_bhd as number, 3)}` : ''
    const how = detail?.method ? ` · ${String(detail.method).replace(/_/g, ' ')}` : ''
    return `Payment: ${PAYMENT_LABEL[to] || to}${amt}${how}`
  }
  if (event === 'returned') {
    const v = typeof detail?.value_bhd === 'number' ? ` · ${bhd(detail.value_bhd as number, 3)}` : ''
    const u = typeof detail?.units === 'number' ? ` · ${detail.units} pcs` : ''
    return `Returned${u}${v}${detail?.reason ? ` · ${String(detail.reason)}` : ''}`
  }
  if (event === 'invoice') return `Focus invoice ${detail?.to ? String(detail.to) : 'recorded'}`
  if (event === 'conflict') return 'Placed for a shop whose rep is someone else'
  if (event === 'status:cancelled') {
    const code = detail?.reason_code ? cancelReasonLabel(String(detail.reason_code)) : ''
    return code ? `Cancelled · ${code}` : 'Marked Cancelled'
  }
  if (event.startsWith('status:')) return `Marked ${STATUS_LABEL[event.slice(7)] || event.slice(7)}`
  return event.replace(/[_:]/g, ' ')
}

/** "Cancelled · Out of stock · the note" for a cancelled row (the code, else the free text). */
function cancelSummary(o: { cancel_reason?: string | null; cancel_reason_code?: string | null }): string | null {
  const code = cancelReasonLabel(o.cancel_reason_code)
  const text = (o.cancel_reason || '').trim()
  if (!code && !text) return null
  return code && text && text.toLowerCase() !== code.toLowerCase() ? `${code} · ${text}` : code || text
}

/** An old unconfirmed order can carry dozens of 'reminded' rows (one per retry) — collapse them
 *  into one summary line at the latest reminder's slot instead of a wall of identical entries
 *  (item 5, R7a). Events arrive oldest-first (app/shop.py _order_events .order("id")), so the
 *  summary is reinserted just before the first surviving event that is newer than it. */
function collapseReminders(events: OrderEvent[]): OrderEvent[] {
  const reminded = events.filter((e) => e.event === 'reminded')
  if (reminded.length < 2) return events
  const reached = reminded.filter((e) => e.detail?.rep).length
  const latestTs = reminded.reduce((max, e) => (Date.parse(e.ts) > Date.parse(max) ? e.ts : max), reminded[0].ts)
  const summary: OrderEvent = { ts: latestTs, event: 'reminded:summary', detail: { total: reminded.length, reached } }
  const rest = events.filter((e) => e.event !== 'reminded')
  const at = rest.findIndex((e) => Date.parse(e.ts) > Date.parse(latestTs))
  if (at === -1) rest.push(summary)
  else rest.splice(at, 0, summary)
  return rest
}

// ── notify_result (app/shop_notify.py) ────────────────────────────────────────
// A flat map channel → { sent, reason? } plus metadata (at, attempts, recipients). Rows written
// before 24-Sep-2026 carry one `email` key; newer ones `email_rep` + `email_owner`.

/** The channels that tell YQ's own people (the customer's receipt is not one of them). */
const INTERNAL_CHANNELS = ['email_rep', 'email_owner', 'email', 'telegram', 'whatsapp']
const CHANNEL_LABEL: Record<string, string> = {
  email_rep: 'Email · rep', email_owner: 'Email · office', email: 'Email',
  customer_email: 'Email · customer', telegram: 'Telegram', whatsapp: 'WhatsApp',
}
const NOTIFY_META = new Set(['at', 'attempts', 'attempt', 'recipients', 'error'])

function isChannel(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && 'sent' in (v as Record<string, unknown>)
}

const NOTIFY_GRACE_MIN = 15   // app/shop_notify.NOTIFY_GRACE_MIN

/** The same definition as app/shop_notify.notify_failed() (which also drives notify_retry and the
 *  list rows' notify_failed): no rep/owner channel delivered, or the fan-out raised before any send
 *  (a result with only `error`), or no result at all once the order is older than 15 minutes — a
 *  younger null may still be the background task. Status is the caller's business. */
function notifyFailed(nr: Record<string, unknown> | null | undefined, createdAt?: string | null): boolean {
  if (!nr || typeof nr !== 'object') {
    if (!createdAt) return false
    const t = Date.parse(createdAt)
    return Number.isFinite(t) && Date.now() - t >= NOTIFY_GRACE_MIN * 60_000
  }
  const flags = INTERNAL_CHANNELS.filter((k) => isChannel(nr[k])).map((k) => Boolean((nr[k] as Record<string, unknown>).sent))
  return !flags.some(Boolean)
}

function attemptsOf(nr: Record<string, unknown> | null | undefined): number {
  if (!nr || typeof nr !== 'object') return 0
  return Array.isArray(nr.attempts) && nr.attempts.length ? nr.attempts.length : 1
}

/** A list row: the server flag when the API sends it, else (an older API) the row's own result. */
function rowNotifyFailed(r: ShopOrderRow): boolean {
  return typeof r.notify_failed === 'boolean' ? r.notify_failed : notifyFailed(r.notify_result, r.created_at)
}

/** Only for a 'new' order: that is the one status notify_retry still acts on, and the one where a
 *  missed alert means nobody is working the order. A closed order gets a muted note in its
 *  Notifications panel instead. */
function NotNotifiedBadge({ status, failed, attempts }: { status: string; failed: boolean; attempts?: number }) {
  if (status !== 'new' || !failed) return null
  const n = attempts || 0
  return (
    <Badge tone="rose" title={`No alert reached the rep or the office${n > 1 ? ` (${n} attempts)` : ''}. Call them.`}>
      Not notified
    </Badge>
  )
}

function nextStatuses(status: string): string[] {
  switch (status) {
    case 'new': return ['confirmed', 'cancelled']
    case 'confirmed': return ['packed', 'out_for_delivery', 'delivered', 'cancelled']
    case 'packed': return ['out_for_delivery', 'delivered', 'cancelled']
    case 'out_for_delivery': return ['delivered', 'cancelled']
    default: return []
  }
}

/** ordered → confirmed quantity, struck through when the salesman changed or removed it */
function QtyCell({ l }: { l: OrderLine }) {
  const st = l.line_status || 'ok'
  if (st === 'removed') return <span className="text-[#9f1239] line-through">{l.qty}</span>
  if (l.qty_confirmed != null && l.qty_confirmed !== l.qty) {
    return <span><span className="text-muted-foreground line-through">{l.qty}</span> <b className="text-[#6D4091]">{l.qty_confirmed}</b></span>
  }
  return <>{l.qty}</>
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

function NotifyResult({ data, status }: { data: Record<string, unknown>; status: string }) {
  const entries = Object.entries(data).filter(([k]) => !NOTIFY_META.has(k))
  const attempts = Array.isArray(data.attempts) ? data.attempts.length : 0
  const error = typeof data.error === 'string' ? data.error : null
  const failed = notifyFailed(data)
  if (!entries.length && !error) return <p className="text-[12px] text-muted-foreground">No notifications sent.</p>
  return (
    <div className="space-y-1 rounded-xl border p-3">
      {entries.map(([channel, v]) => {
        const obj = v && typeof v === 'object' ? (v as Record<string, unknown>) : null
        const sent = obj ? Boolean(obj.sent) : Boolean(v)
        const reason = obj && typeof obj.reason === 'string' ? obj.reason : undefined
        return (
          <div key={channel} className="flex items-center justify-between gap-3 text-[12px]">
            <span className="shrink-0 text-muted-foreground">{CHANNEL_LABEL[channel] || channel.replace(/_/g, ' ')}</span>
            <span className={cn('min-w-0 truncate text-right', sent ? 'font-medium text-emerald-600' : 'text-muted-foreground')} title={sent ? undefined : reason}>
              {sent ? 'Sent' : reason || 'Not sent'}
            </span>
          </div>
        )
      })}
      {error && <div className="text-[12px] text-[#9f1239]">Error: {error}</div>}
      {(attempts > 1 || failed) && (
        <div className="pt-1 text-[11px] text-muted-foreground">
          {attempts > 1 ? `${attempts} attempts` : '1 attempt'}
          {failed && status === 'new' && (attempts >= 3 ? ' · no more retries — call the rep' : ' · retried while the order is new (up to 3)')}
          {failed && status !== 'new' && ' · no alert reached the rep or the office when this order came in; it was handled anyway'}
        </div>
      )}
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
      {focus?.revenue_90d_bhd ? (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-x-3 border-t pt-3 text-[12px] text-muted-foreground">
          <span>90-day accessories sales</span>
          <span className="tabular-nums">{bhd(focus.revenue_90d_bhd, 3)}</span>
        </div>
      ) : null}
    </Card>
  )
}

function OrderDrawer({
  id, onClose, onChanged, readOnly,
}: { id: number; onClose: () => void; onChanged: () => void; readOnly?: boolean }) {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['shop-order', id], queryFn: () => apiGet<OrderDetail>(`/shop/orders/${id}`) })
  const [busy, setBusy] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [reassigning, setReassigning] = useState(false)
  // R3: a staff cancel names its reason; returns are recorded on delivered orders
  const [cancelling, setCancelling] = useState(false)
  const [cancel, setCancel] = useState({ reason_code: '', note: '' })
  const [returning, setReturning] = useState(false)

  const refetchOrder = () => {
    qc.invalidateQueries({ queryKey: ['shop-order', id] })
    qc.invalidateQueries({ queryKey: ['shop-assignment-queue'] })
    qc.invalidateQueries({ queryKey: ['shop-focus-candidates'] })
    onChanged()
  }

  async function setStatus(next: string) {
    setBusy(next)
    try {
      const body: Record<string, unknown> = { status: next }
      if (next === 'cancelled') {
        body.reason_code = cancel.reason_code
        body.note = cancel.note.trim() || undefined
      }
      await apiPost(`/shop/orders/${id}/status`, body)
      toast(next === 'cancelled' ? 'Order cancelled.' : `Order marked ${STATUS_LABEL[next] || next}.`, 'success')
      setCancelling(false); setCancel({ reason_code: '', note: '' })
      refetchOrder()
    } catch (e) {
      toast(apiDetail(e, 'Could not update the order.'), 'error')
    } finally {
      setBusy(null)
    }
  }

  const actions = data ? (data.next_statuses?.length ? data.next_statuses : nextStatuses(data.status)) : []
  const coupon = data?.coupon_code || data?.coupon?.code || null
  const unassigned = Boolean(data && !data.salesman_id && !data.salesman_name)
  const gap = data ? minimumGapText(data) : null
  const cancelled = data ? cancelSummary(data) : null
  const shopRep = data?.customer
  const shopRepName = shopRep?.salesman_name || shopRep?.sticky_name || null

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="flex h-full w-full flex-col overflow-y-auto bg-card shadow-lift sm:max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-5 py-4">
          <div>
            <div className="font-display text-base font-semibold">{data?.order_no || 'Order'}</div>
            {data && (
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <StatusPill status={data.status} />
                <InvoicedBadge invoiceNo={data.focus_invoice_no} />
                {!!data.returned_bhd && <Badge tone="rose">Returned {bhd(data.returned_bhd, 3)}</Badge>}
                <NotNotifiedBadge status={data.status} failed={notifyFailed(data.notify_result, data.created_at)} attempts={attemptsOf(data.notify_result)} />
              </div>
            )}
            {cancelled && data?.status === 'cancelled' && <div className="mt-1 text-[12px] text-muted-foreground">Cancelled · {cancelled}</div>}
            {gap && <div className="mt-1 text-[12px] font-medium text-amber-700">{gap}</div>}
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={18} /></button>
        </div>

        {isLoading || !data ? (
          <div className="space-y-2 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
        ) : (
          <div className="flex-1 space-y-5 p-5">
            <section>
              <div className="mb-1.5 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <span>Salesman</span>
                {!readOnly && !unassigned && !reassigning && data.status !== 'delivered' && data.status !== 'cancelled' && (
                  <button type="button" onClick={() => setReassigning(true)} className="text-[11px] font-semibold normal-case tracking-normal text-primary hover:underline">Reassign</button>
                )}
              </div>
              {!readOnly && (unassigned || reassigning) ? (
                <AssignBox orderId={id} currentSalesmanId={data.salesman_id ?? null} canBindShop={Boolean(data.customer_id)} shopName={data.customer_shop}
                  onDone={() => { setReassigning(false); refetchOrder() }} />
              ) : (
                <div className="flex flex-wrap items-center gap-2 rounded-xl border p-3 text-sm">
                  <span className="font-semibold">{data.salesman_name || 'Unassigned'}</span>
                  {data.attribution_source && <Badge tone="grey">{attributionLabel(data.attribution_source, data.salesman_name)}</Badge>}
                  {data.attribution_conflict && <Badge tone="amber">{data.source === 'salesman' ? 'Another rep’s shop' : `Referral conflict${data.referral_code ? ` · /${data.referral_code}` : ''}`}</Badge>}
                  {data.expected_delivery && <span className="ml-auto text-[12px] text-muted-foreground">Expected: {data.expected_delivery}</span>}
                </div>
              )}
              {shopRep && (
                <p className="mt-1.5 text-[11.5px] text-muted-foreground">
                  {shopRepLine(shopRep, data.salesman_name)}
                  {shopRepName && data.salesman_id && shopRep.salesman_id !== data.salesman_id && shopRep.sticky_salesman_id !== data.salesman_id && !shopRep.salesman_id
                    ? ' — this order is with a different rep' : ''}
                </p>
              )}
            </section>

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
                        <td className="px-2.5 py-1.5 text-right tabular-nums"><QtyCell l={l} /></td>
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
                <span>{data.total_confirmed_bhd != null && data.total_confirmed_bhd !== data.total_bhd ? 'Confirmed total' : 'Total'}</span>
                <span className="tabular-nums text-primary">{bhd(data.total_confirmed_bhd ?? data.total_bhd, 3)}</span>
              </div>
              {data.total_confirmed_bhd != null && data.total_confirmed_bhd !== data.total_bhd && (
                <Row label="As ordered" value={bhd(data.total_bhd, 3)} />
              )}
            </section>

            {data.status === 'new' && !unassigned && confirming && (
              <ConfirmEditor orderId={id} lines={data.lines || []} onCancel={() => setConfirming(false)} onDone={() => { setConfirming(false); refetchOrder() }} />
            )}
            {data.whatsapp_url && data.status !== 'new' && <CustomerWhatsApp url={data.whatsapp_url} first={(data.customer_name || '').split(' ')[0]} />}

            {data.status === 'delivered' && (returning || !readOnly || !!data.returned_bhd) && (
              <section className="space-y-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">After delivery</div>
                {returning ? (
                  <ReturnBox orderId={id} lines={data.lines || []} onCancel={() => setReturning(false)} onDone={() => { setReturning(false); refetchOrder() }} />
                ) : !readOnly ? (
                  <button type="button" onClick={() => setReturning(true)} className="h-10 w-full rounded-xl border border-[#f3c9d2] text-[12.5px] font-semibold text-[#9f1239] hover:bg-[#fff7f8]">
                    Record a return{data.returned_bhd ? ` (so far ${bhd(data.returned_bhd, 3)})` : ''}
                  </button>
                ) : (
                  <p className="text-[12.5px] text-muted-foreground">Returned so far: {bhd(data.returned_bhd, 3)}</p>
                )}
              </section>
            )}

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
                  {collapseReminders(data.events).map((e, i) => (
                    <li key={i} className="flex gap-2 text-[12px]">
                      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                      <div>
                        <div className="font-medium">{eventLabel(e.event, e.detail)}</div>
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
                <NotifyResult data={data.notify_result} status={data.status} />
              </section>
            )}
          </div>
        )}

        {data && actions.length > 0 && !confirming && !readOnly && (
          <div className="sticky bottom-0 space-y-2 border-t bg-card p-4">
            {cancelling ? (
              <div className="space-y-2 rounded-xl border border-[#f3c9d2] bg-[#fff7f8] p-3">
                <CancelReasonPicker value={cancel.reason_code} note={cancel.note} onChange={setCancel} />
                <div className="flex gap-2">
                  <Button type="button" size="sm" variant="outline" onClick={() => { setCancelling(false); setCancel({ reason_code: '', note: '' }) }} disabled={busy !== null}>Keep it</Button>
                  <Button type="button" size="sm" variant="destructive" onClick={() => setStatus('cancelled')} disabled={busy !== null || !cancelReady(cancel.reason_code, cancel.note)}>
                    {busy === 'cancelled' ? <Loader2 className="animate-spin" size={14} /> : <X size={14} />} Yes, cancel {data.order_no}
                  </Button>
                </div>
              </div>
            ) : (
              <>
                {/* Received always shows Confirm first (it opens the quantity editor above) and
                   Cancel second — never just Cancel on its own (item 2, R7a). Every other next
                   status (packed / on the way / delivered) is a plain forward step. */}
                <div className="flex flex-wrap gap-2">
                  {data.status === 'new' && (
                    <Button type="button" onClick={() => setConfirming(true)} disabled={unassigned || busy !== null} className="flex-1">
                      <Check size={14} /> Confirm order
                    </Button>
                  )}
                  {actions.filter((a) => a !== 'confirmed' && a !== 'cancelled').map((a) => (
                    <Button key={a} size="sm" onClick={() => setStatus(a)} disabled={busy !== null}>
                      {busy === a ? <Loader2 className="animate-spin" size={14} /> : <Check size={14} />}
                      {actionLabel(a)}
                    </Button>
                  ))}
                  {actions.includes('cancelled') && (
                    <Button type="button" size="sm" variant="destructive" onClick={() => setCancelling(true)} disabled={busy !== null}>
                      <X size={14} /> Cancel order
                    </Button>
                  )}
                </div>
                {unassigned && data.status === 'new' && <p className="text-[11.5px] text-muted-foreground">Assign a salesman before confirming.</p>}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// The old "Focus check" (delivered orders vs the Focus ledger, via /shop/focus-recon) is gone —
// replaced by OrderActions.FocusExceptionsPanel, fed by /shop/focus/candidates (item 6, R7a).

function DeskOrders({
  meData, rows, isLoading, counts, status, setStatus, qRaw, setQRaw, onRefresh, queueFocus, readOnly,
}: {
  meData?: ShopMe
  rows: ShopOrderRow[]
  isLoading: boolean
  counts: StatusCounts
  status: StatusFilter
  setStatus: (s: StatusFilter) => void
  qRaw: string
  setQRaw: (v: string) => void
  onRefresh: () => void
  queueFocus?: boolean
  readOnly?: boolean
}) {
  const qc = useQueryClient()
  const [openId, setOpenId] = useState<number | null>(null)
  const openRow = (r: ShopOrderRow) => setOpenId(r.id)
  const refreshFocus = () => { qc.invalidateQueries({ queryKey: ['shop-focus-candidates'] }); onRefresh() }

  // "Needs action" (item 4, R7a) — Received orders past the SLA, unassigned orders and open Focus
  // exceptions, all in one number. The desk's own list is scoped by whichever status the office
  // has selected, so the late-Received signal is read from a small dedicated fetch instead
  // (independent of that filter) — the same /shop/orders endpoint, just always scanning 'new'.
  const receivedScan = useQuery({
    queryKey: ['shop-orders-received-scan'],
    queryFn: () => apiGet<ShopOrdersResp>('/shop/orders?status=new&limit=100'),
    staleTime: 30_000,
  })
  const queueQuery = useQuery({
    queryKey: ['shop-assignment-queue'],
    queryFn: () => apiGet<{ orders: unknown[]; count: number; sla_min?: number }>('/shop/assignment-queue'),
    staleTime: 30_000,
  })
  const focusQuery = useFocusCandidates()

  // shop_assign_sla_min (the only SLA any query on this page already reads — AssignmentQueue's
  // own `sla_min`, shared here via the same query key); shop_confirm_sla_min isn't exposed to the
  // web anywhere yet, so 120 min is the documented fallback.
  const slaMin = queueQuery.data?.sla_min ?? 120
  const receivedRows = receivedScan.data?.orders || []
  const oldestReceivedMin = oldestAgeMin(receivedRows)
  const lateReceivedCount = receivedRows.filter((r) => isLateReceived(r, slaMin)).length
  const unassignedCount = queueQuery.data?.count ?? 0
  const focusExceptionsCount = focusQuery.data?.count ?? 0
  const needsActionCount = lateReceivedCount + unassignedCount + focusExceptionsCount
  const totalCount = Object.values(counts).reduce((s, n) => s + (n || 0), 0)

  const displayRows = status === 'needs_action'
    ? rows.filter((r) => isLateReceived(r, slaMin) || (!r.salesman_id && !r.salesman_name))
    : rows

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
    { key: 'salesman_name', label: 'Salesman', render: (_, r) => (
        r.salesman_name
          ? <span className="inline-flex items-center gap-1.5">{r.salesman_name}{r.attribution_conflict && <Badge tone="amber">conflict</Badge>}</span>
          : <Badge tone="rose">Unassigned</Badge>
      ) },
    { key: 'items_count', label: 'Items / Units', align: 'right', render: (_, r) => `${num(r.items_count)} / ${num(r.units_count)}` },
    { key: 'total_bhd', label: 'Total', align: 'right', render: (_, r) => bhd(r.total_bhd, 3) },
    { key: 'status', label: 'Status', render: (_, r) => (
        <span className="inline-flex flex-wrap items-center gap-1.5">
          <StatusPill status={r.status} />
          {r.order_kind === 'small' && <Badge tone="accent" title={minimumGapText(r) || undefined}>Small</Badge>}
          <NotNotifiedBadge status={r.status} failed={rowNotifyFailed(r)} attempts={r.notify_attempts} />
          {!!r.returned_bhd && <Badge tone="rose">Returned {bhd(r.returned_bhd, 3)}</Badge>}
          <InvoicedBadge invoiceNo={r.focus_invoice_no} />
          {r.status === 'cancelled' && cancelSummary(r) && <span className="text-[11px] text-muted-foreground">{cancelSummary(r)}</span>}
        </span>
      ) },
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
      <PageHeader title="Shop Orders" subtitle="Orders from the marketplace, salesman links and the shared catalog" />

      {!readOnly && <AssignmentQueue onChanged={onRefresh} highlight={queueFocus} />}

      <FocusExceptionsPanel onOpenOrder={(id) => setOpenId(id)} onChanged={refreshFocus} readOnly={readOnly} />

      {meData?.salesman && <MyLinkCard me={meData} isAdmin companyKpis={null} />}

      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          <button onClick={() => setStatus('needs_action')}
            className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium tabular-nums transition duration-150 motion-reduce:transition-none',
              status === 'needs_action' ? 'border-primary bg-primary text-primary-foreground'
                : needsActionCount > 0 ? 'border-[#f3c9d2] bg-[#fff7f8] text-[#9f1239] hover:border-[#9f1239]/50' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
            Needs action{needsActionCount > 0 ? ` ${needsActionCount}` : ''}
          </button>
          {(['all', ...STATUSES] as const).map((s) => {
            const n = s === 'all' ? totalCount : (counts[s] ?? 0)
            const late = s === 'new' && oldestReceivedMin != null && oldestReceivedMin > slaMin
            const label = s === 'all' ? `All ${n}`
              : s === 'new' ? `Received ${n}${oldestReceivedMin != null ? ` · oldest ${ageCompact(oldestReceivedMin)}` : ''}`
                : `${STATUS_LABEL[s] || s} ${n}`
            return (
              <button key={s} onClick={() => setStatus(s)}
                className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium tabular-nums transition duration-150 motion-reduce:transition-none',
                  status === s ? 'border-primary bg-primary text-primary-foreground'
                    : late ? 'border-[#f3c9d2] bg-[#fff7f8] text-[#9f1239]' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
                {label}
              </button>
            )
          })}
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
        <DataTable rows={displayRows} cols={cols} searchable={false} exportName="yq-shop-orders"
          empty={status === 'needs_action'
            ? 'Nothing needs action right now.'
            : 'No orders yet — customers order from your shared catalog link (see “My link” above) and orders will appear here automatically.'} />
      )}

      {openId != null && (
        <OrderDrawer id={openId} onClose={() => setOpenId(null)} onChanged={refreshFocus} readOnly={readOnly} />
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// Field view (phone) — one thumb, a shop owner waiting, no table in sight.
// ══════════════════════════════════════════════════════════════════════════════

const INK = 'text-[#1A1428]'
const MUTED = 'text-[#6b6480]'
const HAIRLINE = 'border-[#E9E4EF]'

function OrderCard({ row, onOpen }: { row: ShopOrderRow; onOpen: () => void }) {
  const title = row.customer_shop || row.customer_name || 'Order'
  const sub = [row.customer_shop ? row.customer_name : null, row.customer_area].filter(Boolean).join(' · ')
  const age = relTime(row.created_at)
  const gap = minimumGapText(row)
  const cancelled = row.status === 'cancelled' ? cancelSummary(row) : null
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        'w-full rounded-[20px] border bg-white p-4 text-left transition-colors duration-150 hover:border-[#d9d3ea]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6D4091] motion-reduce:transition-none',
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
        <InvoicedBadge invoiceNo={row.focus_invoice_no} />
        {row.source === 'salesman' && <Badge tone="accent">You placed</Badge>}
        {row.has_backorder && <Badge tone="amber">Backorder</Badge>}
        {row.order_kind === 'small' && <Badge tone="accent">Small order</Badge>}
        {!!row.returned_bhd && <Badge tone="rose">Returned</Badge>}
        <NotNotifiedBadge status={row.status} failed={rowNotifyFailed(row)} attempts={row.notify_attempts} />
        <span className={cn('ml-auto shrink-0 text-[11px]', MUTED)}>
          {row.order_no}{age ? ` · ${age}` : ''}
        </span>
      </div>
      {gap && <div className="mt-1.5 text-[12px] font-medium text-[#96600d]">{gap}</div>}
      {cancelled && <div className={cn('mt-1.5 text-[12px]', MUTED)}>Cancelled · {cancelled}</div>}
    </button>
  )
}

function FieldOrderSheet({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['shop-order', id], queryFn: () => apiGet<OrderDetail>(`/shop/orders/${id}`) })
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  // R3: the cancel names its reason (chips). The old free-text Focus invoice number at Delivered
  // is gone (item 6, R7a) — invoices are matched from the Focus exceptions panel on the desk now.
  const [cancel, setCancel] = useState({ reason_code: '', note: '' })

  async function setStatus(next: string) {
    setBusy(next)
    try {
      const body: Record<string, unknown> = { status: next }
      if (next === 'cancelled') {
        body.reason_code = cancel.reason_code
        body.note = cancel.note.trim() || undefined
      }
      await apiPost(`/shop/orders/${id}/status`, body)
      toast(next === 'cancelled' ? 'Order cancelled.' : `Order marked ${STATUS_LABEL[next] || next}.`, 'success')
      setConfirmCancel(false)
      setCancel({ reason_code: '', note: '' })
      qc.invalidateQueries({ queryKey: ['shop-order', id] })
      onChanged()
    } catch (e) {
      toast(e instanceof ApiError ? apiDetail(e, 'Could not update the order.') : 'Could not update the order.', 'error')
    } finally {
      setBusy(null)
    }
  }

  const allowed = data ? (data.next_statuses?.length ? data.next_statuses : nextStatuses(data.status)) : []
  // The forward step. From Received it is "Confirm" (with the quantity editor); afterwards the
  // next physical stage — Preparing is optional, so from Confirmed the button reads "On the way".
  const forward = data?.status === 'new'
    ? (allowed.includes('confirmed') ? 'confirmed' : null)
    : data?.status === 'confirmed'
      ? (allowed.includes('out_for_delivery') ? 'out_for_delivery' : allowed.find((s) => s !== 'cancelled') || null)
      : allowed.find((s) => s !== 'cancelled') || null
  const canCancel = allowed.includes('cancelled')
  const coupon = data?.coupon_code || data?.coupon?.code || null
  const [confirming, setConfirming] = useState(false)
  const gap = data ? minimumGapText(data) : null
  const cancelledWhy = data?.status === 'cancelled' ? cancelSummary(data) : null

  const footer = !data || confirming ? null : (
    <div className="space-y-2">
      {data.whatsapp_url && data.status !== 'new' && (
        <CustomerWhatsApp url={data.whatsapp_url} first={(data.customer_name || '').split(' ')[0]} />
      )}
      {forward === 'confirmed' ? (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[15px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 motion-reduce:transition-none"
        >
          <Check size={17} aria-hidden="true" /> Confirm order
        </button>
      ) : forward ? (
        <button
          type="button"
          onClick={() => setStatus(forward)}
          disabled={busy !== null}
          className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[15px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 disabled:opacity-60 motion-reduce:transition-none"
        >
          {busy === forward ? <Loader2 className="animate-spin" size={17} /> : <Check size={17} aria-hidden="true" />}
          {actionLabel(forward)}
        </button>
      ) : null}
      {data.status === 'confirmed' && allowed.includes('packed') && (
        <button type="button" onClick={() => setStatus('packed')} disabled={busy !== null}
          className={cn('h-11 w-full rounded-xl border text-[13px] font-semibold transition-colors duration-150 hover:bg-[#F9F7F3] motion-reduce:transition-none', HAIRLINE, INK)}>
          {busy === 'packed' ? <Loader2 className="mx-auto animate-spin" size={15} /> : 'Mark preparing (goods with me)'}
        </button>
      )}
      {canCancel && !confirmCancel && (
        <button
          type="button"
          onClick={() => setConfirmCancel(true)}
          disabled={busy !== null}
          className={cn('h-11 w-full rounded-xl text-[13px] font-semibold transition-colors duration-150 hover:bg-[#F3F0F6] motion-reduce:transition-none', MUTED)}
        >
          Cancel this order
        </button>
      )}
      {canCancel && confirmCancel && (
        <div className={cn('rounded-xl border p-3', HAIRLINE)}>
          <p className={cn('text-[12.5px] leading-snug', INK)}>Cancel {data.order_no}? The shop is told it is cancelled, and the reason you pick. Say why:</p>
          <div className="mt-2.5">
            <CancelReasonPicker value={cancel.reason_code} note={cancel.note} onChange={setCancel} compact />
          </div>
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              onClick={() => { setConfirmCancel(false); setCancel({ reason_code: '', note: '' }) }}
              className={cn('h-11 flex-1 rounded-xl border text-[13px] font-semibold transition-colors duration-150 hover:bg-[#F9F7F3] motion-reduce:transition-none', HAIRLINE, INK)}
            >
              Keep it
            </button>
            <button
              type="button"
              onClick={() => setStatus('cancelled')}
              disabled={busy !== null || !cancelReady(cancel.reason_code, cancel.note)}
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
            <InvoicedBadge invoiceNo={data.focus_invoice_no} />
            {data.source === 'salesman' && <Badge tone="accent">You placed</Badge>}
            {data.source === 'market' && <Badge tone="accent">Marketplace</Badge>}
            {data.attribution_conflict && <Badge tone="amber">{data.source === 'salesman' ? 'Another rep’s shop' : 'Referral conflict'}</Badge>}
            {data.has_backorder && <Badge tone="amber">Backorder</Badge>}
            {!!data.returned_bhd && <Badge tone="rose">Returned {bhd(data.returned_bhd, 3)}</Badge>}
            <NotNotifiedBadge status={data.status} failed={notifyFailed(data.notify_result, data.created_at)} attempts={attemptsOf(data.notify_result)} />
            {data.expected_delivery && <span className={cn('text-[11.5px]', MUTED)}>Expected: {data.expected_delivery}</span>}
          </div>
          {gap && (
            <p className="rounded-xl bg-[#fdf3e3] px-3 py-2 text-[12.5px] font-medium text-[#96600d]">
              Small order · {gap}. Confirm it as it is, or add to it with the shop.
            </p>
          )}
          {cancelledWhy && <p className={cn('text-[12.5px]', MUTED)}>Cancelled · {cancelledWhy}</p>}

          {confirming && (
            <ConfirmEditor
              orderId={id}
              lines={data.lines || []}
              onCancel={() => setConfirming(false)}
              onDone={() => { setConfirming(false); qc.invalidateQueries({ queryKey: ['shop-order', id] }); onChanged() }}
            />
          )}

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
                  className={cn('flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl border text-[13px] font-semibold transition-colors duration-150 hover:bg-[#F9F7F3] motion-reduce:transition-none', HAIRLINE, INK)}
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
                      <QtyCell l={l} /> × {bhd(l.unit_price_bhd, 3)}
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
              <p className={cn('rounded-xl bg-[#F9F7F3] p-3 text-[12.5px] leading-snug', INK)}>{data.note}</p>
            </section>
          )}

          {!!data.events?.length && (
            <section>
              <h3 className={cn('mb-1.5 text-[10.5px] font-semibold uppercase tracking-wide', MUTED)}>Timeline</h3>
              <ul className="space-y-2.5">
                {collapseReminders(data.events).map((e, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="mt-[0.4rem] h-1.5 w-1.5 shrink-0 rounded-full bg-[#6D4091]" aria-hidden="true" />
                    <div className="min-w-0">
                      <div className={cn('text-[12.5px] font-semibold', INK)}>{eventLabel(e.event, e.detail)}</div>
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
  meData, rows, isLoading, isError, counts, bucket, setBucket, qRaw, setQRaw, onRefresh, initialOpen,
}: {
  meData?: ShopMe
  initialOpen?: number | null
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
  const navigate = useNavigate()
  const [openId, setOpenId] = useState<number | null>(initialOpen ?? null)
  const name = meData?.salesman?.name

  return (
    <div className="mx-auto max-w-2xl space-y-4 px-4 py-4 lg:max-w-5xl lg:px-8 lg:py-8">
      <header>
        <h1 className={cn('font-display text-[22px] font-bold leading-tight tracking-tight lg:text-[28px]', INK)}>Orders</h1>
        <p className={cn('mt-0.5 text-[12.5px]', MUTED)}>
          {name ? `${name} · from your link and placed by you` : 'From your link and placed by you'}
        </p>
      </header>

      <div className="space-y-3 lg:flex lg:items-center lg:gap-3 lg:space-y-0">
      <div className={cn('flex h-12 items-center gap-2 rounded-2xl border bg-white px-3.5 focus-within:border-[#6D4091] lg:flex-1', HAIRLINE)}>
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

      <div role="tablist" aria-label="Order stage" className={cn('flex gap-1 rounded-2xl border bg-white p-1 lg:w-[26rem]', HAIRLINE)}>
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
                on ? 'bg-[#6D4091] text-white' : cn(MUTED, 'hover:bg-[#F9F7F3]'),
              )}
            >
              {b.label}
              {n != null && <span className={cn('tabular-nums', on ? 'text-white/70' : 'text-[#9a93ad]')}>{n}</span>}
            </button>
          )
        })}
      </div>
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
            className="mt-4 h-11 rounded-xl bg-[#6D4091] px-5 text-[13.5px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 motion-reduce:transition-none"
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
                onClick={() => navigate('/today')}
                className="mt-4 h-11 rounded-xl bg-[#6D4091] px-5 text-[13.5px] font-semibold text-white transition-opacity duration-150 hover:opacity-95 motion-reduce:transition-none"
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
        <ul className="grid gap-2.5 lg:grid-cols-2">
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
  // The management stream is adding a read-only 'management' role (web/src/lib/auth.tsx) — its
  // helper isn't in this worktree yet, so this is a local, type-safe stand-in (`role` is widened
  // to `string` on purpose: `me.role` is a Role union that doesn't include 'management', and
  // comparing a literal union to a value outside it is a TS2367 build error).
  // TODO(management-stream): replace with the real `isManagement(me)` / readOnly helper once it lands.
  const role: string | undefined = me?.role
  const isManagementRole = role === 'management'
  const showDesk = isAdmin || isManagementRole
  // Deep links: the Telegram "UNASSIGNED" alert opens the queue (?queue=1); Today and Customers
  // open one order (?open=id), a stage (?bucket=progress) or a search (?q=phone).
  const [sp] = useSearchParams()
  const queueFocus = sp.get('queue') === '1'
  const initialBucket = (BUCKETS.find((b) => b.key === sp.get('bucket'))?.key ?? 'new') as BucketKey
  const initialQ = (sp.get('q') || '').trim()
  const initialOpen = Number(sp.get('open')) || null
  const [status, setStatus] = useState<StatusFilter>('all')
  const [bucket, setBucket] = useState<BucketKey>(initialBucket)
  const [qRaw, setQRaw] = useState(initialQ)
  const [q, setQ] = useState(initialQ)

  useEffect(() => {
    const t = setTimeout(() => setQ(qRaw.trim()), 300)
    return () => clearTimeout(t)
  }, [qRaw])

  const meQuery = useQuery({ queryKey: ['shop-me'], queryFn: () => apiGet<ShopMe>('/shop/me') })

  // The desk filters by one status, the field by a bucket of statuses (the API takes a
  // comma list) — both collapse to a single `status` query param. 'needs_action' (item 4, R7a)
  // is a computed view, not a real status, so it fetches the same as 'all' and is filtered
  // client-side (DeskOrders).
  const statusParam = showDesk
    ? (status === 'all' || status === 'unassigned' || status === 'needs_action' ? '' : status)
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

  const rows = ordersQuery.data?.orders || []
  const refreshList = () => {
    qc.invalidateQueries({ queryKey: ['shop-orders'] })
    qc.invalidateQueries({ queryKey: ['shop-orders-new-count'] })
    qc.invalidateQueries({ queryKey: ['shop-orders-received-scan'] })
  }

  if (!showDesk) {
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
        initialOpen={initialOpen}
      />
    )
  }

  return (
    <DeskOrders
      meData={meQuery.data}
      rows={rows}
      isLoading={ordersQuery.isLoading}
      counts={ordersQuery.data?.counts || {}}
      status={status}
      setStatus={setStatus}
      qRaw={qRaw}
      setQRaw={setQRaw}
      onRefresh={refreshList}
      queueFocus={queueFocus}
      readOnly={isManagementRole}
    />
  )
}
