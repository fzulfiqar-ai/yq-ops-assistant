import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  UploadCloud, FileSpreadsheet, CheckCircle2, XCircle, Loader2, X, AlertTriangle, Eraser, ShieldCheck, Undo2, History,
} from 'lucide-react'
import { apiGet, apiUpload, apiPost, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge, type BadgeTone } from '@/components/ui/badge'

// Client-side label hint for dropped files (mirrors scripts/ingest.classify()); backend is authoritative.
// `focus` is the EXACT Focus report name (matches what you export) so there's no confusion.
const REPORTS = [
  { key: 'sales_day_book', focus: 'Sales_day_book', desc: 'Sales — line items' },
  { key: 'summary_sales_register', focus: 'Summary_sales_register', desc: 'Sales — salesman / header' },
  { key: 'stock_balance_by_warehouse', focus: 'Stock_balance_by_warehouse', desc: 'Stock balance' },
  { key: 'stock_ledger', focus: 'Stock_ledger', desc: 'Stock movements + transfers' },
  { key: 'customer_summary_ageing_by_due_date', focus: 'Customer_summary_ageing_by_due_date', desc: 'Receivables' },
  { key: 'product_profitability', focus: 'Product_Profitability_Report', desc: 'Margins' },
  { key: 'masellingpricebook', focus: 'MASellingPriceBook', desc: 'Price book — standard' },
  { key: 'moderntradesellerbook', focus: 'ModernTradeSellerBook', desc: 'Price book — modern trade' },
  { key: 'multi_level_stock_movement', focus: 'Multi_level_stock_movement', desc: 'Product categories (occasional)' },
]
const matchReport = (name: string) => REPORTS.find((r) => name.toLowerCase().includes(r.key))

interface Coverage {
  report: string; label: string; cadence: 'daily' | 'weekly'
  data_until: string | null; days_behind: number | null
  status: 'current' | 'behind' | 'stale' | 'never'
}
interface PurgeTarget { key: string; table: string; date_col: string; label: string }
interface VerifyRow { metric: string; diff_pct: number; passed: boolean; note?: string }
interface IngestResult {
  files: string[]
  recognised?: { file: string; report: string }[]
  ignored?: { file: string; reason: string }[]
  ok: boolean
  data_as_of?: string
  verify?: { ok: boolean; rows: VerifyRow[] }
  changes?: { catalog?: string; new_skus?: string[]; anomaly?: string; loader?: string[] }
  error?: string
  /** loader override markers written for this upload (REPLACE_OK / PARTIAL_OK) */
  markers?: string[]
  /** raw subprocess tail for the developer — never the headline */
  detail?: string
  /** rows written per report, e.g. { Sales_day_book: 140 } */
  loaded?: Record<string, number>
}

// ── batch importer (preview -> commit -> undo; app/ingest_batch_api.py) ──────
type Severity = 'blocking' | 'warning' | 'info'
interface BatchException {
  code: string; severity: Severity; target: string | null; message: string; count: number | null; items: unknown[]
}
interface TargetPreview {
  file_rows: number
  sums: Record<string, string>
  actions?: Record<string, number>
  db_rows?: number
  db_sums?: Record<string, string>
  diff_error?: string
  scope?: { kind: string; col?: string; from?: string; to?: string; partitions?: string[][]; books?: string[] }
}
interface DayRow { key: string; file_rows: number; db_rows: number; file_gross: string; db_gross: string; delta_gross: string }
interface PreviewSummary {
  folder: string
  files: { file: string; target: string; report: string; rows?: number; raw_rows: number }[]
  ignored: { file: string; reason: string }[]
  targets: Record<string, TargetPreview>
  sales?: { per_day_changed?: DayRow[]; per_salesman_changed?: DayRow[]; error?: string }
  stock?: { db_latest_as_of?: string | null; missing_warehouses?: string[]; per_warehouse?: { warehouse: string; delta_qty: string; delta_value: string; file_qty: string | null; db_qty: string | null }[] }
  prices?: Record<string, { file_skus: number; unchanged: number; changes: { sku: string; old: string; new: string }[]; new_skus: string[]; dropped_skus: string[] }>
  receivables?: { rows_sum: string; focus_grand_total: string; gap: string }
  storage?: { stage_bytes?: number; replaced_bytes?: number; stage_rows?: number; committed_batches?: number }
  blocking_codes: string[]
  /** blocking codes no acknowledgement can clear (the preview is unreliable, a guard could not run, join < 80 %) */
  hard_blocking_codes?: string[]
  commit_available: boolean
  migration_applied: boolean
  batch_id: number | null
}
interface PreviewResult {
  ok: boolean; files?: string[]; batch_id: number | null; summary: PreviewSummary; exceptions: BatchException[]; error?: string
}
interface CommitResult {
  ok: boolean; error?: string
  result?: { targets: Record<string, { actions: Record<string, number>; rows: number; timings?: Record<string, number> }>; note?: string }
  after?: Record<string, unknown>
}
interface BatchRow {
  id: number; status: 'parsing' | 'previewed' | 'committed' | 'rejected' | 'undone' | 'failed'
  files: { file: string; target: string; rows?: number }[]
  created_by: string | null; created_at: string; committed_by: string | null; committed_at: string | null
  undone_by: string | null; undone_at: string | null
  targets: Record<string, { file_rows: number; actions: Record<string, number> | null }>
  blocking_codes: string[]
  hard_blocking_codes?: string[]
  last_commit_error?: string | null
  commit: Record<string, Record<string, number>>
}

const TARGET_LABEL: Record<string, string> = {
  orders: 'Invoices (register)', order_lines: 'Sales lines (day book)', stock_movements: 'Stock ledger',
  stock_balance: 'Stock balance', ar_ageing: 'Receivables', product_profitability: 'Profitability',
  selling_prices: 'Price book', ledger_entries: 'Accounts ledger',
}
const SEV_TONE: Record<Severity, BadgeTone> = { blocking: 'rose', warning: 'amber', info: 'grey' }
const STATUS_TONE: Record<BatchRow['status'], BadgeTone> = {
  parsing: 'grey', previewed: 'accent', committed: 'green', rejected: 'grey', undone: 'amber', failed: 'rose',
}
const bhd = (s: string | undefined | null) => (s == null ? '—' : Number(s).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 }))
const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—')
const removedKey = (a: Record<string, number>) => ('voided' in a ? 'voided' : 'deleted')

const DOT: Record<Coverage['status'], string> = {
  current: 'bg-emerald-500', behind: 'bg-amber-500', stale: 'bg-rose-500', never: 'bg-muted-foreground/30',
}

function freshness(r: Coverage) {
  if (!r.data_until) return 'not loaded yet'
  const ago = r.days_behind == null ? '' : r.days_behind <= 0 ? ' · today' : ` · ${r.days_behind}d ago`
  // Daily reports carry a transaction/snapshot date ("until …"); price books have no data date,
  // so we show when they were last loaded ("loaded …") to avoid the misleading "until today".
  return `${r.cadence === 'weekly' ? 'loaded' : 'until'} ${r.data_until}${ago}`
}

/** One previewed batch: per-target counts and totals, the exception list with acknowledgements, Commit / Discard. */
function BatchPreview({ preview, onDone }: { preview: PreviewResult; onDone: () => void }) {
  const qc = useQueryClient()
  const [acked, setAcked] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [commit, setCommit] = useState<CommitResult | null>(null)
  const [busy, setBusy] = useState<'commit' | 'discard' | null>(null)
  const [msg, setMsg] = useState('')
  const s = preview.summary
  const blocking = preview.exceptions.filter((e) => e.severity === 'blocking')
  const hard = new Set(s.hard_blocking_codes || [])
  const pending = s.blocking_codes.filter((c) => !acked.has(c))
  const canCommit = Boolean(s.batch_id) && s.commit_available && hard.size === 0 && pending.length === 0 && !commit?.ok

  function toggleAck(code: string) {
    setAcked((prev) => { const n = new Set(prev); if (n.has(code)) n.delete(code); else n.add(code); return n })
  }
  function toggleOpen(i: number) {
    setOpen((prev) => { const n = new Set(prev); if (n.has(i)) n.delete(i); else n.add(i); return n })
  }

  async function doCommit() {
    if (!s.batch_id) return
    const summary = Object.entries(s.targets).map(([t, v]) => {
      const a = v.actions || {}
      return `${TARGET_LABEL[t] || t}: +${a.inserted ?? 0} / ~${a.updated ?? 0} / −${a[removedKey(a)] ?? 0}`
    }).join('\n')
    if (!window.confirm(`Commit batch ${s.batch_id}? This applies the drop in one database transaction and checks every total again; on any mismatch nothing changes.\n\n${summary}`)) return
    setBusy('commit'); setMsg('')
    try {
      const r = await apiPost<CommitResult>(`/ingest/batches/${s.batch_id}/commit`, { acknowledged: Array.from(acked) })
      setCommit(r)
      if (r.ok) { qc.invalidateQueries({ queryKey: ['data', 'coverage'] }); qc.invalidateQueries({ queryKey: ['ingest', 'batches'] }) }
    } catch (e) {
      setCommit({ ok: false, error: e instanceof ApiError ? `${e.status}: ${e.body.slice(0, 200)}` : 'Commit failed.' })
    } finally { setBusy(null) }
  }

  async function discard() {
    if (!s.batch_id) { onDone(); return }
    const reason = window.prompt('Discard this preview? Nothing was loaded. Reason (optional):') ?? null
    if (reason === null) return
    setBusy('discard')
    try {
      await apiPost(`/ingest/batches/${s.batch_id}/reject`, { reason })
      qc.invalidateQueries({ queryKey: ['ingest', 'batches'] })
      onDone()
    } catch (e) {
      setMsg(e instanceof ApiError ? `${e.status}: ${e.body.slice(0, 160)}` : 'Discard failed.')
    } finally { setBusy(null) }
  }

  const targets = Object.entries(s.targets)
  return (
    <div className="mt-5 space-y-4 rounded-xl border bg-secondary/30 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2 font-semibold">
        <ShieldCheck className="text-primary" size={18} />
        Preview{s.batch_id ? ` · batch ${s.batch_id}` : ''} — nothing has been loaded yet
        <span className="ml-auto flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
          {blocking.length > 0 ? <Badge tone="rose">{blocking.length} blocking</Badge> : <Badge tone="green">no blockers</Badge>}
          {!s.migration_applied && <Badge tone="amber">migration not applied</Badge>}
        </span>
      </div>
      {preview.error && <div className="rounded-lg bg-destructive/10 px-3 py-2 text-[13px] text-destructive">{preview.error}</div>}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-[12.5px]">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <th className="py-1 pr-2 font-semibold">Report</th>
              <th className="py-1 pr-2 text-right font-semibold">File rows</th>
              <th className="py-1 pr-2 text-right font-semibold">Insert</th>
              <th className="py-1 pr-2 text-right font-semibold">Update</th>
              <th className="py-1 pr-2 text-right font-semibold">Same</th>
              <th className="py-1 pr-2 text-right font-semibold">Remove</th>
              <th className="py-1 pr-2 font-semibold">File total</th>
              <th className="py-1 font-semibold">In the database now</th>
            </tr>
          </thead>
          <tbody>
            {targets.map(([t, v]) => {
              const a = v.actions
              const rk = a ? removedKey(a) : 'deleted'
              return (
                <tr key={t} className="border-t border-border/60">
                  <td className="py-1.5 pr-2">
                    <div className="font-medium">{TARGET_LABEL[t] || t}</div>
                    {v.scope?.kind === 'span' && <div className="text-[11px] text-muted-foreground">{v.scope.from} → {v.scope.to}</div>}
                    {v.scope?.kind === 'partition' && <div className="text-[11px] text-muted-foreground">{v.scope.partitions?.map((p) => p.join(' · ')).join(', ')}</div>}
                    {v.scope?.kind === 'book' && <div className="text-[11px] text-muted-foreground">{v.scope.books?.join(', ')} (whole book)</div>}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{v.file_rows.toLocaleString()}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-emerald-700">{a ? a.inserted.toLocaleString() : '—'}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{a ? a.updated.toLocaleString() : '—'}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-muted-foreground">{a ? a.unchanged.toLocaleString() : '—'}</td>
                  <td className={cn('py-1.5 pr-2 text-right tabular-nums', a && a[rk] > 0 ? 'font-semibold text-rose-700' : '')}>
                    {a ? `${a[rk].toLocaleString()}${rk === 'voided' ? ' (void)' : ''}` : '—'}
                  </td>
                  <td className="py-1.5 pr-2 tabular-nums">{Object.entries(v.sums).map(([k, x]) => `${k.replace('_bhd', '').replace('_qty', ' u')} ${bhd(x)}`).join(' · ')}</td>
                  <td className="py-1.5 tabular-nums text-muted-foreground">
                    {v.diff_error ? <span className="text-rose-700">diff failed</span>
                      : v.db_sums ? `${(v.db_rows ?? 0).toLocaleString()} rows · ${Object.entries(v.db_sums).map(([k, x]) => `${k.replace('_bhd', '').replace('_qty', ' u')} ${bhd(x)}`).join(' · ')}` : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {(s.sales?.per_day_changed?.length || 0) > 0 && (
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Days that change</div>
          <div className="grid gap-1 sm:grid-cols-2">
            {s.sales!.per_day_changed!.slice(0, 12).map((d) => (
              <div key={d.key} className="flex items-center gap-2 text-[12.5px]">
                <span className="font-medium tabular-nums">{d.key}</span>
                <span className="text-muted-foreground">file {d.file_rows} lines · BHD {bhd(d.file_gross)}</span>
                <span className="text-muted-foreground">vs now {d.db_rows} · {bhd(d.db_gross)}</span>
                <span className={cn('ml-auto tabular-nums', Number(d.delta_gross) < 0 ? 'text-rose-700' : 'text-emerald-700')}>{Number(d.delta_gross) >= 0 ? '+' : ''}{bhd(d.delta_gross)}</span>
              </div>
            ))}
          </div>
          {s.sales!.per_day_changed!.length > 12 && <div className="mt-1 text-[11px] text-muted-foreground">… and {s.sales!.per_day_changed!.length - 12} more days</div>}
        </div>
      )}

      {s.stock?.per_warehouse?.length ? (
        <div className="text-[12.5px]">
          <span className="font-medium">Stock vs latest snapshot ({s.stock.db_latest_as_of || '—'}):</span>{' '}
          {s.stock.per_warehouse.map((w) => `${w.warehouse} ${Number(w.delta_qty) >= 0 ? '+' : ''}${bhd(w.delta_qty)} u / ${Number(w.delta_value) >= 0 ? '+' : ''}${bhd(w.delta_value)} BHD`).join('; ')}
        </div>
      ) : null}
      {s.prices && Object.entries(s.prices).map(([book, p]) => 'file_skus' in p ? (
        <div key={book} className="text-[12.5px]">
          <span className="font-medium">Price book {book}:</span> {p.file_skus} SKUs · {p.unchanged} unchanged · {p.changes.length} changes · {p.new_skus.length} new · {p.dropped_skus.length} dropped
        </div>
      ) : null)}
      {s.receivables && (
        <div className="text-[12.5px]">
          <span className="font-medium">Receivables:</span> rows {bhd(s.receivables.rows_sum)} · Focus Grand Total {bhd(s.receivables.focus_grand_total)} · gap {bhd(s.receivables.gap)}
        </div>
      )}

      <div>
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Exceptions ({preview.exceptions.length}) — blocking ones must be acknowledged before commit
        </div>
        {preview.exceptions.length === 0 && <div className="text-[12.5px] text-muted-foreground">None.</div>}
        <div className="space-y-1">
          {preview.exceptions.map((e, i) => (
            <div key={`${e.code}-${e.target}-${i}`} className="rounded-lg border bg-background px-3 py-2 text-[12.5px]">
              <div className="flex items-start gap-2">
                {e.severity === 'blocking' && !hard.has(e.code) ? (
                  <label className="mt-0.5 flex cursor-pointer items-center gap-1.5">
                    <input type="checkbox" checked={acked.has(e.code)} onChange={() => toggleAck(e.code)} disabled={Boolean(commit?.ok)} />
                  </label>
                ) : e.severity === 'blocking' ? (
                  <span className="mt-0.5 shrink-0 rounded bg-rose-100 px-1 text-[10px] font-semibold uppercase text-rose-800" title="This cannot be acknowledged: fix the export or the check, then preview again.">no override</span>
                ) : <span className="w-[13px]" />}
                <Badge tone={SEV_TONE[e.severity]}>{e.severity}</Badge>
                <span className="font-mono text-[11px] text-muted-foreground">{e.code}</span>
                <span className="flex-1">{e.message}</span>
                {e.items?.length > 0 && (
                  <button className="shrink-0 text-[11px] text-primary hover:underline" onClick={() => toggleOpen(i)}>
                    {open.has(i) ? 'hide' : `${e.items.length}${(e.count || 0) > e.items.length ? ` of ${e.count}` : ''} items`}
                  </button>
                )}
              </div>
              {open.has(i) && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-secondary/40 p-2 font-mono text-[11px] text-muted-foreground">
                  {e.items.map((it) => (typeof it === 'string' ? it : JSON.stringify(it))).join('\n')}
                </pre>
              )}
            </div>
          ))}
        </div>
      </div>

      {commit && (
        <div className={cn('rounded-lg px-3 py-2 text-[13px]', commit.ok ? 'bg-emerald-50 text-emerald-900' : 'bg-destructive/10 text-destructive')}>
          {commit.ok ? (
            <>
              <div className="flex items-center gap-2 font-semibold"><CheckCircle2 size={15} /> Committed in one transaction; every total re-checked.</div>
              {commit.result?.note && <div className="mt-1 text-[12px]">{commit.result.note}</div>}
              <div className="mt-1 flex flex-wrap gap-1.5">
                {Object.entries(commit.result?.targets || {}).map(([t, r]) => (
                  <span key={t} className="rounded-md border bg-background px-2 py-0.5 text-xs">
                    <span className="font-medium">{TARGET_LABEL[t] || t}</span>
                    <span className="text-muted-foreground"> · +{r.actions.inserted} / ~{r.actions.updated} / −{r.actions[removedKey(r.actions)]}</span>
                  </span>
                ))}
              </div>
            </>
          ) : <div className="flex items-center gap-2"><XCircle size={15} /> {commit.error}</div>}
        </div>
      )}
      {msg && <div className="rounded-lg bg-destructive/10 px-3 py-2 text-[13px] text-destructive">{msg}</div>}

      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={doCommit} disabled={!canCommit || busy !== null}>
          {busy === 'commit' ? <Loader2 className="animate-spin" size={16} /> : <ShieldCheck size={16} />}
          {commit?.ok ? 'Committed' : 'Commit batch'}
        </Button>
        {!commit?.ok && (
          <Button variant="outline" onClick={discard} disabled={busy !== null}>
            {busy === 'discard' ? <Loader2 className="animate-spin" size={15} /> : <X size={15} />} Discard preview
          </Button>
        )}
        {commit?.ok && <button onClick={onDone} className="text-sm text-muted-foreground hover:text-foreground">Close</button>}
        <span className="text-xs text-muted-foreground">
          {!s.batch_id ? 'No batch was created (the importer migration is not applied); commit is not possible.'
            : hard.size > 0 ? `This preview cannot be committed and no acknowledgement changes that (${Array.from(hard).join(', ')}). Fix the export or the check and preview again.`
            : !s.commit_available ? 'This preview cannot be committed — see the exceptions.'
              : pending.length ? `Acknowledge ${pending.length} blocking exception${pending.length > 1 ? 's' : ''} to enable Commit.`
                : commit?.ok ? '' : 'Commit applies the drop in one transaction; a mismatch rolls everything back.'}
        </span>
      </div>
    </div>
  )
}

/** Committed / previewed / undone batches, newest first, with Undo on committed ones. */
function BatchHistory() {
  const qc = useQueryClient()
  const [msg, setMsg] = useState('')
  const { data, isLoading } = useQuery({
    queryKey: ['ingest', 'batches'],
    queryFn: () => apiGet<{ batches: BatchRow[]; migration_applied: boolean; error?: string }>('/ingest/batches?limit=15'),
  })
  const undo = useMutation({
    mutationFn: (id: number) => apiPost<{ ok: boolean; error?: string; result?: { targets: Record<string, Record<string, number>> } }>(`/ingest/batches/${id}/undo`, {}),
    onSuccess: (r, id) => {
      setMsg(r.ok ? `Batch ${id} undone: its rows are gone and the rows it replaced are back.` : r.error || 'Undo refused.')
      qc.invalidateQueries({ queryKey: ['ingest', 'batches'] })
      qc.invalidateQueries({ queryKey: ['data', 'coverage'] })
    },
    onError: (e) => setMsg(e instanceof ApiError ? `${e.status}: ${e.body.slice(0, 160)}` : 'Undo failed.'),
  })
  function askUndo(b: BatchRow) {
    if (!window.confirm(`Undo batch ${b.id}? Its inserted rows are removed and every row it changed or removed is restored exactly.\n\nThe undo checks first that every row the batch wrote still reads as it wrote it, and is refused if anything touched those rows since: a batch committed later on the same dates, the default Upload & refresh, a purge or an edit. Then nothing changes.`)) return
    undo.mutate(b.id)
  }
  const rows = data?.batches || []
  return (
    <Card className="mt-4 p-5">
      <div className="flex items-center gap-2">
        <History size={15} className="text-muted-foreground" />
        <div className="text-sm font-semibold">Batch history</div>
        {data && !data.migration_applied && !data.error && <Badge tone="amber">importer migration not applied</Badge>}
        {data?.error && <Badge tone="rose">batch importer unavailable</Badge>}
      </div>
      {data?.error && <div className="mt-2 text-[13px] text-rose-700">{data.error}</div>}
      {isLoading && <div className="mt-2 text-[13px] text-muted-foreground">Loading…</div>}
      {data && rows.length === 0 && <div className="mt-2 text-[13px] text-muted-foreground">No batches yet. Tick "Preview first" above to use the batch importer.</div>}
      {rows.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[640px] text-[12.5px]">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <th className="py-1 pr-2 font-semibold">Batch</th>
                <th className="py-1 pr-2 font-semibold">Status</th>
                <th className="py-1 pr-2 font-semibold">Reports</th>
                <th className="py-1 pr-2 font-semibold">Result (+insert / ~update / −remove)</th>
                <th className="py-1 pr-2 font-semibold">Who / when</th>
                <th className="py-1 font-semibold" />
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const acts = b.status === 'committed' || b.status === 'undone' ? b.commit : Object.fromEntries(Object.entries(b.targets).map(([t, v]) => [t, v.actions || {}]))
                return (
                  <tr key={b.id} className="border-t border-border/60 align-top">
                    <td className="py-1.5 pr-2 font-medium tabular-nums">#{b.id}</td>
                    <td className="py-1.5 pr-2"><Badge tone={STATUS_TONE[b.status]}>{b.status}</Badge></td>
                    <td className="py-1.5 pr-2 text-muted-foreground">{(b.files || []).map((f) => TARGET_LABEL[f.target] || f.target).join(', ') || '—'}</td>
                    <td className="py-1.5 pr-2 tabular-nums">
                      {Object.entries(acts || {}).map(([t, a]) => (
                        <div key={t}><span className="text-muted-foreground">{TARGET_LABEL[t] || t}</span> +{a.inserted ?? 0} / ~{a.updated ?? 0} / −{a[removedKey(a)] ?? 0}</div>
                      ))}
                    </td>
                    <td className="py-1.5 pr-2 text-muted-foreground">
                      <div>{b.created_by || '—'} · {when(b.created_at)}</div>
                      {b.committed_at && <div>committed {when(b.committed_at)}</div>}
                      {b.undone_at && <div>undone by {b.undone_by || '—'} · {when(b.undone_at)}</div>}
                      {b.status === 'previewed' && b.last_commit_error && <div className="text-rose-700">last commit did not complete: {b.last_commit_error.slice(0, 120)}</div>}
                    </td>
                    <td className="py-1.5 text-right">
                      {b.status === 'committed' && (
                        <Button variant="outline" size="sm" onClick={() => askUndo(b)} disabled={undo.isPending}>
                          {undo.isPending && undo.variables === b.id ? <Loader2 className="animate-spin" size={14} /> : <Undo2 size={14} />} Undo
                        </Button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {msg && <div className="mt-3 rounded-lg bg-secondary/40 px-3 py-2 text-[13px]">{msg}</div>}
    </Card>
  )
}

export default function DataPage() {
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [error, setError] = useState('')
  // The loader's two override markers (scripts/load_supabase.py). Off by default: a normal daily
  // upload never needs them, and each one is audited server-side when ticked.
  const [replaceOk, setReplaceOk] = useState(false)
  const [partialOk, setPartialOk] = useState(false)
  // Off by default: the proven refresh path stays the default until the batch replay is signed off.
  const [batchMode, setBatchMode] = useState(false)

  const { data: cov } = useQuery({ queryKey: ['data', 'coverage'], queryFn: () => apiGet<Coverage[]>('/data/coverage') })
  const { data: purgeTargets } = useQuery({ queryKey: ['purge-targets'], queryFn: () => apiGet<{ targets: PurgeTarget[] }>('/ingest/purge-targets') })

  // ── Fix a bad upload: remove a report's rows for a date (or its null-date junk) ──
  const [pReport, setPReport] = useState('')
  const [pFrom, setPFrom] = useState('')
  const [pTo, setPTo] = useState('')
  const [pBlanks, setPBlanks] = useState(false)
  const [pBusy, setPBusy] = useState(false)
  const [pMsg, setPMsg] = useState('')

  async function purge() {
    const tgt = purgeTargets?.targets.find((t) => t.key === pReport)
    if (!tgt) { setPMsg('Pick a report first.'); return }
    if (!pBlanks && !pFrom) { setPMsg('Pick a date, or tick blank/no-date rows.'); return }
    const what = pBlanks ? 'rows with no date' : pTo && pTo !== pFrom ? `${pFrom} → ${pTo}` : pFrom
    if (!window.confirm(`Remove ${tgt.label} data for ${what}? Then re-upload the correct file. This can't be undone.`)) return
    setPBusy(true); setPMsg('')
    try {
      const r = await apiPost<{ ok?: boolean; deleted?: number; error?: string }>('/ingest/purge', {
        report: pReport, date_from: pBlanks ? null : pFrom, date_to: pBlanks ? null : (pTo || null), blanks: pBlanks,
      })
      if (r.error) setPMsg(r.error)
      else { setPMsg(`Removed ${r.deleted} row(s). Now re-upload the correct ${tgt.label} export above.`); qc.invalidateQueries({ queryKey: ['data', 'coverage'] }) }
    } catch (e) {
      setPMsg(e instanceof ApiError ? `${e.status}: ${e.body.slice(0, 120)}` : 'Remove failed.')
    } finally { setPBusy(false) }
  }

  function addFiles(list: FileList | null) {
    if (!list) return
    setFiles((prev) => {
      const byName = new Map(prev.map((f) => [f.name, f]))
      Array.from(list).forEach((f) => byName.set(f.name, f))
      return Array.from(byName.values())
    })
  }

  async function upload() {
    if (!files.length) return
    setBusy(true); setError(''); setResult(null); setPreview(null)
    try {
      const form = new FormData()
      files.forEach((f) => form.append('files', f))
      if (batchMode) {
        const r = await apiUpload<PreviewResult>('/ingest/preview', form)
        if (!r.ok && !r.summary) { setError(r.error || 'Preview failed.'); return }
        setPreview(r)
        qc.invalidateQueries({ queryKey: ['ingest', 'batches'] })
      } else {
        if (replaceOk) form.append('replace_ok', 'true')
        if (partialOk) form.append('partial_ok', 'true')
        setResult(await apiUpload<IngestResult>('/ingest', form))
        setReplaceOk(false); setPartialOk(false)
        qc.invalidateQueries({ queryKey: ['data', 'coverage'] })
      }
      setFiles([])
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.body.slice(0, 200)}` : 'Upload failed.')
    } finally {
      setBusy(false)
    }
  }

  const groups: Coverage['cadence'][] = ['daily', 'weekly']

  return (
    <div>
      <PageHeader title="Data" subtitle="Upload the day's Focus exports — verified refresh, no duplicates" />

      {/* Zoho-style coverage panel: what's loaded and how fresh */}
      <Card className="mb-4 p-5">
        <div className="mb-3 text-sm font-semibold">Data sources</div>
        {groups.map((g) => (
          <div key={g} className="mb-3 last:mb-0">
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {g === 'daily' ? 'Daily reports' : 'Weekly reports'}
            </div>
            <div className="space-y-1.5">
              {(cov || []).filter((r) => r.cadence === g).map((r) => (
                <div key={r.report} className="flex items-center gap-2 text-[13px]">
                  <span className={cn('h-2 w-2 shrink-0 rounded-full', DOT[r.status])} />
                  <span className="font-medium">{r.report}</span>
                  <span className="hidden text-muted-foreground sm:inline">· {r.label}</span>
                  <span className="ml-auto shrink-0 text-muted-foreground">{freshness(r)}</span>
                </div>
              ))}
              {!cov && <div className="text-[13px] text-muted-foreground">Loading…</div>}
            </div>
          </div>
        ))}
      </Card>

      <Card className="p-6">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files) }}
          onClick={() => inputRef.current?.click()}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-10 text-center transition',
            drag ? 'border-primary bg-accent' : 'border-border hover:border-primary/50',
          )}
        >
          <input ref={inputRef} type="file" multiple accept=".xlsx,.xls,.csv" className="hidden"
            onChange={(e) => addFiles(e.target.files)} />
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-accent text-accent-foreground">
            <UploadCloud size={26} />
          </div>
          <div className="mt-4 font-display text-base font-semibold">Drop the day's Focus exports here</div>
          <div className="mt-1 text-sm text-muted-foreground">
            or click to browse — select all the reports at once (.xlsx). Non-Focus files are ignored.
          </div>
        </div>

        {files.length > 0 && (
          <div className="mt-4 space-y-1.5">
            {files.map((f) => {
              const m = matchReport(f.name)
              return (
                <div key={f.name} className="flex items-center gap-2 rounded-lg border bg-secondary/30 px-3 py-2 text-sm">
                  <FileSpreadsheet size={15} className="text-muted-foreground" />
                  <span className="truncate">{f.name}</span>
                  <span className={cn('ml-auto shrink-0 text-xs', m ? 'text-primary' : 'text-amber-600')}>
                    {m ? `${m.focus} · ${m.desc}` : 'not recognised — will be ignored'}
                  </span>
                  <button onClick={(e) => { e.stopPropagation(); setFiles((p) => p.filter((x) => x.name !== f.name)) }}
                    className="text-muted-foreground hover:text-foreground"><X size={14} /></button>
                </div>
              )
            })}
          </div>
        )}

        {files.length > 0 && !batchMode && (
          <div className="mt-3 grid gap-1.5 text-[12.5px] text-muted-foreground sm:grid-cols-2">
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={replaceOk} onChange={(e) => setReplaceOk(e.target.checked)} className="mt-0.5" />
              <span><span className="font-medium text-foreground">Replace on purpose</span> — this sales export is complete;
                invoices in the database for its dates that it no longer lists should be removed, even a whole salesman's
                (otherwise the loader keeps them and verify fails).</span>
            </label>
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={partialOk} onChange={(e) => setPartialOk(e.target.checked)} className="mt-0.5" />
              <span><span className="font-medium text-foreground">Partial export intended</span> — this stock snapshot covers
                fewer warehouses than the previous one on purpose (the others keep their last snapshot).</span>
            </label>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button onClick={upload} disabled={!files.length || busy}>
            {busy ? <Loader2 className="animate-spin" size={16} /> : batchMode ? <ShieldCheck size={16} /> : <UploadCloud size={16} />}
            {busy ? (batchMode ? 'Previewing…' : 'Refreshing…') : batchMode ? `Preview${files.length ? ` (${files.length})` : ''}` : `Upload & refresh${files.length ? ` (${files.length})` : ''}`}
          </Button>
          {files.length > 0 && !busy && (
            <button onClick={() => setFiles([])} className="text-sm text-muted-foreground hover:text-foreground">Clear</button>
          )}
          <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground" title="Stage the drop, see exactly what would change, then commit it in one transaction (undo-able). Off = the current refresh path.">
            <input type="checkbox" checked={batchMode} onChange={(e) => setBatchMode(e.target.checked)} disabled={busy} />
            Preview first, then commit (batch importer)
          </label>
        </div>

        {error && <div className="mt-4 rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>}

        {preview && <BatchPreview preview={preview} onDone={() => setPreview(null)} />}

        {result && (
          <div className="mt-5 space-y-3 rounded-xl border bg-secondary/30 p-4 text-sm">
            <div className="flex items-center gap-2 font-semibold">
              {result.ok ? <CheckCircle2 className="text-emerald-600" size={18} /> : <AlertTriangle className="text-amber-600" size={18} />}
              {result.ok ? 'Data refreshed' : 'Refresh needs attention'}
              {result.data_as_of && <span className="ml-auto text-xs font-normal text-muted-foreground">data as of {result.data_as_of}</span>}
            </div>
            {result.error && <div className="text-[13px] text-amber-800">{result.error}</div>}
            {result.loaded && Object.keys(result.loaded).length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(result.loaded).map(([report, n]) => (
                  <span key={report} className="rounded-md border bg-background px-2 py-0.5 text-xs">
                    <span className="font-medium">{report}</span>
                    <span className="text-muted-foreground"> · {n.toLocaleString()} rows</span>
                  </span>
                ))}
              </div>
            ) : result.recognised?.length ? (
              <div className="text-[13px]"><span className="font-medium">Files:</span> {result.recognised.map((r) => r.file).join(', ')}</div>
            ) : null}
            {result.ignored?.length ? (
              <div className="text-[13px] text-amber-700">
                <span className="font-medium">Ignored:</span> {result.ignored.map((i) => `${i.file} (${i.reason})`).join('; ')}
              </div>
            ) : null}
            {/* what the loader's guards refused: a skipped span replace (with the invoices and the
                salesmen that would have vanished), a stock snapshot narrower than the previous one */}
            {result.changes?.loader?.length ? (
              <div className="space-y-1 text-[13px] text-amber-800">
                {result.changes.loader.map((t, i) => (
                  <div key={i}><span className="font-medium">Loader:</span> {t}</div>
                ))}
              </div>
            ) : null}
            {result.markers?.length ? (
              <div className="text-[12px] text-muted-foreground">Overrides used: {result.markers.join(', ')}</div>
            ) : null}
            {result.verify?.rows?.length ? (
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Verify {result.verify.ok ? 'PASS' : 'FAIL'}
                </div>
                <div className="grid gap-1 sm:grid-cols-2">
                  {result.verify.rows.map((r) => (
                    <div key={r.metric} className="flex items-center gap-2 text-[13px]" title={r.note}>
                      {r.passed ? <CheckCircle2 size={13} className="shrink-0 text-emerald-600" /> : <XCircle size={13} className="shrink-0 text-rose-600" />}
                      <span className="truncate">{r.metric}</span>
                      {r.note ? null : <span className="shrink-0 text-muted-foreground">({r.diff_pct.toFixed(2)}%)</span>}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {result.detail && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Technical detail</summary>
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-background p-2 font-mono text-[11px] text-muted-foreground">{result.detail}</pre>
              </details>
            )}
            {result.changes?.catalog && (
              <div className="text-[13px]"><span className="font-medium">Changes:</span> {result.changes.catalog}</div>
            )}
            {result.changes?.anomaly && (
              <div className="text-[13px]"><span className="font-medium">Integrity:</span> {result.changes.anomaly}</div>
            )}
          </div>
        )}
      </Card>

      <BatchHistory />

      {/* Fix a bad upload — remove a report's rows for a date, then re-upload */}
      <Card className="mt-4 p-5">
        <div className="flex items-center gap-2">
          <Eraser size={15} className="text-muted-foreground" />
          <div className="text-sm font-semibold">Fix a bad upload — remove a day's data</div>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Uploaded the wrong or a partial export? Remove that report's rows for the date, then re-upload the
          correct file above. (Re-uploading good data never duplicates — this is only for clearing bad data.
          A committed batch is better undone from the history above: that restores exactly what it replaced.)
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Report
            <select value={pReport} onChange={(e) => setPReport(e.target.value)}
              className="w-60 rounded-lg border border-border bg-background px-2.5 py-1.5 text-sm font-normal normal-case text-foreground outline-none focus:border-primary/50">
              <option value="">Select a report…</option>
              {(purgeTargets?.targets || []).map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          </label>
          <label className={cn('flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground', pBlanks && 'opacity-40')}>
            Date {pTo ? 'from' : ''}
            <input type="date" value={pFrom} disabled={pBlanks} onChange={(e) => setPFrom(e.target.value)}
              className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-sm font-normal text-foreground outline-none focus:border-primary/50 disabled:opacity-50" />
          </label>
          <label className={cn('flex flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground', pBlanks && 'opacity-40')}>
            To (optional)
            <input type="date" value={pTo} disabled={pBlanks} onChange={(e) => setPTo(e.target.value)}
              className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-sm font-normal text-foreground outline-none focus:border-primary/50 disabled:opacity-50" />
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 pb-1.5 text-xs text-muted-foreground">
            <input type="checkbox" checked={pBlanks} onChange={(e) => setPBlanks(e.target.checked)} />
            blank / no-date rows
          </label>
          <Button variant="destructive" onClick={purge} disabled={pBusy || !pReport}>
            {pBusy ? <Loader2 className="animate-spin" size={15} /> : <Eraser size={15} />} Remove
          </Button>
        </div>
        {pMsg && <div className="mt-3 rounded-lg bg-secondary/40 px-3 py-2 text-[13px]">{pMsg}</div>}
      </Card>

      <p className="mt-4 text-xs text-muted-foreground">
        Export the 6 daily reports from Focus (date range up to yesterday) and drop them all here at
        once. Figures are verified against the reports before they go live; re-uploading the same days
        never creates duplicates. Weekly price books change rarely. With "Preview first" the drop is staged,
        every change is shown, and Commit applies it in one transaction that can be undone.
      </p>
    </div>
  )
}
