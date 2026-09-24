import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Coins, FileClock, Loader2, RefreshCw } from 'lucide-react'
import { apiGet, apiPost, ApiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, type Column } from '@/components/DataTable'

/**
 * The two admin rollups on the Salesmen page (release R3a):
 *  - Attainment: every rep's current month (Accessories, ex-VAT) against the effective target row,
 *    the tier reached and the estimated kickback — one SQL, the same tier_progress the rep sees.
 *  - Statements: the frozen months. draft → approved → paid, or superseded by a newer draft; amounts
 *    are never edited here or anywhere. Money arrives as 3-dp strings and is shown as such.
 */

/* ───────────────────────── shared ───────────────────────── */

const bhd3s = (v?: string | number | null) => {
  const n = Number(v ?? 0)
  return `BHD ${(Number.isFinite(n) ? n : 0).toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`
}
const month = (ym?: string | null) => {
  if (!ym) return ''
  const [y, m] = ym.split('-').map(Number)
  return new Date(y, (m || 1) - 1, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
}
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '')
const currentPeriod = () => {
  const d = new Date(Date.now() + 3 * 3600 * 1000)   // Bahrain, UTC+3
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}
function apiMessage(e: unknown, fallback: string): string {
  if (!(e instanceof ApiError)) return fallback
  try {
    const d = (JSON.parse(e.body) as { detail?: unknown }).detail
    if (typeof d === 'string' && d) return d
  } catch { /* not JSON */ }
  return e.body.slice(0, 160) || fallback
}

/* ───────────────────────── attainment ───────────────────────── */

interface AttainRow {
  salesman: string
  salesman_id?: number | null
  name?: string | null
  is_active?: boolean | null
  team?: string | null
  target_period?: string | null
  no_target: boolean
  period?: string | null
  data_through?: string | null
  net_bhd: number
  gross_bhd: number
  basis?: string
  invoices: number
  shops: number
  last_sale?: string | null
  tier_reached?: number | null
  kickback_pct?: number | null
  kickback_bhd?: number | null
  next_tier?: { n: number; bhd: number; gap_bhd: number; pct: number } | null
  progress_pct?: number | null
  days_left?: number | null
}
interface AttainResp { rows: AttainRow[]; period?: string | null; data_through?: string | null; basis?: string; division?: string; count: number }

export function AttainmentTab() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['shop-attainment'], queryFn: () => apiGet<AttainResp>('/shop/attainment'), staleTime: 60_000 })
  const rows = useMemo(() => data?.rows || [], [data])
  const total = useMemo(() => rows.reduce((s, r) => s + Number(r.net_bhd || 0), 0), [rows])
  const kick = useMemo(() => rows.reduce((s, r) => s + Number(r.kickback_bhd || 0), 0), [rows])
  const cols: Column<AttainRow>[] = [
    { key: 'salesman', label: 'Rep', render: (_, r) => (
        <span className="flex items-center gap-2">
          <span className="font-semibold">{r.name || r.salesman}</span>
          {r.no_target ? <Badge tone="amber">No target</Badge> : null}
          {r.is_active === false ? <Badge tone="grey">Inactive</Badge> : null}
        </span>
      ) },
    { key: 'team', label: 'Team', render: (_, r) => (r.team ? r.team.replace('_', ' ') : '—') },
    { key: 'net_bhd', label: 'Sold (ex-VAT)', align: 'right', render: (_, r) => <span className="tabular-nums">{bhd3s(r.net_bhd)}</span> },
    { key: 'gross_bhd', label: 'Gross', align: 'right', render: (_, r) => <span className="tabular-nums text-muted-foreground">{bhd3s(r.gross_bhd)}</span> },
    { key: 'tier_reached', label: 'Tier', render: (_, r) => r.no_target ? '—' : (
        <Badge tone={r.tier_reached ? 'accent' : 'grey'}>{r.tier_reached ? `Tier ${r.tier_reached} · ${Math.round((r.kickback_pct || 0) * 100)}%` : 'Below Tier 1'}</Badge>
      ) },
    { key: 'progress_pct', label: 'Progress', render: (_, r) => r.no_target ? '' : (
        <div className="min-w-[120px]">
          <div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, r.progress_pct || 0)}%` }} /></div>
          <div className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">{r.next_tier ? `${bhd3s(r.next_tier.gap_bhd)} to Tier ${r.next_tier.n}` : 'Top tier reached'}</div>
        </div>
      ) },
    { key: 'kickback_bhd', label: 'Kickback (est.)', align: 'right', render: (_, r) => r.no_target ? '—' : <span className="tabular-nums font-semibold">{bhd3s(r.kickback_bhd)}</span> },
    { key: 'invoices', label: 'Invoices', align: 'right' },
    { key: 'shops', label: 'Shops', align: 'right' },
    { key: 'last_sale', label: 'Last sale', render: (_, r) => (r.last_sale ? day(r.last_sale) : '—') },
    { key: 'target_period', label: 'Target row', render: (_, r) => (r.no_target ? '—' : r.target_period ? r.target_period : 'standing') },
  ]
  return (
    <div>
      <p className="mb-3 text-sm text-muted-foreground">
        {data?.period ? <><strong>{month(data.period)}</strong> · </> : null}
        Accessories only, ex-VAT, giveaways excluded, SIM never counts — the same figures as each rep's Today card.
        {data?.data_through ? ` Sales data to ${day(data.data_through)}.` : ''} Kickback is an estimate until a statement is approved; returns are not yet deducted.
      </p>
      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : isError ? (
        <p className="text-sm text-destructive">Could not load the attainment.</p>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              { k: 'Reps', v: String(rows.length) },
              { k: 'With a target', v: String(rows.filter((r) => !r.no_target).length) },
              { k: 'Sold (ex-VAT)', v: bhd3s(total) },
              { k: 'Kickback (est.)', v: bhd3s(kick) },
            ].map((s) => (
              <Card key={s.k} className="p-3">
                <div className="text-[11px] text-muted-foreground">{s.k}</div>
                <div className="font-display text-[17px] font-bold tabular-nums">{s.v}</div>
              </Card>
            ))}
          </div>
          <DataTable rows={rows} cols={cols} exportName="yq-attainment" empty="No targets loaded and no accessories sales this month." />
        </>
      )}
    </div>
  )
}

/* ───────────────────────── statements ───────────────────────── */

interface Statement {
  id: number
  salesman: string
  salesman_id?: number | null
  period: string
  basis: string
  status: string
  data_through?: string | null
  sales_bhd: string
  returns_bhd?: string | null
  tier_reached: number
  rate: number
  kickback_bhd: string
  note?: string | null
  created_by: string
  created_at?: string | null
  approved_by?: string | null
  approved_at?: string | null
  paid_at?: string | null
  paid_by?: string | null
  superseded_at?: string | null
  superseded_by?: string | null
  superseded_reason?: string | null
  superseded_by_id?: number | null
  next_statuses: string[]
}
interface ListResp { statements: Statement[]; available: boolean; summary: Record<string, { count: number; kickback_bhd: string }> }
interface PreviewRow { salesman: string; sales_bhd: string; tier_reached: number; rate: number; kickback_bhd: string; data_through?: string | null }
interface PreviewResp { period: string; basis: string; rows: PreviewRow[]; total_kickback_bhd: string }
interface DraftResp { period: string; created: { salesman: string; id: number | null; kickback_bhd: string; note?: string | null }[]; skipped: { salesman: string; reason: string }[]; superseded: { salesman: string; id: number }[]; total_kickback_bhd: string }

const TONE: Record<string, BadgeTone> = { draft: 'amber', approved: 'accent', paid: 'green', superseded: 'grey', snapshot: 'ink' }

export function StatementsTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const [period, setPeriod] = useState(currentPeriod())
  const [filter, setFilter] = useState<string>('')
  const [preview, setPreview] = useState<PreviewResp | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const listQ = useQuery({ queryKey: ['shop-statements'], queryFn: () => apiGet<ListResp>('/shop/statements'), staleTime: 30_000 })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['shop-statements'] })
    qc.invalidateQueries({ queryKey: ['shop-me'] })
  }

  const draft = useMutation({
    mutationFn: () => apiPost<DraftResp>('/shop/statements/draft', { period }),
    onSuccess: (r) => {
      setPreview(null)
      toast(`${month(r.period)}: ${r.created.length} draft${r.created.length === 1 ? '' : 's'} created${r.skipped.length ? `, ${r.skipped.length} unchanged` : ''}${r.superseded.length ? `, ${r.superseded.length} older draft${r.superseded.length === 1 ? '' : 's'} superseded` : ''} · ${bhd3s(r.total_kickback_bhd)}`, 'success')
      refresh()
    },
    onError: (e) => toast(apiMessage(e, 'Could not create the draft.'), 'error'),
  })
  const move = useMutation({
    mutationFn: ({ id, to, reason }: { id: number; to: 'approve' | 'paid' | 'supersede'; reason?: string }) =>
      apiPost<{ ok: boolean; statement: Statement }>(`/shop/statements/${id}/${to}`, to === 'supersede' ? { reason } : undefined),
    onSuccess: (r) => {
      toast(`${r.statement.salesman} · ${month(r.statement.period)} is now ${r.statement.status}.`, 'success')
      refresh()
    },
    onError: (e) => {
      toast(apiMessage(e, 'Could not update the statement.'), 'error')
      refresh()
    },
  })

  async function doPreview() {
    setPreviewing(true)
    try {
      setPreview(await apiGet<PreviewResp>(`/shop/statements/preview?period=${encodeURIComponent(period)}`))
    } catch (e) {
      toast(apiMessage(e, 'Could not preview.'), 'error')
    } finally {
      setPreviewing(false)
    }
  }
  function approve(s: Statement) {
    if (!window.confirm(`Approve ${s.salesman} · ${month(s.period)} · ${bhd3s(s.kickback_bhd)}? The figures are frozen as they are; approval cannot be undone (only superseded by a new draft).`)) return
    move.mutate({ id: s.id, to: 'approve' })
  }
  function paid(s: Statement) {
    if (!window.confirm(`Mark ${s.salesman} · ${month(s.period)} · ${bhd3s(s.kickback_bhd)} as paid?`)) return
    move.mutate({ id: s.id, to: 'paid' })
  }
  function supersede(s: Statement) {
    const reason = window.prompt(`Why is ${s.salesman} · ${month(s.period)} being superseded? (kept on record)`)
    if (!reason || !reason.trim()) return
    move.mutate({ id: s.id, to: 'supersede', reason: reason.trim() })
  }

  const all = useMemo(() => listQ.data?.statements || [], [listQ.data])
  const periods = useMemo(() => Array.from(new Set(all.map((s) => s.period))).sort().reverse(), [all])
  const rows = useMemo(() => (filter ? all.filter((s) => s.period === filter) : all), [all, filter])
  const summary = listQ.data?.summary || {}

  const cols: Column<Statement>[] = [
    { key: 'period', label: 'Month', render: (_, r) => <span className="font-semibold">{month(r.period)}</span> },
    { key: 'salesman', label: 'Rep' },
    { key: 'status', label: 'Status', render: (_, r) => (
        <span className="flex items-center gap-1.5">
          <Badge tone={TONE[r.status] || 'grey'}>{r.status}</Badge>
          {r.basis === 'vat_incl_display' ? <Badge tone="grey" title="what reps saw before the ex-VAT switch">VAT-incl display</Badge> : null}
        </span>
      ) },
    { key: 'sales_bhd', label: 'Sold', align: 'right', render: (_, r) => <span className="tabular-nums">{bhd3s(r.sales_bhd)}</span> },
    { key: 'tier_reached', label: 'Tier', render: (_, r) => (r.tier_reached ? `Tier ${r.tier_reached} · ${Math.round(r.rate * 100)}%` : 'Below Tier 1') },
    { key: 'kickback_bhd', label: 'Kickback', align: 'right', render: (_, r) => <span className="font-semibold tabular-nums">{bhd3s(r.kickback_bhd)}</span> },
    { key: 'returns_bhd', label: 'Returns', align: 'right', render: (_, r) => (r.returns_bhd == null ? <span className="text-muted-foreground">not valued</span> : bhd3s(r.returns_bhd)) },
    { key: 'data_through', label: 'Data to', render: (_, r) => (r.data_through ? day(r.data_through) : '—') },
    { key: 'approved_at', label: 'Trail', render: (_, r) => (
        <span className="text-[12px] text-muted-foreground">
          {r.created_by} {day(r.created_at)}
          {r.approved_at ? ` · approved ${day(r.approved_at)} by ${r.approved_by || '?'}` : ''}
          {r.paid_at ? ` · paid ${day(r.paid_at)}${r.paid_by ? ` by ${r.paid_by}` : ''}` : ''}
          {r.superseded_at ? ` · superseded ${day(r.superseded_at)}${r.superseded_by_id ? ` by #${r.superseded_by_id}` : ''}${r.superseded_reason ? ` (${r.superseded_reason})` : ''}` : ''}
          {r.note ? ` · ${r.note}` : ''}
        </span>
      ) },
    { key: 'id', label: '', align: 'right', render: (_, r) => (
        <div className="flex justify-end gap-1.5">
          {r.next_statuses.includes('approved') && <Button type="button" size="sm" disabled={move.isPending} onClick={() => approve(r)}><CheckCircle2 size={13} /> Approve</Button>}
          {r.next_statuses.includes('paid') && <Button type="button" size="sm" disabled={move.isPending} onClick={() => paid(r)}><Coins size={13} /> Mark paid</Button>}
          {r.next_statuses.includes('superseded') && <Button type="button" size="sm" variant="outline" disabled={move.isPending} onClick={() => supersede(r)}>Supersede</Button>}
        </div>
      ) },
  ]

  return (
    <div>
      <p className="mb-3 text-sm text-muted-foreground">
        A statement freezes a rep's month exactly as the Today card computed it (Accessories, ex-VAT, whole month at the reached tier).
        It moves <strong>draft → approved → paid</strong>; a correction is a <em>new</em> draft and the old row is superseded. Amounts are never edited.
        Returns are deducted only once the Focus Sales Return register is loaded — until then they show as not valued.
      </p>

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold text-muted-foreground">Month</span>
            <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} className="h-9 rounded-lg border border-border bg-card px-3 text-sm" />
          </label>
          <Button type="button" variant="outline" size="sm" disabled={previewing || !/^\d{4}-\d{2}$/.test(period)} onClick={doPreview}>
            {previewing ? <Loader2 size={14} className="animate-spin" /> : <FileClock size={14} />} Preview
          </Button>
          <Button type="button" size="sm" disabled={draft.isPending || !/^\d{4}-\d{2}$/.test(period)} onClick={() => {
            if (window.confirm(`Freeze ${month(period)} as draft statements for every rep with a target? Nothing is paid until you approve.`)) draft.mutate()
          }}>
            {draft.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Create draft
          </Button>
          {listQ.data && !listQ.data.available && <span className="text-sm text-amber-700">The statements table is not there yet — apply kickback_statements_migration.sql.</span>}
        </div>
        {preview && (
          <div className="mt-3 overflow-x-auto">
            <div className="mb-1 text-[12px] text-muted-foreground">
              What a draft for {month(preview.period)} would freeze now ({preview.basis}) · total {bhd3s(preview.total_kickback_bhd)}. Nothing has been written.
            </div>
            <table className="w-full text-[13px]">
              <thead><tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground"><th className="py-1 pr-3">Rep</th><th className="py-1 pr-3 text-right">Sold</th><th className="py-1 pr-3">Tier</th><th className="py-1 pr-3 text-right">Kickback</th><th className="py-1">Data to</th></tr></thead>
              <tbody>
                {preview.rows.map((r) => (
                  <tr key={r.salesman} className="border-t border-border">
                    <td className="py-1 pr-3">{r.salesman}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{bhd3s(r.sales_bhd)}</td>
                    <td className="py-1 pr-3">{r.tier_reached ? `Tier ${r.tier_reached} · ${Math.round(r.rate * 100)}%` : '—'}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{bhd3s(r.kickback_bhd)}</td>
                    <td className="py-1">{r.data_through ? day(r.data_through) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {Object.entries(summary).map(([st, v]) => (
          <span key={st} className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[12px]">
            <Badge tone={TONE[st] || 'grey'}>{st}</Badge> {v.count} · {bhd3s(v.kickback_bhd)}
          </span>
        ))}
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by month" className="ml-auto h-9 rounded-lg border border-border bg-card px-2 text-sm">
          <option value="">All months</option>
          {periods.map((p) => <option key={p} value={p}>{month(p)}</option>)}
        </select>
      </div>

      {listQ.isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : (
        <DataTable rows={rows} cols={cols} exportName="yq-kickback-statements"
          rowClass={(r) => cn(r.status === 'superseded' && 'opacity-60')}
          empty="No statements yet — preview a month, then create its draft." />
      )}
    </div>
  )
}
