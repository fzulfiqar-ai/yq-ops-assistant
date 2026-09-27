import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ArrowLeft, Boxes, Lightbulb, ReceiptText, Tags, TrendingUp } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { bhd, num, fmtDate } from '@/lib/format'
import { VAT_LABEL } from '@/lib/basisText'
import { LoadError } from '@/components/LoadError'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Stat } from '@/components/DataTable'

// GET /shop/analytics/merchant/{id} (docs/SHOP.md). Facts come from v_merchant_360 (or the same rules in
// Python before the analytics views exist); INSIGHTS are rules with their evidence — shown as insights,
// never as fact. No phone, email or person's name travels in this payload.

interface CategoryRow { category: string; value_bhd?: number | null; units?: number | null }
interface SkuRow { item_code: string; display_name?: string | null; units?: number | null; value_bhd?: number | null; orders?: number | null }
interface Merchant {
  customer_id: number
  shop?: string | null
  area?: string | null
  rep_name?: string | null
  rep_source?: string | null
  focus_customer_id?: number | null
  focus_customer_name?: string | null
  orders?: number | null
  open_orders?: number | null
  cancelled_orders?: number | null
  value_bhd?: number | null
  confirmed_value_bhd?: number | null
  delivered_value_bhd?: number | null
  aov_bhd?: number | null
  first_order_at?: string | null
  last_order_at?: string | null
  days_since_last?: number | null
  order_days?: number | null
  orders_90d?: number | null
  value_90d_bhd?: number | null
  cadence_days?: number | null
  cadence_basis?: 'own' | 'all_shops' | null
  due_status?: 'unknown' | 'ok' | 'due' | 'overdue' | null
  dormant?: boolean | null
  is_new?: boolean | null
  top_categories?: CategoryRow[] | null
  top_skus?: SkuRow[] | null
}
interface Insight { kind: string; level: 'act' | 'watch' | 'info'; title: string; evidence: string; rule: string }
interface Trend { text?: string | null; enough_history?: boolean | null; change_pct?: number | null }
interface MonthRow { month: string; orders?: number | null; value_bhd?: number | null }
interface OrderRow {
  id: number
  order_no?: string | null
  status_label?: string | null
  created_at?: string | null
  value_bhd?: number | null
  requested_bhd?: number | null
  units?: number | null
  rep?: string | null
}
interface ProfileResp {
  merchant: Merchant
  source?: 'views' | 'orders' | null
  notes?: string[] | null
  trend?: Trend | null
  insights?: Insight[] | null
  monthly?: MonthRow[] | null
  orders?: OrderRow[] | null
}

const LEVEL: Record<Insight['level'], { tone: BadgeTone; label: string }> = {
  act: { tone: 'rose', label: 'Act' },
  watch: { tone: 'amber', label: 'Watch' },
  info: { tone: 'grey', label: 'Note' },
}
const REP_SOURCE: Record<string, string> = {
  assigned: 'assigned in Shop admin',
  sticky: 'the rep it keeps ordering through',
  last_order: 'from its last order',
}

function monthShort(m: string): string {
  const d = new Date(`${m}-01T00:00:00Z`)
  return Number.isNaN(d.getTime()) ? m : d.toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' })
}
function rhythm(m: Merchant): { value: string; foot: string } {
  if (m.cadence_days == null) return { value: 'Not known yet', foot: `${num(m.order_days ?? 0)} order day(s) — a rhythm needs 4` }
  const basis = m.cadence_basis === 'own' ? 'its own median gap' : 'all shops (not enough of its own yet)'
  return { value: `Every ~${m.cadence_days} days`, foot: basis }
}
function statusBadges(m: Merchant) {
  const out: { tone: BadgeTone; label: string }[] = []
  if (m.dormant) out.push({ tone: 'rose', label: 'Dormant' })
  if (m.due_status === 'overdue') out.push({ tone: 'rose', label: 'Overdue' })
  if (m.due_status === 'due') out.push({ tone: 'amber', label: 'Due' })
  if (m.due_status === 'ok') out.push({ tone: 'green', label: 'On rhythm' })
  if (m.is_new) out.push({ tone: 'accent', label: 'New' })
  return out
}

export default function MerchantProfile() {
  const { id = '' } = useParams()
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['shop-merchant', id],
    queryFn: () => apiGet<ProfileResp>(`/shop/analytics/merchant/${encodeURIComponent(id)}`),
    enabled: !!id,
  })
  const back = (
    <Link to="/shop-analytics" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition hover:text-foreground">
      <ArrowLeft size={15} /> Shop analytics
    </Link>
  )

  if (isLoading && !data) {
    return (
      <div>
        {back}
        <Skeleton className="mb-4 h-[60px]" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[76px]" />)}
        </div>
        <Skeleton className="mt-4 h-[180px]" />
      </div>
    )
  }
  if (isError && !data) {
    return (
      <div>
        {back}
        <LoadError error={error} onRetry={() => refetch()} isRetrying={isFetching} fallback="Could not load this merchant." />
      </div>
    )
  }
  if (!data) return null

  const m = data.merchant
  const r = rhythm(m)
  const insights = data.insights || []
  const monthly = data.monthly || []
  const cats = m.top_categories || []
  const skus = m.top_skus || []
  const orders = data.orders || []
  const subtitle = [m.area, m.rep_name ? `Rep: ${m.rep_name}` : 'No rep yet',
    m.focus_customer_name ? `Focus: ${m.focus_customer_name}` : 'Not linked to a Focus customer yet'].filter(Boolean).join(' · ')

  return (
    <div>
      {back}
      <PageHeader title={m.shop || `Merchant #${m.customer_id}`} subtitle={subtitle}
        actions={<div className="flex flex-wrap gap-1.5">{statusBadges(m).map((b) => <Badge key={b.label} tone={b.tone}>{b.label}</Badge>)}</div>} />

      {(data.notes || []).length > 0 && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-2.5 text-[12.5px] text-amber-800">
          {(data.notes || []).map((n, i) => <div key={i}>{n}</div>)}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Lifetime value" value={bhd(m.value_bhd, 3)} tone="violet"
          foot={`${num(m.orders ?? 0)} orders · AOV ${m.aov_bhd == null ? '—' : bhd(m.aov_bhd, 3)} · ${VAT_LABEL}`} />
        <Stat label="Last 90 days" value={bhd(m.value_90d_bhd, 3)} foot={`${num(m.orders_90d ?? 0)} orders · ${VAT_LABEL}`} />
        <Stat label="Last order" value={m.last_order_at ? fmtDate(m.last_order_at) : '—'}
          foot={m.days_since_last == null ? undefined : m.days_since_last === 0 ? 'today' : `${num(m.days_since_last)} days ago`} />
        <Stat label="Rhythm" value={r.value} foot={r.foot} />
      </div>
      <p className="mt-2 text-[12px] text-muted-foreground">
        Confirmed {bhd(m.confirmed_value_bhd, 3)} · delivered {bhd(m.delivered_value_bhd, 3)} · {num(m.open_orders ?? 0)} open
        {m.cancelled_orders ? ` · ${num(m.cancelled_orders)} cancelled` : ''}. Values are confirmed where confirmed, else as
        ordered, {VAT_LABEL} (the marketplace's stored totals; the Command Centre shows them ex-VAT); test orders are
        left out.
        {m.rep_name && m.rep_source && REP_SOURCE[m.rep_source] ? ` Rep: ${REP_SOURCE[m.rep_source]}.` : ''}
      </p>

      {/* Insights — rules with their evidence, never facts */}
      <Card className="mt-5 p-5">
        <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
          <Lightbulb size={16} className="text-primary" /> Insights
        </div>
        {!insights.length ? (
          <p className="text-sm text-muted-foreground">No insight yet — they come from its order history.</p>
        ) : (
          <ul className="space-y-3">
            {insights.map((x, i) => (
              <li key={i} className="rounded-xl border px-3.5 py-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge tone="ink">INSIGHT</Badge>
                  <Badge tone={LEVEL[x.level]?.tone ?? 'grey'}>{LEVEL[x.level]?.label ?? x.level}</Badge>
                  <span className="text-[13.5px] font-semibold">{x.title}</span>
                </div>
                <div className="mt-1 text-[12.5px] text-foreground/80">{x.evidence}</div>
                <div className="mt-0.5 text-[11.5px] text-muted-foreground">Rule: {x.rule}</div>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-[11.5px] text-muted-foreground">
          Insights are simple rules on this shop's own orders — a prompt to look, not a prediction.
        </p>
      </Card>

      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Trend against its own usual */}
        <Card className="p-5">
          <div className="mb-1 flex items-center gap-2 font-display text-base font-semibold">
            <TrendingUp size={16} className="text-primary" /> Against its own usual
          </div>
          <div className="mb-3 text-[12.5px] text-muted-foreground">{data.trend?.text || '—'}</div>
          {monthly.some((x) => (x.orders ?? 0) > 0) ? (
            <ResponsiveContainer width="100%" height={170}>
              <BarChart data={monthly} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="month" tickFormatter={monthShort} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                  axisLine={false} tickLine={false} />
                <YAxis width={44} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(v) => [bhd(Number(v), 3), `Value (${VAT_LABEL})`]} labelFormatter={(l) => monthShort(String(l))}
                  contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }}
                  cursor={{ fill: 'hsl(var(--accent) / 0.5)' }} />
                <Bar dataKey="value_bhd" fill="#6d28d9" radius={[4, 4, 0, 0]} maxBarSize={28} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="rounded-xl border border-dashed py-8 text-center text-sm text-muted-foreground">No orders in the last six months.</div>
          )}
        </Card>

        {/* What it buys */}
        <Card className="p-5">
          <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
            <Tags size={16} className="text-primary" /> What it buys
          </div>
          {!cats.length ? (
            <p className="text-sm text-muted-foreground">Nothing yet.</p>
          ) : (
            <div className="space-y-2">
              {cats.map((c) => {
                const share = m.value_bhd ? Math.round(((c.value_bhd ?? 0) / m.value_bhd) * 100) : 0
                return (
                  <div key={c.category} className="flex items-center gap-3">
                    <span className="w-36 shrink-0 truncate text-[13px] font-medium capitalize">{c.category.toLowerCase()}</span>
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
                      <span className="block h-full rounded-full bg-primary" style={{ width: `${Math.max(3, share)}%` }} />
                    </span>
                    <span className="w-32 shrink-0 text-right text-[12.5px] tabular-nums">{bhd(c.value_bhd, 3)} · {share}%</span>
                  </div>
                )
              })}
            </div>
          )}
          <div className="mb-2 mt-5 flex items-center gap-2 text-[13px] font-semibold"><Boxes size={14} className="text-primary" /> Top items</div>
          {!skus.length ? (
            <p className="text-sm text-muted-foreground">Nothing yet.</p>
          ) : (
            <table className="w-full text-[13px]">
              <tbody>
                {skus.map((s) => (
                  <tr key={s.item_code} className="border-t first:border-t-0">
                    <td className="py-1.5 pr-2">
                      <div className="font-semibold">{s.item_code}</div>
                      {s.display_name && s.display_name !== s.item_code && <div className="truncate text-[11px] text-muted-foreground">{s.display_name}</div>}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-muted-foreground">{num(s.units ?? 0)} u · {num(s.orders ?? 0)} ord.</td>
                    <td className="py-1.5 pl-2 text-right font-medium tabular-nums">{bhd(s.value_bhd, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      {/* Orders */}
      <Card className="mt-5 p-5">
        <div className="mb-3 flex items-center gap-2 font-display text-base font-semibold">
          <ReceiptText size={16} className="text-primary" /> Recent orders
        </div>
        {!orders.length ? (
          <p className="text-sm text-muted-foreground">No orders.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full text-[13px]">
              <thead className="bg-secondary/50 text-[11px] uppercase text-muted-foreground">
                <tr>
                  <th className="px-2.5 py-1.5 text-left">Order</th>
                  <th className="px-2.5 py-1.5 text-left">Placed</th>
                  <th className="px-2.5 py-1.5 text-left">Status</th>
                  <th className="px-2.5 py-1.5 text-left">Rep</th>
                  <th className="px-2.5 py-1.5 text-right">Units</th>
                  <th className="px-2.5 py-1.5 text-right">Value</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className="border-t">
                    <td className="px-2.5 py-1.5 font-medium">{o.order_no || `#${o.id}`}</td>
                    <td className="px-2.5 py-1.5">{fmtDate(o.created_at)}</td>
                    <td className="px-2.5 py-1.5">{o.status_label || '—'}</td>
                    <td className="max-w-[140px] truncate px-2.5 py-1.5">{o.rep || '—'}</td>
                    <td className="px-2.5 py-1.5 text-right tabular-nums">{num(o.units ?? 0)}</td>
                    <td className="px-2.5 py-1.5 text-right tabular-nums font-medium"
                      title={o.requested_bhd != null && o.requested_bhd !== o.value_bhd ? `Ordered ${bhd(o.requested_bhd, 3)}` : undefined}>
                      {bhd(o.value_bhd, 3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
