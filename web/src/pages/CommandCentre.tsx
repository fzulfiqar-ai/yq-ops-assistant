import type { ReactNode } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, CheckCircle2, Info, Loader2, RefreshCw,
} from 'lucide-react'
import { apiGet } from '@/lib/api'
import { isManagement, useAuth, type Me } from '@/lib/auth'
import { managementMayOpen } from '@/lib/nav'
import { cn } from '@/lib/utils'
import { LoadError } from '@/components/LoadError'
import { FreshnessChip, type Freshness } from '@/components/FreshnessChip'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * The Management Command Centre (Sprint 4, plan §8): the company on one page, the same layout every
 * week, inputs before outputs. Needs attention first, then Sales, Order health, Team, Customers,
 * Products & stock, Profitability and Receivables. Every tile carries its basis line ("Accessories ·
 * ex-VAT · 1–24 Sep · data to 24 Sep") exactly as the API's metric dictionary (app/metrics.py) wrote
 * it; nothing is recomputed here. Read-only for everyone; admin and management only.
 *
 * `view` = 'team' and 'customers' are the same payload with that module in full (management's Team
 * and Customers menu items).
 */

type PeriodKey = 'today' | '7d' | 'mtd' | 'last_month' | 'quarter'
const PERIODS: { key: PeriodKey; label: string }[] = [
  { key: 'today', label: 'Today' },
  { key: '7d', label: '7 days' },
  { key: 'mtd', label: 'Month to date' },
  { key: 'last_month', label: 'Last month' },
  { key: 'quarter', label: 'Quarter' },
]

interface Drill { to: string; label: string }
interface Chip { label: string; value: number | null; unit: 'bhd' | 'count' | 'pct'; share_pct?: number | null; items?: number }
interface Stage { key: string; label: string; orders: number; units: number; bhd: number }
interface WeekPoint { week_start: string; label: string; value: number; partial: boolean }
interface TeamRow {
  salesman: string; name: string; salesman_id: number | null; net_bhd: number; no_target: boolean; tier_reached: number | null
  next_tier: number | null; gap_to_next_bhd: number | null; progress_pct: number | null; invoices: number; shops: number
  named_share_pct: number | null; last_invoice: string | null; business_days_since_invoice: number | null
  marketplace_orders: number; marketplace_bhd: number
}
interface Mover { item: string; net_30_bhd: number; net_prev_bhd: number; delta_bhd: number; qty_30: number; qty_prev: number }
type Row = Record<string, string | number | null>

interface Tile {
  key: string; label: string; unit: string; basis: string; drill: Drill | null; available: boolean
  value: number | null; note?: string | null
  compare?: { value: number; label: string } | null; delta_pct?: number | null; invoices?: number
  chips?: Chip[]
  month?: string; mtd_bhd?: number; target_bhd?: number | null; projected_bhd?: number
  business_days_done?: number; business_days_total?: number; business_days_left?: number
  pct_of_target?: number | null; on_track?: boolean | null; needed_per_business_day_bhd?: number | null
  series?: WeekPoint[]
  stages?: Stage[]; cancelled?: number; open?: number
  units_ordered?: number; units_accepted?: number; orders?: number
  p50_hours?: number | null; p90_hours?: number | null; open_included?: number
  bhd?: number; oldest_hours?: number | null; by_rep?: { rep: string; orders: number; bhd: number }[]
  matched?: number; eligible?: number; unmatched_bhd?: number; by_shop?: number
  rows?: TeamRow[]; totals?: { net_bhd: number; marketplace_orders: number }
  new_30d?: number; items?: Row[]; all?: Row[]
  top10_bhd?: number; named_bhd?: number; invoices_pct?: number | null; sales_pct?: number | null
  rising?: Mover[]; falling?: Mover[]; items_held?: number; items_uncosted?: number
  gp_bhd?: number; net_ex_vat_bhd?: number; below_cost?: number; coverage_pct?: number | null
  source?: string; rows_bhd?: number; gap_bhd?: number; accounts?: number; over_90_bhd?: number
}
interface AttentionRow { label: string; sub?: string; bhd?: number | null; to?: string }
interface AttentionItem {
  key: string; tone: 'alert' | 'warn' | 'info'; title: string; detail: string; basis: string
  count: number | null; bhd: number | null; drill: Drill | null; rows: AttentionRow[]
}
interface Module {
  key: string; title: string; tiles: Tile[]; drill?: Drill | null; live?: boolean
  items?: AttentionItem[]; all_clear?: boolean
}
interface Span { start: string; end: string; label: string; business_days: number; basis?: string }
interface Overview {
  period: { key: PeriodKey; label: string; focus: Span | null; compare: Span | null; live: Span }
  freshness: Freshness & { stock_as_of?: string | null; ar_as_of?: string | null }
  modules: Module[]
  notes: string[]
  unavailable: string[]
  generated_at: string
}

// Colour: the marketplace's plum identity (#6D4091 plum, #824FAB sash, #F3ECF8 lilac; plan §27) for accents
// only — surfaces stay on the portal tokens (bg-card, border, muted) so dark mode holds.

/* ─────────────────────────── formatting ─────────────────────────── */

const fmtBhd = (n?: number | null) =>
  n == null ? '—' : `BHD ${n.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`
const fmtPct = (n?: number | null) => (n == null ? '—' : `${n.toFixed(1)} %`)
const fmtCount = (n?: number | null) => (n == null ? '—' : n.toLocaleString('en-US'))
const fmtHours = (h?: number | null) => (h == null ? '—' : h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} days`)
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** '2026-09-24' → '24 Sep', the same form the API's basis lines use */
function fmtDay(iso?: string | number | null): string {
  if (!iso) return '—'
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso))
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : String(iso)
}
function fmtValue(t: Tile): string {
  if (t.unit === 'pct') return fmtPct(t.value)
  if (t.unit === 'count' || t.unit === 'list') return fmtCount(t.value)
  if (t.unit === 'hours') return fmtHours(t.value)
  return fmtBhd(t.value)
}
const num = (v: string | number | null | undefined) => (typeof v === 'number' ? v : v == null ? null : Number(v))

/** Drill links the viewer may open (management reads its own pages only; admins everything). */
function mayOpen(me: Me | null, to: string): boolean {
  if (!me) return false
  if (!isManagement(me)) return true
  return managementMayOpen(to.split('?')[0])
}

/* ─────────────────────────── small pieces ─────────────────────────── */

function DrillLink({ drill, me, className }: { drill?: Drill | null; me: Me | null; className?: string }) {
  if (!drill || !mayOpen(me, drill.to)) return null
  return (
    <Link to={drill.to} className={cn('inline-flex items-center gap-1 text-[12px] font-semibold text-[#6D4091] hover:underline dark:text-[#c7a6e6]', className)}>
      {drill.label} <ArrowRight size={13} aria-hidden="true" />
    </Link>
  )
}

function Basis({ text }: { text: string }) {
  return (
    <p className="mt-3 flex items-start gap-1.5 border-t border-border/70 pt-2 text-[11px] leading-snug text-muted-foreground">
      <Info size={12} className="mt-[1px] shrink-0" aria-hidden="true" />
      <span>{text}</span>
    </p>
  )
}

function Delta({ pct, label }: { pct?: number | null; label?: string }) {
  if (pct == null) return label ? <span className="text-[12px] text-muted-foreground">{label}</span> : null
  const up = pct >= 0
  return (
    <span className={cn('inline-flex items-center gap-0.5 text-[12px] font-semibold',
      up ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400')}>
      {up ? <ArrowUpRight size={13} aria-hidden="true" /> : <ArrowDownRight size={13} aria-hidden="true" />}
      {up ? '+' : ''}{pct.toFixed(1)} %{label ? <span className="ml-1 font-normal text-muted-foreground">{label}</span> : null}
    </span>
  )
}

function ChipRow({ chips }: { chips: Chip[] }) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {chips.map((c) => (
        <span key={c.label} className="inline-flex items-baseline gap-1.5 rounded-full bg-[#F3ECF8] px-2.5 py-1 text-[12px] text-[#3b2352] dark:bg-[#6D4091]/20 dark:text-[#e6d6f5]">
          <span className="font-medium">{c.label}</span>
          <span className="font-bold tabular-nums">{c.unit === 'bhd' ? fmtBhd(c.value) : c.unit === 'pct' ? fmtPct(c.value) : fmtCount(c.value)}</span>
          {c.share_pct != null && <span className="text-[11px] opacity-75">{fmtPct(c.share_pct)}</span>}
        </span>
      ))}
    </div>
  )
}

function Bar({ pct, className }: { pct: number; className?: string }) {
  const w = Math.max(0, Math.min(100, pct))
  return (
    <div className={cn('h-2 w-full overflow-hidden rounded-full bg-[#F3ECF8] dark:bg-[#6D4091]/20', className)}>
      <div className="h-full rounded-full bg-gradient-to-r from-[#6D4091] to-[#824FAB]" style={{ width: `${w}%` }} />
    </div>
  )
}

function Sparkline({ series }: { series: WeekPoint[] }) {
  const max = Math.max(1, ...series.map((p) => p.value))
  return (
    <div className="mt-3">
      <div className="flex h-20 items-end gap-1" role="img"
        aria-label={`Weekly Accessories sales, ${series.map((p) => `${p.label}: ${fmtBhd(p.value)}`).join('; ')}`}>
        {series.map((p) => (
          <div key={p.week_start} className="flex h-full flex-1 flex-col justify-end" title={`Week of ${p.label}: ${fmtBhd(p.value)}${p.partial ? ' (so far)' : ''}`}>
            <div
              className={cn('w-full rounded-t-sm', p.partial ? 'bg-[#824FAB]/45' : 'bg-[#6D4091]')}
              style={{ height: `${Math.max(2, (p.value / max) * 100)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span>{series[0]?.label}</span>
        <span>{series[series.length - 1]?.label} (so far)</span>
      </div>
    </div>
  )
}

function MiniList({ rows }: { rows: { label: string; value: string; sub?: string; tone?: 'up' | 'down' }[] }) {
  if (!rows.length) return <p className="mt-2 text-[12.5px] text-muted-foreground">Nothing to list.</p>
  return (
    <ul className="mt-2 divide-y divide-border/70">
      {rows.map((r, i) => (
        <li key={`${r.label}-${i}`} className="flex items-baseline justify-between gap-3 py-1.5 text-[12.5px]">
          <span className="min-w-0">
            <span className="block truncate font-medium" title={r.label}>{r.label}</span>
            {r.sub && <span className="block truncate text-[11px] text-muted-foreground">{r.sub}</span>}
          </span>
          <span className={cn('shrink-0 font-semibold tabular-nums',
            r.tone === 'up' && 'text-emerald-600 dark:text-emerald-400', r.tone === 'down' && 'text-rose-600 dark:text-rose-400')}>
            {r.value}
          </span>
        </li>
      ))}
    </ul>
  )
}

/* ─────────────────────────── tiles ─────────────────────────── */

function TileBody({ t }: { t: Tile }) {
  switch (t.key) {
    case 'sales.accessories':
      return (
        <>
          <BigValue t={t} />
          <div className="mt-1"><Delta pct={t.delta_pct} label={t.compare ? `vs ${fmtBhd(t.compare.value)} (${t.compare.label})` : undefined} /></div>
          {t.invoices != null && <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.invoices)} invoices</p>}
        </>
      )
    case 'sales.pace': {
      const target = t.target_bhd
      return (
        <>
          <BigValue t={t} suffix="projected" />
          {target ? (
            <>
              <Bar pct={t.pct_of_target ?? 0} className="mt-3" />
              <p className="mt-1.5 text-[12px] text-muted-foreground">
                {fmtBhd(t.mtd_bhd)} of {fmtBhd(target)} ({fmtPct(t.pct_of_target)}) · {t.business_days_done} of {t.business_days_total} business days
              </p>
              <p className={cn('mt-1 text-[12px] font-semibold', t.on_track ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400')}>
                {t.on_track ? 'On track for the target' : t.business_days_left
                  ? `Needs ${fmtBhd(t.needed_per_business_day_bhd)} a business day for the last ${t.business_days_left}`
                  : 'Short of the target this month'}
              </p>
            </>
          ) : (
            <p className="mt-2 text-[12px] text-muted-foreground">No company target is set (Business settings).</p>
          )}
        </>
      )
    }
    case 'sales.trend':
      return t.series ? <Sparkline series={t.series} /> : null
    case 'orders.funnel':
      return (
        <>
          <ol className="mt-2 grid grid-cols-3 gap-2">
            {(t.stages || []).map((s, i) => (
              <li key={s.key} className="relative rounded-xl bg-[#F3ECF8] p-2.5 dark:bg-[#6D4091]/15">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[#6D4091] dark:text-[#c7a6e6]">{s.label}</div>
                <div className="mt-0.5 font-display text-[1.35rem] font-bold tabular-nums">{fmtCount(s.orders)}</div>
                <div className="text-[11px] text-muted-foreground">{fmtCount(s.units)} units</div>
                <div className="text-[11px] font-medium tabular-nums">{fmtBhd(s.bhd)}</div>
                {i < 2 && <ArrowRight size={14} className="absolute -right-2 top-1/2 hidden -translate-y-1/2 text-[#824FAB] sm:block" aria-hidden="true" />}
              </li>
            ))}
          </ol>
          <p className="mt-2 text-[12px] text-muted-foreground">{fmtCount(t.open)} waiting to be confirmed · {fmtCount(t.cancelled)} cancelled</p>
        </>
      )
    case 'orders.accepted':
      return (
        <>
          <BigValue t={t} />
          {t.units_ordered != null && <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.units_accepted)} of {fmtCount(t.units_ordered)} units on {fmtCount(t.orders)} orders</p>}
        </>
      )
    case 'orders.confirm_time':
      return (
        <>
          <div className="flex items-baseline gap-4">
            <div><div className="text-[11px] text-muted-foreground">Typical (P50)</div><div className="font-display text-[1.5rem] font-bold">{fmtHours(t.p50_hours)}</div></div>
            <div><div className="text-[11px] text-muted-foreground">Slowest 10 % (P90)</div><div className="font-display text-[1.5rem] font-bold">{fmtHours(t.p90_hours)}</div></div>
          </div>
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.orders)} orders, {fmtCount(t.open_included)} still waiting</p>
        </>
      )
    case 'orders.waiting':
      return (
        <>
          <BigValue t={t} tone={(t.value ?? 0) > 0 ? 'alert' : 'ok'} />
          {(t.value ?? 0) > 0 && <p className="mt-1 text-[12px] text-muted-foreground">{fmtBhd(t.bhd)} · oldest {fmtHours(t.oldest_hours)}</p>}
          {!!t.by_rep?.length && <MiniList rows={t.by_rep.map((r) => ({ label: r.rep, value: `${r.orders}`, sub: fmtBhd(r.bhd) }))} />}
        </>
      )
    case 'orders.match_rate':
      return (
        <>
          <BigValue t={t} />
          {t.eligible != null && t.eligible > 0 && (
            <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.matched)} of {fmtCount(t.eligible)} delivered orders · {fmtBhd(t.unmatched_bhd)} still to match</p>
          )}
        </>
      )
    case 'orders.self_order':
      return (
        <>
          <BigValue t={t} />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.by_shop)} of {fmtCount(t.orders)} orders</p>
        </>
      )
    case 'customers.active':
      return (
        <>
          <BigValue t={t} suffix="in 30 days" />
          {t.chips && <ChipRow chips={t.chips} />}
          <p className="mt-2 text-[12px] text-muted-foreground">{fmtCount(t.new_30d)} new accounts in 30 days</p>
        </>
      )
    case 'customers.dormant':
      return (
        <>
          <BigValue t={t} suffix="accounts" />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtBhd(t.bhd)} bought by them in the last 12 months</p>
          <MiniList rows={(t.items || []).map((r) => ({ label: String(r.account), value: fmtBhd(num(r.net_12m_bhd)), sub: `last invoice ${fmtDay(r.last_invoice)} · ${r.days} days` }))} />
        </>
      )
    case 'customers.concentration':
      return (
        <>
          <BigValue t={t} />
          <Bar pct={t.value ?? 0} className="mt-3" />
          <p className="mt-1.5 text-[12px] text-muted-foreground">{fmtBhd(t.top10_bhd)} of {fmtBhd(t.named_bhd)}</p>
        </>
      )
    case 'customers.cash':
      return (
        <>
          <BigValue t={t} suffix="of invoices" />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtPct(t.sales_pct)} of ex-VAT sales</p>
        </>
      )
    case 'products.movers':
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">Rising</div>
            <MiniList rows={(t.rising || []).map((m) => ({ label: m.item, value: `+${fmtBhd(m.delta_bhd)}`, sub: `${m.qty_prev} → ${m.qty_30} units`, tone: 'up' as const }))} />
          </div>
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-rose-700 dark:text-rose-400">Losing momentum</div>
            <MiniList rows={(t.falling || []).map((m) => ({ label: m.item, value: fmtBhd(m.delta_bhd), sub: `${m.qty_prev} → ${m.qty_30} units`, tone: 'down' as const }))} />
          </div>
        </div>
      )
    case 'products.sold_out':
      return (
        <>
          <BigValue t={t} suffix="items" tone={(t.value ?? 0) > 0 ? 'warn' : 'ok'} />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtBhd(t.bhd)} sold in the last 60 days</p>
          <MiniList rows={(t.items || []).map((r) => ({ label: String(r.item), value: fmtBhd(num(r.net_60_bhd)), sub: `${r.qty_60} sold` }))} />
        </>
      )
    case 'products.stock_shape':
      return (
        <>
          <BigValue t={t} suffix="at cost" />
          {t.chips && (
            <>
              <div className="mt-3 flex h-2.5 w-full overflow-hidden rounded-full bg-[#F3ECF8] dark:bg-[#6D4091]/20" aria-hidden="true">
                <div className="h-full bg-[#6D4091]" style={{ width: `${t.chips[0]?.share_pct ?? 0}%` }} />
                <div className="h-full bg-amber-500" style={{ width: `${t.chips[1]?.share_pct ?? 0}%` }} />
              </div>
              <ChipRow chips={t.chips} />
            </>
          )}
          {!!t.items_uncosted && <p className="mt-2 text-[11px] text-muted-foreground">{t.items_uncosted} item(s) have no cost yet and are left out.</p>}
        </>
      )
    case 'profit.official':
      return (
        <>
          <BigValue t={t} />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtBhd(t.gp_bhd)} gross profit on {fmtBhd(t.net_ex_vat_bhd)}</p>
        </>
      )
    case 'profit.landed':
      return (
        <>
          <BigValue t={t} />
          <p className="mt-1 text-[12px] text-muted-foreground">Covers {fmtPct(t.coverage_pct)} of Accessories revenue</p>
        </>
      )
    case 'profit.below_cost':
      return (
        <>
          <BigValue t={t} suffix="items" tone={(t.value ?? 0) > 0 ? 'alert' : 'ok'} />
          <MiniList rows={(t.items || []).map((r) => ({ label: String(r.item), value: fmtBhd(num(r.gp_bhd)), sub: r.margin_pct != null ? `${Number(r.margin_pct).toFixed(1)} % margin` : undefined, tone: 'down' as const }))} />
        </>
      )
    case 'ar.total':
      return (
        <>
          <BigValue t={t} />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.accounts)} accounts · {t.source === 'focus_total' ? "Focus's Grand Total" : 'sum of the account rows'}</p>
          {t.note && <p className="mt-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11.5px] text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">{t.note}</p>}
        </>
      )
    case 'ar.over90':
      return (
        <>
          <BigValue t={t} tone={(t.value ?? 0) >= 30 ? 'alert' : 'ok'} />
          <Bar pct={t.value ?? 0} className="mt-3" />
          <p className="mt-1.5 text-[12px] text-muted-foreground">{fmtBhd(t.over_90_bhd)}</p>
        </>
      )
    case 'ar.top_overdue':
      return (
        <>
          <p className="text-[12px] text-muted-foreground">{fmtCount(t.value)} accounts overdue · {fmtBhd(t.bhd)}</p>
          <MiniList rows={(t.items || []).map((r) => ({ label: String(r.account), value: fmtBhd(num(r.overdue_bhd)), sub: `${fmtBhd(num(r.over_90_bhd))} over 90 days · last receipt ${r.last_receipt ? fmtDay(r.last_receipt) : 'none'}` }))} />
        </>
      )
    case 'ar.no_receipt':
      return (
        <>
          <BigValue t={t} suffix="accounts" />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtBhd(t.bhd)} owed by them</p>
        </>
      )
    default:
      return (
        <>
          <BigValue t={t} />
          {t.chips && <ChipRow chips={t.chips} />}
          {t.delta_pct != null && <div className="mt-1"><Delta pct={t.delta_pct} /></div>}
        </>
      )
  }
}

function BigValue({ t, suffix, tone }: { t: Tile; suffix?: string; tone?: 'alert' | 'warn' | 'ok' }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2">
      <span className={cn('font-display text-[1.6rem] font-bold leading-tight tabular-nums tracking-tight',
        tone === 'alert' && 'text-rose-600 dark:text-rose-400', tone === 'warn' && 'text-amber-700 dark:text-amber-400')}>
        {fmtValue(t)}
      </span>
      {suffix && t.value != null && <span className="text-[12px] text-muted-foreground">{suffix}</span>}
    </div>
  )
}

function TileCard({ t, me, wide }: { t: Tile; me: Me | null; wide?: boolean }) {
  return (
    <article aria-labelledby={`tile-${t.key}`} className={cn('flex min-w-0 flex-col rounded-2xl border bg-card p-4 shadow-sm', wide && 'md:col-span-2')}>
      <div className="flex items-start justify-between gap-2">
        <h3 id={`tile-${t.key}`} className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[#6D4091] dark:text-[#c7a6e6]">{t.label}</h3>
        <DrillLink drill={t.drill} me={me} className="shrink-0" />
      </div>
      <div className="mt-1.5 min-w-0 flex-1">
        {t.available ? <TileBody t={t} /> : (
          <p className="mt-1 text-[12.5px] text-muted-foreground">{t.note || 'Not available yet.'}</p>
        )}
      </div>
      <Basis text={t.basis} />
    </article>
  )
}

/* ─────────────────────────── modules ─────────────────────────── */

const WIDE = new Set(['sales.trend', 'orders.funnel', 'products.movers'])

function ModuleSection({ m, me, children }: { m: Module; me: Me | null; children: ReactNode }) {
  return (
    <section aria-labelledby={`mod-${m.key}`} className="mt-7">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id={`mod-${m.key}`} className="flex items-center gap-2 font-display text-[1.15rem] font-bold">
          <span className="h-4 w-1 rounded-full bg-gradient-to-b from-[#6D4091] to-[#824FAB]" aria-hidden="true" />
          {m.title}
          {m.live && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10.5px] font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">Live</span>}
        </h2>
        <DrillLink drill={m.drill} me={me} />
      </div>
      {children}
    </section>
  )
}

function TileGrid({ m, me }: { m: Module; me: Me | null }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {m.tiles.map((t) => <TileCard key={t.key} t={t} me={me} wide={WIDE.has(t.key)} />)}
    </div>
  )
}

function Attention({ m, me }: { m: Module; me: Me | null }) {
  const items = m.items || []
  if (!items.length) {
    return (
      <div className="flex items-center gap-2.5 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[13.5px] font-medium text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">
        <CheckCircle2 size={18} aria-hidden="true" /> Nothing needs attention right now.
      </div>
    )
  }
  return (
    // two balanced columns on a wide screen (ranked order reads down the first, then the second)
    <ul className="gap-3 lg:columns-2">
      {items.map((it) => (
        <li key={it.key} className={cn('mb-3 flex min-w-0 break-inside-avoid flex-col rounded-2xl border border-l-4 bg-card p-4 shadow-sm',
          it.tone === 'alert' ? 'border-l-rose-500' : it.tone === 'warn' ? 'border-l-amber-500' : 'border-l-[#6D4091]')}>
          <div className="flex items-start gap-2.5">
            <AlertTriangle size={17} aria-hidden="true" className={cn('mt-0.5 shrink-0',
              it.tone === 'alert' ? 'text-rose-600 dark:text-rose-400' : 'text-amber-600 dark:text-amber-400')} />
            <div className="min-w-0 flex-1">
              <h3 className="text-[14px] font-bold leading-snug">{it.title}</h3>
              <p className="mt-0.5 text-[12.5px] text-muted-foreground">{it.detail}</p>
            </div>
          </div>
          {!!it.rows.length && (
            <ul className="mt-2 divide-y divide-border/70 pl-7">
              {it.rows.map((r, i) => {
                const inner = (
                  <>
                    <span className="min-w-0">
                      <span className="block truncate font-medium" title={r.label}>{r.label}</span>
                      {r.sub && <span className="block truncate text-[11px] text-muted-foreground">{r.sub}</span>}
                    </span>
                    {r.bhd != null && <span className="shrink-0 font-semibold tabular-nums">{fmtBhd(r.bhd)}</span>}
                  </>
                )
                return (
                  <li key={`${r.label}-${i}`} className="py-1.5 text-[12.5px]">
                    {r.to && mayOpen(me, r.to)
                      ? <Link to={r.to} className="flex items-baseline justify-between gap-3 hover:text-[#6D4091]">{inner}</Link>
                      : <div className="flex items-baseline justify-between gap-3">{inner}</div>}
                  </li>
                )
              })}
            </ul>
          )}
          <div className="mt-auto flex flex-wrap items-end justify-between gap-2 pt-2">
            <p className="flex items-start gap-1.5 text-[11px] leading-snug text-muted-foreground">
              <Info size={12} className="mt-[1px] shrink-0" aria-hidden="true" />{it.basis}
            </p>
            <DrillLink drill={it.drill} me={me} />
          </div>
        </li>
      ))}
    </ul>
  )
}

function TeamTable({ t, full }: { t: Tile; full: boolean }) {
  const rows = (t.rows || []).slice(0, full ? undefined : 8)
  if (!rows.length) return <p className="text-[13px] text-muted-foreground">No rep has a target or a sale this month yet.</p>
  return (
    <div className="overflow-x-auto rounded-2xl border bg-card shadow-sm">
      <table className="w-full min-w-[720px] text-left text-[12.5px]">
        <caption className="sr-only">Reps this month</caption>
        <thead className="bg-[#F3ECF8] text-[11px] uppercase tracking-wide text-[#6D4091] dark:bg-[#6D4091]/15 dark:text-[#c7a6e6]">
          <tr>
            <th scope="col" className="px-3 py-2 font-semibold">Rep</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Sales ex-VAT</th>
            <th scope="col" className="px-3 py-2 font-semibold">Tier</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">To next tier</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Named shops</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Marketplace orders</th>
            <th scope="col" className="px-3 py-2 font-semibold">Last invoice</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/70">
          {rows.map((r) => {
            // only a rep can go quiet: a counter (Causeway, Roadshow) has no salesman row
            const silent = r.salesman_id != null && (r.business_days_since_invoice ?? 0) > 7
            return (
              <tr key={r.salesman}>
                <th scope="row" className="px-3 py-2 font-semibold">{r.name || r.salesman}</th>
                <td className="px-3 py-2 text-right tabular-nums">{fmtBhd(r.net_bhd)}</td>
                <td className="px-3 py-2">
                  {r.no_target ? <span className="text-muted-foreground">No target</span> : (
                    <div className="flex min-w-[7rem] items-center gap-2">
                      <span className="font-semibold">{r.tier_reached ? `Tier ${r.tier_reached}` : 'Below tier 1'}</span>
                      <Bar pct={r.progress_pct ?? 0} className="h-1.5 w-16" />
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{r.gap_to_next_bhd != null ? fmtBhd(r.gap_to_next_bhd) : r.no_target ? '—' : 'Top tier'}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtPct(r.named_share_pct)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtCount(r.marketplace_orders)}{r.marketplace_orders ? <span className="ml-1 text-muted-foreground">· {fmtBhd(r.marketplace_bhd)}</span> : null}</td>
                <td className={cn('px-3 py-2', silent && 'font-semibold text-amber-700 dark:text-amber-400')}>
                  {fmtDay(r.last_invoice)}{silent ? ` · ${r.business_days_since_invoice} business days` : ''}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function DormantTable({ rows }: { rows: Row[] }) {
  if (!rows.length) return <p className="text-[13px] text-muted-foreground">No dormant account with sales in the last 12 months.</p>
  return (
    <div className="overflow-x-auto rounded-2xl border bg-card shadow-sm">
      <table className="w-full min-w-[520px] text-left text-[12.5px]">
        <caption className="sr-only">Dormant accounts</caption>
        <thead className="bg-[#F3ECF8] text-[11px] uppercase tracking-wide text-[#6D4091] dark:bg-[#6D4091]/15 dark:text-[#c7a6e6]">
          <tr>
            <th scope="col" className="px-3 py-2 font-semibold">Account</th>
            <th scope="col" className="px-3 py-2 font-semibold">Last invoice</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Days quiet</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Last 12 months</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/70">
          {rows.map((r) => (
            <tr key={String(r.account)}>
              <th scope="row" className="px-3 py-2 font-semibold">{r.account}</th>
              <td className="px-3 py-2">{fmtDay(r.last_invoice)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtCount(num(r.days))}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtBhd(num(r.net_12m_bhd))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ─────────────────────────── the page ─────────────────────────── */

export default function CommandCentre({ view = 'all' }: { view?: 'all' | 'team' | 'customers' }) {
  const { me } = useAuth()
  const [params, setParams] = useSearchParams()
  const raw = params.get('period')
  const period: PeriodKey = PERIODS.some((p) => p.key === raw) ? (raw as PeriodKey) : 'mtd'
  const q = useQuery({
    queryKey: ['management', 'overview', period],
    queryFn: () => apiGet<Overview>(`/management/overview?period=${period}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchInterval: 120_000,
  })
  const ov = q.data
  const setPeriod = (p: PeriodKey) => {
    const next = new URLSearchParams(params)
    if (p === 'mtd') next.delete('period')
    else next.set('period', p)
    setParams(next, { replace: true })
  }
  const title = view === 'team' ? 'Team' : view === 'customers' ? 'Customers' : 'Command Centre'
  const sub = view === 'team'
    ? 'Every rep this month: sales ex-VAT, tier, named shops, marketplace orders and the last Focus invoice.'
    : view === 'customers'
      ? 'Who is buying, who has gone quiet, and how much rests on the biggest accounts.'
      : 'The whole company on one page. Every tile says what it counts and how fresh it is.'
  const updated = ov ? new Date(ov.generated_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : ''

  return (
    <div className="mx-auto w-full max-w-[1400px]">
      {/* plum band: title, freshness, period */}
      <div className="rounded-3xl bg-gradient-to-br from-[#6D4091] via-[#6D4091] to-[#824FAB] p-5 text-white shadow-[0_18px_40px_-18px_rgba(109,64,145,0.65)] sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-display text-[1.6rem] font-bold leading-tight sm:text-[1.85rem]">{title}</h1>
            <p className="mt-1 max-w-2xl text-[13px] text-white/80">{sub}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {ov && <FreshnessChip data={ov.freshness} variant="onPlum" />}
            <button type="button" onClick={() => q.refetch()} disabled={q.isFetching}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/25 bg-white/10 px-3 text-[11.5px] font-semibold text-white transition-colors hover:bg-white/20 disabled:opacity-60"
              title={updated ? `Updated at ${updated}` : 'Refresh'}>
              {q.isFetching ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <RefreshCw size={13} aria-hidden="true" />}
              {updated ? `Updated ${updated}` : 'Refresh'}
            </button>
          </div>
        </div>
        <div role="group" aria-label="Period" className="mt-4 flex flex-wrap gap-1.5">
          {PERIODS.map((p) => (
            <button key={p.key} type="button" aria-pressed={period === p.key} onClick={() => setPeriod(p.key)}
              className={cn('h-8 rounded-full px-3.5 text-[12.5px] font-semibold transition-colors motion-reduce:transition-none',
                period === p.key ? 'bg-white text-[#6D4091]' : 'bg-white/10 text-white hover:bg-white/20')}>
              {p.label}
            </button>
          ))}
        </div>
        {ov?.period.focus && (
          <p className="mt-3 text-[12px] text-white/75">
            Focus figures: {ov.period.focus.label}{ov.period.compare ? ` vs ${ov.period.compare.basis || ov.period.compare.label} (${ov.period.compare.label})` : ''}
            {' · '}Marketplace figures: {ov.period.live.label}
          </p>
        )}
      </div>

      {q.isError && !ov ? (
        <div className="mt-6"><LoadError error={q.error} onRetry={() => q.refetch()} isRetrying={q.isFetching} fallback="Could not load the Command Centre." /></div>
      ) : !ov ? (
        <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 9 }).map((_, i) => <Skeleton key={i} className="h-40 rounded-2xl" />)}
        </div>
      ) : (
        <div className={cn(q.isFetching && q.isPlaceholderData && 'opacity-70 transition-opacity')}>
          {!!ov.notes.length && (
            <div role="status" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-[12.5px] text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              {ov.notes.map((n) => <p key={n}>{n}</p>)}
            </div>
          )}
          {!!ov.unavailable.length && (
            <p className="mt-3 text-[12px] text-muted-foreground">
              Some figures could not be read just now and show as not available; the rest of the page is complete.
            </p>
          )}
          {ov.modules.filter((m) => view === 'all' || m.key === view || (view === 'team' && m.key === 'attention')).map((m) => {
            if (m.key === 'attention') {
              const items = view === 'team' ? (m.items || []).filter((i) => i.key === 'rep_silence') : m.items
              if (view === 'team' && !items?.length) return null
              return <ModuleSection key={m.key} m={m} me={me}><Attention m={{ ...m, items }} me={me} /></ModuleSection>
            }
            if (m.key === 'team') {
              const t = m.tiles[0]
              return (
                <ModuleSection key={m.key} m={view === 'team' ? { ...m, drill: null } : m} me={me}>
                  {t?.available ? (
                    <>
                      <TeamTable t={t} full={view === 'team'} />
                      <p className="mt-2 flex items-start gap-1.5 text-[11px] text-muted-foreground"><Info size={12} className="mt-[1px] shrink-0" aria-hidden="true" />{t.basis}</p>
                    </>
                  ) : <p className="text-[13px] text-muted-foreground">{t?.note || 'Not available yet.'}</p>}
                </ModuleSection>
              )
            }
            if (m.key === 'customers' && view === 'customers') {
              const dormant = m.tiles.find((t) => t.key === 'customers.dormant')
              return (
                <ModuleSection key={m.key} m={{ ...m, drill: null }} me={me}>
                  <TileGrid m={{ ...m, tiles: m.tiles.map((t) => (t.key === 'customers.dormant' ? { ...t, drill: null } : t)) }} me={me} />
                  {dormant?.available && (
                    <div className="mt-5">
                      <h3 className="mb-2 font-display text-[1rem] font-bold">Every dormant account, by the last 12 months</h3>
                      <DormantTable rows={dormant.all || []} />
                    </div>
                  )}
                </ModuleSection>
              )
            }
            return <ModuleSection key={m.key} m={m} me={me}><TileGrid m={m} me={me} /></ModuleSection>
          })}
          <p className="mt-8 text-center text-[11px] text-muted-foreground">
            Read only. Focus figures change when the daily reports are uploaded; marketplace figures refresh every two minutes.
          </p>
        </div>
      )}
    </div>
  )
}
