import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Barcode,
  Camera,
  CheckCircle2,
  ChevronRight,
  Eye,
  EyeOff,
  FileSpreadsheet,
  History,
  ImagePlus,
  Inbox,
  Library,
  ListChecks,
  Loader2,
  MessageSquareReply,
  PackageCheck,
  Radar,
  Search,
  Send,
  Siren,
  Sparkles,
  Store,
  UserRound,
  X,
} from 'lucide-react'
import { apiGet, apiPatch, apiPost, apiUpload } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { errorText } from '@/lib/errorText'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/Toast'
import { LoadError } from '@/components/LoadError'
import { PageHeader } from '@/components/PageHeader'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import {
  ACTIONS,
  fetchLists,
  KINDS,
  preparePhoto,
  priceRange,
  shortDate,
  STATUS_LABEL,
  type MiAction,
  type MiBoard,
  type MiCluster,
  type MiItemDetail,
  type MiMine,
  type MiObservation,
  type MiReview,
  type MiStatus,
  type ReadyPhoto,
} from '@/lib/marketIntel'
import { Cover, Empty, Fact, Loading, NotReady, Pager, PhotoTile, Tabs } from './market-intel/ui'
import { INPUT, qs, SELECT, sinceDay, useMiRoles } from './market-intel/util'

/* the office's v2 tabs load on their first open: a rep's phone never downloads them */
const CasesTab = lazy(() => import('./market-intel/CasesTab').then((m) => ({ default: m.CasesTab })))
const ListsTab = lazy(() => import('./market-intel/ListsTab').then((m) => ({ default: m.ListsTab })))
const ReportsTab = lazy(() => import('./market-intel/ReportsTab').then((m) => ({ default: m.ReportsTab })))

/**
 * /market-intel — Market Intelligence (release R7b, plan §18.2; v2 in release r8m, plan Part F),
 * replacing Product Finds and Field Notes in the nav (their pages still open at /finds and /field-notes).
 *
 *  • Office / management: the board — clusters, not photos ("7 sightings of Brand X 20W charger at
 *    BHD 2.500–3.000 across 5 shops, 3 reps, first seen 12 Oct"), 320 px thumbnails, filters and pages
 *    over the true count; the review queue (new items + photo-only sightings to identify); the quality
 *    CASES (Safety · New · Need info · Photos to identify; each opens /market-intel/cases/:id); the
 *    imported Finds library; the admin's LISTS (suppliers, brands, issue types); and the monthly
 *    SUPPLIER REPORTS (build, edit, approve, download). Reviewers move statuses and edit; management
 *    reads, approves an Opportunity or a supplier report, and downloads.
 *  • A salesman: "My reports" (his own sightings, what became of each in plain words, and the office's
 *    questions, answered in place with a line and a photo) and what the team has seen, aggregated,
 *    never another rep's name. The Spotted button is the way in. He never sees Cases, Lists or reports.
 */

const STATUS_TONE: Record<MiStatus, BadgeTone> = {
  new: 'accent',
  researching: 'amber',
  opportunity: 'green',
  approved: 'ink',
  rejected: 'grey',
  merged: 'grey',
}

const SINCE: { value: string; label: string }[] = [
  { value: '', label: 'Any time' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
]

const BOARD_PAGE = 48
const REVIEW_PAGE = 60

export default function MarketIntel() {
  const { me } = useAuth()
  return me?.role === 'salesman' ? <RepSignals /> : <OfficeBoard />
}

/* ───────────────────────── shared bits ───────────────────────── */

function ClusterCard({ c, onOpen, showMine }: { c: MiCluster; onOpen?: () => void; showMine?: boolean }) {
  const range = priceRange(c.price_min, c.price_max)
  const body = (
    <>
      <div className="relative aspect-[4/3] overflow-hidden bg-muted">
        {/* the 320 px thumbnail (r8m); an older API sends only the master */}
        <Cover url={c.thumb_url || c.cover_thumb_url || c.cover_url} alt={c.title || c.kind_label} />
        <span className="absolute left-2 right-2 top-2 flex">
          <Badge tone={STATUS_TONE[c.status]}>{c.status_label}</Badge>
        </span>
        {c.photos > 1 && (
          <span className="absolute bottom-2 right-2 inline-flex items-center gap-1 rounded-full bg-black/55 px-2 py-0.5 text-[10.5px] font-semibold text-white">
            <Camera size={11} aria-hidden="true" /> {c.photos}
          </span>
        )}
      </div>
      <div className="space-y-1.5 p-3">
        {showMine && c.mine && <div className="text-[11.5px] font-bold text-[#6D4091]">You spotted this</div>}
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="grey">{c.kind_label}</Badge>
          {c.yq_item_code ? <Badge tone="green">YQ {c.yq_item_code}</Badge> : c.kind === 'new_product' ? <Badge tone="amber">Gap</Badge> : null}
          {c.system_signals > 0 && <Badge tone="accent">Marketplace demand</Badge>}
        </div>
        <div className="line-clamp-2 text-[14px] font-semibold leading-snug text-foreground">{c.title || `${c.kind_label}, not named yet`}</div>
        {(c.brand || c.category) && (
          <div className="truncate text-[12px] text-muted-foreground">{[c.brand, c.category].filter(Boolean).join(' · ')}</div>
        )}
        <div className="text-[12px] leading-snug text-muted-foreground">{c.summary}</div>
        {range && <div className="font-display text-[14px] font-bold tabular-nums text-[#6D4091]">{range}</div>}
      </div>
    </>
  )
  return onOpen ? (
    <button type="button" onClick={onOpen} className="overflow-hidden rounded-2xl border border-border bg-card text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      {body}
    </button>
  ) : (
    <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">{body}</div>
  )
}

function SightingRow({ o, action, showBy = true, words }: { o: MiObservation; action?: ReactNode; showBy?: boolean; words?: string | null }) {
  const photo = o.photos[0]
  const people = Boolean(photo && (photo.has_people || photo.has_people_flag))
  const status = words || o.status_label
  const issue = o.issue_type?.label_en || o.issue_label
  return (
    <li className="flex gap-3 rounded-2xl border border-border bg-card p-3">
      <div className="h-20 w-20 shrink-0 overflow-hidden rounded-xl bg-muted">
        {people ? (
          <span className="grid h-full w-full place-items-center bg-muted text-[11px] text-muted-foreground">People</span>
        ) : (
          <Cover url={photo?.thumb_url || photo?.url} alt={o.title || o.kind_label} />
        )}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="grey">{o.kind_label}</Badge>
          {status && <Badge tone={o.open_question ? 'amber' : o.item_status ? STATUS_TONE[o.item_status] : 'accent'}>{status}</Badge>}
          {issue && <Badge tone="accent">{issue}</Badge>}
          {o.safety_flag && <Badge tone="rose">Safety</Badge>}
          {o.near_duplicate_of ? <Badge tone="amber">Photo seen before</Badge> : null}
          {o.photos.length > 1 && <span className="text-[11px] text-muted-foreground">{o.photos.length} photos</span>}
        </div>
        <div className="line-clamp-2 text-[13.5px] font-semibold leading-snug">{o.item_title || o.title || o.note || 'Photo only'}</div>
        {o.note && (o.item_title || o.title) && <div className="line-clamp-2 text-[12.5px] text-muted-foreground">{o.note}</div>}
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-muted-foreground">
          <span>{shortDate(o.client_observed_at || o.observed_at)}</span>
          {o.price_bhd && <span className="font-semibold tabular-nums text-foreground">BHD {o.price_bhd}</span>}
          {o.shop_name && (
            <span className="inline-flex items-center gap-1">
              <Store size={11} aria-hidden="true" /> {o.shop_name}
              {o.area ? ` · ${o.area}` : ''}
            </span>
          )}
          {showBy && o.by && (
            <span className="inline-flex items-center gap-1">
              <UserRound size={11} aria-hidden="true" /> {o.by}
            </span>
          )}
          {o.demand_level && <span>Demand: {o.demand_level}{o.demand_qty ? ` (${o.demand_qty})` : ''}</span>}
          {!o.demand_level && o.demand_qty ? <span>Qty {o.demand_qty}</span> : null}
          {o.qty_affected ? <span>{o.qty_affected} affected</span> : null}
          {o.barcode && <span className="tabular-nums">Barcode {o.barcode}</span>}
        </div>
        {action}
      </div>
    </li>
  )
}

/* ───────────────────────── a salesman: my reports ───────────────────────── */

function RepSignals() {
  const [tab, setTab] = useState<'mine' | 'team'>('mine')
  const mineQ = useQuery({ queryKey: ['mi-mine'], queryFn: () => apiGet<MiMine>('/market-intel/mine') })
  const teamQ = useQuery({ queryKey: ['mi-board', 'rep'], queryFn: () => apiGet<MiBoard>('/market-intel/items'), enabled: tab === 'team' })
  const asked = (mineQ.data?.observations || []).filter((o) => o.open_question).length
  return (
    <div className="mx-auto max-w-5xl px-4 pb-24 pt-4 lg:px-8 lg:py-8">
      <div className="mb-4">
        <h1 className="font-display text-[24px] font-bold leading-tight tracking-tight lg:text-[28px]">Market Intel</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">What you reported from the market, and what the office made of it. Tap Spotted to add one.</p>
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: 'mine', label: 'My reports', count: mineQ.data?.observations?.length, icon: <Camera size={15} aria-hidden="true" /> },
          { value: 'team', label: 'Seen by the team', icon: <Radar size={15} aria-hidden="true" /> },
        ]}
      />
      {tab === 'mine' && asked > 0 && (
        <div role="status" className="mb-4 flex items-center gap-3 rounded-2xl border border-[#f1dfb8] bg-[#fdf8ee] px-4 py-3 text-[13.5px] text-[#7a4d0a]">
          <MessageSquareReply size={18} className="shrink-0" aria-hidden="true" />
          <span>
            <span className="font-bold">The office asked you {asked === 1 ? 'a question' : `${asked} questions`}.</span> Answer below; a photo helps.
          </span>
        </div>
      )}
      {tab === 'mine' &&
        (mineQ.isError ? (
          <LoadError error={mineQ.error} onRetry={() => mineQ.refetch()} isRetrying={mineQ.isFetching} />
        ) : mineQ.isLoading ? (
          <Loading />
        ) : !mineQ.data?.available ? (
          <NotReady hint={mineQ.data?.hint} />
        ) : mineQ.data.observations.length === 0 ? (
          <Empty icon={<Camera size={18} aria-hidden="true" />}>
            Nothing yet. Tap <span className="font-semibold text-foreground">Spotted</span> when a product has a quality problem, a shop asks for something, or you see a new product, a competitor's price or a promotion.
          </Empty>
        ) : (
          <ul className="space-y-2.5">
            {mineQ.data.observations.map((o) => (
              <SightingRow
                key={o.id}
                o={o}
                showBy={false}
                words={o.case_status_words}
                action={
                  o.open_question ? (
                    <AnswerBox obs={o} />
                  ) : o.item_shops && o.item_shops > 1 ? (
                    <div className="text-[11.5px] font-semibold text-primary">Seen in {o.item_shops} shops</div>
                  ) : undefined
                }
              />
            ))}
          </ul>
        ))}
      {tab === 'team' &&
        (teamQ.isError ? (
          <LoadError error={teamQ.error} onRetry={() => teamQ.refetch()} isRetrying={teamQ.isFetching} />
        ) : teamQ.isLoading ? (
          <Loading />
        ) : !teamQ.data?.available ? (
          <NotReady hint={teamQ.data?.hint} />
        ) : teamQ.data.items.length === 0 ? (
          <Empty icon={<Radar size={18} aria-hidden="true" />}>Nothing spotted by the team yet.</Empty>
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {teamQ.data.items.map((c) => (
              <ClusterCard key={c.item_id} c={c} showMine />
            ))}
          </div>
        ))}
    </div>
  )
}

/**
 * The office's question on one of the rep's own reports, answered in place: a line and, if it helps,
 * one photo (resized on the phone like a capture). POST /market-intel/observations/{id}/answer; the
 * case goes back to the office's review.
 */
function AnswerBox({ obs }: { obs: MiObservation }) {
  const toast = useToast()
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [text, setText] = useState('')
  const [photo, setPhoto] = useState<ReadyPhoto | null>(null)
  const [preparing, setPreparing] = useState(false)
  const [busy, setBusy] = useState(false)
  // the preview is an object URL: let it go with the box
  const photoRef = useRef<ReadyPhoto | null>(null)
  useEffect(() => {
    photoRef.current = photo
  }, [photo])
  useEffect(
    () => () => {
      if (photoRef.current) URL.revokeObjectURL(photoRef.current.url)
    },
    [],
  )

  async function pick(list: FileList | null) {
    const f = list?.[0]
    if (!f) return
    setPreparing(true)
    try {
      const ready = await preparePhoto(f, 0)
      if (photo) URL.revokeObjectURL(photo.url)
      setPhoto({ ...ready, name: `answer-${obs.id}.${ready.name.split('.').pop() || 'webp'}` })
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not read that photo.', 'error')
    } finally {
      setPreparing(false)
    }
  }

  function dropPhoto() {
    if (photo) URL.revokeObjectURL(photo.url)
    setPhoto(null)
  }

  async function send() {
    setBusy(true)
    const form = new FormData()
    form.append('text', text.trim())
    if (photo) form.append('photo', photo.blob, photo.name)
    try {
      await apiUpload(`/market-intel/observations/${obs.id}/answer`, form)
      toast('Answer sent. The office takes it from here.', 'success')
      setText('')
      dropPhoto()
      qc.invalidateQueries({ queryKey: ['mi-mine'] })
    } catch (e) {
      toast(errorText(e, 'Could not send the answer. Please try again.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-2 space-y-2 rounded-xl border border-[#f1dfb8] bg-[#fdf8ee] p-3">
      <p className="text-[13px] leading-snug text-[#7a4d0a]">
        <span className="font-bold">The office asks:</span> {obs.open_question}
      </p>
      <textarea
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={1000}
        placeholder="Your answer"
        aria-label="Your answer"
        className="min-h-[64px] w-full resize-none rounded-xl border border-[#E2DCEA] bg-white px-3 py-2 text-[14px] leading-snug text-[#1A1428] placeholder:text-[#9a93ad] focus:border-[#824FAB] focus:outline-none focus:ring-2 focus:ring-[#824FAB]/25"
      />
      <input ref={fileRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => { void pick(e.target.files); e.target.value = '' }} />
      <div className="flex flex-wrap items-center gap-2">
        {photo ? (
          <span className="relative">
            <img src={photo.url} alt="Your photo" className="h-14 w-14 rounded-lg border border-[#E2DCEA] object-cover" />
            <button type="button" onClick={dropPhoto} aria-label="Remove the photo" className="absolute -right-1.5 -top-1.5 grid h-6 w-6 place-items-center rounded-full bg-[#1A1428] text-white ring-2 ring-white">
              <X size={12} aria-hidden="true" />
            </button>
          </span>
        ) : (
          <button type="button" disabled={preparing} onClick={() => fileRef.current?.click()} className="inline-flex h-11 items-center gap-1.5 rounded-xl border border-[#E2DCEA] bg-white px-3 text-[13px] font-semibold text-[#4B2C66] hover:bg-[#F7F3FA] disabled:opacity-50">
            {preparing ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <ImagePlus size={15} aria-hidden="true" />} Add a photo
          </button>
        )}
        <button
          type="button"
          disabled={busy || preparing || (!text.trim() && !photo)}
          onClick={() => void send()}
          className="ml-auto inline-flex h-11 items-center gap-1.5 rounded-xl bg-[#6D4091] px-4 text-[13.5px] font-semibold text-white hover:bg-[#5A3478] disabled:opacity-45"
        >
          {busy ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Send size={15} aria-hidden="true" />} Send answer
        </button>
      </div>
    </div>
  )
}

/* ───────────────────────── the office ───────────────────────── */

interface Filters {
  q: string
  kind: string
  status: string
  brand: string
  category: string
  rep: string
  area: string
  since: string
}

const NO_FILTERS: Filters = { q: '', kind: '', status: '', brand: '', category: '', rep: '', area: '', since: '' }

type OfficeTab = 'board' | 'review' | 'cases' | 'library' | 'lists' | 'reports'
const OFFICE_TABS: readonly OfficeTab[] = ['board', 'review', 'cases', 'library', 'lists', 'reports']

function OfficeBoard() {
  const [params, setParams] = useSearchParams()
  const [f, setF] = useState<Filters>(NO_FILTERS)
  const [page, setPage] = useState(0)
  const [reviewPage, setReviewPage] = useState(0)
  const [openId, setOpenId] = useState<number | null>(null)
  const [identify, setIdentify] = useState<MiObservation | null>(null)
  const [libPage, setLibPage] = useState(0)
  const query = qs({
    q: f.q.trim(),
    kind: f.kind,
    status: f.status,
    brand: f.brand,
    category: f.category,
    rep: f.rep,
    area: f.area,
    since: sinceDay(f.since),
    offset: page * BOARD_PAGE,
    limit: BOARD_PAGE,
  })
  const boardQ = useQuery({ queryKey: ['mi-board', query], queryFn: () => apiGet<MiBoard>(`/market-intel/items${query}`), placeholderData: keepPreviousData })
  const data = boardQ.data
  const caps = data?.capabilities
  const roles = useMiRoles(caps)
  const allowed = OFFICE_TABS.filter((t) => (t !== 'lists' || roles.admin) && (t !== 'reports' || roles.review || roles.approve))
  const want = params.get('tab') as OfficeTab | null
  const tab: OfficeTab = want && allowed.includes(want) ? want : 'board'
  const setTab = (t: OfficeTab) => setParams(t === 'board' ? {} : { tab: t }, { replace: true })

  const reviewQ = useQuery({
    queryKey: ['mi-review', 'queue', reviewPage],
    queryFn: () => apiGet<MiReview>(`/market-intel/review?limit=${REVIEW_PAGE}&offset=${reviewPage * REVIEW_PAGE}`),
    enabled: tab === 'review',
    placeholderData: keepPreviousData,
  })
  const libQ = useQuery({
    queryKey: ['mi-review', 'library', libPage],
    queryFn: () => apiGet<MiReview>(`/market-intel/review?library=true&limit=48&offset=${libPage * 48}`),
    enabled: tab === 'library',
  })
  // the admin's lists: the Cases filters and the report builder's suppliers (one cache with the capture)
  const listsQ = useQuery({ queryKey: ['mi-lists'], queryFn: () => fetchLists(), enabled: tab === 'cases' || tab === 'reports', staleTime: 10 * 60_000, retry: 1 })
  const lists = listsQ.data?.available ? listsQ.data : null
  const facets = data?.facets || {}
  const set = (k: keyof Filters) => (v: string) => {
    setF((cur) => ({ ...cur, [k]: v }))
    setPage(0)
  }
  const filtered = Object.values(f).some(Boolean)

  return (
    <div>
      <PageHeader title="Market Intel" subtitle="What the field sees: products, competitor prices, promotions and shops' asks on the board; quality and packaging problems as cases." />

      {data && !data.available ? (
        <NotReady hint={data.hint} />
      ) : (
        <>
          {data?.queue && (
            <div className="mb-5 grid grid-cols-3 gap-2 sm:gap-3">
              {[
                { label: 'Items on the board', value: data.count ?? data.items.length, icon: <Radar size={16} aria-hidden="true" /> },
                { label: 'New to review', value: data.queue.new_items, icon: <Inbox size={16} aria-hidden="true" /> },
                { label: 'Photos to identify', value: data.queue.unassigned, icon: <Camera size={16} aria-hidden="true" /> },
              ].map((k) => (
                <div key={k.label} className="rounded-2xl border border-[#E6DAF0] bg-gradient-to-br from-[#FAF7FC] to-[#F1E9F7] p-3 sm:p-4">
                  <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[#6D4091]">{k.icon}{k.label}</div>
                  <div className="mt-1 font-display text-[22px] font-bold tabular-nums text-[#1A1428] sm:text-[26px]">{k.value}</div>
                </div>
              ))}
            </div>
          )}

          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { value: 'board' as const, label: 'Board', icon: <Radar size={15} aria-hidden="true" /> },
              { value: 'review' as const, label: 'Review queue', count: data?.queue ? data.queue.new_items + data.queue.unassigned : null, icon: <Inbox size={15} aria-hidden="true" /> },
              { value: 'cases' as const, label: 'Cases', icon: <Siren size={15} aria-hidden="true" /> },
              { value: 'library' as const, label: 'Finds library', count: data?.queue?.library, icon: <Library size={15} aria-hidden="true" /> },
              { value: 'lists' as const, label: 'Lists', icon: <ListChecks size={15} aria-hidden="true" /> },
              { value: 'reports' as const, label: 'Supplier reports', icon: <FileSpreadsheet size={15} aria-hidden="true" /> },
            ].filter((t) => allowed.includes(t.value))}
          />

          {tab === 'board' && (
            <>
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <div className="relative min-w-[200px] flex-1">
                  <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                  <input value={f.q} onChange={(e) => set('q')(e.target.value)} placeholder="Search name, brand, YQ code or barcode" aria-label="Search" className={cn(INPUT, 'pl-9')} />
                </div>
                <select aria-label="Kind" value={f.kind} onChange={(e) => set('kind')(e.target.value)} className={SELECT}>
                  <option value="">All kinds</option>
                  {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
                </select>
                <select aria-label="Status" value={f.status} onChange={(e) => set('status')(e.target.value)} className={SELECT}>
                  <option value="">Open and decided</option>
                  {(facets.statuses || []).map((s) => <option key={s.value} value={s.value}>{s.label} ({s.count})</option>)}
                </select>
                <select aria-label="Category" value={f.category} onChange={(e) => set('category')(e.target.value)} className={SELECT}>
                  <option value="">All categories</option>
                  {(facets.categories || []).map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <select aria-label="Brand" value={f.brand} onChange={(e) => set('brand')(e.target.value)} className={SELECT}>
                  <option value="">All brands</option>
                  {(facets.brands || []).map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
                <select aria-label="Rep" value={f.rep} onChange={(e) => set('rep')(e.target.value)} className={SELECT}>
                  <option value="">All reps</option>
                  {(facets.reps || []).map((r) => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
                </select>
                <select aria-label="Area" value={f.area} onChange={(e) => set('area')(e.target.value)} className={SELECT}>
                  <option value="">All areas</option>
                  {(facets.areas || []).map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
                <select aria-label="Seen" value={f.since} onChange={(e) => set('since')(e.target.value)} className={SELECT}>
                  {SINCE.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                {filtered && (
                  <button type="button" onClick={() => { setF(NO_FILTERS); setPage(0) }} className="h-10 rounded-xl px-3 text-[13px] font-semibold text-primary hover:bg-accent">Clear</button>
                )}
              </div>
              {boardQ.isError ? (
                <LoadError error={boardQ.error} onRetry={() => boardQ.refetch()} isRetrying={boardQ.isFetching} />
              ) : boardQ.isLoading ? (
                <Loading className="h-48" />
              ) : !data?.items.length ? (
                <Empty icon={<Radar size={18} aria-hidden="true" />}>
                  {filtered ? 'Nothing matches these filters.' : 'Nothing on the board yet. Reps add sightings with the Spotted button.'}
                </Empty>
              ) : (
                <>
                  <div className={cn('grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4', boardQ.isFetching && boardQ.isPlaceholderData && 'opacity-70')}>
                    {data.items.map((c) => <ClusterCard key={c.item_id} c={c} onOpen={() => setOpenId(c.item_id)} />)}
                  </div>
                  <Pager offset={page * BOARD_PAGE} limit={BOARD_PAGE} count={data.count ?? data.items.length} onPage={(o) => setPage(Math.floor(o / BOARD_PAGE))} />
                </>
              )}
            </>
          )}

          {tab === 'review' &&
            (reviewQ.isError ? (
              <LoadError error={reviewQ.error} onRetry={() => reviewQ.refetch()} isRetrying={reviewQ.isFetching} />
            ) : reviewQ.isLoading ? (
              <Loading className="h-48" />
            ) : (
              <div className="space-y-6">
                <section>
                  <h2 className="mb-2 font-display text-[15px] font-bold">New items</h2>
                  {reviewQ.data?.new_items.length ? (
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                      {reviewQ.data.new_items.map((c) => <ClusterCard key={c.item_id} c={c} onOpen={() => setOpenId(c.item_id)} />)}
                    </div>
                  ) : (
                    <Empty icon={<CheckCircle2 size={18} aria-hidden="true" />}>No new items waiting.</Empty>
                  )}
                </section>
                <section>
                  <h2 className="mb-2 font-display text-[15px] font-bold">Photos to identify</h2>
                  {reviewQ.data?.unassigned.length ? (
                    <>
                      <ul className="grid gap-2.5 lg:grid-cols-2">
                        {reviewQ.data.unassigned.map((o) => (
                          <SightingRow key={o.id} o={o} action={roles.review ? <IdentifyButton onClick={() => setIdentify(o)} /> : undefined} />
                        ))}
                      </ul>
                      <Pager offset={reviewPage * REVIEW_PAGE} limit={REVIEW_PAGE} count={reviewQ.data.count} onPage={(o) => setReviewPage(Math.floor(o / REVIEW_PAGE))} />
                    </>
                  ) : (
                    <Empty icon={<CheckCircle2 size={18} aria-hidden="true" />}>Every photo is identified.</Empty>
                  )}
                </section>
              </div>
            ))}

          {tab === 'cases' && (
            <Suspense fallback={<Loading />}>
              <CasesTab lists={lists} />
            </Suspense>
          )}

          {tab === 'library' &&
            (libQ.isError ? (
              <LoadError error={libQ.error} onRetry={() => libQ.refetch()} isRetrying={libQ.isFetching} />
            ) : libQ.isLoading ? (
              <Loading className="h-48" />
            ) : !libQ.data?.unassigned.length ? (
              <Empty icon={<Library size={18} aria-hidden="true" />}>The old Finds board has not been copied in yet (scripts/market_intel_import.py).</Empty>
            ) : (
              <>
                <p className="mb-3 text-[12.5px] text-muted-foreground">
                  The {libQ.data.count} sightings copied from the old Finds board and Field Notes, kept as a baseline: new photos are checked against them for near-duplicates.
                </p>
                <ul className="grid gap-2.5 lg:grid-cols-2">
                  {libQ.data.unassigned.map((o) => (
                    <SightingRow key={o.id} o={o} action={roles.review ? <IdentifyButton onClick={() => setIdentify(o)} /> : undefined} />
                  ))}
                </ul>
                <Pager offset={libPage * 48} limit={48} count={libQ.data.count} onPage={(o) => setLibPage(Math.floor(o / 48))} />
              </>
            ))}

          {tab === 'lists' && (
            <Suspense fallback={<Loading />}>
              <ListsTab />
            </Suspense>
          )}

          {tab === 'reports' && (
            <Suspense fallback={<Loading />}>
              <ReportsTab roles={roles} lists={lists} />
            </Suspense>
          )}
        </>
      )}

      <ItemDrawer id={openId} onClose={() => setOpenId(null)} boardItems={data?.items || []} />
      <IdentifySheet obs={identify} onClose={() => setIdentify(null)} boardItems={data?.items || []} />
    </div>
  )
}

function IdentifyButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="mt-1 inline-flex h-9 items-center gap-1 rounded-lg bg-accent px-3 text-[12.5px] font-semibold text-accent-foreground hover:bg-primary hover:text-primary-foreground">
      Identify <ChevronRight size={13} aria-hidden="true" />
    </button>
  )
}

function useInvalidate() {
  const qc = useQueryClient()
  return (id?: number | null) => {
    qc.invalidateQueries({ queryKey: ['mi-board'] })
    qc.invalidateQueries({ queryKey: ['mi-review'] })
    if (id) qc.invalidateQueries({ queryKey: ['mi-item', id] })
  }
}

/* ───────────────────────── identify a photo-only sighting ───────────────────────── */

function IdentifySheet({ obs, onClose, boardItems }: { obs: MiObservation | null; onClose: () => void; boardItems: MiCluster[] }) {
  const toast = useToast()
  const invalidate = useInvalidate()
  const [mode, setMode] = useState<'existing' | 'new'>('new')
  const [itemId, setItemId] = useState('')
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState('')
  const [category, setCategory] = useState('')
  const m = useMutation({
    mutationFn: () =>
      apiPost<{ ok: boolean; item_id: number }>(
        `/market-intel/observations/${obs?.id}/identify`,
        mode === 'existing' ? { item_id: Number(itemId) } : { title: title.trim(), kind: kind || obs?.kind || 'other', category: category || undefined },
      ),
    onSuccess: () => {
      toast('Sighting identified.', 'success')
      invalidate()
      setTitle('')
      setItemId('')
      onClose()
    },
    onError: (e) => toast(errorText(e, 'Could not identify it.'), 'error'),
  })
  const ok = mode === 'existing' ? !!itemId : title.trim().length > 0
  return (
    <Sheet
      open={!!obs}
      onClose={onClose}
      title="Identify this sighting"
      subtitle="Put it on an item already on the board, or name a new one."
      footer={
        <button type="button" disabled={!ok || m.isPending} onClick={() => m.mutate()} className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[14px] font-semibold text-white hover:bg-[#5A3478] disabled:opacity-45">
          {m.isPending && <Loader2 size={16} className="animate-spin" aria-hidden="true" />} Save
        </button>
      }
    >
      {obs && (
        <div className="space-y-4 px-4 py-4 sm:px-5">
          {obs.photos.length > 0 && (
            <div className="flex gap-2 overflow-x-auto">
              {obs.photos.map((p) => (
                <PhotoTile key={p.id} p={p} alt="Sighting photo" className="h-40 w-40" />
              ))}
            </div>
          )}
          {obs.note && <p className="text-[13px] text-muted-foreground">{obs.note}</p>}
          <div className="flex gap-2">
            {(['new', 'existing'] as const).map((v) => (
              <button key={v} type="button" onClick={() => setMode(v)} className={cn('h-10 flex-1 rounded-xl text-[13px] font-semibold', mode === v ? 'bg-[#6D4091] text-white' : 'bg-[#F3EDF8] text-[#4B2C66]')}>
                {v === 'new' ? 'New item' : 'Existing item'}
              </button>
            ))}
          </div>
          {mode === 'existing' ? (
            <select aria-label="Item" value={itemId} onChange={(e) => setItemId(e.target.value)} className={cn(SELECT, 'w-full')}>
              <option value="">Pick the item</option>
              {boardItems.map((c) => <option key={c.item_id} value={c.item_id}>{c.title || `Item ${c.item_id}`} · {c.kind_label}</option>)}
            </select>
          ) : (
            <div className="space-y-3">
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What is it? (brand, model, spec)" aria-label="Name" className={INPUT} />
              <div className="grid grid-cols-2 gap-2">
                <select aria-label="Kind" value={kind || obs.kind} onChange={(e) => setKind(e.target.value)} className={SELECT}>
                  {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
                </select>
                <input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Category" aria-label="Category" className={INPUT} />
              </div>
            </div>
          )}
        </div>
      )}
    </Sheet>
  )
}

/* ───────────────────────── the item ───────────────────────── */

function ItemDrawer({ id, onClose, boardItems }: { id: number | null; onClose: () => void; boardItems: MiCluster[] }) {
  const { me } = useAuth()
  // an admin may open the original of a photo flagged with people (the server signs its blurred copy otherwise)
  const [raw, setRaw] = useState(false)
  const q = useQuery({ queryKey: ['mi-item', id, raw], queryFn: () => apiGet<MiItemDetail>(`/market-intel/items/${id}${raw ? '?raw=1' : ''}`), enabled: !!id })
  const d = q.data
  return (
    <Sheet open={!!id} onClose={() => { setRaw(false); onClose() }} variant="drawer" title={d?.item.title || 'Item'} subtitle={d ? `${d.item.kind_label} · ${d.item.status_label}` : undefined}>
      {q.isError ? (
        <div className="p-4"><LoadError error={q.error} onRetry={() => q.refetch()} isRetrying={q.isFetching} /></div>
      ) : !d ? (
        <Loading className="h-48" />
      ) : (
        <ItemBody d={d} boardItems={boardItems} raw={raw} onRaw={me?.role === 'admin' ? setRaw : undefined} />
      )}
    </Sheet>
  )
}

function ItemBody({ d, boardItems, raw, onRaw }: { d: MiItemDetail; boardItems: MiCluster[]; raw: boolean; onRaw?: (v: boolean) => void }) {
  const toast = useToast()
  const invalidate = useInvalidate()
  const caps = d.capabilities
  const photos = useMemo(() => d.observations.flatMap((o) => o.photos.map((p) => ({ ...p, obs: o }))), [d.observations])
  const people = useMutation({
    mutationFn: (p: { id: number; flag: boolean }) => apiPost(`/market-intel/photos/${p.id}/people`, { flag: p.flag }),
    onSuccess: () => invalidate(d.item.id),
    onError: (e) => toast(errorText(e, 'Could not change the photo.'), 'error'),
  })
  const verify = useMutation({
    mutationFn: () => apiPatch(`/market-intel/items/${d.item.id}`, { verified: true }),
    onSuccess: () => {
      toast('AI reading marked verified.', 'success')
      invalidate(d.item.id)
    },
    onError: (e) => toast(errorText(e, 'Could not verify it.'), 'error'),
  })
  const c = d.cluster
  const anyPeople = photos.some((p) => p.has_people || p.has_people_flag)
  return (
    <div className="space-y-5 px-4 py-4 sm:px-5">
      {photos.length > 0 && (
        <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
          {photos.map((p) => (
            <PhotoTile
              key={p.id}
              p={p}
              alt={p.obs.title || 'Sighting photo'}
              raw={raw}
              className="h-44 w-44"
              canFlag={caps.can_review}
              onFlag={(flag) => people.mutate({ id: p.id, flag })}
            />
          ))}
        </div>
      )}
      {anyPeople && (
        <p className="-mt-3 flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground">
          Photos with people are blurred and kept out of reports.
          {onRaw && (
            <button type="button" onClick={() => onRaw(!raw)} className="inline-flex h-8 items-center gap-1 rounded-lg px-2 font-semibold text-primary hover:bg-accent">
              {raw ? <EyeOff size={12} aria-hidden="true" /> : <Eye size={12} aria-hidden="true" />} {raw ? 'Blur again' : 'Show originals (admin)'}
            </button>
          )}
        </p>
      )}

      {c && (
        <div className="rounded-2xl border border-[#E6DAF0] bg-gradient-to-br from-[#FAF7FC] to-[#F1E9F7] p-4">
          <div className="text-[13.5px] font-semibold leading-snug text-[#1A1428]">{c.summary}</div>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <Fact label="Sightings">{c.observations}</Fact>
            <Fact label="Shops">{c.shops}</Fact>
            <Fact label="Reps">{c.reps}</Fact>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <Fact label="Brand">{d.item.brand || '—'}</Fact>
        <Fact label="Category">{d.item.category || '—'}</Fact>
        <Fact label="Barcode">{d.item.barcode ? <span className="inline-flex items-center gap-1 tabular-nums"><Barcode size={13} aria-hidden="true" />{d.item.barcode}</span> : '—'}</Fact>
        <Fact label="Price seen">{priceRange(c?.price_min, c?.price_max) || '—'}</Fact>
      </div>

      {d.yq_item ? (
        <div className="flex items-center gap-3 rounded-2xl border border-[#cfe9da] bg-[#f3fbf6] p-3">
          <PackageCheck size={20} className="shrink-0 text-[#137a48]" aria-hidden="true" />
          <span className="min-w-0 text-[13px]">
            <span className="block font-semibold text-[#0f5a36]">Already in YQ: {d.yq_item.display_name}</span>
            <span className="text-[#137a48]">Code {d.yq_item.item_code}</span>
          </span>
        </div>
      ) : (
        d.gap && d.item.kind === 'new_product' && (
          <div className="rounded-2xl border border-[#f1dfb8] bg-[#fdf8ee] p-3 text-[13px] text-[#7a4d0a]">
            <span className="font-semibold">Gap:</span> not in the YQ catalog.
          </div>
        )
      )}

      {d.ai_suggestion && (
        <div className="rounded-2xl border border-border p-3">
          {caps.can_review && !d.ai_suggestion.verified && (
            <button
              type="button"
              disabled={verify.isPending}
              onClick={() => verify.mutate()}
              className="float-right -mr-1 -mt-1 inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[12px] font-semibold text-primary hover:bg-accent disabled:opacity-50"
            >
              {verify.isPending ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : <CheckCircle2 size={12} aria-hidden="true" />} Mark verified
            </button>
          )}
          <div className="mb-1.5 flex items-center gap-2">
            <Sparkles size={15} className="text-primary" aria-hidden="true" />
            <span className="text-[13px] font-semibold">AI reading of the photos</span>
            <Badge tone={d.ai_suggestion.verified ? 'green' : 'amber'}>{d.ai_suggestion.label}</Badge>
            {d.ai_suggestion.confidence != null && <span className="text-[11.5px] text-muted-foreground">confidence {Math.round(Number(d.ai_suggestion.confidence) * 100)}%</span>}
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12.5px]">
            {Object.entries(d.ai_suggestion.suggestion || {}).slice(0, 8).map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="truncate font-medium">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {(caps.can_review || caps.can_approve) && <Decide key={`${d.item.id}-${d.item.status}`} d={d} boardItems={boardItems} />}

      {d.shops && d.shops.length > 0 && (
        <section>
          <h3 className="mb-2 flex items-center gap-1.5 font-display text-[14px] font-bold"><Store size={15} aria-hidden="true" /> Shops</h3>
          <ul className="flex flex-wrap gap-1.5">
            {d.shops.map((s) => (
              <li key={`${s.shop}-${s.area}`} className="rounded-full bg-muted px-2.5 py-1 text-[12px]">
                {s.shop}{s.area ? ` · ${s.area}` : ''}{s.sightings > 1 ? ` ×${s.sightings}` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3 className="mb-2 flex items-center gap-1.5 font-display text-[14px] font-bold"><Camera size={15} aria-hidden="true" /> Sightings</h3>
        <ul className="space-y-2">
          {d.observations.map((o) => <SightingRow key={o.id} o={o} />)}
        </ul>
      </section>

      {d.decisions && d.decisions.length > 0 && (
        <section>
          <h3 className="mb-2 flex items-center gap-1.5 font-display text-[14px] font-bold"><History size={15} aria-hidden="true" /> Decisions</h3>
          <ol className="space-y-2 border-l-2 border-[#E6DAF0] pl-3">
            {d.decisions.map((x) => (
              <li key={x.id} className="text-[12.5px]">
                <div className="font-semibold">
                  {x.event === 'edit'
                    ? editLabel(x.detail)
                    : x.event === 'identify'
                      ? 'A photo was identified'
                      : x.event === 'merge'
                        ? 'Merged into another item'
                        : `${x.from_status ? STATUS_LABEL[x.from_status] : ''} → ${x.to_status ? STATUS_LABEL[x.to_status] : ''}`}
                  {x.action_label ? `: ${x.action_label}` : ''}
                </div>
                <div className="text-muted-foreground">
                  {x.actor}
                  {x.actor_role ? ` (${x.actor_role})` : ''} · {shortDate(x.created_at)}
                  {x.reason ? ` · “${x.reason}”` : ''}
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}

function editLabel(detail?: Record<string, unknown> | null): string {
  const d = detail || {}
  const people = d.photo_people as { to?: boolean } | undefined
  if (people) return people.to ? 'A photo was flagged: people in it' : 'A photo flag was removed'
  const verified = d.verified as { to?: boolean } | undefined
  if (verified && Object.keys(d).length === 1) return verified.to ? 'AI reading verified' : 'AI reading marked unverified again'
  const names: Record<string, string> = { title: 'name', yq_item_code: 'YQ code', verified: 'AI verification' }
  return `Edited ${Object.keys(d).map((k) => names[k] || k).join(', ')}`
}

/* ───────────────────────── decide: statuses, approval, edits ───────────────────────── */

function Decide({ d, boardItems }: { d: MiItemDetail; boardItems: MiCluster[] }) {
  const toast = useToast()
  const invalidate = useInvalidate()
  const caps = d.capabilities
  const [to, setTo] = useState<MiStatus | ''>('')
  const [action, setAction] = useState<MiAction | ''>('')
  const [target, setTarget] = useState('')
  const [reason, setReason] = useState('')
  const [edit, setEdit] = useState(false)
  const [fields, setFields] = useState({ title: d.item.title || '', brand: d.item.brand || '', category: d.item.category || '', barcode: d.item.barcode || '', yq_item_code: d.item.yq_item_code || '' })

  const done = (msg: string) => {
    toast(msg, 'success')
    invalidate(d.item.id)
    setTo('')
    setAction('')
    setTarget('')
    setReason('')
  }
  const onError = (e: unknown) => toast(errorText(e, 'Could not save the decision.'), 'error')
  const move = useMutation({
    mutationFn: () =>
      apiPatch(`/market-intel/items/${d.item.id}`, {
        status: to,
        ...(to === 'approved' ? { action } : {}),
        ...(to === 'merged' ? { merged_into: Number(target) } : {}),
        reason: reason.trim() || undefined,
      }),
    onSuccess: () => done('Decision saved.'),
    onError,
  })
  const approve = useMutation({
    mutationFn: () => apiPost(`/market-intel/items/${d.item.id}/approve`, { action, reason: reason.trim() || undefined }),
    onSuccess: () => done('Action approved.'),
    onError,
  })
  const save = useMutation({
    mutationFn: () => apiPatch(`/market-intel/items/${d.item.id}`, { ...fields, reason: reason.trim() || undefined }),
    onSuccess: () => {
      setEdit(false)
      done('Item updated.')
    },
    onError,
  })

  // management: the one decision it makes — approve an action on an Opportunity
  if (!caps.can_review) {
    if (d.item.status !== 'opportunity') return null
    return (
      <section className="space-y-2 rounded-2xl border border-[#cfe9da] bg-[#f3fbf6] p-3">
        <h3 className="font-display text-[14px] font-bold text-[#0f5a36]">Approve an action</h3>
        <select aria-label="Action" value={action} onChange={(e) => setAction(e.target.value as MiAction)} className={cn(SELECT, 'w-full')}>
          <option value="">Choose what to do</option>
          {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why (optional)" aria-label="Reason" className={INPUT} />
        <button type="button" disabled={!action || approve.isPending} onClick={() => approve.mutate()} className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#137a48] text-[13.5px] font-semibold text-white disabled:opacity-45">
          {approve.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Approve
        </button>
      </section>
    )
  }

  const next = d.item.next_statuses || []
  const needs = to === 'approved' ? !!action : to === 'merged' ? !!target : !!to
  return (
    <section className="space-y-3 rounded-2xl border border-border p-3">
      <div className="flex items-center justify-between">
        <h3 className="font-display text-[14px] font-bold">Decide</h3>
        <button type="button" onClick={() => setEdit((v) => !v)} className="h-8 rounded-lg px-2 text-[12.5px] font-semibold text-primary hover:bg-accent">{edit ? 'Close edit' : 'Edit details'}</button>
      </div>
      {next.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {next.map((s) => (
            <button key={s} type="button" onClick={() => setTo(to === s ? '' : s)} className={cn('h-9 rounded-full px-3 text-[12.5px] font-semibold', to === s ? 'bg-[#6D4091] text-white' : 'bg-[#F3EDF8] text-[#4B2C66] hover:bg-[#EADFF3]')}>
              {STATUS_LABEL[s]}
            </button>
          ))}
        </div>
      ) : (
        <p className="text-[12.5px] text-muted-foreground">This item is merged into item {d.item.merged_into}; its sightings moved there.</p>
      )}
      {to === 'approved' && (
        <select aria-label="Action" value={action} onChange={(e) => setAction(e.target.value as MiAction)} className={cn(SELECT, 'w-full')}>
          <option value="">Choose the action</option>
          {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
      )}
      {to === 'merged' && (
        <select aria-label="Duplicate of" value={target} onChange={(e) => setTarget(e.target.value)} className={cn(SELECT, 'w-full')}>
          <option value="">This duplicates…</option>
          {boardItems.filter((c) => c.item_id !== d.item.id).map((c) => <option key={c.item_id} value={c.item_id}>{c.title || `Item ${c.item_id}`} · {c.kind_label}</option>)}
        </select>
      )}
      {(to || edit) && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why (kept in the history)" aria-label="Reason" className={INPUT} />}
      {to && (
        <button type="button" disabled={!needs || move.isPending} onClick={() => move.mutate()} className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[13.5px] font-semibold text-white hover:bg-[#5A3478] disabled:opacity-45">
          {move.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Move to {STATUS_LABEL[to]}
        </button>
      )}
      {edit && (
        <div className="grid grid-cols-2 gap-2">
          {(
            [
              ['title', 'Name'],
              ['brand', 'Brand'],
              ['category', 'Category'],
              ['barcode', 'Barcode'],
              ['yq_item_code', 'YQ code'],
            ] as const
          ).map(([k, label]) => (
            <input key={k} value={fields[k]} onChange={(e) => setFields((cur) => ({ ...cur, [k]: e.target.value }))} placeholder={label} aria-label={label} className={cn(INPUT, k === 'title' && 'col-span-2')} />
          ))}
          <button type="button" disabled={save.isPending} onClick={() => save.mutate()} className="col-span-2 flex h-10 items-center justify-center gap-2 rounded-xl border border-[#6D4091] text-[13.5px] font-semibold text-[#6D4091] hover:bg-[#F3EDF8] disabled:opacity-45">
            {save.isPending && <Loader2 size={15} className="animate-spin" aria-hidden="true" />} Save details
          </button>
        </div>
      )}
    </section>
  )
}
