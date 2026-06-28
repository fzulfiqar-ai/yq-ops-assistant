import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { UploadCloud, FileSpreadsheet, CheckCircle2, XCircle, Loader2, X, AlertTriangle, Eraser } from 'lucide-react'
import { apiGet, apiUpload, apiPost, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

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
interface VerifyRow { metric: string; diff_pct: number; passed: boolean }
interface IngestResult {
  files: string[]
  recognised?: { file: string; report: string }[]
  ignored?: { file: string; reason: string }[]
  ok: boolean
  data_as_of?: string
  verify?: { ok: boolean; rows: VerifyRow[] }
  changes?: { catalog?: string; new_skus?: string[]; anomaly?: string }
  error?: string
}

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

export default function DataPage() {
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [error, setError] = useState('')

  const { data: cov } = useQuery({ queryKey: ['coverage'], queryFn: () => apiGet<Coverage[]>('/data/coverage') })
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
      else { setPMsg(`Removed ${r.deleted} row(s). Now re-upload the correct ${tgt.label} export above.`); qc.invalidateQueries({ queryKey: ['coverage'] }) }
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
    setBusy(true); setError(''); setResult(null)
    try {
      const form = new FormData()
      files.forEach((f) => form.append('files', f))
      setResult(await apiUpload<IngestResult>('/ingest', form))
      setFiles([])
      qc.invalidateQueries({ queryKey: ['coverage'] })
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

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={upload} disabled={!files.length || busy}>
            {busy ? <Loader2 className="animate-spin" size={16} /> : <UploadCloud size={16} />}
            {busy ? 'Refreshing…' : `Upload & refresh${files.length ? ` (${files.length})` : ''}`}
          </Button>
          {files.length > 0 && !busy && (
            <button onClick={() => setFiles([])} className="text-sm text-muted-foreground hover:text-foreground">Clear</button>
          )}
        </div>

        {error && <div className="mt-4 rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>}

        {result && (
          <div className="mt-5 space-y-3 rounded-xl border bg-secondary/30 p-4 text-sm">
            <div className="flex items-center gap-2 font-semibold">
              {result.ok ? <CheckCircle2 className="text-emerald-600" size={18} /> : <AlertTriangle className="text-amber-600" size={18} />}
              {result.ok ? 'Data refreshed' : 'Refresh needs attention'}
              {result.data_as_of && <span className="ml-auto text-xs font-normal text-muted-foreground">data as of {result.data_as_of}</span>}
            </div>
            {result.error && <div className="text-xs text-amber-700">{result.error}</div>}
            {result.recognised?.length ? (
              <div className="text-[13px]"><span className="font-medium">Loaded:</span> {result.recognised.map((r) => r.file).join(', ')}</div>
            ) : null}
            {result.ignored?.length ? (
              <div className="text-[13px] text-amber-700">
                <span className="font-medium">Ignored:</span> {result.ignored.map((i) => `${i.file} (${i.reason})`).join('; ')}
              </div>
            ) : null}
            {result.verify?.rows?.length ? (
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Verify {result.verify.ok ? 'PASS' : 'FAIL'}
                </div>
                <div className="grid gap-1 sm:grid-cols-2">
                  {result.verify.rows.map((r) => (
                    <div key={r.metric} className="flex items-center gap-2 text-[13px]">
                      {r.passed ? <CheckCircle2 size={13} className="text-emerald-600" /> : <XCircle size={13} className="text-rose-600" />}
                      {r.metric} <span className="text-muted-foreground">({r.diff_pct.toFixed(2)}%)</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {result.changes?.catalog && (
              <div className="text-[13px]"><span className="font-medium">Changes:</span> {result.changes.catalog}</div>
            )}
            {result.changes?.anomaly && (
              <div className="text-[13px]"><span className="font-medium">Integrity:</span> {result.changes.anomaly}</div>
            )}
          </div>
        )}
      </Card>

      {/* Fix a bad upload — remove a report's rows for a date, then re-upload */}
      <Card className="mt-4 p-5">
        <div className="flex items-center gap-2">
          <Eraser size={15} className="text-muted-foreground" />
          <div className="text-sm font-semibold">Fix a bad upload — remove a day's data</div>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Uploaded the wrong or a partial export? Remove that report's rows for the date, then re-upload the
          correct file above. (Re-uploading good data never duplicates — this is only for clearing bad data.)
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
        never creates duplicates. Weekly price books change rarely. (Full hands-off auto-export: later step.)
      </p>
    </div>
  )
}
