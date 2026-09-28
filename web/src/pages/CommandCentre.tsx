import type { ReactNode } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, CheckCircle2, ChevronDown, Info, Loader2, RefreshCw,
} from 'lucide-react'
import {
  Bar as ChartBar, Cell, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
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
 * R7e: the whole page opens on a first screen read in seconds — six KPI cards (one number, one chip,
 * one caption with its window; the basis in the tooltip), the weekly chart beside the top five
 * exceptions, then Salesmen · Products · Merchants. Every module in full sits below, collapsed, under
 * "Details". A card whose tile this login may not read (the API left it out or marked it restricted)
 * is simply not drawn.
 *
 * `view` = 'team' and 'customers' are the same payload with that module in full (management's Team
 * and Customers menu items).
 */

type PeriodKey = 'today' | '7d' | 'mtd' | 'last_month' | 'quarter'
const PERIODS: { key: PeriodKey; label: string }[] = [
  { key: 'today', label: 'Latest day' },
  { key: '7d', label: '7 days' },
  { key: 'mtd', label: 'Month to date' },
  { key: 'last_month', label: 'Last month' },
  { key: 'quarter', label: 'Quarter' },
]

interface Drill { to: string; label: string }
interface Chip { label: string; value: number | null; unit: 'bhd' | 'count' | 'pct'; share_pct?: number | null; items?: number }
/** units = what the shop asked for; units_added = lines the rep added (counted apart); bhd ex-VAT */
interface Stage { key: string; label: string; orders: number; units: number; bhd: number; units_added?: number }
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
  /** set by the API when this login lacks the page the figure belongs to (e.g. 'Margins' for stock at cost) */
  restricted?: string
  compare?: { value: number; label: string } | null; delta_pct?: number | null; invoices?: number
  chips?: Chip[]
  month?: string; mtd_bhd?: number; target_bhd?: number | null; projected_bhd?: number
  business_days_done?: number; business_days_total?: number; business_days_left?: number
  pct_of_target?: number | null; projected_pct_of_target?: number | null; on_track?: boolean | null
  needed_per_business_day_bhd?: number | null
  /** the days a figure really covers (the official margin: every loaded day, whatever the period) */
  covers?: { from: string | null; to: string | null; period_bound?: boolean } | null
  /** products.price_drops: items are { code, item, was_bhd, now_bhd, cut_pct, on } (trade prices incl. VAT);
   *  profit.thin_drops adds now_ex_vat_bhd, unit_cost_bhd and margin_pct (that tile only reaches a login holding
   *  'Margins'). drop_days: the window both count, always the last N days — never the period chosen above */
  latest_on?: string | null; costed?: number; drops?: number; threshold_pct?: number; drop_days?: number
  series?: WeekPoint[]
  stages?: Stage[]; cancelled?: number; open?: number
  units_ordered?: number; units_accepted?: number; orders?: number; units_added?: number; staff_orders_left_out?: number
  p50_hours?: number | null; p90_hours?: number | null; open_included?: number
  bhd?: number; oldest_hours?: number | null; by_rep?: { rep: string; orders: number; bhd: number }[]
  matched?: number; eligible?: number; unmatched_bhd?: number; by_shop?: number
  awaiting?: number; awaiting_bhd?: number; no_invoice?: number; no_invoice_bhd?: number
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
/** whole BHD for a first-screen card; the exact 3-dp figure goes in its tooltip */
const fmtBhd0 = (n?: number | null) =>
  n == null ? '—' : `BHD ${n.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
/** '2026-09-24' → '24 Sep 2026' */
const fmtDayYear = (iso: string) => `${fmtDay(iso)} ${iso.slice(0, 4)}`
/** "12 months to 24 Sep" only when the figure really spans about a year (11–13 months); else its real span */
function coversLabel(c?: Tile['covers']): string {
  if (!c?.from || !c.to) return 'every loaded day'
  const days = (Date.parse(`${c.to}T00:00:00Z`) - Date.parse(`${c.from}T00:00:00Z`)) / 86_400_000 + 1
  const months = days / 30.44
  if (months >= 11 && months <= 13) return `12 months to ${fmtDay(c.to)}`
  return c.from.slice(0, 4) === c.to.slice(0, 4)
    ? `${fmtDay(c.from)} – ${fmtDayYear(c.to)}`
    : `${fmtDayYear(c.from)} – ${fmtDayYear(c.to)}`
}
function fmtValue(t: Tile): string {
  if (t.unit === 'pct') return fmtPct(t.value)
  if (t.unit === 'count' || t.unit === 'list') return fmtCount(t.value)
  if (t.unit === 'hours') return fmtHours(t.value)
  return fmtBhd(t.value)
}
const num = (v: string | number | null | undefined) => (typeof v === 'number' ? v : v == null ? null : Number(v))

/** Drill links the viewer may open (management reads its own pages only, each only with its grant; admins everything). */
function mayOpen(me: Me | null, to: string): boolean {
  if (!me) return false
  if (!isManagement(me)) return true
  return managementMayOpen(to.split('?')[0], me.features || [])
}

/** Tiles this login may see: a figure the API marked restricted (no grant for its page) is left out. */
function shownTiles(tiles: Tile[]): Tile[] {
  return tiles.filter((t) => !t.restricted)
}

/** A tile from any module for the first screen — undefined when this login may not read it (the API left
 *  its module out, or marked it restricted) or it did not build, so its card is simply not drawn. */
function findTile(ov: Overview, key: string): Tile | undefined {
  for (const m of ov.modules) {
    const t = m.tiles.find((x) => x.key === key)
    if (t) return t.restricted || !t.available ? undefined : t
  }
  return undefined
}

/* ─────────────────────────── small pieces ─────────────────────────── */

function DrillLink({ drill, me, className, iconOnly }: { drill?: Drill | null; me: Me | null; className?: string; iconOnly?: boolean }) {
  if (!drill || !mayOpen(me, drill.to)) return null
  if (iconOnly) {
    // the first screen's compact form: the arrow alone, its label for screen readers and the tooltip
    return (
      <Link to={drill.to} aria-label={drill.label} title={drill.label}
        className={cn('inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[#6D4091] hover:bg-[#F3ECF8] dark:text-[#c7a6e6] dark:hover:bg-[#6D4091]/20', className)}>
        <ArrowRight size={14} aria-hidden="true" />
      </Link>
    )
  }
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

/** `compact` = the first screen's KPI card: a short strip, no axis row */
function Sparkline({ series, compact }: { series: WeekPoint[]; compact?: boolean }) {
  const max = Math.max(1, ...series.map((p) => p.value))
  return (
    <div className={compact ? 'mt-2' : 'mt-3'}>
      <div className={cn('flex items-end gap-1', compact ? 'h-10' : 'h-20')} role="img"
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
      {!compact && (
        <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
          <span>{series[0]?.label}</span>
          <span>{series[series.length - 1]?.label} (so far)</span>
        </div>
      )}
    </div>
  )
}

type ListRow = { label: string; value: string; sub?: string; tone?: 'up' | 'down' }

/** a price drop as one line: the item, was → now (the trade price incl. VAT, as the marketplace shows it — the
 *  rest of this page is ex-VAT, so the row says so), then the cut, the day and its code */
function dropRow(r: Row): ListRow {
  const cut = num(r.cut_pct)
  return {
    label: String(r.item || r.code),
    value: `${fmtBhd(num(r.was_bhd))} → ${(num(r.now_bhd) ?? 0).toFixed(3)}`,
    sub: [cut != null ? `↓${cut.toFixed(1)} %` : null, r.on ? fmtDay(r.on) : null, 'trade price incl. VAT', r.code]
      .filter(Boolean).join(' · '),
  }
}

/** a price cut that left a thin margin (Profitability, 'Margins' logins only): the margin, then the price it is
 *  worked on (ex-VAT, with the VAT-inclusive book price the marketplace shows) and the landed cost — so
 *  (price ex-VAT − landed) ÷ price ex-VAT is the margin on the row */
function thinRow(r: Row): ListRow {
  const m = num(r.margin_pct)
  const now = (num(r.now_bhd) ?? 0).toFixed(3)
  const ex = num(r.now_ex_vat_bhd)
  return {
    label: String(r.item || r.code),
    value: m != null ? `${m.toFixed(1)} % margin` : '—',
    sub: `${ex != null ? `price ${ex.toFixed(3)} ex-VAT (${now} incl. VAT)` : `price ${now} incl. VAT`} · landed ${(num(r.unit_cost_bhd) ?? 0).toFixed(3)}`,
    tone: 'down',
  }
}

/** a row: the label and its value on one line, then the sub-line under both — the whole row's width, and it
 *  wraps rather than cuts (a price row's "↓40.0 % · 10 Sep · trade price incl. VAT" is read in full at 1366) */
function MiniList({ rows }: { rows: ListRow[] }) {
  if (!rows.length) return <p className="mt-2 text-[12.5px] text-muted-foreground">Nothing to list.</p>
  return (
    <ul className="mt-2 divide-y divide-border/70">
      {rows.map((r, i) => (
        <li key={`${r.label}-${i}`} className="py-1.5 text-[12.5px]">
          <span className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate font-medium" title={r.label}>{r.label}</span>
            <span className={cn('shrink-0 font-semibold tabular-nums',
              r.tone === 'up' && 'text-emerald-600 dark:text-emerald-400', r.tone === 'down' && 'text-rose-600 dark:text-rose-400')}>
              {r.value}
            </span>
          </span>
          {r.sub && <span className="block text-pretty text-[11px] leading-snug text-muted-foreground" title={r.sub}>{r.sub}</span>}
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
                <div className="text-[11px] text-muted-foreground">
                  {fmtCount(s.units)} units{s.units_added ? ` · +${fmtCount(s.units_added)} added by the rep` : ''}
                </div>
                <div className="text-[11px] font-medium tabular-nums">{fmtBhd(s.bhd)} <span className="font-normal text-muted-foreground">ex-VAT</span></div>
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
          {t.units_ordered != null && <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.units_accepted)} of {fmtCount(t.units_ordered)} units the shops asked for, on {fmtCount(t.orders)} orders</p>}
          {!!t.units_added && <p className="mt-0.5 text-[11.5px] text-muted-foreground">+{fmtCount(t.units_added)} units added by the rep, counted apart</p>}
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
          {(t.value ?? 0) > 0 && <p className="mt-1 text-[12px] text-muted-foreground">{fmtBhd(t.bhd)} <span>ex-VAT</span> · oldest {fmtHours(t.oldest_hours)}</p>}
          {!!t.by_rep?.length && <MiniList rows={t.by_rep.map((r) => ({ label: r.rep, value: `${r.orders}`, sub: fmtBhd(r.bhd) }))} />}
        </>
      )
    case 'orders.match_rate':
      return (
        <>
          <BigValue t={t} />
          {t.eligible != null && t.eligible > 0 && (
            <>
              <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.matched)} of {fmtCount(t.eligible)} delivered orders have an accepted Focus invoice link</p>
              {(t.eligible - (t.matched ?? 0)) > 0 && (
                <p className="mt-0.5 text-[12px] text-muted-foreground">
                  Not yet matched: {fmtCount(t.awaiting ?? 0)} awaiting acceptance · {fmtCount(t.no_invoice ?? 0)} no Focus invoice found · {fmtBhd(t.unmatched_bhd)} <span>ex-VAT</span>
                </p>
              )}
            </>
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
    case 'products.price_drops':
      return (
        <>
          <BigValue t={t} suffix="items" />
          {t.latest_on && <p className="mt-1 text-[12px] text-muted-foreground">Latest cut {fmtDay(t.latest_on)}</p>}
          <MiniList rows={(t.items || []).map(dropRow)} />
        </>
      )
    case 'profit.thin_drops':
      return (
        <>
          <BigValue t={t} suffix="items" tone={(t.value ?? 0) > 0 ? 'warn' : 'ok'} />
          <p className="mt-1 text-[12px] text-muted-foreground">{fmtCount(t.costed)} of {fmtCount(t.drops)} price drops have a usable cost</p>
          <MiniList rows={(t.items || []).map(thinRow)} />
        </>
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
      {shownTiles(m.tiles).map((t) => <TileCard key={t.key} t={t} me={me} wide={WIDE.has(t.key)} />)}
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
            <th scope="col" className="px-3 py-2 text-right font-semibold">Marketplace orders (ex-VAT)</th>
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
  if (!rows.length) return <p className="text-[13px] text-muted-foreground">No dormant account with BHD 100 or more in the last 12 months.</p>
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

/* ─────────────────────────── the first screen ─────────────────────────── */
// Read in seconds: a card says what happened (the number), whether it is improving (one chip) and over which
// days (one caption); how it is counted is in its tooltip. Anything longer lives in Details below.

const PILL = {
  up: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
  down: 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300',
  warn: 'bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300',
  plum: 'bg-[#F3ECF8] text-[#6D4091] dark:bg-[#6D4091]/20 dark:text-[#e6d6f5]',
  muted: 'bg-muted text-muted-foreground',
} as const

function Pill({ tone, children }: { tone: keyof typeof PILL; children: ReactNode }) {
  return (
    <span className={cn('inline-flex items-center gap-0.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-semibold tabular-nums', PILL[tone])}>
      {children}
    </span>
  )
}

function DeltaPill({ pct }: { pct?: number | null }) {
  if (pct == null) return null
  const up = pct >= 0
  return (
    <Pill tone={up ? 'up' : 'down'}>
      {up ? <ArrowUpRight size={12} aria-hidden="true" /> : <ArrowDownRight size={12} aria-hidden="true" />}
      {up ? '+' : ''}{pct.toFixed(1)} %
    </Pill>
  )
}

function KpiCard({ label, value, exact, chip, caption, basis, drill, me, children }: {
  label: string; value?: string; exact?: string; chip?: ReactNode; caption: string; basis: string
  drill?: Drill | null; me: Me | null; children?: ReactNode
}) {
  return (
    <article title={basis} className="flex min-w-0 flex-col rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex min-h-6 items-center justify-between gap-2">
        <h3 className="truncate text-[11.5px] font-semibold uppercase tracking-[0.08em] text-[#6D4091] dark:text-[#c7a6e6]">{label}</h3>
        <DrillLink drill={drill} me={me} iconOnly />
      </div>
      {(value != null || chip) && (
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
          {value != null && (
            <span title={exact} className="font-display text-[1.7rem] font-bold leading-tight tabular-nums tracking-tight">{value}</span>
          )}
          {chip}
        </div>
      )}
      {children}
      <p className="mt-auto pt-2 text-[11.5px] leading-snug text-muted-foreground">{caption}</p>
    </article>
  )
}

/** the KPI row's columns by how many cards this login gets (no 'Margins': five), so a row never has a hole or an
 *  empty column: an odd last card spans the gap (five at lg: 3 + 2 with the last one double), 2xl is one row */
const KPI_GRID: Record<number, string> = {
  6: 'sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6',
  5: 'sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5 sm:[&>:last-child]:col-span-2 2xl:[&>:last-child]:col-span-1',
  4: 'sm:grid-cols-2 2xl:grid-cols-4',
  3: 'sm:grid-cols-2 lg:grid-cols-3 sm:[&>:last-child]:col-span-2 lg:[&>:last-child]:col-span-1',
  2: 'sm:grid-cols-2',
  1: '',
}

function KpiRow({ ov, me }: { ov: Overview; me: Me | null }) {
  const sales = findTile(ov, 'sales.accessories')
  const trend = findTile(ov, 'sales.trend')
  const pace = findTile(ov, 'sales.pace')
  const funnel = findTile(ov, 'orders.funnel')
  const waiting = findTile(ov, 'orders.waiting')
  const active = findTile(ov, 'customers.active')
  const margin = findTile(ov, 'profit.official')
  const ar = findTile(ov, 'ar.total')
  const over90 = findTile(ov, 'ar.over90')
  const span = ov.period.focus?.label ?? ''
  const waitingN = waiting?.value ?? 0
  const cards = [sales, pace, funnel, active, margin, ar].filter(Boolean).length
  return (
    <div className={cn('grid grid-cols-1 gap-3', KPI_GRID[cards] ?? KPI_GRID[6])}>
      {sales && (
        <KpiCard label="Accessories sales" value={fmtBhd0(sales.value)} exact={`${fmtBhd(sales.value)} ex-VAT`}
          chip={<DeltaPill pct={sales.delta_pct} />} basis={sales.basis} drill={sales.drill} me={me}
          caption={`${span}${sales.compare ? ` vs ${sales.compare.label}` : ''} · ex-VAT`}>
          {!!trend?.series?.length && <Sparkline series={trend.series} compact />}
        </KpiCard>
      )}
      {pace && (
        <KpiCard label="Month pace" value={fmtBhd0(pace.projected_bhd)} exact={`${fmtBhd(pace.projected_bhd)} projected`}
          chip={pace.target_bhd ? <Pill tone={pace.on_track ? 'up' : 'warn'}>projected {fmtPct(pace.projected_pct_of_target)} of target</Pill> : null}
          basis={pace.basis} me={me}
          caption={pace.target_bhd
            ? `Projected ${pace.month ?? ''} · target ${fmtBhd0(pace.target_bhd)}`
            : `Projected ${pace.month ?? ''} · no company target set`}>
          {!!pace.target_bhd && <Bar pct={pace.projected_pct_of_target ?? 0} className="mt-2" />}
        </KpiCard>
      )}
      {funnel && (
        <KpiCard label="Marketplace orders" basis={funnel.basis} drill={waiting?.drill} me={me}
          chip={waitingN > 0 ? <Pill tone="down">{fmtCount(waitingN)} waiting &gt;24 business h</Pill> : null}
          caption={`Placed ${ov.period.live.label} · live`}>
          {/* three cells side by side; on the one-row strip (2xl, up to six across) the card is narrow, so three short rows */}
          <dl className="mt-1.5 grid grid-cols-3 gap-1.5 2xl:grid-cols-1 2xl:gap-1">
            {(funnel.stages || []).map((s) => (
              <div key={s.key} className="min-w-0 rounded-lg bg-[#F3ECF8] px-2 py-1 dark:bg-[#6D4091]/15 2xl:flex 2xl:items-baseline 2xl:justify-between 2xl:py-0.5">
                <dt className="truncate text-[10.5px] font-semibold text-[#6D4091] dark:text-[#c7a6e6]">{s.label}</dt>
                <dd className="font-display text-[1.2rem] font-bold leading-tight tabular-nums 2xl:text-[1rem]">{fmtCount(s.orders)}</dd>
              </div>
            ))}
          </dl>
        </KpiCard>
      )}
      {active && (
        <KpiCard label="Active accounts" value={fmtCount(active.value)} basis={active.basis} me={me}
          chip={active.new_30d ? <Pill tone="plum">+{fmtCount(active.new_30d)} new</Pill> : null}
          caption={`Named B2B · 30 days to ${fmtDay(ov.freshness.focus_to)}`} />
      )}
      {margin && (
        <KpiCard label="Gross margin" value={fmtPct(margin.value)} basis={margin.basis} drill={margin.drill} me={me}
          exact={`${fmtBhd(margin.gp_bhd)} gross profit on ${fmtBhd(margin.net_ex_vat_bhd)} ex-VAT`}
          caption={`${coversLabel(margin.covers)} · ex-VAT vs Focus COGS`} />
      )}
      {ar && (
        <KpiCard label="Receivables" value={fmtBhd0(ar.value)} exact={fmtBhd(ar.value)} basis={ar.basis} drill={ar.drill} me={me}
          chip={over90 ? <Pill tone={(over90.value ?? 0) >= 30 ? 'down' : 'muted'}>{fmtPct(over90.value)} over 90 days</Pill> : null}
          caption={`Focus ageing · ${fmtDay(ov.freshness.ar_as_of)}`} />
      )}
    </div>
  )
}

function WeeklyChart({ t, pace, drill, me }: { t: Tile; pace?: Tile; drill?: Drill | null; me: Me | null }) {
  const series = t.series || []
  // a week is five business days: the monthly target over the month's business days, five times
  const weekly = pace?.target_bhd && pace.business_days_total ? (pace.target_bhd / pace.business_days_total) * 5 : null
  return (
    <section aria-labelledby="fs-weekly" title={t.basis} className="flex min-w-0 flex-col rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="fs-weekly" className="font-display text-[1rem] font-bold">Weekly Accessories sales</h2>
          <p className="text-[11.5px] text-muted-foreground">ex-VAT · Sunday–Saturday weeks · the lighter bar is this week so far</p>
        </div>
        <DrillLink drill={drill} me={me} />
      </div>
      <div className="mt-2 h-[210px] min-w-0">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={series} margin={{ top: 14, right: 8, left: 0, bottom: 0 }}>
            <XAxis dataKey="label" interval="preserveStartEnd" minTickGap={10}
              tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} axisLine={false} tickLine={false}
              width={44} tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v}`)} />
            <Tooltip cursor={{ fill: 'hsl(var(--accent))' }}
              formatter={(v) => [fmtBhd(Number(v)), 'Accessories ex-VAT']}
              labelFormatter={(label, payload) => {
                const p = payload?.[0]?.payload as WeekPoint | undefined
                return `Week of ${String(label)}${p?.partial ? ' (so far)' : ''}`
              }}
              contentStyle={{ borderRadius: 12, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))', color: 'hsl(var(--foreground))', fontSize: 13 }} />
            {/* the label sits ABOVE the line (insideBottom…): under it, it sat on the tallest bar's top */}
            {weekly != null && (
              <ReferenceLine y={weekly} stroke="#d97706" strokeDasharray="5 4" ifOverflow="extendDomain"
                label={{ value: `weekly target ${fmtBhd0(weekly)}`, position: 'insideBottomRight', offset: 5, fontSize: 10, fill: '#d97706' }} />
            )}
            <ChartBar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={34}>
              {series.map((p) => <Cell key={p.week_start} fill={p.partial ? '#824FAB' : '#6D4091'} fillOpacity={p.partial ? 0.45 : 1} />)}
            </ChartBar>
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

// the orders card carries the waiting count and the header chip the data date: not repeated in the list
const EXCLUDE_ON_CARDS = new Set(['orders_waiting', 'stale_data'])
const TONE_DOT = { alert: 'bg-rose-500', warn: 'bg-amber-500', info: 'bg-[#6D4091]' } as const

function Exceptions({ m, me }: { m?: Module; me: Me | null }) {
  const all = (m?.items || []).filter((i) => !EXCLUDE_ON_CARDS.has(i.key))
  const top = all.slice(0, 5)
  return (
    <section aria-labelledby="fs-attention" className="flex min-w-0 flex-col rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="fs-attention" className="font-display text-[1rem] font-bold">Needs attention</h2>
        {all.length > top.length && <span className="text-[11.5px] text-muted-foreground">{top.length} of {all.length} · the rest in Details</span>}
      </div>
      {!top.length ? (
        <p className="mt-3 flex items-center gap-2 text-[13px] font-medium text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 size={16} aria-hidden="true" />
          {m?.items?.length ? 'Nothing else needs attention.' : 'Nothing needs attention right now.'}
        </p>
      ) : (
        <ul className="mt-1.5 divide-y divide-border/70">
          {top.map((it) => (
            <li key={it.key} title={`${it.detail} · ${it.basis}`} className="flex items-center gap-2.5 py-2 text-[13px]">
              <span aria-hidden="true" className={cn('h-2 w-2 shrink-0 rounded-full', TONE_DOT[it.tone])} />
              <span className="sr-only">{it.tone === 'alert' ? 'Alert:' : 'Warning:'}</span>
              {/* a narrow column wraps a long title once rather than cutting its meaning off */}
              <span className="line-clamp-2 min-w-0 flex-1 font-medium leading-snug">{it.title}</span>
              {/* the money once: a title that already names it ("BHD 300.000 over 90 days …") needs no column */}
              {it.bhd != null && !it.title.includes('BHD') && (
                <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground">{fmtBhd(it.bhd)}</span>
              )}
              <DrillLink drill={it.drill} me={me} iconOnly />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Panel({ id, title, drill, me, children }: { id: string; title: string; drill?: Drill | null; me: Me | null; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <h2 id={id} className="flex items-center gap-2 font-display text-[1rem] font-bold">
          <span className="h-3.5 w-1 rounded-full bg-gradient-to-b from-[#6D4091] to-[#824FAB]" aria-hidden="true" />
          {title}
        </h2>
        <DrillLink drill={drill} me={me} />
      </div>
      {children}
    </section>
  )
}

function SubHead({ tone = 'plum', title, children }: { tone?: 'plum' | 'up' | 'down' | 'warn'; title?: string; children: ReactNode }) {
  return (
    <div title={title} className={cn('mt-3 text-[11px] font-semibold uppercase tracking-wide',
      tone === 'plum' && 'text-[#6D4091] dark:text-[#c7a6e6]', tone === 'up' && 'text-emerald-700 dark:text-emerald-400',
      tone === 'down' && 'text-rose-700 dark:text-rose-400', tone === 'warn' && 'text-amber-700 dark:text-amber-400')}>
      {children}
    </div>
  )
}

function SalesmenPanel({ t, me }: { t: Tile; me: Me | null }) {
  const reps = (t.rows || []).filter((r) => !r.no_target)
  const lead = [...reps].sort((a, b) => b.net_bhd - a.net_bhd).slice(0, 3)
  const shown = new Set(lead.map((r) => r.salesman))
  // furthest behind: the lowest progress among the reps not already leading (a short team shows them once)
  const behind = reps.filter((r) => !shown.has(r.salesman))
    .sort((a, b) => (a.progress_pct ?? 0) - (b.progress_pct ?? 0)).slice(0, 3)
  const below = reps.filter((r) => !r.tier_reached).length
  const tier = (r: TeamRow) => (r.tier_reached ? `Tier ${r.tier_reached}` : 'Below tier 1')
  const gap = (r: TeamRow) => (r.gap_to_next_bhd != null && r.next_tier
    ? `${tier(r)} · ${fmtBhd0(r.gap_to_next_bhd)} to tier ${r.next_tier}` : tier(r))
  return (
    <Panel id="fs-reps" title="Salesmen" drill={t.drill} me={me}>
      {!reps.length ? <p className="mt-2 text-[12.5px] text-muted-foreground">No rep has a target this month yet.</p> : (
        <>
          <p className="mt-1 text-[12px] text-muted-foreground">
            <span className={cn('font-semibold', below ? 'text-amber-700 dark:text-amber-400' : 'text-emerald-700 dark:text-emerald-400')}>
              {below} of {reps.length}
            </span> below tier 1 · Accessories ex-VAT this month
          </p>
          <SubHead tone="up">Leading</SubHead>
          <MiniList rows={lead.map((r) => ({ label: r.name || r.salesman, value: fmtBhd0(r.net_bhd), sub: tier(r) }))} />
          {!!behind.length && (
            <>
              <SubHead tone="down">Furthest behind</SubHead>
              <MiniList rows={behind.map((r) => ({ label: r.name || r.salesman, value: fmtBhd0(r.net_bhd), sub: gap(r),
                tone: r.tier_reached ? undefined : ('down' as const) }))} />
            </>
          )}
        </>
      )}
    </Panel>
  )
}

/** ' · last 30 days': the price-drop window beside a count (none when an older API sends no drop_days) */
const dropWindow = (t: Tile) => (t.drop_days ? ` · last ${t.drop_days} days` : '')

function ProductsPanel({ ov, me }: { ov: Overview; me: Me | null }) {
  const drops = findTile(ov, 'products.price_drops')
  // the Profitability module: the API drops it for a login without 'Margins', so the cost never reaches one
  const thin = findTile(ov, 'profit.thin_drops')
  const movers = findTile(ov, 'products.movers')
  const soldOut = findTile(ov, 'products.sold_out')
  if (!drops && !movers && !soldOut) return null
  return (
    <Panel id="fs-products" title="Products" drill={movers?.drill ?? soldOut?.drill} me={me}>
      {drops && (
        <>
          {/* the count is always the last N days (app_settings.shop_price_drop_days), whatever the period above */}
          <SubHead title={drops.basis}>
            <span className="inline-flex items-center gap-1">
              {fmtCount(drops.value)} price drop{drops.value === 1 ? '' : 's'}{dropWindow(drops)}
              <DrillLink drill={drops.drill} me={me} iconOnly className="h-5 w-5" />
            </span>
          </SubHead>
          {!!drops.value && <MiniList rows={(drops.items || []).slice(0, 3).map(dropRow)} />}
        </>
      )}
      {thin && !!thin.value && (
        <>
          <SubHead tone="warn" title={thin.basis}>
            {fmtCount(thin.value)} cut to {thin.threshold_pct ?? 20} % margin or less{dropWindow(thin)}
          </SubHead>
          <MiniList rows={(thin.items || []).slice(0, 3).map(thinRow)} />
        </>
      )}
      {movers && (
        <>
          <SubHead tone="up">Rising · 30 days</SubHead>
          <MiniList rows={(movers.rising || []).slice(0, 3).map((m) => ({ label: m.item, value: `+${fmtBhd0(m.delta_bhd)}`, tone: 'up' as const }))} />
          <SubHead tone="down">Losing momentum</SubHead>
          <MiniList rows={(movers.falling || []).slice(0, 3).map((m) => ({ label: m.item, value: fmtBhd0(m.delta_bhd), tone: 'down' as const }))} />
        </>
      )}
      {soldOut && (
        <p className="mt-3 text-[12.5px]">
          <span className={cn('font-semibold', (soldOut.value ?? 0) > 0 && 'text-amber-700 dark:text-amber-400')}>
            {fmtCount(soldOut.value)} sold out with demand
          </span>
          <span className="text-muted-foreground"> · {fmtBhd0(soldOut.bhd)} sold in 60 days</span>
        </p>
      )}
    </Panel>
  )
}

function MerchantsPanel({ ov, me }: { ov: Overview; me: Me | null }) {
  const dormant = findTile(ov, 'customers.dormant')
  const active = findTile(ov, 'customers.active')
  if (!dormant && !active) return null
  return (
    <Panel id="fs-merchants" title="Merchants" drill={dormant?.drill ?? { to: '/command/customers', label: 'Customers' }} me={me}>
      {active && (
        <p className="mt-1 text-[12px] text-muted-foreground">
          <span className="font-semibold text-foreground">{fmtCount(active.new_30d)} new</span> in 30 days · {fmtCount(active.value)} active
        </p>
      )}
      {dormant && (
        <>
          <SubHead tone={(dormant.value ?? 0) > 0 ? 'warn' : 'plum'}>
            {fmtCount(dormant.value)} gone quiet · {fmtBhd0(dormant.bhd)} a year
          </SubHead>
          <MiniList rows={(dormant.items || []).slice(0, 3).map((r) => ({
            label: String(r.account), value: fmtBhd0(num(r.net_12m_bhd)), sub: `no invoice for ${r.days} days`,
          }))} />
        </>
      )}
    </Panel>
  )
}

function FirstScreen({ ov, me }: { ov: Overview; me: Me | null }) {
  const trend = findTile(ov, 'sales.trend')
  const sales = findTile(ov, 'sales.accessories')
  const team = findTile(ov, 'team.reps')
  const attention = ov.modules.find((m) => m.key === 'attention')
  return (
    <div className="mt-5 space-y-3">
      <KpiRow ov={ov} me={me} />
      <div className={cn('grid grid-cols-1 gap-3', trend?.series && 'xl:grid-cols-[2fr_1fr]')}>
        {trend?.series && (
          <WeeklyChart t={trend} pace={findTile(ov, 'sales.pace')} me={me}
            drill={sales?.drill ? { to: sales.drill.to, label: 'Sales' } : null} />
        )}
        <Exceptions m={attention} me={me} />
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {team && <SalesmenPanel t={team} me={me} />}
        <ProductsPanel ov={ov} me={me} />
        <MerchantsPanel ov={ov} me={me} />
      </div>
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
      : 'The whole company on one page. Hover any figure for how it is counted; every module in full is under Details.'
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
          {view === 'all' && <FirstScreen ov={ov} me={me} />}
          {view === 'all' ? (
            <details className="group mt-6 rounded-2xl border bg-card/40 px-4 pb-4 pt-1 sm:px-5">
              <summary className="flex cursor-pointer list-none items-center gap-2 py-3 font-display text-[1rem] font-bold">
                <ChevronDown size={17} className="text-[#6D4091] transition-transform group-open:rotate-180 motion-reduce:transition-none dark:text-[#c7a6e6]" aria-hidden="true" />
                Details · every module
                <span className="font-sans text-[12px] font-normal text-muted-foreground">each tile with its basis</span>
              </summary>
              <ModuleList ov={ov} me={me} view={view} />
            </details>
          ) : <ModuleList ov={ov} me={me} view={view} />}
          <p className="mt-8 text-center text-[11px] text-muted-foreground">
            Read only. Focus figures change when the daily reports are uploaded; marketplace figures refresh every two minutes.
          </p>
        </div>
      )}
    </div>
  )
}

/** Every module in full: the page's Details on the whole view, the page itself on Team and Customers. */
function ModuleList({ ov, me, view }: { ov: Overview; me: Me | null; view: 'all' | 'team' | 'customers' }) {
  return (
    <>
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
    </>
  )
}
