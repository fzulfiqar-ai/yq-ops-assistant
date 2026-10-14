import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ArrowRightLeft,
  BadgeCheck,
  Camera,
  Eye,
  EyeOff,
  GitMerge,
  History,
  Loader2,
  Lock,
  MessageSquareReply,
  Scale,
  Siren,
  Store,
  Truck,
  UserRound,
} from 'lucide-react'
import { ApiError, apiGet, apiPatch, apiPost } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { errorText } from '@/lib/errorText'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { LoadError } from '@/components/LoadError'
import { ProductPicker, ProductRow } from '@/components/ProductPicker'
import { Badge } from '@/components/ui/badge'
import {
  ACTION_STATUS_LABEL,
  CASE_STATUS_LABEL,
  caseMoves,
  caseOf,
  caseProduct,
  EVIDENCE_LEVELS,
  evidenceLabel,
  fetchLists,
  nameOf,
  notReady,
  REPORT_BASIS_LABEL,
  safetyMail,
  VERIFY_METHODS,
  type CaseMove,
  type MiActionStatus,
  type MiCase,
  type MiCaseDetail,
  type MiCaseEvent,
  type MiCaseRow,
  type MiCaseStatus,
  type MiLists,
  type MiObservation,
  type MiProduct,
  type MiSupplierAction,
} from '@/lib/marketIntel'
import { CaseCard } from './CasesTab'
import { Empty, Fact, Loading, NotAvailableYet, PhotoTile } from './ui'
import {
  ACTION_STATUS_TONE,
  BTN,
  BTN_GHOST,
  BTN_OUTLINE,
  CARD,
  CASE_STATUS_TONE,
  dayText,
  INPUT,
  isPast,
  LABEL,
  SELECT,
  TEXTAREA,
  useMiRoles,
  whenText,
  who,
  type MiRoles,
} from './util'

/**
 * /market-intel/cases/:id — one quality case (release r8m, plan F3.4 and F7). The office reads the
 * whole story here: the sightings with their photos (people photos come blurred from the server), the
 * timeline, the verification and the supplier actions; a reviewer moves the case through the eight
 * statuses (only the moves the API allows, each with what it needs), raises the evidence level (it
 * never goes down), asks the rep a question, links the product, merges a duplicate, moves a sighting
 * and records the supplier's response. Management reads it all and changes nothing. A salesman never
 * opens it: his reports and their status words are on Market Intel -> My reports.
 */

const SEVERITY: Record<number, string> = { 1: 'Low', 2: 'Medium', 3: 'High' }

/**
 * What the server KEEPS of each text (app/market_quality.py clean_text): a longer one is cut there, so
 * the fields stop at the same length (the API's own limits are a little higher).
 */
const LIMIT = { title: 160, assignee: 120, reason: 500, question: 500, request: 1000, owner: 120, response: 2000, check: 1000 } as const

export default function CasePage() {
  const { id } = useParams()
  const caseId = Number(id)
  const { me } = useAuth()
  const isRep = me?.role === 'salesman'
  const valid = Number.isInteger(caseId) && caseId > 0
  const [raw, setRaw] = useState(false)
  const q = useQuery({
    queryKey: ['mi-case', caseId, raw],
    queryFn: () => apiGet<MiCaseDetail>(`/market-intel/cases/${caseId}${raw ? '?raw=1' : ''}`),
    enabled: !isRep && valid,
    retry: (n, e) => !notReady(e) && n < 1,
  })
  const listsQ = useQuery({ queryKey: ['mi-lists'], queryFn: () => fetchLists(), staleTime: 10 * 60_000, retry: 1, enabled: !isRep })
  const lists = listsQ.data?.available ? listsQ.data : null
  const roles = useMiRoles(q.data?.capabilities)

  let body: ReactNode
  if (isRep) {
    body = (
      <Empty icon={<Lock size={18} aria-hidden="true" />}>
        Cases are for the office. Your own reports and what became of them are under Market Intel, My reports.
      </Empty>
    )
  } else if (!valid) {
    body = <Empty icon={<Lock size={18} aria-hidden="true" />}>That is not a case number.</Empty>
  } else if (q.isError && q.error instanceof ApiError && q.error.status === 404 && !notReady(q.error, false)) {
    // the case route exists but this case does not, or the API is older than this page
    body = <Empty icon={<Lock size={18} aria-hidden="true" />}>{errorText(q.error, 'This case was not found.')}</Empty>
  } else if (q.isError && notReady(q.error)) {
    body = <NotAvailableYet what="Quality cases" />
  } else if (q.isError) {
    body = <LoadError error={q.error} onRetry={() => q.refetch()} isRetrying={q.isFetching} />
  } else if (!q.data) {
    body = <Loading className="h-64" />
  } else if (q.data.available === false) {
    body = <NotAvailableYet what="Quality cases" />
  } else {
    const c = caseOf(q.data)
    body = c ? (
      <CaseView d={q.data} c={c} roles={roles} lists={lists} raw={raw} onRaw={setRaw} />
    ) : (
      <Empty icon={<Lock size={18} aria-hidden="true" />}>This case could not be read.</Empty>
    )
  }

  return (
    <div className="mx-auto max-w-6xl">
      <Link to="/market-intel?tab=cases" className="mb-4 inline-flex h-10 items-center gap-1.5 rounded-lg pr-2 text-[13px] font-semibold text-primary hover:underline">
        <ArrowLeft size={15} aria-hidden="true" /> Cases
      </Link>
      {body}
    </div>
  )
}

/** After a write: say so, and read the case and the lists of cases again. */
function useCaseWrite(caseId: number) {
  const toast = useToast()
  const qc = useQueryClient()
  return {
    ok: (msg: string) => {
      toast(msg, 'success')
      qc.invalidateQueries({ queryKey: ['mi-case', caseId] })
      qc.invalidateQueries({ queryKey: ['mi-cases'] })
    },
    fail: (fallback: string) => (e: unknown) => toast(errorText(e, fallback), 'error'),
  }
}

function CaseView({ d, c, roles, lists, raw, onRaw }: { d: MiCaseDetail; c: MiCase; roles: MiRoles; lists: MiLists | null; raw: boolean; onRaw: (v: boolean) => void }) {
  const navigate = useNavigate()
  const { ok, fail } = useCaseWrite(c.id)
  const actions = d.actions || []
  const observations = d.observations || []
  const events = [...(d.events || [])].sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')) || b.id - a.id)
  const suggestions = (d.suggestions || []).filter((s) => s.id !== c.id)
  const merged = c.merged_into != null
  const canEdit = roles.review && !merged
  const brand = nameOf(c.brand) || lists?.brands.find((b) => b.id === c.brand_id)?.name || ''
  const supplier = nameOf(c.supplier) || lists?.suppliers.find((s) => s.id === c.supplier_id)?.name || ''
  const statusText = c.status_label || CASE_STATUS_LABEL[c.status] || c.status
  const mail = safetyMail(d.events)
  const flag = useMutation({
    mutationFn: (p: { id: number; flag: boolean }) => apiPost(`/market-intel/photos/${p.id}/people`, { flag: p.flag }),
    onSuccess: () => ok('Photo updated.'),
    onError: fail('Could not change the photo.'),
  })

  return (
    <div>
      <header className="mb-5">
        <div className="flex flex-wrap items-center gap-1.5">
          {c.safety && (
            <Badge tone="rose">
              <span className="inline-flex items-center gap-1"><Siren size={10} aria-hidden="true" /> Safety</span>
            </Badge>
          )}
          <Badge tone={CASE_STATUS_TONE[c.status] || 'grey'}>{statusText}</Badge>
          <Badge tone="grey">{c.evidence_label || evidenceLabel(c.evidence_level)}</Badge>
          {c.issue_type?.label_en && <Badge tone="accent">{c.issue_type.label_en}</Badge>}
          {c.severity ? <Badge tone="amber">Severity: {SEVERITY[c.severity] || c.severity}</Badge> : null}
        </div>
        <h1 className="mt-2 font-display text-[22px] font-bold leading-tight tracking-tight lg:text-[26px]">{c.title || `Case ${c.id}`}</h1>
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          Case {c.id}
          {c.obs_count != null && ` · ${c.obs_count} report${c.obs_count === 1 ? '' : 's'}`}
          {c.shop_count ? ` · ${c.shop_count} shop${c.shop_count === 1 ? '' : 's'}` : ''}
          {c.first_seen && ` · first seen ${dayText(c.first_seen)}`}
          {c.last_seen && ` · last seen ${dayText(c.last_seen)}`}
        </p>
        {merged && (
          <div className="mt-3 rounded-xl border border-border bg-muted px-3 py-2.5 text-[13px]">
            Merged into{' '}
            <Link to={`/market-intel/cases/${c.merged_into}`} className="font-semibold text-primary hover:underline">
              case {c.merged_into}
            </Link>
            ; its sightings moved there.
          </div>
        )}
        {c.safety && !merged && !['resolved', 'closed', 'not_substantiated'].includes(c.status) && (
          <p className="mt-3 rounded-xl bg-[#fdecef] px-3 py-2 text-[12.5px] font-semibold text-[#9f1239]">
            Safety case: review it within one business day.{' '}
            {/* only a "sent" outcome says the manager was e-mailed (market_quality.send_safety_alert) */}
            {mail.state === 'sent'
              ? 'The manager was e-mailed.'
              : mail.state === 'not_sent'
                ? `The safety e-mail did NOT go out${mail.reason ? ` (${mail.reason})` : ''}: tell the manager.`
                : mail.state === 'queued'
                  ? 'The safety e-mail to the manager is queued.'
                  : ''}
          </p>
        )}
        {roles.readOnly && <p className="mt-2 text-[12px] text-muted-foreground">Read-only: management sees every case and changes nothing here.</p>}
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-5">
          <Details c={c} brand={brand} supplier={supplier} lists={lists} canEdit={canEdit} onSaved={ok} onError={fail} />

          <section>
            <h2 className="mb-2 flex items-center gap-1.5 font-display text-[15px] font-bold">
              <Camera size={15} aria-hidden="true" /> Sightings
            </h2>
            {observations.length ? (
              <ul className="space-y-2.5">
                {observations.map((o) => (
                  <SightingCard
                    key={o.id}
                    o={o}
                    raw={raw}
                    canEdit={canEdit}
                    onFlag={(photoId, f) => flag.mutate({ id: photoId, flag: f })}
                    suggestions={suggestions}
                    caseId={c.id}
                    onMoved={ok}
                    onError={fail}
                  />
                ))}
              </ul>
            ) : (
              <p className="rounded-2xl border border-dashed border-border bg-card px-4 py-6 text-center text-[13px] text-muted-foreground">No sightings on this case.</p>
            )}
          </section>

          <Timeline events={events} />
        </div>

        <aside className="space-y-4">
          {canEdit && <StatusPanel key={`${c.status}-${c.changed_at || ''}`} c={c} statusText={statusText} actions={actions} admin={roles.admin} lists={lists} onDone={ok} onError={fail} />}
          {!canEdit && !merged && (
            <section className={CARD}>
              <h2 className="mb-1 font-display text-[15px] font-bold">Status</h2>
              <Badge tone={CASE_STATUS_TONE[c.status] || 'grey'}>{statusText}</Badge>
            </section>
          )}
          <EvidencePanel key={`ev-${c.evidence_level}`} c={c} canEdit={canEdit} onDone={ok} onError={fail} />
          {c.verification_method && (
            <section className={CARD}>
              <h2 className="mb-1.5 flex items-center gap-1.5 font-display text-[15px] font-bold">
                <BadgeCheck size={15} className="text-[#137a48]" aria-hidden="true" /> Verification
              </h2>
              <p className="text-[13px] font-semibold">{c.verification_label || VERIFY_METHODS.find((m) => m.value === c.verification_method)?.label || c.verification_method}</p>
              {c.verification_reason && <p className="mt-1 whitespace-pre-line text-[12.5px] text-muted-foreground">{c.verification_reason}</p>}
            </section>
          )}
          {c.status_reason && (
            <section className={CARD}>
              <h2 className="mb-1.5 flex items-center gap-1.5 font-display text-[15px] font-bold">
                <Scale size={15} aria-hidden="true" /> Last decision
              </h2>
              <p className="whitespace-pre-line text-[12.5px]">{c.status_reason}</p>
              {(c.changed_by || c.changed_at) && (
                <p className="mt-1 text-[11.5px] text-muted-foreground">
                  {c.changed_by ? who(c.changed_by) : ''}
                  {c.changed_at ? ` · ${whenText(c.changed_at)}` : ''}
                </p>
              )}
            </section>
          )}
          {/* the first question is the status panel's "More info required"; a follow-up is asked here */}
          {canEdit && c.status === 'more_info_required' && (
            <section className={CARD}>
              <h2 className="mb-2 flex items-center gap-1.5 font-display text-[15px] font-bold">
                <MessageSquareReply size={15} aria-hidden="true" /> Ask the rep again
              </h2>
              <AskRepForm caseId={c.id} onDone={ok} onError={fail} />
            </section>
          )}
          <ActionsPanel c={c} actions={actions} canEdit={canEdit} lists={lists} onDone={ok} onError={fail} />
          {canEdit && <MergePanel c={c} suggestions={suggestions} onMerged={(into) => { ok(`Merged into case ${into}.`); navigate(`/market-intel/cases/${into}`) }} onError={fail} />}
          {roles.admin && observations.some((o) => (o.photos || []).some((p) => p.has_people || p.has_people_flag)) && (
            <section className={CARD}>
              <h2 className="mb-1 font-display text-[15px] font-bold">Photos with people</h2>
              <p className="text-[12px] text-muted-foreground">Everyone sees them blurred. An admin may open the originals here.</p>
              <button type="button" onClick={() => onRaw(!raw)} className={cn(BTN_OUTLINE, 'mt-2 w-full')}>
                {raw ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                {raw ? 'Blur them again' : 'Show the originals (admin)'}
              </button>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}

/* ───────────────────────── details: product, brand, supplier, owner, due date ───────────────────────── */

function Details({
  c,
  brand,
  supplier,
  lists,
  canEdit,
  onSaved,
  onError,
}: {
  c: MiCase
  brand: string
  supplier: string
  lists: MiLists | null
  canEdit: boolean
  onSaved: (msg: string) => void
  onError: (fallback: string) => (e: unknown) => void
}) {
  const [edit, setEdit] = useState(false)
  return (
    <section className={CARD}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-display text-[15px] font-bold">Details</h2>
        {canEdit && (
          <button type="button" onClick={() => setEdit((v) => !v)} className={BTN_GHOST}>
            {edit ? 'Close' : 'Edit details'}
          </button>
        )}
      </div>
      {edit ? (
        <EditCase key={`${c.id}-${c.changed_at || ''}`} c={c} lists={lists} onSaved={(m) => { setEdit(false); onSaved(m) }} onError={onError} onCancel={() => setEdit(false)} />
      ) : (
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          {/* the code first: a long product name truncates, the code never does */}
          <Fact label="Product">{c.yq_item_code ? caseProduct(c) : <span className="text-[#96600d]">Not linked yet</span>}</Fact>
          <Fact label="Brand">{brand || '—'}</Fact>
          <Fact label="Supplier">{supplier || '—'}</Fact>
          <Fact label="Batch">{c.batch_ref || '—'}</Fact>
          <Fact label="Packaging version">{c.packaging_version || '—'}</Fact>
          <Fact label="Severity">{c.severity ? SEVERITY[c.severity] || c.severity : '—'}</Fact>
          <Fact label="Assignee">{c.assignee || '—'}</Fact>
          <Fact label="Due">{c.due_date ? <span className={cn(isPast(c.due_date) && !['resolved', 'closed'].includes(c.status) && 'text-[#9f1239]')}>{dayText(c.due_date)}</span> : '—'}</Fact>
          <Fact label="Safety">{c.safety ? 'Yes' : 'No'}</Fact>
        </div>
      )}
    </section>
  )
}

function EditCase({
  c,
  lists,
  onSaved,
  onError,
  onCancel,
}: {
  c: MiCase
  lists: MiLists | null
  onSaved: (msg: string) => void
  onError: (fallback: string) => (e: unknown) => void
  onCancel: () => void
}) {
  const [title, setTitle] = useState(c.title || '')
  const [product, setProduct] = useState<MiProduct | null>(
    c.yq_item_code
      ? { code: c.yq_item_code, name: c.yq_item?.display_name || c.product_name || c.yq_item_code, brand: c.yq_item?.brand, thumb_url: c.yq_item?.thumb_url }
      : null,
  )
  const [picking, setPicking] = useState(false)
  const [brandId, setBrandId] = useState(c.brand_id ? String(c.brand_id) : '')
  const [supplierId, setSupplierId] = useState(c.supplier_id ? String(c.supplier_id) : '')
  const [severity, setSeverity] = useState(c.severity ? String(c.severity) : '')
  const [assignee, setAssignee] = useState(c.assignee || '')
  const [due, setDue] = useState(c.due_date ? c.due_date.slice(0, 10) : '')
  const [safety, setSafety] = useState(Boolean(c.safety))

  const body: Record<string, unknown> = {}
  if (title.trim() && title.trim() !== (c.title || '')) body.title = title.trim()
  if ((product?.code || null) !== (c.yq_item_code || null)) body.yq_item_code = product?.code || null
  if ((brandId ? Number(brandId) : null) !== (c.brand_id ?? null)) body.brand_id = brandId ? Number(brandId) : null
  if ((supplierId ? Number(supplierId) : null) !== (c.supplier_id ?? null)) body.supplier_id = supplierId ? Number(supplierId) : null
  if ((severity ? Number(severity) : null) !== (c.severity ?? null)) body.severity = severity ? Number(severity) : null
  if (assignee.trim() !== (c.assignee || '')) body.assignee = assignee.trim() || null
  if (due !== (c.due_date ? c.due_date.slice(0, 10) : '')) body.due_date = due || null
  if (safety !== Boolean(c.safety)) body.safety = safety
  const changed = Object.keys(body).length > 0

  const save = useMutation({
    mutationFn: () => apiPatch(`/market-intel/cases/${c.id}`, body),
    onSuccess: () => onSaved('Case updated.'),
    onError: onError('Could not save the case.'),
  })

  return (
    <div className="space-y-3">
      <div>
        <label htmlFor="case-title" className={LABEL}>Title</label>
        <input id="case-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={LIMIT.title} className={INPUT} />
      </div>
      <div>
        <span className={LABEL}>Product</span>
        {product && !picking ? (
          <ProductRow
            p={product}
            action={
              <span className="flex shrink-0 gap-1">
                <button type="button" onClick={() => setPicking(true)} className={BTN_GHOST}>Change</button>
                <button type="button" onClick={() => setProduct(null)} className={BTN_GHOST}>Unlink</button>
              </span>
            }
          />
        ) : (
          <div className="rounded-xl border border-border p-2.5">
            <ProductPicker
              showRecent={false}
              onPick={(p) => {
                setProduct(p)
                setPicking(false)
                // the product's brand, when the admin's list knows it, with its supplier
                const b = lists?.brands.find((x) => x.active !== false && p.brand && (x.name.toLowerCase() === p.brand.toLowerCase() || x.aliases.some((a) => a.toLowerCase() === String(p.brand).toLowerCase())))
                if (b && !brandId) {
                  setBrandId(String(b.id))
                  if (b.supplier_id && !supplierId) setSupplierId(String(b.supplier_id))
                }
              }}
              words={{
                search: 'Search the catalog: name or code',
                atShop: 'Bought by the shop',
                recent: 'Recent',
                products: 'Products',
                results: 'Results',
                none: 'No product matches.',
                failed: 'The catalog could not load.',
                loading: 'Loading…',
              }}
            />
            {picking && (
              <button type="button" onClick={() => setPicking(false)} className={cn(BTN_GHOST, 'mt-1')}>Keep the current product</button>
            )}
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="case-brand" className={LABEL}>Brand</label>
          <select
            id="case-brand"
            value={brandId}
            onChange={(e) => {
              setBrandId(e.target.value)
              const b = lists?.brands.find((x) => String(x.id) === e.target.value)
              if (b?.supplier_id && !supplierId) setSupplierId(String(b.supplier_id))
            }}
            className={cn(SELECT, 'w-full')}
          >
            <option value="">No brand</option>
            {(lists?.brands || []).filter((b) => b.active !== false || String(b.id) === brandId).map((b) => <option key={b.id} value={String(b.id)}>{b.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="case-supplier" className={LABEL}>Supplier</label>
          <select id="case-supplier" value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={cn(SELECT, 'w-full')}>
            <option value="">No supplier</option>
            {(lists?.suppliers || []).filter((s) => s.active !== false || String(s.id) === supplierId).map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="case-severity" className={LABEL}>Severity</label>
          <select id="case-severity" value={severity} onChange={(e) => setSeverity(e.target.value)} className={cn(SELECT, 'w-full')}>
            <option value="">Not set</option>
            {[1, 2, 3].map((n) => <option key={n} value={String(n)}>{SEVERITY[n]}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="case-due" className={LABEL}>Due date</label>
          <input id="case-due" type="date" value={due} onChange={(e) => setDue(e.target.value)} className={INPUT} />
        </div>
        <div className="col-span-2">
          <label htmlFor="case-assignee" className={LABEL}>Assignee</label>
          <input id="case-assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)} maxLength={LIMIT.assignee} className={INPUT} placeholder="Who follows it up" />
        </div>
      </div>
      <label className="flex min-h-10 items-center gap-2 text-[13px] font-semibold">
        <input type="checkbox" checked={safety} onChange={(e) => setSafety(e.target.checked)} className="h-4 w-4 accent-[#9f1239]" />
        Safety case (heat, smoke, swelling, shock)
      </label>
      <div className="flex gap-2">
        <button type="button" disabled={!changed || save.isPending} onClick={() => save.mutate()} className={BTN}>
          {save.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save details
        </button>
        <button type="button" onClick={onCancel} className={BTN_OUTLINE}>Cancel</button>
      </div>
    </div>
  )
}

/* ───────────────────────── sightings ───────────────────────── */

function SightingCard({
  o,
  raw,
  canEdit,
  onFlag,
  suggestions,
  caseId,
  onMoved,
  onError,
}: {
  o: MiObservation
  raw: boolean
  canEdit: boolean
  onFlag: (photoId: number, flag: boolean) => void
  suggestions: MiCaseRow[]
  caseId: number
  onMoved: (msg: string) => void
  onError: (fallback: string) => (e: unknown) => void
}) {
  const when = o.client_observed_at || o.observed_at
  const photos = o.photos || []
  return (
    <li className="rounded-2xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11.5px] font-semibold text-muted-foreground">MI-{o.id}</span>
        {o.safety_flag && <Badge tone="rose">Safety</Badge>}
        {/* the case page sends the sighting's own issue as issue_label */}
        {(o.issue_label || o.issue_type?.label_en) && <Badge tone="accent">{o.issue_label || o.issue_type?.label_en}</Badge>}
        {o.report_basis && <Badge tone="grey">{REPORT_BASIS_LABEL[o.report_basis] || o.report_basis}</Badge>}
        {o.qty_affected ? <Badge tone="grey">{o.qty_affected} affected</Badge> : null}
        {o.near_duplicate_of ? <Badge tone="amber">Photo seen before</Badge> : null}
      </div>
      <div className="mt-1 text-[13.5px] font-semibold leading-snug">{o.title || o.item_title || (o.note ? '' : 'Photo only')}</div>
      {o.note && <p className="mt-0.5 whitespace-pre-line text-[12.5px] leading-snug text-muted-foreground">{o.note}</p>}
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-muted-foreground">
        {when && <span>{whenText(when)}</span>}
        {o.shop_name && (
          <span className="inline-flex items-center gap-1">
            <Store size={11} aria-hidden="true" /> {o.shop_name}
            {o.area ? ` · ${o.area}` : ''}
          </span>
        )}
        {o.by && (
          <span className="inline-flex items-center gap-1">
            <UserRound size={11} aria-hidden="true" /> {o.by}
          </span>
        )}
        {o.batch_ref && <span>Batch {o.batch_ref}</span>}
        {o.packaging_version && <span>Packaging {o.packaging_version}</span>}
      </div>
      {photos.length > 0 && (
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {photos.map((p) => (
            <PhotoTile key={p.id} p={p} alt={`MI-${o.id} photo`} raw={raw} canFlag={canEdit} onFlag={(f) => onFlag(p.id, f)} />
          ))}
        </div>
      )}
      {canEdit && <MoveBox obsId={o.id} caseId={caseId} suggestions={suggestions} onDone={onMoved} onError={onError} />}
    </li>
  )
}

function MoveBox({
  obsId,
  caseId,
  suggestions,
  onDone,
  onError,
}: {
  obsId: number
  caseId: number
  suggestions: MiCaseRow[]
  onDone: (msg: string) => void
  onError: (fallback: string) => (e: unknown) => void
}) {
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState('')
  const n = Number(target)
  const valid = Number.isInteger(n) && n > 0 && n !== caseId
  const m = useMutation({
    mutationFn: () => apiPost(`/market-intel/observations/${obsId}/move`, { case_id: n }),
    onSuccess: () => {
      setOpen(false)
      setTarget('')
      onDone(`MI-${obsId} moved to case ${n}.`)
    },
    onError: onError('Could not move the sighting.'),
  })
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={cn(BTN_GHOST, 'mt-1.5')}>
        <ArrowRightLeft size={13} aria-hidden="true" /> Move to another case
      </button>
    )
  }
  return (
    <div className="mt-2 space-y-2 rounded-xl border border-border bg-muted/40 p-2.5">
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.slice(0, 4).map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setTarget(String(s.id))}
              className={cn('h-9 max-w-full truncate rounded-full px-3 text-[12px] font-semibold', String(s.id) === target ? 'bg-[#6D4091] text-white' : 'bg-[#F3EDF8] text-[#4B2C66] hover:bg-[#EADFF3]')}
            >
              Case {s.id}: {s.title}
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input type="number" inputMode="numeric" min={1} value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Case number" aria-label="Case number" className={cn(INPUT, 'w-36')} />
        <button type="button" disabled={!valid || m.isPending} onClick={() => m.mutate()} className={BTN}>
          {m.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Move
        </button>
        <button type="button" onClick={() => setOpen(false)} className={BTN_OUTLINE}>Cancel</button>
      </div>
    </div>
  )
}

/* ───────────────────────── the timeline ───────────────────────── */

function detailText(d: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    const v = d[k]
    if (typeof v === 'string' && v.trim()) return v.trim()
    if (typeof v === 'number') return String(v)
  }
  return ''
}

function statusWord(s?: string | null): string {
  return s ? CASE_STATUS_LABEL[s as MiCaseStatus] || s : ''
}

/** Why the evidence rose (market_quality._raise_evidence / update_case: detail.why). */
const EVIDENCE_WHY: Record<string, string> = {
  photo: 'a photo came with a sighting',
  verified: 'the case was verified',
  reviewer: 'raised by a reviewer',
  merge: 'a merged case brought it',
}

/** The fields an edit or an action update names (detail = {field: {from, to}}). */
const FIELD_WORDS: Record<string, string> = {
  title: 'title',
  yq_item_code: 'product',
  brand_id: 'brand',
  supplier_id: 'supplier',
  severity: 'severity',
  assignee: 'assignee',
  due_date: 'due date',
  safety: 'safety',
  verification_method: 'verification method',
  verification_reason: 'verification reason',
  batch_ref: 'batch',
  packaging_version: 'packaging version',
  supplier_response: "supplier's response",
  resolution_check: "YQ's resolution check",
  status: 'status',
}
/** Keys an event's detail carries beside its changes (never "edited"). */
const NOT_CHANGES = new Set(['reason', 'observation_id', 'from_case', 'photo_id', 'action_id'])

function changeOf(v: unknown): { from: unknown; to: unknown } | null {
  return v && typeof v === 'object' && 'to' in (v as object) ? (v as { from: unknown; to: unknown }) : null
}

/** "status Open → Supplier responded; supplier's response recorded" from an action update's changes. */
function actionChanges(changes: Record<string, unknown>): string {
  const out: string[] = []
  for (const [k, v] of Object.entries(changes)) {
    const ch = changeOf(v)
    if (!ch) continue
    if (k === 'status') {
      const word = (s: unknown) => ACTION_STATUS_LABEL[String(s) as MiActionStatus] || String(s ?? '')
      out.push(`status ${word(ch.from)} → ${word(ch.to)}`)
    } else {
      out.push(`${FIELD_WORDS[k] || k.replace(/_/g, ' ')} ${ch.to ? 'recorded' : 'cleared'}`)
    }
  }
  return out.join('; ')
}

/** One timeline line: `quote` marks words a person typed (a reason, a question, an answer, a request). */
function eventText(e: MiCaseEvent): { title: string; body: string; quote?: boolean; alarm?: boolean } {
  const d = (e.detail || {}) as Record<string, unknown>
  switch (e.event) {
    case 'created': {
      const obs = detailText(d, 'observation_id')
      return { title: 'Case opened', body: obs ? `from MI-${obs}` : '' }
    }
    case 'status': {
      const aid = detailText(d, 'action_id')
      const reason = detailText(d, 'reason', 'verification_reason')
      const title = `${statusWord(e.from_status) || 'Status'} → ${statusWord(e.to_status)}`
      return reason ? { title, body: reason, quote: true } : { title, body: aid ? `with CA-${aid}` : '' }
    }
    case 'evidence': {
      const to = Number(d.to ?? d.evidence_level ?? d.level)
      const why = EVIDENCE_WHY[String(d.why || '')] || detailText(d, 'why')
      const from = detailText(d, 'from_case')
      return { title: to ? `Evidence raised to "${evidenceLabel(to)}"` : 'Evidence level raised', body: [why, from ? `from case ${from}` : ''].filter(Boolean).join(', ') }
    }
    case 'question':
      return { title: 'Asked the rep', body: detailText(d, 'text', 'question'), quote: true }
    case 'answer':
      return { title: d.photo_id ? 'The rep answered, with a photo' : 'The rep answered', body: detailText(d, 'text', 'answer'), quote: true }
    case 'link': {
      const obs = detailText(d, 'observation_id')
      return { title: obs ? `Sighting MI-${obs} joined` : 'A sighting joined', body: '' }
    }
    case 'move': {
      const obs = detailText(d, 'observation_id')
      const to = detailText(d, 'to_case', 'case_id', 'to')
      const from = detailText(d, 'from_case', 'from')
      return { title: obs ? `MI-${obs} moved` : 'A sighting moved', body: to ? `to case ${to}` : from ? `from case ${from}` : '' }
    }
    case 'merge': {
      const into = detailText(d, 'into_id', 'into')
      const from = detailText(d, 'from_id', 'merged_from', 'from')
      return { title: into ? `Merged into case ${into}` : from ? `Case ${from} merged into this one` : 'Merged', body: '' }
    }
    case 'edit': {
      const safety = changeOf(d.safety)
      if (safety && safety.to === true && Object.keys(d).every((k) => k === 'safety' || NOT_CHANGES.has(k))) {
        return { title: 'Marked as a safety case', body: detailText(d, 'reason'), quote: true }
      }
      const fields = Object.keys(d).filter((k) => !NOT_CHANGES.has(k)).map((k) => FIELD_WORDS[k] || k.replace(/_/g, ' '))
      return { title: `Edited ${fields.join(', ') || 'details'}`, body: detailText(d, 'reason'), quote: true }
    }
    case 'action': {
      const aid = detailText(d, 'action_id', 'id')
      const ref = aid ? `CA-${aid}` : 'A supplier action'
      // an update nests what changed (market_quality.update_action); the first event is the request itself
      if (d.changes && typeof d.changes === 'object') return { title: `${ref} updated`, body: actionChanges(d.changes as Record<string, unknown>) }
      const due = detailText(d, 'due_date')
      return { title: `${aid ? `Supplier action ${ref}` : 'Supplier action'} requested${due ? `, due ${dayText(due)}` : ''}`, body: detailText(d, 'request'), quote: true }
    }
    case 'safety_alert': {
      // the claim is written first ("queued"), the outcome after the answer ("sent" / "not_sent" + reason)
      const mail = String(d.email || '')
      if (mail === 'sent') return { title: 'Safety alert e-mailed to the manager', body: '' }
      if (mail === 'not_sent') return { title: 'Safety alert NOT e-mailed', body: detailText(d, 'reason'), alarm: true }
      if (mail === 'queued') return { title: 'Safety alert queued for the manager', body: '' }
      return { title: 'Safety alert', body: '' }
    }
    default:
      return { title: e.event.replace(/_/g, ' '), body: detailText(d, 'reason', 'text'), quote: true }
  }
}

function Timeline({ events }: { events: MiCaseEvent[] }) {
  return (
    <section className={CARD}>
      <h2 className="mb-3 flex items-center gap-1.5 font-display text-[15px] font-bold">
        <History size={15} aria-hidden="true" /> Timeline
      </h2>
      {events.length ? (
        <ol className="space-y-2.5 border-l-2 border-[#E6DAF0] pl-3">
          {events.map((e) => {
            const t = eventText(e)
            return (
              <li key={e.id} className="text-[12.5px]">
                <div className={cn('font-semibold', e.event === 'safety_alert' && 'text-[#9f1239]', t.alarm && '-mx-1 inline-block rounded bg-[#fdecef] px-1')}>{t.title}</div>
                {t.body && <div className="whitespace-pre-line text-foreground/80">{t.quote ? `“${t.body}”` : t.body}</div>}
                <div className="text-muted-foreground">
                  {who(e.actor)}
                  {e.at ? ` · ${whenText(e.at)}` : ''}
                </div>
              </li>
            )
          })}
        </ol>
      ) : (
        <p className="text-[12.5px] text-muted-foreground">Nothing has happened on this case yet.</p>
      )}
    </section>
  )
}

/* ───────────────────────── status: only the moves the API allows ───────────────────────── */

function moveHint(m: CaseMove, from: MiCaseStatus): string {
  if (m.via === 'question') return 'Sends a question to the rep; the case waits for the answer.'
  if (m.via === 'action') return 'Creates a supplier action: what is asked, who follows it up, by when.'
  if (m.to === 'verified') return 'Needs a method and a reason. The evidence level becomes at least "Inspected & verified by YQ".'
  if (m.to === 'resolved') return "Needs a supplier action carrying the supplier's response and YQ's resolution check."
  if (from === 'closed') return 'Admins only. Needs a reason.'
  if (m.reason === 'required') return 'Needs a reason (kept in the history).'
  return 'A reason is optional.'
}

function StatusPanel({
  c,
  statusText,
  actions,
  admin,
  lists,
  onDone,
  onError,
}: {
  c: MiCase
  statusText: string
  actions: MiSupplierAction[]
  admin: boolean
  lists: MiLists | null
  onDone: (msg: string) => void
  onError: (fallback: string) => (e: unknown) => void
}) {
  const actionDone = actions.some((a) => (a.supplier_response || '').trim() && (a.resolution_check || '').trim())
  // the contract's table, narrowed to what the server says this login may do now (next_statuses)
  const moves = caseMoves(c.status, { admin, actionDone, allowed: c.next_statuses })
  const [to, setTo] = useState<MiCaseStatus | null>(null)
  const [reason, setReason] = useState('')
  const [method, setMethod] = useState('')
  const move = moves.find((m) => m.to === to) || null
  const reasonOk = !move || move.reason !== 'required' || reason.trim().length >= 3
  const ready = Boolean(move && move.via === 'patch' && !move.blocked && reasonOk && (!move.method || method))
  const patch = useMutation({
    mutationFn: () => {
      const r = reason.trim()
      const body: Record<string, unknown> = { status: move?.to }
      if (r) body.reason = r
      if (move?.method) {
        body.verification_method = method
        body.verification_reason = r
      }
      return apiPatch(`/market-intel/cases/${c.id}`, body)
    },
    onSuccess: () => onDone(`Moved to ${move ? CASE_STATUS_LABEL[move.to] : 'the new status'}.`),
    onError: onError('Could not move the case.'),
  })

  return (
    <section className={CARD}>
      <h2 className="mb-1.5 font-display text-[15px] font-bold">Status</h2>
      <div className="mb-3 flex items-center gap-2 text-[12.5px] text-muted-foreground">
        Now <Badge tone={CASE_STATUS_TONE[c.status] || 'grey'}>{statusText}</Badge>
      </div>
      {moves.length ? (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Move the case to">
          {moves.map((m) => (
            <button
              key={m.to}
              type="button"
              aria-pressed={to === m.to}
              onClick={() => setTo(to === m.to ? null : m.to)}
              className={cn('h-10 rounded-full px-3.5 text-[12.5px] font-semibold transition-colors', to === m.to ? 'bg-[#6D4091] text-white' : 'bg-[#F3EDF8] text-[#4B2C66] hover:bg-[#EADFF3]')}
            >
              {CASE_STATUS_LABEL[m.to]}
            </button>
          ))}
        </div>
      ) : (
        <p className="text-[12.5px] text-muted-foreground">No further move from {statusText}.</p>
      )}

      {move && (
        <div className="mt-3 space-y-2.5 border-t border-border pt-3">
          <p className="text-[12px] text-muted-foreground">{moveHint(move, c.status)}</p>
          {move.blocked ? (
            <p className="rounded-xl bg-[#fdf3e3] px-3 py-2 text-[12.5px] font-medium text-[#7a4d0a]">{move.blocked}</p>
          ) : move.via === 'question' ? (
            <AskRepForm caseId={c.id} onDone={onDone} onError={onError} />
          ) : move.via === 'action' ? (
            <ActionForm c={c} lists={lists} onDone={onDone} onError={onError} />
          ) : (
            <>
              {move.method && (
                <div>
                  <label htmlFor="verify-method" className={LABEL}>How it was verified</label>
                  <select id="verify-method" value={method} onChange={(e) => setMethod(e.target.value)} className={cn(SELECT, 'w-full')}>
                    <option value="">Choose the method</option>
                    {VERIFY_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                </div>
              )}
              <div>
                <label htmlFor="move-reason" className={LABEL}>
                  {move.method ? 'Why it is verified' : move.reason === 'required' ? 'Reason (required)' : 'Reason (optional)'}
                </label>
                <textarea id="move-reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={LIMIT.reason} className={TEXTAREA} placeholder="Kept in the history" />
              </div>
              <button type="button" disabled={!ready || patch.isPending} onClick={() => patch.mutate()} className={cn(BTN, 'w-full')}>
                {patch.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Move to {CASE_STATUS_LABEL[move.to]}
              </button>
            </>
          )}
        </div>
      )}
    </section>
  )
}

function AskRepForm({ caseId, onDone, onError }: { caseId: number; onDone: (msg: string) => void; onError: (fallback: string) => (e: unknown) => void }) {
  const [text, setText] = useState('')
  const m = useMutation({
    mutationFn: () => apiPost(`/market-intel/cases/${caseId}/question`, { text: text.trim() }),
    onSuccess: () => {
      setText('')
      onDone('Question sent to the rep. The case waits for the answer.')
    },
    onError: onError('Could not send the question.'),
  })
  return (
    <div className="space-y-2">
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} maxLength={LIMIT.question} className={TEXTAREA} placeholder="e.g. Can you photograph the batch code on the box?" aria-label="Question to the rep" />
      <p className="text-[11.5px] text-muted-foreground">The rep sees it under My reports and answers there, with a photo if needed.</p>
      <button type="button" disabled={text.trim().length < 3 || m.isPending} onClick={() => m.mutate()} className={cn(BTN, 'w-full')}>
        {m.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Send to the rep
      </button>
    </div>
  )
}

/* ───────────────────────── evidence: only ever up ───────────────────────── */

function EvidencePanel({ c, canEdit, onDone, onError }: { c: MiCase; canEdit: boolean; onDone: (msg: string) => void; onError: (fallback: string) => (e: unknown) => void }) {
  const cur = Math.max(1, Number(c.evidence_level || 1))
  const [to, setTo] = useState('')
  const m = useMutation({
    mutationFn: () => apiPatch(`/market-intel/cases/${c.id}`, { evidence_level: Number(to) }),
    onSuccess: () => onDone(`Evidence raised to "${evidenceLabel(Number(to))}".`),
    onError: onError('Could not raise the evidence level.'),
  })
  return (
    <section className={CARD}>
      <h2 className="mb-2 font-display text-[15px] font-bold">Evidence</h2>
      <ol className="space-y-1.5">
        {EVIDENCE_LEVELS.map((e) => (
          <li key={e.level} className={cn('flex items-center gap-2 text-[12.5px]', e.level <= cur ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
            <span className={cn('grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10.5px] font-bold', e.level <= cur ? 'bg-[#6D4091] text-white' : 'bg-muted text-muted-foreground')}>{e.level}</span>
            {e.label}
          </li>
        ))}
      </ol>
      {canEdit && cur < 5 && (
        <div className="mt-3 flex gap-2">
          <select aria-label="Raise the evidence level to" value={to} onChange={(e) => setTo(e.target.value)} className={cn(SELECT, 'min-w-0 flex-1')}>
            <option value="">Raise to…</option>
            {EVIDENCE_LEVELS.filter((e) => e.level > cur).map((e) => <option key={e.level} value={String(e.level)}>{e.level}. {e.label}</option>)}
          </select>
          <button type="button" disabled={!to || m.isPending} onClick={() => m.mutate()} className={BTN}>
            {m.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Raise
          </button>
        </div>
      )}
      <p className="mt-2 text-[11.5px] text-muted-foreground">The level only moves up. Verifying the case sets it to at least 3.</p>
    </section>
  )
}

/* ───────────────────────── supplier actions ───────────────────────── */

function ActionForm({ c, lists, onDone, onError }: { c: MiCase; lists: MiLists | null; onDone: (msg: string) => void; onError: (fallback: string) => (e: unknown) => void }) {
  const [request, setRequest] = useState('')
  const [owner, setOwner] = useState('')
  const [due, setDue] = useState('')
  const [supplierId, setSupplierId] = useState(c.supplier_id ? String(c.supplier_id) : '')
  const m = useMutation({
    mutationFn: () =>
      apiPost(`/market-intel/cases/${c.id}/action`, {
        request: request.trim(),
        owner: owner.trim() || null,
        due_date: due || null,
        ...(supplierId ? { supplier_id: Number(supplierId) } : {}),
      }),
    onSuccess: () => onDone('Supplier action created; the case is now Action requested.'),
    onError: onError('Could not create the action.'),
  })
  return (
    <div className="space-y-2.5">
      <div>
        <label htmlFor="act-request" className={LABEL}>What we ask the supplier</label>
        <textarea id="act-request" rows={3} value={request} onChange={(e) => setRequest(e.target.value)} maxLength={LIMIT.request} className={TEXTAREA} placeholder="e.g. Root cause and a replacement for the affected units" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="act-owner" className={LABEL}>Owner at YQ</label>
          <input id="act-owner" value={owner} onChange={(e) => setOwner(e.target.value)} maxLength={LIMIT.owner} className={INPUT} placeholder="Who follows it up" />
        </div>
        <div>
          <label htmlFor="act-due" className={LABEL}>Due</label>
          <input id="act-due" type="date" value={due} onChange={(e) => setDue(e.target.value)} className={INPUT} />
        </div>
      </div>
      <div>
        <label htmlFor="act-supplier" className={LABEL}>Supplier</label>
        <select id="act-supplier" value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={cn(SELECT, 'w-full')}>
          <option value="">The case's supplier</option>
          {(lists?.suppliers || []).filter((s) => s.active !== false).map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
        </select>
      </div>
      <button type="button" disabled={request.trim().length < 3 || m.isPending} onClick={() => m.mutate()} className={cn(BTN, 'w-full')}>
        {m.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Request the action
      </button>
    </div>
  )
}

function ActionsPanel({
  c,
  actions,
  canEdit,
  lists,
  onDone,
  onError,
}: {
  c: MiCase
  actions: MiSupplierAction[]
  canEdit: boolean
  lists: MiLists | null
  onDone: (msg: string) => void
  onError: (fallback: string) => (e: unknown) => void
}) {
  const [adding, setAdding] = useState(false)
  return (
    <section className={CARD}>
      <h2 className="mb-2 flex items-center gap-1.5 font-display text-[15px] font-bold">
        <Truck size={15} aria-hidden="true" /> Supplier actions
      </h2>
      {actions.length ? (
        <ul className="space-y-3">
          {actions.map((a) => (
            <ActionItem key={`${a.id}-${a.updated_at || ''}`} a={a} canEdit={canEdit} onDone={onDone} onError={onError} />
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-muted-foreground">
          No supplier action yet.{' '}
          {c.status === 'verified' ? 'Move the case to Action requested to create one.' : 'An action is requested once the case is verified.'}
        </p>
      )}
      {canEdit && c.status === 'action_requested' && (
        <div className="mt-3 border-t border-border pt-3">
          {adding ? (
            <ActionForm c={c} lists={lists} onDone={(msg) => { setAdding(false); onDone(msg) }} onError={onError} />
          ) : (
            <button type="button" onClick={() => setAdding(true)} className={BTN_GHOST}>Add another action</button>
          )}
        </div>
      )}
    </section>
  )
}

function ActionItem({ a, canEdit, onDone, onError }: { a: MiSupplierAction; canEdit: boolean; onDone: (msg: string) => void; onError: (fallback: string) => (e: unknown) => void }) {
  const [edit, setEdit] = useState(false)
  const [response, setResponse] = useState(a.supplier_response || '')
  const [check, setCheck] = useState(a.resolution_check || '')
  const [status, setStatus] = useState<MiActionStatus>(a.status)
  const body: Record<string, unknown> = {}
  if (response.trim() !== (a.supplier_response || '')) body.supplier_response = response.trim() || null
  if (check.trim() !== (a.resolution_check || '')) body.resolution_check = check.trim() || null
  if (status !== a.status) body.status = status
  const m = useMutation({
    mutationFn: () => apiPatch(`/market-intel/actions/${a.id}`, body),
    onSuccess: () => {
      setEdit(false)
      onDone(`CA-${a.id} updated.`)
    },
    onError: onError('Could not update the action.'),
  })
  const overdue = isPast(a.due_date) && (a.status === 'open' || a.status === 'responded')
  const supplier = nameOf(a.supplier)
  return (
    <li className="rounded-xl border border-border p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11.5px] font-semibold text-muted-foreground">CA-{a.id}</span>
        <Badge tone={ACTION_STATUS_TONE[a.status] || 'grey'}>{ACTION_STATUS_LABEL[a.status] || a.status}</Badge>
        {overdue && <Badge tone="rose">Overdue</Badge>}
      </div>
      <p className="mt-1 whitespace-pre-line text-[13px] font-semibold leading-snug">{a.request}</p>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-muted-foreground">
        {supplier && <span>{supplier}</span>}
        {a.owner && <span>Owner {a.owner}</span>}
        {a.due_date && <span className={cn(overdue && 'font-semibold text-[#9f1239]')}>Due {dayText(a.due_date)}</span>}
        {a.days_open != null && <span>{a.days_open} day{a.days_open === 1 ? '' : 's'} open</span>}
      </div>
      {!edit && (a.supplier_response || a.resolution_check) && (
        <dl className="mt-2 space-y-1 text-[12.5px]">
          {a.supplier_response && (
            <div>
              <dt className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Supplier response</dt>
              <dd className="whitespace-pre-line">{a.supplier_response}</dd>
            </div>
          )}
          {a.resolution_check && (
            <div>
              <dt className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">YQ resolution check</dt>
              <dd className="whitespace-pre-line">{a.resolution_check}</dd>
            </div>
          )}
        </dl>
      )}
      {canEdit &&
        (edit ? (
          <div className="mt-2 space-y-2">
            <div>
              <label htmlFor={`resp-${a.id}`} className={LABEL}>Supplier response</label>
              <textarea id={`resp-${a.id}`} rows={3} value={response} onChange={(e) => setResponse(e.target.value)} maxLength={LIMIT.response} className={TEXTAREA} />
            </div>
            <div>
              <label htmlFor={`check-${a.id}`} className={LABEL}>YQ resolution check</label>
              <textarea id={`check-${a.id}`} rows={2} value={check} onChange={(e) => setCheck(e.target.value)} maxLength={LIMIT.check} className={TEXTAREA} placeholder="What YQ checked to confirm the fix" />
            </div>
            <div>
              <label htmlFor={`status-${a.id}`} className={LABEL}>Action status</label>
              <select id={`status-${a.id}`} value={status} onChange={(e) => setStatus(e.target.value as MiActionStatus)} className={cn(SELECT, 'w-full')}>
                {(Object.keys(ACTION_STATUS_LABEL) as MiActionStatus[]).map((s) => <option key={s} value={s}>{ACTION_STATUS_LABEL[s]}</option>)}
              </select>
            </div>
            <div className="flex gap-2">
              <button type="button" disabled={!Object.keys(body).length || m.isPending} onClick={() => m.mutate()} className={BTN}>
                {m.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save
              </button>
              <button type="button" onClick={() => setEdit(false)} className={BTN_OUTLINE}>Cancel</button>
            </div>
          </div>
        ) : (
          <button type="button" onClick={() => setEdit(true)} className={cn(BTN_GHOST, 'mt-1.5')}>
            Record the response or the check
          </button>
        ))}
    </li>
  )
}

/* ───────────────────────── merge a duplicate ───────────────────────── */

function MergePanel({
  c,
  suggestions,
  onMerged,
  onError,
}: {
  c: MiCase
  suggestions: MiCaseRow[]
  onMerged: (into: number) => void
  onError: (fallback: string) => (e: unknown) => void
}) {
  const [target, setTarget] = useState('')
  const [sure, setSure] = useState(false)
  const n = Number(target)
  const valid = Number.isInteger(n) && n > 0 && n !== c.id
  const m = useMutation({
    mutationFn: () => apiPost(`/market-intel/cases/${c.id}/merge`, { into_id: n }),
    onSuccess: () => onMerged(n),
    onError: onError('Could not merge the cases.'),
  })
  return (
    <section className={CARD}>
      <h2 className="mb-1.5 flex items-center gap-1.5 font-display text-[15px] font-bold">
        <GitMerge size={15} aria-hidden="true" /> Merge a duplicate
      </h2>
      <p className="mb-2 text-[12px] text-muted-foreground">The same problem reported twice: merge this case into the other one. Its sightings move there; this case stays, marked merged.</p>
      {suggestions.length > 0 && (
        <div className="mb-3 space-y-2">
          <div className={LABEL}>Open cases like this one</div>
          {suggestions.slice(0, 5).map((s) => (
            <div key={s.id} className={cn('rounded-2xl ring-2 ring-transparent transition', String(s.id) === target && 'ring-[#6D4091]')}>
              <CaseCard r={s} compact />
              <button type="button" onClick={() => { setTarget(String(s.id)); setSure(false) }} className={cn(BTN_GHOST, 'mt-0.5')}>
                Merge into case {s.id}
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input type="number" inputMode="numeric" min={1} value={target} onChange={(e) => { setTarget(e.target.value); setSure(false) }} placeholder="Case number" aria-label="Merge into case number" className={cn(INPUT, 'w-36')} />
        {!sure ? (
          <button type="button" disabled={!valid} onClick={() => setSure(true)} className={BTN_OUTLINE}>Merge…</button>
        ) : (
          <button type="button" disabled={!valid || m.isPending} onClick={() => m.mutate()} className={BTN}>
            {m.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Yes, merge into case {n}
          </button>
        )}
      </div>
    </section>
  )
}
