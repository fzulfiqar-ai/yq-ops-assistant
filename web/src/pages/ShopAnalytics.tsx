import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, LayoutList, Link2, Loader2, RefreshCw, Search, Share2, Ticket, UserRoundCheck, type LucideIcon } from 'lucide-react'
import { apiGet, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd, num, pct, fmtDate } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, Stat, type Column } from '@/components/DataTable'

// ── types (kept close to docs/SHOP.md's GET /shop/analytics — every field is
// treated as possibly missing; the backend may not even be reachable) ──────────

interface FunnelData {
  sessions?: number | null
  item_views?: number | null
  adds?: number | null
  checkouts?: number | null
  orders?: number | null
  conversion_pct?: number | null
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
}
// marketplace learning loop (16-Sep-2026): search terms, rails, attribution sources, SLA, identity, vitals
interface SearchTermRow {
  term: string
  searches?: number | null
  zero?: number | null
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
  lcp_ms_p75?: number | null
  inp_ms_p75?: number | null
  cls_p75?: number | null
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
}

const PERIODS = [7, 30, 90] as const

// Ordinal ramp for the funnel steps (sessions -> orders): one hue — the brand
// purple — monotone light-to-dark, the darkest step landing exactly on the
// primary (#6d28d9). Validated with the dataviz skill's ordinal checks against
// this app's white card surface:
//   node scripts/validate_palette.js "#c797ff,#af7dff,#9862ff,#8247f4,#6d28d9" \
//     --ordinal --surface "#ffffff"   ->   ALL CHECKS PASS
const FUNNEL_STEPS: { key: keyof FunnelData; label: string; color: string }[] = [
  { key: 'sessions', label: 'Sessions', color: '#c797ff' },
  { key: 'item_views', label: 'Item views', color: '#af7dff' },
  { key: 'adds', label: 'Added to cart', color: '#9862ff' },
  { key: 'checkouts', label: 'Checkouts', color: '#8247f4' },
  { key: 'orders', label: 'Orders', color: '#6d28d9' },
]

function fmtDayMonth(d?: string | null): string {
  if (!d) return ''
  try {
    return new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  } catch {
    return String(d)
  }
}
function pctOrDash(n: number | null | undefined, dp = 1): string {
  return n == null ? '—' : pct(n, dp)
}
function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    try {
      const parsed = JSON.parse(e.body) as { detail?: unknown }
      if (parsed && typeof parsed.detail === 'string') return parsed.detail
    } catch {
      /* body wasn't JSON — fall through to the raw text below */
    }
    return e.body ? e.body.slice(0, 200) : e.message
  }
  if (e instanceof Error) return e.message
  return fallback
}
// docs/SHOP.md names these mappings explicitly for by_referral / by_src; by_coupon
// isn't spelled out field-by-field so its rows are shown as-is (see report).
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
  offers: 'On offer',
  regulars: 'Order again',
  together: 'Bought together',
  complete: 'Complete your order',
  reorder: 'Reorder button',
}
function labelForRail(key?: string | null): string {
  return (key && RAIL_LABEL[key]) || key || '—'
}

// ── small local atoms ───────────────────────────────────────────────────────

function ErrorPanel({ onRetry, isRetrying, message }: { onRetry: () => void; isRetrying: boolean; message?: string }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-10 text-center text-sm text-rose-700">
      <AlertTriangle size={20} className="shrink-0" />
      <span className="max-w-sm">{message || 'Could not load shop analytics from the server.'}</span>
      <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={isRetrying}>
        {isRetrying ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={14} />} Retry
      </Button>
    </div>
  )
}

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

function FunnelCard({ funnel, days }: { funnel: FunnelData | null | undefined; days: number }) {
  const steps = FUNNEL_STEPS.map((s) => ({ ...s, count: Number(funnel?.[s.key] ?? 0) }))
  const allZero = steps.every((s) => s.count === 0)
  const sessions = steps[0].count
  const denom = sessions > 0 ? sessions : Math.max(1, ...steps.map((s) => s.count))

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="font-display text-base font-semibold">Funnel</div>
          <div className="text-xs text-muted-foreground">Sessions to orders · last {days} days</div>
        </div>
        {!allZero && (
          <div className="text-[13px] text-muted-foreground">
            Overall conversion <span className="font-semibold text-foreground">{pctOrDash(funnel?.conversion_pct)}</span>
          </div>
        )}
      </div>

      {allZero ? (
        <div className="rounded-xl border border-dashed py-10 text-center text-sm text-muted-foreground">
          No shop visits yet — share the catalog link to start collecting data.
        </div>
      ) : (
        <div className="space-y-2.5">
          {steps.map((s) => {
            const w = s.count > 0 ? Math.min(100, Math.max(3, Math.round((s.count / denom) * 100))) : 0
            const ofSessions = sessions > 0 ? (s.count / sessions) * 100 : 0
            return (
              <div key={s.key} className="flex items-center gap-3 rounded-lg px-1 py-1 transition-colors hover:bg-accent/30">
                <span className="w-24 shrink-0 text-[13px] font-medium text-muted-foreground sm:w-32">{s.label}</span>
                <span className="h-6 flex-1 overflow-hidden rounded-r bg-secondary">
                  <span className="block h-full rounded-r transition-[width]" style={{ width: `${w}%`, background: s.color }} />
                </span>
                <span className="w-24 shrink-0 text-right text-[13px] tabular-nums sm:w-32">
                  <span className="font-semibold">{num(s.count)}</span>
                  {s.key !== 'sessions' && <span className="ml-1 text-muted-foreground">· {ofSessions.toFixed(0)}%</span>}
                </span>
              </div>
            )
          })}
        </div>
      )}
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
                <th className="px-2.5 py-1.5 text-right">Value</th>
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
  title, icon: Icon, rows, cols, empty, foot,
}: {
  title: string
  icon: LucideIcon
  rows: T[]
  cols: { label: string; align?: 'right'; render: (r: T) => React.ReactNode }[]
  empty: string
  foot?: React.ReactNode
}) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
        <Icon size={16} className="text-primary" /> {title}
      </div>
      {!rows.length ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div className="overflow-hidden rounded-xl border">
          <table className="w-full text-[13px]">
            <thead className="bg-secondary/50 text-[11px] uppercase text-muted-foreground">
              <tr>
                {cols.map((c) => <th key={c.label} className={cn('px-2.5 py-1.5', c.align === 'right' ? 'text-right' : 'text-left')}>{c.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 10).map((r, i) => (
                <tr key={i} className="border-t">
                  {cols.map((c) => <td key={c.label} className={cn('max-w-[160px] truncate px-2.5 py-1.5 tabular-nums', c.align === 'right' ? 'text-right' : 'font-medium')}>{c.render(r)}</td>)}
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

// ── page ─────────────────────────────────────────────────────────────────────

export default function ShopAnalytics() {
  const [days, setDays] = useState<number>(30)
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['shop-analytics', days],
    queryFn: () => apiGet<ShopAnalyticsResp>(`/shop/analytics?days=${days}`),
    // Keep the previous period's figures on screen while the new one loads —
    // no skeleton flash when switching 7d / 30d / 90d.
    placeholderData: (prev) => prev,
  })

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
  const cancelledTotal = (ops.cancelled_by_customer ?? 0) + (ops.cancelled_by_staff ?? 0)

  // react-query keeps a stable array reference across renders when the underlying
  // data hasn't changed, so depending on `data?.leaderboard` directly (rather than
  // an intermediate `|| []` local, a fresh reference every render) lets this memoize.
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
    { key: 'value_bhd', label: 'Value', align: 'right', render: (_, r) => <span className="font-semibold">{bhd(r.value_bhd, 3)}</span> },
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
    { key: 'aov_bhd', label: 'AOV', align: 'right', render: (_, r) => bhd(r.aov_bhd, 3) },
    { key: 'value_bhd', label: 'Value', align: 'right', render: (_, r) => {
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

  const kpis = {
    orders: data?.orders ?? 0,
    cancelled: data?.cancelled ?? 0,
    value_bhd: data?.value_bhd ?? 0,
    aov_bhd: data?.aov_bhd ?? 0,
    units: data?.units ?? 0,
    customers: data?.customers ?? 0,
    backorder_rate_pct: data?.backorder_rate_pct ?? 0,
    conversion_pct: data?.funnel?.conversion_pct ?? null,
  }

  const showInitialSkeleton = isLoading && !data
  const showHardError = isError && !data
  const showStaleWarning = isError && !!data

  return (
    <div>
      <PageHeader
        title="Shop Analytics"
        subtitle="Orders, funnel, attribution and the marketplace learning loop"
        actions={
          <div className="flex gap-1.5">
            {PERIODS.map((d) => (
              <button key={d} type="button" onClick={() => setDays(d)}
                className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium transition',
                  days === d ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
                {d}d
              </button>
            ))}
          </div>
        }
      />

      {showStaleWarning && <StaleBanner onRetry={() => refetch()} isRetrying={isFetching} />}

      {showInitialSkeleton ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
            {Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-[72px]" />)}
          </div>
          <Skeleton className="h-[220px]" />
          <Skeleton className="h-[220px]" />
          <Skeleton className="h-[200px]" />
          <Skeleton className="h-[200px]" />
        </div>
      ) : showHardError ? (
        <ErrorPanel onRetry={() => refetch()} isRetrying={isFetching}
          message={errorText(error, 'Could not load shop analytics from the server.')} />
      ) : (
        <div className={cn(isFetching && !isLoading && 'opacity-60 transition-opacity duration-200')}>
          {/* KPI row */}
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Overview · last {days} days
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
            <Stat label="Orders" value={num(kpis.orders)} foot={kpis.cancelled > 0 ? `${num(kpis.cancelled)} cancelled` : undefined} />
            <Stat label="Order value" value={bhd(kpis.value_bhd, 3)} tone="violet" />
            <Stat label="Avg order value" value={bhd(kpis.aov_bhd, 3)} />
            <Stat label="Units" value={num(kpis.units)} />
            <Stat label="Customers" value={num(kpis.customers)} />
            <Stat label="Backorder rate" value={pct(kpis.backorder_rate_pct, 1)} tone={kpis.backorder_rate_pct > 0 ? 'amber' : undefined} />
            <Stat label="Conversion" value={pctOrDash(kpis.conversion_pct)} />
          </div>

          {/* Funnel */}
          <div className="mt-5">
            <FunnelCard funnel={data?.funnel} days={days} />
          </div>

          {/* Daily orders */}
          <Card className="mt-5 p-5">
            <div className="mb-1 font-display text-base font-semibold">Orders per day</div>
            <div className="mb-4 text-xs text-muted-foreground">Orders placed · last {days} days</div>
            {!daily.length ? (
              <div className="rounded-xl border border-dashed py-10 text-center text-sm text-muted-foreground">
                No orders yet in this period.
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

          {/* Top products */}
          <div className="mb-3 mt-6">
            <div className="font-display text-base font-semibold">Top products</div>
            <div className="text-xs text-muted-foreground">By order value · last {days} days</div>
          </div>
          <DataTable rows={topProducts} cols={productCols} exportName="yq-shop-top-products"
            empty="No product sales yet in this period." />

          {/* Salesman leaderboard */}
          <div className="mb-3 mt-6">
            <div className="font-display text-base font-semibold">Salesman leaderboard</div>
            <div className="text-xs text-muted-foreground">By order value · last {days} days</div>
          </div>
          <DataTable rows={leaderboard} cols={leaderCols} exportName="yq-shop-leaderboard"
            empty="No salesman activity yet in this period." />

          {/* Attribution */}
          <div className="mb-3 mt-6">
            <div className="font-display text-base font-semibold">Attribution</div>
            <div className="text-xs text-muted-foreground">Where orders came from · last {days} days</div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <AttributionTable title="By referral link" icon={Link2} rows={byReferral} labelFor={labelForReferral}
              keyHeader="Link" empty="No orders yet." />
            <AttributionTable title="By source" icon={Share2} rows={bySrc} labelFor={labelForSrc}
              keyHeader="Source" empty="No orders yet." />
            <AttributionTable title="By coupon" icon={Ticket} rows={byCoupon} labelFor={(k) => k || '—'}
              keyHeader="Coupon" empty="No coupons used yet." />
          </div>

          {/* Marketplace learning loop */}
          <div className="mb-3 mt-6">
            <div className="font-display text-base font-semibold">Marketplace</div>
            <div className="text-xs text-muted-foreground">How fast orders are taken, what merchants search for, which rails they use · last {days} days</div>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Unassigned now" value={num(ops.unassigned_now ?? 0)} tone={ops.unassigned_now ? 'amber' : undefined} />
            <Stat label={`Waited past ${num(ops.sla_min ?? 30)} min`} value={num(ops.sla_breaches ?? 0)} tone={ops.sla_breaches ? 'amber' : undefined} />
            <Stat label="Attribution conflicts" value={num(ops.conflicts ?? 0)} tone={ops.conflicts ? 'amber' : undefined} />
            <Stat label="Median time to confirm" value={ops.median_time_to_confirm_min == null ? '—' : `${num(ops.median_time_to_confirm_min)} min`} />
            <Stat label="Repeat customers" value={pctOrDash(identity.repeat_rate_pct)} foot={`${num(identity.repeat_customers ?? 0)} of ${num(identity.customers ?? 0)}`} />
            <Stat label="Cancelled" value={num(cancelledTotal)} foot={cancelledTotal ? `${num(ops.cancelled_by_customer ?? 0)} by merchants` : undefined} />
          </div>
          <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
            <MiniTable<SearchTermRow> title="Search terms" icon={Search} rows={search.terms || []}
              cols={[
                { label: 'Term', render: (r) => r.term },
                { label: 'Searches', align: 'right', render: (r) => num(r.searches ?? 0) },
                { label: 'No result', align: 'right', render: (r) => (r.zero ? <span className="font-semibold text-amber-600">{num(r.zero)}</span> : '—') },
              ]}
              empty="No searches yet."
              foot={search.searches ? <>{num(search.searches)} searches · {pctOrDash(search.zero_rate_pct)} found nothing — add synonyms or products for those terms</> : undefined} />
            <MiniTable<RailRow> title="Rails & recommendations" icon={LayoutList} rows={rails}
              cols={[
                { label: 'Rail', render: (r) => labelForRail(r.rail) },
                { label: 'Taps', align: 'right', render: (r) => num(r.clicks ?? 0) },
                { label: 'Sessions', align: 'right', render: (r) => num(r.sessions ?? 0) },
              ]}
              empty="No rail taps yet." foot="A rail nobody taps after four weeks comes off the home page." />
            <AttributionTable title="By attribution" icon={UserRoundCheck} rows={ops.by_attribution || []} labelFor={labelForAttribution}
              keyHeader="How the rep was chosen" empty="No orders yet." />
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
            <Stat label="Devices" value={num(engagement.devices ?? 0)} />
            <Stat label="Searched" value={num(engagement.search ?? 0)} foot="sessions" />
            <Stat label="Checkout started" value={num(engagement.checkout_start ?? 0)} foot="sessions" />
            <Stat label="Reorders" value={num(engagement.reorder ?? 0)} />
            <Stat label="Shares" value={num(engagement.share ?? 0)} />
            <Stat label="Installs" value={num(engagement.install ?? 0)} />
            <Stat label="Marketplace orders" value={num(identity.market_orders ?? 0)} foot={`${num(identity.staff_orders ?? 0)} by staff · ${num(identity.legacy_orders ?? 0)} legacy link`} />
            <Stat label="Speed (LCP p75)" value={vitals.lcp_ms_p75 == null ? '—' : `${(vitals.lcp_ms_p75 / 1000).toFixed(1)} s`}
              tone={vitals.lcp_ms_p75 != null && vitals.lcp_ms_p75 > 2500 ? 'amber' : undefined}
              foot={vitals.samples ? `INP ${num(vitals.inp_ms_p75 ?? 0)} ms · CLS ${vitals.cls_p75 ?? '—'} · ${num(vitals.samples)} visits` : 'No field data yet'} />
          </div>
        </div>
      )}
    </div>
  )
}
