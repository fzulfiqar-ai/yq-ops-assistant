import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  AlertTriangle, ChevronRight, Gauge, LayoutList, Link2, Loader2, RefreshCw, Search, SearchX, Share2, Store, Ticket,
  UserRoundCheck, type LucideIcon,
} from 'lucide-react'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd, num, pct, fmtDate } from '@/lib/format'
import { MONEY_NOTE, VAT_LABEL } from '@/lib/basisText'
import { LoadError } from '@/components/LoadError'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, Stat, type Column } from '@/components/DataTable'

// ── types (docs/SHOP.md GET /shop/analytics — every field may be missing: an older API, a rep's
// slice, or the analytics views not set up yet) ─────────────────────────────────

interface FunnelData {
  sessions?: number | null
  item_views?: number | null
  adds?: number | null
  carts?: number | null
  checkouts?: number | null
  orders?: number | null
  conversion_pct?: number | null
  devices?: number | null
}
interface TopProductRow {
  item_code: string
  display_name?: string | null
  units?: number | null
  value_bhd?: number | null
  orders?: number | null
}
interface LeaderboardRow {
  salesman: string
  orders?: number | null
  value_bhd?: number | null
  customers?: number | null
  aov_bhd?: number | null
}
type LeaderboardDisplayRow = LeaderboardRow & { rank: number }
interface AttributionRow {
  key?: string | null
  orders?: number | null
  value_bhd?: number | null
}
interface DailyRow {
  date: string
  orders?: number | null
  value_bhd?: number | null
  sessions?: number | null
}
interface SearchTermRow {
  term: string
  searches?: number | null
  zero?: number | null
  devices?: number | null
  last_seen?: string | null
}
interface SearchData {
  searches?: number | null
  zero_results?: number | null
  zero_rate_pct?: number | null
  terms?: SearchTermRow[] | null
  zero_terms?: SearchTermRow[] | null
}
interface RailRow {
  rail: string
  clicks?: number | null
  adds?: number | null
  sessions?: number | null
}
interface EngagementData {
  search?: number | null
  share?: number | null
  install?: number | null
  reorder?: number | null
  cancel?: number | null
  checkout_start?: number | null
  devices?: number | null
  new_devices?: number | null
}
interface OpsData {
  by_attribution?: AttributionRow[] | null
  unassigned_now?: number | null
  conflicts?: number | null
  sla_min?: number | null
  sla_breaches?: number | null
  median_time_to_confirm_min?: number | null
  cancelled_by_customer?: number | null
  cancelled_by_staff?: number | null
}
interface IdentityData {
  customers?: number | null
  repeat_customers?: number | null
  repeat_rate_pct?: number | null
  market_orders?: number | null
  staff_orders?: number | null
  legacy_orders?: number | null
}
interface VitalsData {
  samples?: number | null
  visits?: number | null
  lcp_ms_p75?: number | null
  inp_ms_p75?: number | null
  cls_p75?: number | null
}
interface WindowData {
  start?: string | null
  end?: string | null
  days?: number | null
  events_since?: string | null
  before_history?: boolean | null
}
interface LinkRow {
  key: string
  rep?: string | null
  sessions?: number | null
  devices?: number | null
  orders?: number | null
  value_bhd?: number | null
  conversion_pct?: number | null
}
interface MerchantRow {
  customer_id: number
  shop?: string | null
  area?: string | null
  rep?: string | null
  rep_name?: string | null
  orders?: number | null
  value_bhd?: number | null
  value_90d_bhd?: number | null
  last_order_at?: string | null
  days_since_last?: number | null
  cadence_days?: number | null
  cadence_basis?: string | null
  due_status?: string | null
  dormant?: boolean | null
  is_new?: boolean | null
}
interface MerchantCounts {
  merchants?: number | null
  new?: number | null
  returning?: number | null
  due?: number | null
  overdue?: number | null
  dormant?: number | null
}
interface MerchantsData {
  available?: boolean | null
  counts?: MerchantCounts | null
  active?: MerchantRow[] | null
  watch?: MerchantRow[] | null
}
interface ShopAnalyticsResp {
  search?: SearchData | null
  rails?: RailRow[] | null
  engagement?: EngagementData | null
  ops?: OpsData | null
  identity?: IdentityData | null
  vitals?: VitalsData | null
  days: number
  since?: string | null
  funnel?: FunnelData | null
  orders?: number | null
  cancelled?: number | null
  value_bhd?: number | null
  aov_bhd?: number | null
  units?: number | null
  customers?: number | null
  backorder_rate_pct?: number | null
  top_products?: TopProductRow[] | null
  leaderboard?: LeaderboardRow[] | null
  attribution?: {
    by_referral?: AttributionRow[] | null
    by_src?: AttributionRow[] | null
    by_coupon?: AttributionRow[] | null
  } | null
  daily?: DailyRow[] | null
  window?: WindowData | null
  source?: 'views' | 'events' | null
  notes?: string[] | null
  links?: LinkRow[] | null
  merchants?: MerchantsData | null
}

// ── periods that mean something: the storefront's event history starts on one day, so a window
// longer than that history is "since <that day>", shown once ─────────────────────

const CHIP_DAYS = [1, 7, 30, 90] as const

function isoAddDays(iso: string, delta: number): string {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + delta)
  return d.toISOString().slice(0, 10)
}
function bahrainToday(): string {
  return new Date(Date.now() + 3 * 3600_000).toISOString().slice(0, 10)
}
function dayMonth(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
}
function periodChips(end: string, since?: string | null): { days: number; label: string; since: boolean }[] {
  const out: { days: number; label: string; since: boolean }[] = []
  let sinceShown = false
  for (const d of CHIP_DAYS) {
    const start = isoAddDays(end, -(d - 1))
    if (since && start < since) {
      if (sinceShown) continue          // a longer window holds nothing more than "since" already does
      sinceShown = true
      out.push({ days: d, label: `Since ${dayMonth(since)}`, since: true })
    } else {
      out.push({ days: d, label: d === 1 ? 'Today' : `${d} days`, since: false })
    }
  }
  return out
}
function windowLabel(days: number, win?: WindowData | null): string {
  if (win?.before_history && win.events_since) return `Since ${dayMonth(win.events_since)} — all the history there is`
  return days === 1 ? 'Today' : `Last ${days} days`
}

// Ordinal ramp for the funnel steps (visits -> orders): one hue — the brand purple — monotone
// light-to-dark, the darkest step on the primary (#6d28d9). Checked with the dataviz skill's ordinal
// validator against the white card surface.
const FUNNEL_STEPS: { key: keyof FunnelData; label: string; color: string }[] = [
  { key: 'sessions', label: 'Visits', color: '#d4b3ff' },
  { key: 'item_views', label: 'Viewed a product', color: '#c797ff' },
  { key: 'adds', label: 'Added to cart', color: '#af7dff' },
  { key: 'carts', label: 'Opened the cart', color: '#9862ff' },
  { key: 'checkouts', label: 'Started checkout', color: '#8247f4' },
  { key: 'orders', label: 'Ordered', color: '#6d28d9' },
]

function fmtDayMonth(d?: string | null): string {
  return dayMonth(d)
}
function pctOrDash(n: number | null | undefined, dp = 1): string {
  return n == null ? '—' : pct(n, dp)
}
function labelForReferral(key?: string | null): string {
  if (!key || key === 'direct') return 'Direct link'
  if (key === 'dropdown') return 'Chosen in checkout'
  return `ref: ${key}`
}
function labelForSrc(key?: string | null): string {
  if (!key || key === 'direct') return 'Direct'
  return key
}
const ATTRIBUTION_LABEL: Record<string, string> = {
  customer_admin: 'Assigned to the merchant',
  focus_map: 'Focus mapping',
  sticky: 'Remembered rep',
  session_ref: 'Storefront link / QR',
  checkout_pick: 'Chosen at checkout',
  staff: 'Placed by staff',
  default: 'Default rep',
  unassigned: 'Unassigned (queue)',
  legacy: 'Legacy catalog link',
}
function labelForAttribution(key?: string | null): string {
  return (key && ATTRIBUTION_LABEL[key]) || key || '—'
}
const RAIL_LABEL: Record<string, string> = {
  best: 'Best sellers',
  arrived: 'Just arrived',
  fresh: 'Just arrived',
  offers: 'On offer',
  deals: 'Deals',
  regulars: 'Order again',
  together: 'Bought together',
  complete: 'Complete your order',
  reorder: 'Reorder button',
  tile: 'Category tiles',
  grid: 'Product grid',
  search_row: 'Search results',
  panel: 'Product panel',
  essentials: 'Essentials',
  category: 'Category page',
  slider: 'Promo slider',
}
function labelForRail(key?: string | null): string {
  return (key && RAIL_LABEL[key]) || key || '—'
}
function linkLabel(r: LinkRow): string {
  if (r.key === 'direct') return 'No rep link'
  return r.rep ? `${r.rep}` : `ref: ${r.key}`
}
const STATUS_BADGE: Record<string, { tone: BadgeTone; label: string }> = {
  due: { tone: 'amber', label: 'Due' },
  overdue: { tone: 'rose', label: 'Overdue' },
}
function MerchantFlags({ m }: { m: MerchantRow }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {m.dormant && <Badge tone="rose">Dormant</Badge>}
      {m.due_status && STATUS_BADGE[m.due_status] && <Badge tone={STATUS_BADGE[m.due_status].tone}>{STATUS_BADGE[m.due_status].label}</Badge>}
      {m.is_new && <Badge tone="accent">New</Badge>}
    </span>
  )
}

// ── small local atoms ───────────────────────────────────────────────────────

function StaleBanner({ onRetry, isRetrying }: { onRetry: () => void; isRetrying: boolean }) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm font-medium text-amber-700">
      <AlertTriangle size={16} className="shrink-0" />
      <span className="flex-1">Could not refresh — showing figures from a moment ago.</span>
      <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={isRetrying}
        className="border-amber-300 text-amber-700 hover:bg-amber-100">
        {isRetrying ? <Loader2 className="animate-spin" size={13} /> : <RefreshCw size={13} />} Retry
      </Button>
    </div>
  )
}

function SectionTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-3 mt-7">
      <div className="font-display text-base font-semibold">{title}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  )
}

function FunnelCard({ funnel, label }: { funnel: FunnelData | null | undefined; label: string }) {
  const steps = FUNNEL_STEPS.map((s) => ({ ...s, count: Number(funnel?.[s.key] ?? 0) }))
  const allZero = steps.every((s) => s.count === 0)
  const visits = steps[0].count
  const denom = visits > 0 ? visits : Math.max(1, ...steps.map((s) => s.count))

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="font-display text-base font-semibold">Funnel</div>
          <div className="text-xs text-muted-foreground">Visits to orders · {label}</div>
        </div>
        {!allZero && (
          <div className="text-[13px] text-muted-foreground">
            Visits that ordered <span className="font-semibold text-foreground">{pctOrDash(funnel?.conversion_pct)}</span>
          </div>
        )}
      </div>

      {allZero ? (
        <div className="rounded-xl border border-dashed py-10 text-center text-sm text-muted-foreground">
          No shop visits in this period — share a rep's link to start collecting data.
        </div>
      ) : (
        <div className="space-y-2.5">
          {steps.map((s) => {
            const w = s.count > 0 ? Math.min(100, Math.max(3, Math.round((s.count / denom) * 100))) : 0
            const ofVisits = visits > 0 ? (s.count / visits) * 100 : 0
            return (
              <div key={s.key} className="flex items-center gap-3 rounded-lg px-1 py-1 transition-colors hover:bg-accent/30">
                <span className="w-28 shrink-0 text-[13px] font-medium text-muted-foreground sm:w-36">{s.label}</span>
                <span className="h-6 flex-1 overflow-hidden rounded-r bg-secondary">
                  <span className="block h-full rounded-r transition-[width]" style={{ width: `${w}%`, background: s.color }} />
                </span>
                <span className="w-24 shrink-0 text-right text-[13px] tabular-nums sm:w-32">
                  <span className="font-semibold">{num(s.count)}</span>
                  {s.key !== 'sessions' && <span className="ml-1 text-muted-foreground">· {ofVisits.toFixed(0)}%</span>}
                </span>
              </div>
            )
          })}
        </div>
      )}
      <p className="mt-3 text-[12px] text-muted-foreground">
        A visit is one phone's session on one day. "Ordered" counts the orders placed on the marketplace itself — orders a
        rep entered for a shop are in the totals above, not here.
      </p>
    </Card>
  )
}

function AttributionTable({
  title, icon: Icon, rows, labelFor, keyHeader, empty,
}: {
  title: string
  icon: LucideIcon
  rows: AttributionRow[]
  labelFor: (key?: string | null) => string
  keyHeader: string
  empty: string
}) {
  const sorted = useMemo(
    () => [...rows].sort((a, b) => (Number(b.value_bhd) || 0) - (Number(a.value_bhd) || 0)).slice(0, 8),
    [rows],
  )
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
        <Icon size={16} className="text-primary" /> {title}
      </div>
      {!sorted.length ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div className="overflow-hidden rounded-xl border">
          <table className="w-full text-[13px]">
            <thead className="bg-secondary/50 text-[11px] uppercase text-muted-foreground">
              <tr>
                <th className="px-2.5 py-1.5 text-left">{keyHeader}</th>
                <th className="px-2.5 py-1.5 text-right">Orders</th>
                <th className="px-2.5 py-1.5 text-right">Value <span className="normal-case">({VAT_LABEL})</span></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={i} className="border-t">
                  <td className="max-w-[140px] truncate px-2.5 py-1.5 font-medium" title={labelFor(r.key)}>{labelFor(r.key)}</td>
                  <td className="px-2.5 py-1.5 text-right tabular-nums">{num(r.orders)}</td>
                  <td className="px-2.5 py-1.5 text-right tabular-nums font-medium">{bhd(r.value_bhd, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function MiniTable<T>({
  title, icon: Icon, rows, cols, empty, foot, max = 10,
}: {
  title: string
  icon: LucideIcon
  rows: T[]
  cols: { label: string; align?: 'right'; render: (r: T) => React.ReactNode }[]
  empty: string
  foot?: React.ReactNode
  max?: number
}) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
        <Icon size={16} className="text-primary" /> {title}
      </div>
      {!rows.length ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full text-[13px]">
            <thead className="bg-secondary/50 text-[11px] uppercase text-muted-foreground">
              <tr>
                {cols.map((c) => <th key={c.label} className={cn('px-2.5 py-1.5', c.align === 'right' ? 'text-right' : 'text-left')}>{c.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, max).map((r, i) => (
                <tr key={i} className="border-t">
                  {cols.map((c) => <td key={c.label} className={cn('max-w-[180px] truncate px-2.5 py-1.5 tabular-nums', c.align === 'right' ? 'text-right' : 'font-medium')}>{c.render(r)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {foot && <div className="mt-2 text-[12px] text-muted-foreground">{foot}</div>}
    </Card>
  )
}

function MerchantLink({ m }: { m: MerchantRow }) {
  return (
    <Link to={`/shop-analytics/merchant/${m.customer_id}`}
      className="inline-flex max-w-full items-center gap-1 text-primary hover:underline">
      <span className="truncate">{m.shop || `Merchant #${m.customer_id}`}</span>
      <ChevronRight size={13} className="shrink-0" />
    </Link>
  )
}

function SiteSpeed({ vitals }: { vitals: VitalsData }) {
  if (!vitals.samples) return null
  const lcp = vitals.lcp_ms_p75 == null ? '—' : `${(vitals.lcp_ms_p75 / 1000).toFixed(1)} s`
  return (
    <p className="mt-8 flex flex-wrap items-center gap-1.5 border-t pt-3 text-[12px] text-muted-foreground">
      <Gauge size={13} className="shrink-0" />
      <span className="font-semibold">Site speed</span>
      <span>
        (p75 of {num(vitals.visits ?? vitals.samples)} visits): loads in {lcp} · responds in {num(vitals.inp_ms_p75 ?? 0)} ms ·
        layout shift {vitals.cls_p75 ?? '—'}. For the engineering watch — a load over 2.5 s is slow on a phone.
      </span>
    </p>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function ShopAnalytics() {
  const [days, setDays] = useState<number>(30)
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['shop-analytics', days],
    queryFn: () => apiGet<ShopAnalyticsResp>(`/shop/analytics?days=${days}`),
    // Keep the previous period's figures on screen while the new one loads — no skeleton flash.
    placeholderData: (prev) => prev,
  })

  const win = data?.window
  const chips = useMemo(() => periodChips(win?.end || bahrainToday(), win?.events_since), [win?.end, win?.events_since])
  const activeDays = chips.some((c) => c.days === days) ? days : (chips.find((c) => c.since)?.days ?? days)
  const label = windowLabel(days, win)

  const topProducts = data?.top_products || []
  const daily = data?.daily || []
  const byReferral = data?.attribution?.by_referral || []
  const bySrc = data?.attribution?.by_src || []
  const byCoupon = data?.attribution?.by_coupon || []
  const search = data?.search || {}
  const rails = data?.rails || []
  const engagement = data?.engagement || {}
  const ops = data?.ops || {}
  const identity = data?.identity || {}
  const vitals = data?.vitals || {}
  const links = data?.links || []
  const merchants = data?.merchants
  const counts = merchants?.counts
  const notes = data?.notes || []
  const cancelledTotal = (ops.cancelled_by_customer ?? 0) + (ops.cancelled_by_staff ?? 0)

  const leaderboard: LeaderboardDisplayRow[] = useMemo(
    () => (data?.leaderboard || []).map((r, i) => ({ ...r, rank: i + 1 })),
    [data?.leaderboard],
  )
  const maxLeaderboardValue = useMemo(
    () => Math.max(1, ...leaderboard.map((r) => Number(r.value_bhd) || 0)),
    [leaderboard],
  )

  const productCols: Column<TopProductRow>[] = [
    { key: 'item_code', label: 'Product', render: (_, r) => (
        <div>
          <div className="font-semibold">{r.item_code}</div>
          {r.display_name && r.display_name !== r.item_code && (
            <div className="text-[11px] text-muted-foreground">{r.display_name}</div>
          )}
        </div>
      ) },
    { key: 'units', label: 'Units', align: 'right', render: (_, r) => num(r.units) },
    { key: 'orders', label: 'Orders', align: 'right', render: (_, r) => num(r.orders) },
    { key: 'value_bhd', label: `Value (${VAT_LABEL})`, align: 'right', render: (_, r) => <span className="font-semibold">{bhd(r.value_bhd, 3)}</span> },
  ]

  const leaderCols: Column<LeaderboardDisplayRow>[] = [
    { key: 'salesman', label: 'Salesman', render: (_, r) => (
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-bold text-accent-foreground">{r.rank}</span>
          <span className="font-semibold">{r.salesman || '—'}</span>
        </div>
      ) },
    { key: 'orders', label: 'Orders', align: 'right', render: (_, r) => num(r.orders) },
    { key: 'customers', label: 'Customers', align: 'right', render: (_, r) => num(r.customers) },
    { key: 'aov_bhd', label: `AOV (${VAT_LABEL})`, align: 'right', render: (_, r) => bhd(r.aov_bhd, 3) },
    { key: 'value_bhd', label: `Value (${VAT_LABEL})`, align: 'right', render: (_, r) => {
        const w = Math.max(4, Math.round(((Number(r.value_bhd) || 0) / maxLeaderboardValue) * 100))
        return (
          <div className="flex flex-col items-end gap-1">
            <span className="font-semibold tabular-nums">{bhd(r.value_bhd, 3)}</span>
            <span className="h-1.5 w-20 overflow-hidden rounded-full bg-secondary">
              <span className="block h-full rounded-full bg-primary" style={{ width: `${w}%` }} />
            </span>
          </div>
        )
      } },
  ]

  const showInitialSkeleton = isLoading && !data
  const showHardError = isError && !data
  const showStaleWarning = isError && !!data

  return (
    <div>
      <PageHeader
        title="Shop Analytics"
        subtitle={`What merchants did on the marketplace: visits, searches, orders and who is due · ${MONEY_NOTE}`}
        actions={
          <div className="flex flex-wrap gap-1.5">
            {chips.map((c) => (
              <button key={c.days} type="button" onClick={() => setDays(c.days)}
                className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium transition',
                  activeDays === c.days ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
                {c.label}
              </button>
            ))}
          </div>
        }
      />

      {showStaleWarning && <StaleBanner onRetry={() => refetch()} isRetrying={isFetching} />}

      {showInitialSkeleton ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[72px]" />)}
          </div>
          <Skeleton className="h-[260px]" />
          <Skeleton className="h-[220px]" />
          <Skeleton className="h-[200px]" />
        </div>
      ) : showHardError ? (
        <LoadError error={error} onRetry={() => refetch()} isRetrying={isFetching}
          fallback="Could not load shop analytics from the server." />
      ) : (
        <div className={cn(isFetching && !isLoading && 'opacity-60 transition-opacity duration-200')}>
          {notes.length > 0 && (
            <div className="mb-4 space-y-1 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-2.5 text-[12.5px] text-amber-800">
              {notes.map((n, i) => <div key={i}>{n}</div>)}
            </div>
          )}

          {/* KPI row */}
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Overview · {label}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Orders" value={num(data?.orders ?? 0)} foot={(data?.cancelled ?? 0) > 0 ? `${num(data?.cancelled)} cancelled` : undefined} />
            <Stat label="Order value" value={bhd(data?.value_bhd ?? 0, 3)} tone="violet" foot={`Confirmed, else as ordered · ${VAT_LABEL}`} />
            <Stat label="Avg order value" value={bhd(data?.aov_bhd ?? 0, 3)} foot={VAT_LABEL} />
            <Stat label="Merchants ordering" value={num(data?.customers ?? 0)} foot={identity.repeat_customers ? `${num(identity.repeat_customers)} ordered twice or more` : undefined} />
            <Stat label="Visits" value={num(data?.funnel?.sessions ?? 0)} foot={engagement.new_devices != null ? `${num(engagement.new_devices)} new phones` : undefined} />
            <Stat label="Visits that ordered" value={pctOrDash(data?.funnel?.conversion_pct)} />
          </div>

          {/* Funnel */}
          <div className="mt-5">
            <FunnelCard funnel={data?.funnel} label={label} />
          </div>

          {/* Daily orders */}
          <Card className="mt-5 p-5">
            <div className="mb-1 font-display text-base font-semibold">Orders per day</div>
            <div className="mb-4 text-xs text-muted-foreground">Orders placed (Bahrain days) · {label}</div>
            {!daily.some((d) => (d.orders ?? 0) > 0) ? (
              <div className="rounded-xl border border-dashed py-10 text-center text-sm text-muted-foreground">
                No orders in this period.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={daily} margin={{ top: 6, right: 8, left: 8, bottom: 0 }}>
                  <XAxis dataKey="date" tickFormatter={(d: string) => fmtDayMonth(d)} interval="preserveStartEnd"
                    tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} width={32}
                    tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip
                    formatter={(v) => [num(Number(v)), 'Orders']}
                    labelFormatter={(_, tip) => {
                      const row = tip?.[0]?.payload as DailyRow | undefined
                      return row ? (
                        <>
                          <div className="font-medium text-foreground">{fmtDate(row.date)}</div>
                          <div className="mt-0.5 text-muted-foreground">
                            Value <span className="font-semibold text-foreground">{bhd(row.value_bhd, 3)}</span>
                            {row.sessions != null && <> · Visits <span className="font-semibold text-foreground">{num(row.sessions)}</span></>}
                          </div>
                        </>
                      ) : ''
                    }}
                    contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }}
                    cursor={{ fill: 'hsl(var(--accent) / 0.5)' }} />
                  <Bar dataKey="orders" fill="#6d28d9" radius={[4, 4, 0, 0]} maxBarSize={24} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </Card>

          {/* Rep links */}
          <SectionTitle title="By rep link" sub={`Visits that came through each rep's link, and the orders placed from them · ${label}`} />
          <MiniTable<LinkRow> title="Rep links" icon={Link2} rows={links} max={14}
            cols={[
              { label: 'Link', render: (r) => linkLabel(r) },
              { label: 'Visits', align: 'right', render: (r) => num(r.sessions ?? 0) },
              { label: 'Orders', align: 'right', render: (r) => num(r.orders ?? 0) },
              { label: `Value (${VAT_LABEL})`, align: 'right', render: (r) => bhd(r.value_bhd, 3) },
              { label: 'Ordered', align: 'right', render: (r) => pctOrDash(r.conversion_pct) },
            ]}
            empty="No visits or orders in this period."
            foot="A phone's day counts for the first rep link it came through that day; 'No rep link' is everyone else." />

          {/* Merchants */}
          <SectionTitle title="Merchants" sub="Who ordered in this period, and who is due or has gone quiet (rules, not predictions)" />
          {counts && (
            <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="Merchants" value={num(counts.merchants ?? 0)} foot="Ever ordered" />
              <Stat label="New" value={num(counts.new ?? 0)} foot="First order in 30 days" />
              <Stat label="Came back" value={num(counts.returning ?? 0)} foot="Ordered on 2+ days" />
              <Stat label="Due" value={num(counts.due ?? 0)} tone={counts.due ? 'amber' : undefined} />
              <Stat label="Overdue" value={num(counts.overdue ?? 0)} tone={counts.overdue ? 'rose' : undefined} />
              <Stat label="Dormant" value={num(counts.dormant ?? 0)} tone={counts.dormant ? 'rose' : undefined} />
            </div>
          )}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <MiniTable<MerchantRow> title="Ordered in this period" icon={Store} rows={merchants?.active || []} max={12}
              cols={[
                { label: 'Shop', render: (r) => <MerchantLink m={r} /> },
                { label: 'Rep', render: (r) => r.rep || '—' },
                { label: 'Orders', align: 'right', render: (r) => num(r.orders ?? 0) },
                { label: `Value (${VAT_LABEL})`, align: 'right', render: (r) => bhd(r.value_bhd, 3) },
                { label: '', render: (r) => <MerchantFlags m={r} /> },
              ]}
              empty="No merchant ordered in this period." />
            <MiniTable<MerchantRow> title="Worth a call" icon={UserRoundCheck} rows={merchants?.watch || []} max={12}
              cols={[
                { label: 'Shop', render: (r) => <MerchantLink m={r} /> },
                { label: 'Rep', render: (r) => r.rep_name || '—' },
                { label: 'Last order', align: 'right', render: (r) => (r.days_since_last == null ? '—' : `${num(r.days_since_last)} d ago`) },
                { label: '', render: (r) => <MerchantFlags m={r} /> },
              ]}
              empty={merchants?.available === false
                ? 'Due and dormant flags arrive with the analytics views.'
                : 'Nobody is due or dormant — a rhythm needs 4 order days, so this fills in over the coming weeks.'}
              foot="Due = more than 1.5 x its usual gap since the last order; overdue = 2 x; dormant = 2 of 3 warning signs." />
          </div>

          {/* Search */}
          <SectionTitle title="Search" sub={`What merchants typed, and what they asked for that we could not show · ${label}`} />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <MiniTable<SearchTermRow> title="Top searches" icon={Search} rows={search.terms || []}
              cols={[
                { label: 'Search', render: (r) => r.term },
                { label: 'Times', align: 'right', render: (r) => num(r.searches ?? 0) },
                { label: 'No result', align: 'right', render: (r) => (r.zero ? <span className="font-semibold text-amber-600">{num(r.zero)}</span> : '—') },
              ]}
              empty="No searches in this period."
              foot={search.searches
                ? <>{num(search.searches)} searches · {pctOrDash(search.zero_rate_pct)} found nothing. Counted once per finished search — typing and filter taps are left out.</>
                : undefined} />
            <MiniTable<SearchTermRow> title="Asked for, not found" icon={SearchX} rows={search.zero_terms || []}
              cols={[
                { label: 'Search', render: (r) => r.term },
                { label: 'Times', align: 'right', render: (r) => num(r.zero ?? 0) },
                { label: 'Phones', align: 'right', render: (r) => num(r.devices ?? 0) },
                { label: 'Last', align: 'right', render: (r) => fmtDayMonth(r.last_seen) },
              ]}
              empty="Every search found something."
              foot="Demand we did not meet: add the product, a synonym, or tell the rep." />
          </div>

          {/* Rails */}
          <SectionTitle title="Home rails" sub={`Which parts of the storefront merchants tap and add from · ${label}`} />
          <MiniTable<RailRow> title="Rails & recommendations" icon={LayoutList} rows={rails} max={14}
            cols={[
              { label: 'Rail', render: (r) => labelForRail(r.rail) },
              { label: 'Taps', align: 'right', render: (r) => num(r.clicks ?? 0) },
              { label: 'Adds', align: 'right', render: (r) => num(r.adds ?? 0) },
              { label: 'Visits', align: 'right', render: (r) => num(r.sessions ?? 0) },
            ]}
            empty="No rail taps in this period." foot="A rail nobody taps after four weeks comes off the home page." />

          {/* Top products */}
          <SectionTitle title="Top products" sub={`By order value · ${label}`} />
          <DataTable rows={topProducts} cols={productCols} exportName="yq-shop-top-products"
            empty="No product sales in this period." />

          {/* Salesman leaderboard */}
          <SectionTitle title="Salesman leaderboard" sub={`By order value · ${label}`} />
          <DataTable rows={leaderboard} cols={leaderCols} exportName="yq-shop-leaderboard"
            empty="No salesman activity in this period." />

          {/* Attribution */}
          <SectionTitle title="Attribution" sub={`Where orders came from · ${label}`} />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <AttributionTable title="By referral link" icon={Link2} rows={byReferral} labelFor={labelForReferral}
              keyHeader="Link" empty="No orders yet." />
            <AttributionTable title="By source" icon={Share2} rows={bySrc} labelFor={labelForSrc}
              keyHeader="Source" empty="No orders yet." />
            <AttributionTable title="By coupon" icon={Ticket} rows={byCoupon} labelFor={(k) => k || '—'}
              keyHeader="Coupon" empty="No coupons used yet." />
            <AttributionTable title="How the rep was chosen" icon={UserRoundCheck} rows={ops.by_attribution || []} labelFor={labelForAttribution}
              keyHeader="Rule" empty="No orders yet." />
          </div>

          {/* Order desk */}
          <SectionTitle title="Order desk" sub={`How fast orders are taken · ${label}`} />
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Unassigned now" value={num(ops.unassigned_now ?? 0)} tone={ops.unassigned_now ? 'amber' : undefined} />
            <Stat label={`Waited past ${num(ops.sla_min ?? 30)} min`} value={num(ops.sla_breaches ?? 0)} tone={ops.sla_breaches ? 'amber' : undefined} />
            <Stat label="Median time to confirm" value={ops.median_time_to_confirm_min == null ? '—' : `${num(ops.median_time_to_confirm_min)} min`} />
            <Stat label="Cancelled" value={num(cancelledTotal)} foot={cancelledTotal ? `${num(ops.cancelled_by_customer ?? 0)} by merchants` : undefined} />
            <Stat label="Backorder rate" value={pct(data?.backorder_rate_pct ?? 0, 1)} tone={(data?.backorder_rate_pct ?? 0) > 0 ? 'amber' : undefined} />
            <Stat label="Marketplace orders" value={num(identity.market_orders ?? 0)} foot={`${num(identity.staff_orders ?? 0)} by reps · ${num(identity.legacy_orders ?? 0)} legacy link`} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            <Stat label="Searched" value={num(engagement.search ?? 0)} foot="visits" />
            <Stat label="Started checkout" value={num(engagement.checkout_start ?? 0)} foot="visits" />
            <Stat label="Reorders" value={num(engagement.reorder ?? 0)} />
            <Stat label="Shares" value={num(engagement.share ?? 0)} />
            <Stat label="Installs" value={num(engagement.install ?? 0)} />
            <Stat label="Attribution conflicts" value={num(ops.conflicts ?? 0)} tone={ops.conflicts ? 'amber' : undefined} />
          </div>

          <SiteSpeed vitals={vitals} />
        </div>
      )}
    </div>
  )
}
