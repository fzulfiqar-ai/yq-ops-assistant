import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Download, FileSpreadsheet, Loader2, Stamp } from 'lucide-react'
import { apiGet, apiGetDownload, apiPatch, apiPost } from '@/lib/api'
import { errorText } from '@/lib/errorText'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { LoadError } from '@/components/LoadError'
import { Badge } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import {
  evidenceLabel,
  monthText,
  nameOf,
  notReady,
  previousMonth,
  type MiEdition,
  type MiLists,
  type MiReport,
  type MiReportDetail,
  type MiReportRow,
} from '@/lib/marketIntel'
import { Empty, Loading, NotAvailableYet } from './ui'
import { BTN, BTN_OUTLINE, CARD, dayText, INPUT, LABEL, qs, REPORT_STATUS_TONE, SELECT, TEXTAREA, whenText, who, type MiRoles } from './util'

/**
 * Supplier reports (release r8m, plan F9, Phase 2): month + supplier -> a DRAFT built by the API from
 * that supplier's reports and cases (counts by issue, new vs recurring, open actions, and a narrative
 * in which every finding cites its report IDs [MI-…] and every action [CA-…]; weak evidence sits in
 * its own paragraph and is not counted) -> the narrative edited -> approved -> the .xlsx downloaded.
 * Reviewers build and edit drafts; approvers (reviewers and management) approve and download. A newer
 * approval of the same supplier, month and edition supersedes the older one. Nothing is e-mailed:
 * the approver sends the workbook himself, and every download is logged with the file's sha256.
 */

const EDITION_LABEL: Record<MiEdition, string> = { supplier: 'Supplier edition', internal: 'Internal edition' }
const STATUS_LABEL: Record<MiReport['status'], string> = { draft: 'Draft', approved: 'Approved', superseded: 'Superseded' }

function rowsOf(d: unknown): MiReport[] {
  if (Array.isArray(d)) return d as MiReport[]
  const o = (d || {}) as { rows?: MiReport[]; reports?: MiReport[]; items?: MiReport[] }
  return o.rows || o.reports || o.items || []
}

function supplierOf(r: Partial<MiReport>, lists: MiLists | null): string {
  return nameOf(r.supplier) || r.supplier_name || lists?.suppliers.find((s) => s.id === r.supplier_id)?.name || (r.supplier_id ? `Supplier ${r.supplier_id}` : 'Supplier')
}

export function ReportsTab({ roles, lists }: { roles: MiRoles; lists: MiLists | null }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [fSupplier, setFSupplier] = useState('')
  const [fMonth, setFMonth] = useState('')
  const [month, setMonth] = useState(() => previousMonth(new Date()))
  const [supplierId, setSupplierId] = useState('')
  const [edition, setEdition] = useState<MiEdition>('supplier')
  const [openId, setOpenId] = useState<number | null>(null)
  const query = qs({ supplier_id: fSupplier, month: fMonth })
  const listQ = useQuery({
    queryKey: ['mi-reports', query],
    queryFn: () => apiGet<unknown>(`/market-intel/supplier-reports${query}`),
    retry: (n, e) => !notReady(e) && n < 1,
  })
  const build = useMutation({
    mutationFn: () => apiPost<Partial<MiReport> & { report?: MiReport }>('/market-intel/supplier-reports', { supplier_id: Number(supplierId), month, edition }),
    onSuccess: (res) => {
      toast('Draft built. Read it, edit the narrative, then an approver approves it.', 'success')
      qc.invalidateQueries({ queryKey: ['mi-reports'] })
      const id = res?.report?.id ?? res?.id
      if (id) setOpenId(Number(id))
    },
    onError: (e) => toast(errorText(e, 'Could not build the report.'), 'error'),
  })

  if (listQ.isError && notReady(listQ.error)) return <NotAvailableYet what="Supplier reports" />
  const data = listQ.data as { available?: boolean } | undefined
  if (data && !Array.isArray(data) && data.available === false) return <NotAvailableYet what="Supplier reports" />
  const reports = rowsOf(listQ.data)
  const suppliers = (lists?.suppliers || []).filter((s) => s.active !== false && !s.merged_into)

  return (
    <div className="space-y-5">
      {roles.review && (
        <section className={CARD}>
          <h3 className="flex items-center gap-1.5 font-display text-[15px] font-bold">
            <FileSpreadsheet size={16} className="text-primary" aria-hidden="true" /> Build a monthly report
          </h3>
          <p className="mb-3 mt-0.5 max-w-3xl text-[12.5px] text-muted-foreground">
            One supplier, one month. Every finding cites its report IDs; reports without enough evidence are listed apart and not counted. The supplier edition never shows shops, reps, prices or photos with people.
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label htmlFor="rep-month" className={LABEL}>Month</label>
              <input id="rep-month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} className={cn(INPUT, 'w-44')} />
            </div>
            <div className="min-w-[200px] flex-1">
              <label htmlFor="rep-supplier" className={LABEL}>Supplier</label>
              <select id="rep-supplier" value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={cn(SELECT, 'w-full')}>
                <option value="">{suppliers.length ? 'Pick the supplier' : 'No suppliers listed yet (Lists tab)'}</option>
                {suppliers.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="rep-edition" className={LABEL}>Edition</label>
              <select id="rep-edition" value={edition} onChange={(e) => setEdition(e.target.value as MiEdition)} className={SELECT}>
                <option value="supplier">{EDITION_LABEL.supplier}</option>
                <option value="internal">{EDITION_LABEL.internal}</option>
              </select>
            </div>
            <button type="button" disabled={!supplierId || !/^\d{4}-\d{2}$/.test(month) || build.isPending} onClick={() => build.mutate()} className={BTN}>
              {build.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Build the draft
            </button>
          </div>
        </section>
      )}

      <section>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h3 className="mr-auto font-display text-[15px] font-bold">Reports</h3>
          <select aria-label="Supplier" value={fSupplier} onChange={(e) => setFSupplier(e.target.value)} className={SELECT}>
            <option value="">All suppliers</option>
            {(lists?.suppliers || []).map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
          </select>
          <input type="month" aria-label="Month" value={fMonth} onChange={(e) => setFMonth(e.target.value)} className={cn(INPUT, 'w-44')} />
          {(fSupplier || fMonth) && (
            <button type="button" onClick={() => { setFSupplier(''); setFMonth('') }} className="h-10 rounded-xl px-3 text-[13px] font-semibold text-primary hover:bg-accent">
              Clear
            </button>
          )}
        </div>
        {listQ.isError ? (
          <LoadError error={listQ.error} onRetry={() => listQ.refetch()} isRetrying={listQ.isFetching} />
        ) : listQ.isLoading ? (
          <Loading />
        ) : !reports.length ? (
          <Empty icon={<FileSpreadsheet size={18} aria-hidden="true" />}>
            {fSupplier || fMonth ? 'No report for this supplier or month.' : roles.review ? 'No reports yet. Build the first one above.' : 'No reports yet. The office builds them.'}
          </Empty>
        ) : (
          <ul className="grid gap-2.5 lg:grid-cols-2">
            {reports.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setOpenId(r.id)}
                  className="flex w-full items-center gap-3 rounded-2xl border border-border bg-card p-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent text-accent-foreground">
                    <FileSpreadsheet size={19} aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-semibold">
                      {supplierOf(r, lists)} · {monthText(r.month)}
                    </span>
                    <span className="block truncate text-[11.5px] text-muted-foreground">
                      {EDITION_LABEL[r.edition] || r.edition}
                      {r.approved_at ? ` · approved ${dayText(r.approved_at)}${r.approved_by ? ` by ${who(r.approved_by)}` : ''}` : ''}
                      {r.downloads?.length ? ` · ${r.downloads.length} download${r.downloads.length === 1 ? '' : 's'}` : ''}
                    </span>
                  </span>
                  <Badge tone={REPORT_STATUS_TONE[r.status] || 'grey'}>{STATUS_LABEL[r.status] || r.status}</Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <ReportSheet id={openId} onClose={() => setOpenId(null)} roles={roles} lists={lists} />
    </div>
  )
}

function ReportSheet({ id, onClose, roles, lists }: { id: number | null; onClose: () => void; roles: MiRoles; lists: MiLists | null }) {
  const q = useQuery({
    queryKey: ['mi-report', id],
    queryFn: () => apiGet<MiReportDetail>(`/market-intel/supplier-reports/${id}`),
    enabled: id != null,
  })
  const r = q.data ? q.data.report || (q.data as MiReport) : null
  return (
    <Sheet
      open={id != null}
      onClose={onClose}
      title={r ? `${supplierOf(r, lists)} · ${monthText(r.month)}` : 'Supplier report'}
      subtitle={r ? `${EDITION_LABEL[r.edition] || r.edition} · ${STATUS_LABEL[r.status] || r.status}` : undefined}
    >
      {q.isError ? (
        <div className="p-4">
          <LoadError error={q.error} onRetry={() => q.refetch()} isRetrying={q.isFetching} />
        </div>
      ) : !q.data || !r ? (
        <Loading className="h-48" />
      ) : (
        <ReportBody key={`${r.id}-${r.status}-${r.updated_at || ''}`} d={q.data} r={r} roles={roles} />
      )}
    </Sheet>
  )
}

/** The narrative with its evidence references set apart: [MI-123] a report, [CA-7] an action. */
function Narrative({ text }: { text: string }) {
  const parts = text.split(/(\[(?:MI|CA)-\d+(?:,\s*(?:MI|CA)-\d+)*\])/g)
  return (
    <p className="whitespace-pre-line text-[13.5px] leading-relaxed">
      {parts.map((p, i) =>
        /^\[(?:MI|CA)-/.test(p) ? (
          <span key={i} className="mx-0.5 rounded bg-accent px-1 font-mono text-[11.5px] font-semibold text-accent-foreground">
            {p}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </p>
  )
}

function humanKey(k: string): string {
  const s = k.replace(/_/g, ' ').trim()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Counts as the API sends them: numbers become tiles, objects and lists become small "label: n" lists. */
function CountsView({ counts }: { counts: Record<string, unknown> }) {
  const tiles: [string, number][] = []
  const groups: [string, [string, number][]][] = []
  for (const [k, v] of Object.entries(counts)) {
    if (typeof v === 'number') tiles.push([k, v])
    else if (Array.isArray(v)) {
      const pairs: [string, number][] = []
      for (const item of v) {
        if (!item || typeof item !== 'object') continue
        const o = item as Record<string, unknown>
        const label = o.label ?? o.label_en ?? o.issue ?? o.name ?? o.code ?? o.key
        const n = o.count ?? o.n ?? o.total ?? o.value
        if (label != null && typeof n === 'number') pairs.push([String(label), n])
      }
      if (pairs.length) groups.push([k, pairs])
    } else if (v && typeof v === 'object') {
      const pairs = Object.entries(v as Record<string, unknown>).filter((e): e is [string, number] => typeof e[1] === 'number')
      if (pairs.length) groups.push([k, pairs])
    }
  }
  if (!tiles.length && !groups.length) return null
  return (
    <div className="space-y-3">
      {tiles.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {tiles.map(([k, n]) => (
            <div key={k} className="rounded-xl border border-[#E6DAF0] bg-gradient-to-br from-[#FAF7FC] to-[#F1E9F7] p-2.5">
              <div className="text-[11px] font-semibold text-[#6D4091]">{humanKey(k)}</div>
              <div className="font-display text-[20px] font-bold tabular-nums text-[#1A1428]">{n}</div>
            </div>
          ))}
        </div>
      )}
      {groups.map(([k, pairs]) => (
        <div key={k}>
          <div className={LABEL}>{humanKey(k)}</div>
          <ul className="divide-y divide-border rounded-xl border border-border">
            {pairs.map(([label, n]) => (
              <li key={label} className="flex items-center justify-between gap-3 px-3 py-1.5 text-[13px]">
                <span className="min-w-0 truncate">{humanKey(label)}</span>
                <span className="font-semibold tabular-nums">{n}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function rowRef(row: MiReportRow): string {
  if (row.ref) return row.ref
  const id = row.observation_id ?? row.id
  return id != null ? `MI-${id}` : ''
}

function FindingRows({ title, rows, note }: { title: string; rows: MiReportRow[]; note?: ReactNode }) {
  if (!rows.length) return null
  return (
    <section>
      <div className={LABEL}>{title}</div>
      {note}
      <ul className="space-y-1.5">
        {rows.map((row, i) => {
          const verified = row.verified ?? (row.evidence_level != null ? Number(row.evidence_level) >= 3 : null)
          return (
            <li key={`${rowRef(row)}-${i}`} className="rounded-xl border border-border px-3 py-2 text-[12.5px]">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[11.5px] font-semibold text-muted-foreground">{rowRef(row)}</span>
                {verified != null && <Badge tone={verified ? 'green' : 'amber'}>{row.label || (verified ? 'Verified' : 'Unverified report')}</Badge>}
                {(row.evidence_label || row.evidence_level != null) && <Badge tone="grey">{row.evidence_label || evidenceLabel(row.evidence_level)}</Badge>}
                {(row.issue_label || row.issue) && <Badge tone="accent">{row.issue_label || row.issue}</Badge>}
              </div>
              <div className="mt-0.5 font-semibold">
                {[row.product_name || row.product, row.yq_item_code || row.sku].filter(Boolean).join(' · ') || 'Product not named'}
              </div>
              {row.description && <div className="text-muted-foreground">{row.description}</div>}
              <div className="text-[11.5px] text-muted-foreground">
                {[dayText(row.date || row.observed_at), row.qty_affected ? `${row.qty_affected} affected` : '', row.status || ''].filter(Boolean).join(' · ')}
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function ReportBody({ d, r, roles }: { d: MiReportDetail; r: MiReport; roles: MiRoles }) {
  const toast = useToast()
  const qc = useQueryClient()
  const draft = r.status === 'draft'
  const canEdit = roles.review && draft
  const [narrative, setNarrative] = useState(r.narrative || '')
  const [sure, setSure] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const rows = d.rows || d.included || d.observations || []
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['mi-report', r.id] })
    qc.invalidateQueries({ queryKey: ['mi-reports'] })
  }
  const fail = (fallback: string) => (e: unknown) => toast(errorText(e, fallback), 'error')
  const save = useMutation({
    mutationFn: () => apiPatch(`/market-intel/supplier-reports/${r.id}`, { narrative }),
    onSuccess: () => {
      toast('Narrative saved.', 'success')
      refresh()
    },
    onError: fail('Could not save the narrative.'),
  })
  const approve = useMutation({
    mutationFn: () => apiPost(`/market-intel/supplier-reports/${r.id}/approve`, {}),
    onSuccess: () => {
      toast('Report approved. It can be downloaded now.', 'success')
      refresh()
    },
    onError: fail('Could not approve the report.'),
  })
  async function download() {
    setDownloading(true)
    try {
      await apiGetDownload(`/market-intel/supplier-reports/${r.id}/download`, `supplier-report-${String(r.month).slice(0, 7)}-${r.edition}.xlsx`)
      refresh() // the download is logged with its sha256
    } catch (e) {
      fail('Could not download the workbook.')(e)
    } finally {
      setDownloading(false)
    }
  }
  const narrativeChanged = narrative !== (r.narrative || '')

  return (
    <div className="space-y-5 px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={REPORT_STATUS_TONE[r.status] || 'grey'}>{STATUS_LABEL[r.status] || r.status}</Badge>
        <Badge tone="grey">{EDITION_LABEL[r.edition] || r.edition}</Badge>
        {r.approved_at && (
          <span className="text-[12px] text-muted-foreground">
            Approved {whenText(r.approved_at)}
            {r.approved_by ? ` by ${who(r.approved_by)}` : ''}
          </span>
        )}
      </div>
      {r.status === 'superseded' && (
        <p className="rounded-xl bg-muted px-3 py-2 text-[12.5px] text-muted-foreground">A newer approved report for the same supplier, month and edition replaced this one. Use that one.</p>
      )}

      {r.counts && Object.keys(r.counts).length > 0 && <CountsView counts={r.counts} />}

      <section>
        <div className={LABEL}>Narrative</div>
        {canEdit ? (
          <>
            <textarea rows={10} value={narrative} onChange={(e) => setNarrative(e.target.value)} className={cn(TEXTAREA, 'font-[inherit]')} aria-label="Narrative" />
            <p className="mt-1 text-[11.5px] text-muted-foreground">Keep the [MI-…] and [CA-…] references: every finding must cite the reports it rests on.</p>
            <button type="button" disabled={!narrativeChanged || save.isPending} onClick={() => save.mutate()} className={cn(BTN_OUTLINE, 'mt-2')}>
              {save.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save the narrative
            </button>
          </>
        ) : r.narrative ? (
          <div className="rounded-xl border border-border bg-card p-3">
            <Narrative text={r.narrative} />
          </div>
        ) : (
          <p className="text-[12.5px] text-muted-foreground">No narrative.</p>
        )}
      </section>

      <FindingRows title={`Findings in the report (${rows.length})`} rows={rows} />
      <FindingRows
        title="Insufficient evidence (not counted)"
        rows={d.insufficient || []}
        note={<p className="mb-1.5 text-[11.5px] text-muted-foreground">No photo, a single report, or level 1: mentioned, not counted.</p>}
      />

      <section className="space-y-2 border-t border-border pt-4">
        {draft && roles.approve && (
          sure ? (
            <div className="space-y-2 rounded-xl border border-[#cfe9da] bg-[#f3fbf6] p-3">
              <p className="text-[12.5px] text-[#0f5a36]">Approve this report? An earlier approved report of the same supplier, month and edition becomes superseded.</p>
              <div className="flex gap-2">
                <button type="button" disabled={approve.isPending || (canEdit && narrativeChanged)} onClick={() => approve.mutate()} className={cn(BTN, 'bg-[#137a48] hover:bg-[#0f5a36]')}>
                  {approve.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Stamp size={15} aria-hidden="true" />} Yes, approve
                </button>
                <button type="button" onClick={() => setSure(false)} className={BTN_OUTLINE}>Not yet</button>
              </div>
              {canEdit && narrativeChanged && <p className="text-[11.5px] text-[#96600d]">Save the narrative first.</p>}
            </div>
          ) : (
            <button type="button" onClick={() => setSure(true)} className={cn(BTN, 'w-full bg-[#137a48] hover:bg-[#0f5a36]')}>
              <CheckCircle2 size={16} aria-hidden="true" /> Approve
            </button>
          )
        )}
        {r.status === 'approved' && (roles.approve || roles.review) && (
          <button type="button" disabled={downloading} onClick={() => void download()} className={cn(BTN, 'w-full')}>
            {downloading ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Download size={16} aria-hidden="true" />} Download the workbook (.xlsx)
          </button>
        )}
        {draft && !roles.approve && <p className="text-[12px] text-muted-foreground">An approver approves the draft before it can be downloaded.</p>}
      </section>

      {Boolean(r.downloads?.length || r.file_sha256) && (
        <section>
          <div className={LABEL}>Downloads</div>
          {r.file_sha256 && <p className="mb-1 font-mono text-[11.5px] text-muted-foreground">File sha256 {r.file_sha256.slice(0, 16)}…</p>}
          <ul className="space-y-1 text-[12.5px]">
            {(r.downloads || []).map((x, i) => (
              <li key={`${x.at}-${i}`} className="flex flex-wrap gap-x-2">
                <span>{whenText(x.at)}</span>
                <span className="text-muted-foreground">{who(x.by)}</span>
                {x.sha256 && <span className="font-mono text-[11.5px] text-muted-foreground">{x.sha256.slice(0, 12)}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
