import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Download, FileSpreadsheet, Loader2, Stamp, TriangleAlert } from 'lucide-react'
import { apiGet, apiGetDownload, apiPatch, apiPost } from '@/lib/api'
import { errorText } from '@/lib/errorText'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { LoadError } from '@/components/LoadError'
import { Badge } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import {
  approvableNow,
  monthText,
  nameOf,
  notReady,
  previousMonth,
  REPORT_SHEET_LABEL,
  reportIssue,
  reportRows,
  type MiEdition,
  type MiLists,
  type MiReport,
  type MiReportCaps,
  type MiReportDetail,
  type MiReportRow,
  type MiReportRowState,
} from '@/lib/marketIntel'
import { Empty, Loading, NotAvailableYet } from './ui'
import { BTN, BTN_OUTLINE, CARD, dayText, INPUT, LABEL, qs, REPORT_STATUS_TONE, SELECT, TEXTAREA, whenText, who, type MiRoles } from './util'

/**
 * Supplier reports (release r8m, plan F9, Phase 2; app/supplier_reports.py): month + supplier -> a DRAFT
 * built by the API from that supplier's reports and cases (counts by issue, new vs recurring, open
 * actions, and a narrative in which every finding cites its report IDs [MI-…] and every action [CA-…];
 * weak evidence is listed apart and not counted) -> rows put in or left out, the narrative edited ->
 * approved -> the .xlsx downloaded. Building a month again REBUILDS its draft (hand edits replaced), so
 * the page asks first. Who may do what comes from the routes' own capabilities (reviewers build and
 * edit; approvers approve; both download); the roles are the fallback. Nothing is e-mailed: the approver
 * sends the workbook himself, and every download is logged with the file's sha256.
 */

const EDITION_LABEL: Record<MiEdition, string> = { supplier: 'Supplier edition', internal: 'Internal edition' }
const STATUS_LABEL: Record<MiReport['status'], string> = { draft: 'Draft', approved: 'Approved', superseded: 'Superseded' }

interface Can {
  build: boolean
  edit: boolean
  approve: boolean
  download: boolean
}

/**
 * The report routes' capabilities when the payload has them (always, on this API); the page's roles
 * otherwise. Approving is management's or an admin's only (supplier_reports.APPROVER_ROLES).
 */
function reportCan(caps: MiReportCaps | null | undefined, roles: MiRoles): Can {
  return {
    build: caps?.can_build ?? roles.review,
    edit: caps?.can_edit ?? roles.review,
    approve: caps?.can_approve ?? (roles.admin || (roles.readOnly && roles.approve)),
    download: caps?.can_download ?? (roles.approve || roles.review),
  }
}

function rowsOf(d: unknown): MiReport[] {
  if (Array.isArray(d)) return d as MiReport[]
  const o = (d || {}) as { rows?: MiReport[]; reports?: MiReport[]; items?: MiReport[] }
  return o.rows || o.reports || o.items || []
}

function supplierOf(r: Partial<MiReport>, lists: MiLists | null): string {
  return nameOf(r.supplier) || r.supplier_name || lists?.suppliers.find((s) => s.id === r.supplier_id)?.name || (r.supplier_id ? `Supplier ${r.supplier_id}` : 'Supplier')
}

function monthOf(r: Pick<MiReport, 'month' | 'month_label'>): string {
  return r.month_label || monthText(r.month)
}

/** The list sends the number of downloads; a report's own payload the downloads themselves. */
function downloadCount(r: Pick<MiReport, 'downloads'>): number {
  return typeof r.downloads === 'number' ? r.downloads : (r.downloads || []).length
}

export function ReportsTab({ roles, lists }: { roles: MiRoles; lists: MiLists | null }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [fSupplier, setFSupplier] = useState('')
  const [fMonth, setFMonth] = useState('')
  const [month, setMonth] = useState(() => previousMonth(new Date()))
  const [supplierId, setSupplierId] = useState('')
  const [edition, setEdition] = useState<MiEdition>('supplier')
  // a draft of the same supplier, month and edition exists: building again replaces it (asked first)
  const [existing, setExisting] = useState<MiReport | null>(null)
  const [checking, setChecking] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)
  // the draft just rebuilt over an existing one: its sheet says so too (a toast sits under the sheet)
  const [rebuiltId, setRebuiltId] = useState<number | null>(null)
  // what an approval reported (a photo that could not be read is frozen as "photo not available")
  const [approveNotes, setApproveNotes] = useState<Record<number, string[]>>({})
  const query = qs({ supplier_id: fSupplier, month: fMonth })
  const listQ = useQuery({
    queryKey: ['mi-reports', query],
    queryFn: () => apiGet<unknown>(`/market-intel/supplier-reports${query}`),
    retry: (n, e) => !notReady(e) && n < 1,
  })
  const can = reportCan((listQ.data as { capabilities?: MiReportCaps } | undefined)?.capabilities, roles)
  const build = useMutation({
    mutationFn: () => apiPost<MiReportDetail>('/market-intel/supplier-reports', { supplier_id: Number(supplierId), month, edition }),
    onSuccess: (res) => {
      setExisting(null)
      // build_draft answers rebuilt: true when it replaced an existing draft's narrative and row choices
      toast(
        res?.rebuilt ? 'The existing draft was rebuilt from the data: its hand edits were replaced.' : 'Draft built. Read it, then an approver approves it.',
        res?.rebuilt ? 'info' : 'success',
      )
      qc.invalidateQueries({ queryKey: ['mi-reports'] })
      const id = res?.report?.id
      if (id) {
        qc.setQueryData(['mi-report', Number(id)], res)
        setRebuiltId(res?.rebuilt ? Number(id) : null)
        setOpenId(Number(id))
      }
    },
    onError: (e) => toast(errorText(e, 'Could not build the report.'), 'error'),
  })

  function pickBuild(set: () => void) {
    set()
    setExisting(null)
  }

  async function startBuild() {
    if (!existing) {
      setChecking(true)
      let found: MiReport | null
      try {
        const got = await apiGet<unknown>(`/market-intel/supplier-reports${qs({ supplier_id: supplierId, month, edition, status: 'draft' })}`)
        found = rowsOf(got)[0] || null
      } catch {
        found = null // the build itself still says rebuilt: true
      } finally {
        setChecking(false)
      }
      if (found) {
        setExisting(found)
        return
      }
    }
    build.mutate()
  }

  if (listQ.isError && notReady(listQ.error)) return <NotAvailableYet what="Supplier reports" />
  const data = listQ.data as { available?: boolean } | undefined
  if (data && !Array.isArray(data) && data.available === false) return <NotAvailableYet what="Supplier reports" />
  const reports = rowsOf(listQ.data)
  const suppliers = (lists?.suppliers || []).filter((s) => s.active !== false && !s.merged_into)

  return (
    <div className="space-y-5">
      {can.build && (
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
              <input id="rep-month" type="month" value={month} onChange={(e) => pickBuild(() => setMonth(e.target.value))} className={cn(INPUT, 'w-44')} />
            </div>
            <div className="min-w-[200px] flex-1">
              <label htmlFor="rep-supplier" className={LABEL}>Supplier</label>
              <select id="rep-supplier" value={supplierId} onChange={(e) => pickBuild(() => setSupplierId(e.target.value))} className={cn(SELECT, 'w-full')}>
                <option value="">{suppliers.length ? 'Pick the supplier' : 'No suppliers listed yet (Lists tab)'}</option>
                {suppliers.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="rep-edition" className={LABEL}>Edition</label>
              <select id="rep-edition" value={edition} onChange={(e) => pickBuild(() => setEdition(e.target.value as MiEdition))} className={SELECT}>
                <option value="supplier">{EDITION_LABEL.supplier}</option>
                <option value="internal">{EDITION_LABEL.internal}</option>
              </select>
            </div>
            {!existing && (
              <button type="button" disabled={!supplierId || !/^\d{4}-\d{2}$/.test(month) || build.isPending || checking} onClick={() => void startBuild()} className={BTN}>
                {(build.isPending || checking) && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Build the draft
              </button>
            )}
          </div>
          {existing && (
            <div role="alert" className="mt-3 space-y-2 rounded-xl border border-warning/25 bg-warning/10 p-3 text-[12.5px] text-warning">
              <p>
                <span className="font-bold">A draft for {supplierOf(existing, lists)}, {monthOf(existing)} ({EDITION_LABEL[existing.edition] || existing.edition}) already exists.</span>{' '}
                Building again replaces its narrative and its row choices from the data: hand edits are lost.
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" disabled={build.isPending} onClick={() => void startBuild()} className={cn(BTN, 'bg-warning text-warning-foreground hover:bg-warning/90')}>
                  {build.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Build again
                </button>
                <button type="button" onClick={() => { setOpenId(existing.id); setExisting(null) }} className={BTN_OUTLINE}>
                  Open the draft instead
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      <section>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h3 className="mr-auto font-display text-[15px] font-bold">Reports</h3>
          <select aria-label="Supplier" value={fSupplier} onChange={(e) => setFSupplier(e.target.value)} className={SELECT}>
            <option value="">All suppliers</option>
            {(lists?.suppliers || []).map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
          </select>
          {/* named, as the Cases dates are: an empty month box alone reads "--------- ----" */}
          <label className="flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
            Month
            <input type="month" aria-label="Month" value={fMonth} onChange={(e) => setFMonth(e.target.value)} className={cn(INPUT, 'w-44')} />
          </label>
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
            {fSupplier || fMonth ? 'No report for this supplier or month.' : can.build ? 'No reports yet. Build the first one above.' : 'No reports yet. The office builds them.'}
          </Empty>
        ) : (
          <ul className="grid grid-cols-1 gap-2.5 lg:grid-cols-2">
            {reports.map((r) => {
              const n = downloadCount(r)
              return (
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
                        {supplierOf(r, lists)} · {monthOf(r)}
                      </span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {EDITION_LABEL[r.edition] || r.edition}
                        {r.status === 'draft' && r.approvable_from && !approvableNow(r) ? ` · can be approved from ${dayText(r.approvable_from)}` : ''}
                        {r.approved_at ? ` · approved ${dayText(r.approved_at)}${r.approved_by ? ` by ${who(r.approved_by)}` : ''}` : ''}
                        {n ? ` · ${n} download${n === 1 ? '' : 's'}` : ''}
                      </span>
                    </span>
                    <Badge tone={REPORT_STATUS_TONE[r.status] || 'grey'}>{STATUS_LABEL[r.status] || r.status}</Badge>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <ReportSheet
        id={openId}
        rebuilt={openId != null && openId === rebuiltId}
        notes={openId != null ? approveNotes[openId] || [] : []}
        onApproved={(id, warnings) => setApproveNotes((cur) => ({ ...cur, [id]: warnings }))}
        onClose={() => {
          setOpenId(null)
          setRebuiltId(null)
        }}
        roles={roles}
        lists={lists}
      />
    </div>
  )
}

function ReportSheet({
  id,
  rebuilt,
  notes,
  onApproved,
  onClose,
  roles,
  lists,
}: {
  id: number | null
  rebuilt: boolean
  notes: string[]
  onApproved: (id: number, warnings: string[]) => void
  onClose: () => void
  roles: MiRoles
  lists: MiLists | null
}) {
  const q = useQuery({
    queryKey: ['mi-report', id],
    queryFn: () => apiGet<MiReportDetail>(`/market-intel/supplier-reports/${id}`),
    enabled: id != null,
  })
  const r = q.data ? q.data.report || (q.data as unknown as MiReport) : null
  const can = reportCan(q.data?.capabilities, roles)
  return (
    <Sheet
      open={id != null}
      onClose={onClose}
      title={r ? `${supplierOf(r, lists)} · ${monthOf(r)}` : 'Supplier report'}
      subtitle={r ? `${EDITION_LABEL[r.edition] || r.edition} · ${STATUS_LABEL[r.status] || r.status}` : undefined}
    >
      {q.isError ? (
        <div className="p-4">
          <LoadError error={q.error} onRetry={() => q.refetch()} isRetrying={q.isFetching} />
        </div>
      ) : !q.data || !r ? (
        <Loading className="h-48" />
      ) : (
        <ReportBody
          key={`${r.id}-${r.status}-${r.updated_at || ''}`}
          d={q.data}
          r={r}
          can={can}
          rebuilt={rebuilt}
          notes={notes}
          onApproved={(warnings) => onApproved(r.id, warnings)}
        />
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

/** The counts' numbers worth a tile, in this order (supplier_reports.assemble counts). */
const TILES: [string, string][] = [
  ['reports', 'Reports counted'],
  ['product_issues', 'Product issues'],
  ['packaging', 'Packaging'],
  ['shops', 'Shops'],
  ['verified', 'Verified'],
  ['unverified', 'Unverified'],
  ['safety', 'Safety'],
  ['photos', 'Photos'],
  ['insufficient', 'Insufficient evidence'],
  ['excluded', 'Left out'],
  ['open_actions', 'Open actions'],
  ['competitor', 'Competitor sightings'],
  ['demand', 'Demand sightings'],
]
/** Never a tile: the counts' version, the month and edition, ids, the rules, the narrative's source. */
const NOT_TILES = new Set(['version', 'month', 'edition', 'period', 'supplier', 'rules', 'new', 'recurring', 'by_issue',
  'insufficient_observation_ids', 'action_ids', 'narrative_source', 'built_at'])

/** A rule the API states ("... in >= 2 cases or from >= 3 shops ...") as a sentence: "... in 2 or more cases ..." */
function ruleText(t: string): string {
  const s = t.replace(/>=\s*(\d+)/g, '$1 or more').replace(/<=\s*(\d+)/g, '$1 or fewer').trim()
  return `${s.charAt(0).toUpperCase()}${s.slice(1)}${/[.!?]$/.test(s) ? '' : '.'}`
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function CountsView({ counts, edition }: { counts: Record<string, unknown>; edition: MiEdition }) {
  const tiles: [string, number][] = []
  for (const [k, label] of TILES) {
    const n = num(counts[k])
    if (n == null) continue
    if ((k === 'competitor' || k === 'demand') && edition !== 'internal') continue
    tiles.push([label, n])
  }
  // a number this page does not know yet still shows (never the version)
  const known = new Set(TILES.map(([k]) => k))
  for (const [k, v] of Object.entries(counts)) {
    const n = num(v)
    if (n != null && !known.has(k) && !NOT_TILES.has(k)) tiles.push([humanKey(k), n])
  }
  const part = (v: unknown) => (v && typeof v === 'object' ? (v as { problems?: unknown; reports?: unknown }) : {})
  const fresh = part(counts.new)
  const again = part(counts.recurring)
  const period = part(counts.period) as { from?: unknown; to?: unknown }
  const byIssue = Array.isArray(counts.by_issue) ? (counts.by_issue as Record<string, unknown>[]) : []
  const rules = counts.rules && typeof counts.rules === 'object' ? Object.values(counts.rules as Record<string, unknown>).filter((x): x is string => typeof x === 'string') : []
  return (
    <div className="space-y-3">
      {typeof period.from === 'string' && typeof period.to === 'string' && (
        <p className="text-[12px] text-muted-foreground">
          {dayText(period.from)} to {dayText(period.to)}, Bahrain time
        </p>
      )}
      {tiles.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {tiles.map(([label, n]) => (
            <div key={label} className="rounded-xl border border-primary/20 bg-gradient-to-br from-accent/50 to-accent p-2.5">
              <div className="text-[11px] font-semibold text-primary">{label}</div>
              <div className="font-display text-[20px] font-bold tabular-nums text-foreground">{n}</div>
            </div>
          ))}
        </div>
      )}
      {(num(fresh.problems) != null || num(again.problems) != null) && (
        <div className="grid grid-cols-2 gap-2 text-[12.5px]">
          <div className="rounded-xl border border-border px-3 py-2">
            <div className={LABEL}>New</div>
            {num(fresh.problems) ?? 0} problem{num(fresh.problems) === 1 ? '' : 's'} · {num(fresh.reports) ?? 0} report{num(fresh.reports) === 1 ? '' : 's'}
          </div>
          <div className="rounded-xl border border-border px-3 py-2">
            <div className={LABEL}>Recurring</div>
            {num(again.problems) ?? 0} problem{num(again.problems) === 1 ? '' : 's'} · {num(again.reports) ?? 0} report{num(again.reports) === 1 ? '' : 's'}
          </div>
        </div>
      )}
      {byIssue.length > 0 && (
        <div>
          <div className={LABEL}>By issue</div>
          <ul className="divide-y divide-border rounded-xl border border-border">
            {byIssue.map((b, i) => {
              const reports = num(b.reports) ?? num(b.count) ?? num(b.n) ?? 0
              const shops = num(b.shops)
              const verified = num(b.verified)
              return (
                <li key={`${String(b.label ?? b.code ?? i)}-${String(b.kind ?? '')}`} className="flex items-center justify-between gap-3 px-3 py-1.5 text-[13px]">
                  <span className="min-w-0 truncate">
                    {String(b.label ?? b.code ?? 'Unspecified')}
                    {b.recurring ? <span className="ml-1.5 text-[11px] font-semibold text-primary">recurring</span> : null}
                  </span>
                  <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground">
                    <span className="font-semibold text-foreground">{reports}</span> report{reports === 1 ? '' : 's'}
                    {shops != null ? ` · ${shops} shop${shops === 1 ? '' : 's'}` : ''}
                    {verified ? ` · ${verified} verified` : ''}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
      {rules.length > 0 && (
        <ul className="space-y-0.5 text-[11.5px] text-muted-foreground">
          {rules.map((t) => (
            <li key={t}>{ruleText(t)}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

const STATE_GROUPS: { state: MiReportRowState; title: string; note: string }[] = [
  { state: 'included', title: 'In the report', note: '' },
  { state: 'insufficient', title: 'Insufficient evidence: listed, not counted', note: 'No photo, evidence level 1 and a single report. Tick one to count it anyway.' },
  { state: 'excluded', title: 'Left out', note: 'Not in this report: taken out by hand, or reported after the draft was built. Tick one to put it in.' },
  { state: 'withheld', title: 'Withheld: not sent to the supplier', note: 'Never shown, counted or cited in the workbook. Send one again to list it.' },
]

function FindingRow({
  sheet,
  row,
  editable,
  inReport,
  onToggle,
  onHold,
  onHoldPhoto,
  busy = false,
}: {
  sheet: string
  row: MiReportRow
  editable: boolean
  inReport: boolean
  onToggle: () => void
  /** withhold the row from the supplier (true) or send it again (false): a draft, reviewers */
  onHold?: (hold: boolean) => void
  /** withhold one of its photos from the supplier */
  onHoldPhoto?: (photoId: number) => void
  busy?: boolean
}) {
  const issue = reportIssue(row)
  const held = row.state === 'withheld'
  const changed = editable && !held && inReport !== (row.state === 'included')
  const what = row.product || row.product_seen || row.asked_for
  const meta = [
    dayText(row.date),
    row.qty ? (row.kind === 'demand' ? `${row.qty} wanted` : `${row.qty} affected`) : '',
    row.batch_ref ? `batch ${row.batch_ref}` : '',
    row.packaging_version ? `packaging ${row.packaging_version}` : '',
    row.basis || '',
    row.brand || '',
    row.competitor ? `competitor ${row.competitor}` : '',
    row.price_seen ? `BHD ${row.price_seen}` : '',
    row.source || '',
    row.demand_level ? `demand ${row.demand_level}` : '',
    row.area || '',
    row.status || '',
  ].filter(Boolean)
  const photos = row.photos || []
  // a frozen report lists every photo it approved; one no longer fit to show has no thumbnail
  const unshown = photos.filter((p) => !p.thumb_url).length
  return (
    <li className={cn('rounded-xl border px-3 py-2 text-[12.5px]', changed ? 'border-primary/30 bg-accent/50' : 'border-border', held && 'border-dashed bg-muted/40')}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11.5px] font-semibold text-muted-foreground">{row.ref || `MI-${row.id}`}</span>
        <span className="text-[11px] text-muted-foreground">{REPORT_SHEET_LABEL[sheet] || humanKey(sheet)}</span>
        {held && <Badge tone="grey">Withheld</Badge>}
        {row.finding && <Badge tone={row.finding === 'Verified' ? 'green' : 'amber'}>{row.finding}</Badge>}
        {row.evidence_label && <Badge tone="grey">{row.evidence_label}</Badge>}
        {issue && <Badge tone="accent">{issue}</Badge>}
        {row.safety && <Badge tone="rose">Safety</Badge>}
        {row.recurring && <Badge tone="ink">Recurring</Badge>}
        {editable && (
          <span className="ml-auto flex flex-wrap items-center gap-1">
            {!held && (
              <label className="flex min-h-9 cursor-pointer items-center gap-1.5 px-1 text-[12px] font-semibold">
                <input type="checkbox" checked={inReport} onChange={onToggle} className="h-4 w-4 accent-primary" />
                In the report{changed ? (inReport ? ' (to add)' : ' (to leave out)') : ''}
              </label>
            )}
            {onHold && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onHold(!held)}
                title={held ? 'List it in the report again' : 'Never send this report to the supplier: not shown, counted or cited'}
                className="inline-flex h-9 items-center rounded-lg px-2 text-[12px] font-semibold text-primary hover:bg-accent disabled:opacity-45"
              >
                {held ? 'Send again' : 'Withhold'}
              </button>
            )}
          </span>
        )}
      </div>
      <div className="mt-0.5 font-semibold">{[what, row.sku].filter(Boolean).join(' · ') || 'Product not named'}</div>
      {(row.description || row.note) && <div className="text-muted-foreground">{row.description || row.note}</div>}
      {meta.length > 0 && <div className="text-[11.5px] text-muted-foreground">{meta.join(' · ')}</div>}
      {(photos.length > 0 || Boolean(row.photos_withheld)) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {photos.map((p, i) =>
            p.thumb_url ? (
              <span key={p.ref || p.id || i} className="relative inline-block">
                <img src={p.thumb_url} alt={p.role_words || 'Evidence photo'} loading="lazy" className="h-14 w-14 rounded-lg border border-border object-cover" />
                {onHoldPhoto && !held && p.id != null && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onHoldPhoto(Number(p.id))}
                    aria-label={`Withhold ${p.ref || `photo ${p.id}`} from the supplier`}
                    title="Withhold this photo from the supplier"
                    className="absolute inset-x-0 bottom-0 flex min-h-[44px] items-end justify-center rounded-b-lg bg-gradient-to-t from-black/70 to-black/0 pb-1 text-[10px] font-semibold text-white hover:from-black/80 disabled:opacity-60"
                  >
                    Withhold
                  </button>
                )}
              </span>
            ) : null,
          )}
          {unshown > 0 && (
            <span className="text-[11px] text-muted-foreground">
              {unshown} photo{unshown === 1 ? '' : 's'} not shown here
            </span>
          )}
          {row.photos_withheld ? (
            <span className="text-[11px] text-muted-foreground">
              {row.photos_withheld} photo{row.photos_withheld === 1 ? '' : 's'} not sent (people, or withheld)
            </span>
          ) : null}
        </div>
      )}
    </li>
  )
}

function ReportBody({
  d,
  r,
  can,
  rebuilt = false,
  notes = [],
  onApproved,
}: {
  d: MiReportDetail
  r: MiReport
  can: Can
  rebuilt?: boolean
  /** what the approval of this report reported (kept by the tab: the body is rebuilt on approval) */
  notes?: string[]
  onApproved?: (warnings: string[]) => void
}) {
  const toast = useToast()
  const qc = useQueryClient()
  const draft = r.status === 'draft'
  // an approved (or superseded) report is read from the manifest frozen at approval: never edited
  const frozen = Boolean(d.frozen || r.frozen)
  const editable = can.edit && draft && !frozen
  const opens = approvableNow(r)
  const all = reportRows(d)
  const heldRows = new Set<number>(r.withheld_observation_ids || all.filter((x) => x.row.state === 'withheld').map((x) => x.row.id))
  const heldPhotos = [...new Set<number>(r.withheld_photo_ids || [])].sort((a, b) => a - b)
  const [narrative, setNarrative] = useState(r.narrative || '')
  // the rows in the report: what the preview says now, changed by the ticks until saved
  const [picked, setPicked] = useState<Set<number>>(() => new Set(all.filter((x) => x.row.state === 'included').map((x) => x.row.id)))
  const [sure, setSure] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const was = new Set(all.filter((x) => x.row.state === 'included').map((x) => x.row.id))
  const changes = all.filter((x) => picked.has(x.row.id) !== was.has(x.row.id)).length
  const narrativeChanged = narrative !== (r.narrative || '')

  const refreshed = (res: MiReportDetail | undefined) => {
    if (res && res.report) qc.setQueryData(['mi-report', r.id], res)
    else qc.invalidateQueries({ queryKey: ['mi-report', r.id] })
    qc.invalidateQueries({ queryKey: ['mi-reports'] })
  }
  const fail = (fallback: string) => (e: unknown) => toast(errorText(e, fallback), 'error')
  const save = useMutation({
    mutationFn: () => apiPatch<MiReportDetail>(`/market-intel/supplier-reports/${r.id}`, { narrative }),
    onSuccess: (res) => {
      toast('Narrative saved.', 'success')
      refreshed(res)
    },
    onError: fail('Could not save the narrative.'),
  })
  const select = useMutation({
    mutationFn: () => apiPatch<MiReportDetail>(`/market-intel/supplier-reports/${r.id}`, { included_observation_ids: [...picked].sort((a, b) => a - b) }),
    onSuccess: (res) => {
      toast(r.narrative_source === 'edited' ? 'Rows saved. The narrative was edited by hand, so it was kept: check it still matches.' : 'Rows saved; the narrative follows them.', 'success')
      refreshed(res)
    },
    onError: fail('Could not save the rows.'),
  })
  // withhold / send again: a row or a photo never reaches the supplier. Unsaved row ticks go with it
  // (the answer re-reads the draft), and the narrative must be saved first for the same reason.
  const hold = useMutation({
    mutationFn: (body: { withheld_observation_ids?: number[]; withheld_photo_ids?: number[] }) =>
      apiPatch<MiReportDetail>(`/market-intel/supplier-reports/${r.id}`, {
        ...body,
        ...(changes > 0 ? { included_observation_ids: [...picked].sort((a, b) => a - b) } : {}),
      }),
    onSuccess: (res) => {
      toast('Saved: what the supplier receives has changed.', 'success')
      refreshed(res)
    },
    onError: fail('Could not change what the supplier receives.'),
  })
  function holdRow(id: number, on: boolean) {
    const next = new Set(heldRows)
    if (on) next.add(id)
    else next.delete(id)
    hold.mutate({ withheld_observation_ids: [...next].sort((a, b) => a - b) })
  }
  function holdPhoto(id: number, on: boolean) {
    const next = new Set(heldPhotos)
    if (on) next.add(id)
    else next.delete(id)
    hold.mutate({ withheld_photo_ids: [...next].sort((a, b) => a - b) })
  }
  const approve = useMutation({
    mutationFn: () => apiPost<{ superseded?: number[]; warnings?: string[] }>(`/market-intel/supplier-reports/${r.id}/approve`, {}),
    onSuccess: (res) => {
      const n = res?.superseded?.length || 0
      toast(`Report approved. It can be downloaded now.${n ? ` ${n} earlier approved report${n === 1 ? ' is' : 's are'} superseded.` : ''}`, 'success')
      // e.g. a photo that could not be read at approval, frozen as "photo not available"
      onApproved?.(Array.isArray(res?.warnings) ? res.warnings : [])
      refreshed(undefined)
    },
    onError: fail('Could not approve the report.'),
  })
  async function download() {
    setDownloading(true)
    try {
      await apiGetDownload(`/market-intel/supplier-reports/${r.id}/download`, `supplier-report-${String(r.month).slice(0, 7)}-${r.edition}.xlsx`)
      refreshed(undefined) // the download is logged with its sha256
    } catch (e) {
      fail('Could not download the workbook.')(e)
    } finally {
      setDownloading(false)
    }
  }
  function toggle(id: number) {
    setPicked((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const counts = (draft ? d.live_counts : r.counts) || r.counts || d.live_counts || null
  const downloads = Array.isArray(r.downloads) ? r.downloads : []
  const warnings = d.warnings || []
  const actions = d.actions || []

  return (
    <div className="space-y-5 px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={REPORT_STATUS_TONE[r.status] || 'grey'}>{STATUS_LABEL[r.status] || r.status}</Badge>
        <Badge tone="grey">{EDITION_LABEL[r.edition] || r.edition}</Badge>
        {frozen && <Badge tone="ink">Frozen at approval</Badge>}
        {r.narrative_source === 'edited' && <Badge tone="accent">Narrative edited by hand</Badge>}
        {r.approved_at && (
          <span className="text-[12px] text-muted-foreground">
            Approved {whenText(r.approved_at)}
            {r.approved_by ? ` by ${who(r.approved_by)}` : ''}
          </span>
        )}
      </div>
      {frozen && (
        <p className="rounded-xl bg-muted px-3 py-2 text-[12.5px] text-muted-foreground">
          Frozen at approval: the rows, photos and narrative exactly as the workbook prints them. Nothing here changes any more.
        </p>
      )}
      {r.status === 'superseded' && (
        <p className="rounded-xl bg-muted px-3 py-2 text-[12.5px] text-muted-foreground">A newer approved report for the same supplier, month and edition replaced this one. Use that one.</p>
      )}
      {notes.length > 0 && (
        <div role="alert" className="space-y-1 rounded-xl border border-warning/25 bg-warning/10 px-3 py-2.5 text-[12.5px] text-warning">
          <p className="font-semibold">At approval:</p>
          {notes.map((w) => (
            <p key={w} className="flex gap-2">
              <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" /> {w}
            </p>
          ))}
        </div>
      )}
      {rebuilt && draft && (
        <p role="status" className="rounded-xl bg-accent px-3 py-2 text-[12.5px] font-medium text-accent-foreground">
          Just rebuilt from the data: the narrative and the rows are the template's again, earlier hand edits were replaced.
        </p>
      )}
      {warnings.length > 0 && (
        <div role="alert" className="space-y-1 rounded-xl border border-warning/25 bg-warning/10 px-3 py-2.5 text-[12.5px] text-warning">
          {warnings.map((w) => (
            <p key={w} className="flex gap-2">
              <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" /> {w}
            </p>
          ))}
        </div>
      )}

      {counts && Object.keys(counts).length > 0 && <CountsView counts={counts} edition={r.edition} />}

      <section>
        <div className={LABEL}>Narrative</div>
        {editable ? (
          <>
            <textarea rows={10} value={narrative} onChange={(e) => setNarrative(e.target.value)} maxLength={20000} className={TEXTAREA} aria-label="Narrative" />
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

      {STATE_GROUPS.map((g) => {
        const rows = all.filter((x) => (x.row.state || 'included') === g.state)
        if (!rows.length) return null
        return (
          <section key={g.state}>
            <div className={LABEL}>
              {g.title} ({rows.length})
            </div>
            {g.note && editable && <p className="mb-1.5 text-[11.5px] text-muted-foreground">{g.note}</p>}
            <ul className="space-y-1.5">
              {rows.map((x) => (
                <FindingRow
                  key={`${x.sheet}-${x.row.id}`}
                  sheet={x.sheet}
                  row={x.row}
                  editable={editable}
                  inReport={picked.has(x.row.id)}
                  onToggle={() => toggle(x.row.id)}
                  onHold={editable && !narrativeChanged ? (on) => holdRow(x.row.id, on) : undefined}
                  onHoldPhoto={editable && !narrativeChanged ? (pid) => holdPhoto(pid, true) : undefined}
                  busy={hold.isPending}
                />
              ))}
            </ul>
          </section>
        )
      })}
      {!all.length && <p className="text-[12.5px] text-muted-foreground">No reports of this supplier in the month.</p>}
      {editable && narrativeChanged && <p className="text-[11.5px] text-warning">Save the narrative before withholding rows or photos.</p>}

      {editable && heldPhotos.length > 0 && (
        <section>
          <div className={LABEL}>Photos withheld from the supplier ({heldPhotos.length})</div>
          <ul className="flex flex-wrap gap-1.5">
            {heldPhotos.map((pid) => (
              <li key={pid} className="inline-flex items-center gap-1 rounded-full border border-border bg-card py-0.5 pl-2.5 pr-1 text-[12px]">
                <span className="font-mono">PH-{pid}</span>
                <button
                  type="button"
                  disabled={hold.isPending || narrativeChanged}
                  onClick={() => holdPhoto(pid, false)}
                  className="h-8 rounded-full px-2 text-[11.5px] font-semibold text-primary hover:bg-accent disabled:opacity-45"
                >
                  Send again
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {editable && changes > 0 && (
        <div className="sticky bottom-0 -mx-4 flex flex-wrap items-center gap-2 border-t border-border bg-card px-4 py-2.5 sm:-mx-5 sm:px-5">
          <span className="text-[12.5px] text-muted-foreground">
            {changes} row{changes === 1 ? '' : 's'} changed{narrativeChanged ? ': save the narrative first' : ''}
          </span>
          <button type="button" onClick={() => setPicked(new Set(was))} className={cn(BTN_OUTLINE, 'ml-auto')}>
            Undo
          </button>
          <button type="button" disabled={select.isPending || narrativeChanged} onClick={() => select.mutate()} className={BTN}>
            {select.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save the rows
          </button>
        </div>
      )}

      {actions.length > 0 && (
        <section>
          <div className={LABEL}>Corrective actions</div>
          <ul className="space-y-1.5">
            {actions.map((a) => (
              <li key={a.id} className="rounded-xl border border-border px-3 py-2 text-[12.5px]">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-[11.5px] font-semibold text-muted-foreground">{a.ref || `CA-${a.id}`}</span>
                  {(a.status_words || a.status) && <Badge tone="grey">{a.status_words || a.status}</Badge>}
                </div>
                {a.request && <div className="mt-0.5 font-semibold">{a.request}</div>}
                <div className="text-[11.5px] text-muted-foreground">
                  {[a.due_date ? `due ${dayText(a.due_date)}` : '', a.days_open != null ? `${a.days_open} day${a.days_open === 1 ? '' : 's'} open` : ''].filter(Boolean).join(' · ')}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-2 border-t border-border pt-4">
        {/* approval opens 7 days after the month ends (late captures): the server refuses it before */}
        {draft && !opens && r.approvable_from && (
          <p className="rounded-xl bg-muted px-3 py-2 text-[12.5px] text-muted-foreground">
            Can be approved from {dayText(r.approvable_from)}: reports of the month's last days may still arrive until then.
          </p>
        )}
        {draft && can.approve && !opens && (
          <button type="button" disabled className={cn(BTN, 'w-full bg-success text-success-foreground')}>
            <CheckCircle2 size={16} aria-hidden="true" /> Approve
          </button>
        )}
        {draft && can.approve && opens &&
          (sure ? (
            <div className="space-y-2 rounded-xl border border-success/25 bg-success/10 p-3">
              <p className="text-[12.5px] text-success">Approve this report? An earlier approved report of the same supplier, month and edition becomes superseded.</p>
              <div className="flex gap-2">
                <button type="button" disabled={approve.isPending || (editable && (narrativeChanged || changes > 0))} onClick={() => approve.mutate()} className={cn(BTN, 'bg-success text-success-foreground hover:bg-success/90')}>
                  {approve.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Stamp size={15} aria-hidden="true" />} Yes, approve
                </button>
                <button type="button" onClick={() => setSure(false)} className={BTN_OUTLINE}>Not yet</button>
              </div>
              {editable && (narrativeChanged || changes > 0) && <p className="text-[11.5px] text-warning">Save the narrative and the rows first.</p>}
            </div>
          ) : (
            <button type="button" onClick={() => setSure(true)} className={cn(BTN, 'w-full bg-success text-success-foreground hover:bg-success/90')}>
              <CheckCircle2 size={16} aria-hidden="true" /> Approve
            </button>
          ))}
        {r.status === 'approved' && can.download && (
          <button type="button" disabled={downloading} onClick={() => void download()} className={cn(BTN, 'w-full')}>
            {downloading ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Download size={16} aria-hidden="true" />} Download the workbook (.xlsx)
          </button>
        )}
        {draft && !can.approve && <p className="text-[12px] text-muted-foreground">Management (or an admin) approves the draft before it can be downloaded.</p>}
      </section>

      {Boolean(downloads.length || r.file_sha256) && (
        <section>
          <div className={LABEL}>Downloads</div>
          {r.file_sha256 && <p className="mb-1 font-mono text-[11.5px] text-muted-foreground">File sha256 {r.file_sha256.slice(0, 16)}…</p>}
          <ul className="space-y-1 text-[12.5px]">
            {downloads.map((x, i) => (
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
