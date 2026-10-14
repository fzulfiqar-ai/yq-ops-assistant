import { useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { CheckCircle2, ChevronRight, ClipboardCheck, Inbox, Layers, MessageSquareReply, PackageOpen, Siren } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { LoadError } from '@/components/LoadError'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import {
  CASE_STATUSES,
  CASE_STATUS_LABEL,
  evidenceLabel,
  nameOf,
  notReady,
  shortDate,
  type MiCaseList,
  type MiCaseRow,
  type MiCaseTab,
  type MiLists,
} from '@/lib/marketIntel'
import { Cover, Empty, Loading, NotAvailableYet, Pager, Tabs } from './ui'
import { CASE_STATUS_TONE, dayText, INPUT, qs, SELECT } from './util'

/**
 * The Cases tab (release r8m, plan F3.3 "Review queue v2"): quality and packaging reports grouped into
 * cases — Safety (open safety cases, to be reviewed within one business day) · New · Need info (a
 * question is with the rep) · Photos to identify (no product linked yet) · All. Filters by status,
 * supplier, brand, issue and date; 30 rows a page with the true count. A row opens the case page.
 */

const PAGE = 30

interface CaseFilters {
  status: string
  supplier_id: string
  brand_id: string
  issue_type_id: string
  from: string
  to: string
}
const NO_FILTERS: CaseFilters = { status: '', supplier_id: '', brand_id: '', issue_type_id: '', from: '', to: '' }

const EMPTY: Record<MiCaseTab, string> = {
  safety: 'No open safety reports.',
  new: 'No new reports waiting.',
  need_info: 'No case is waiting on a rep.',
  identify: 'Every report is linked to a product.',
  all: 'No cases match these filters.',
}

export function CasesTab({ lists }: { lists: MiLists | null }) {
  const [tab, setTab] = useState<MiCaseTab>('new')
  const [f, setF] = useState<CaseFilters>(NO_FILTERS)
  const [offset, setOffset] = useState(0)
  const set = (k: keyof CaseFilters) => (v: string) => {
    setF((cur) => ({ ...cur, [k]: v }))
    setOffset(0)
  }
  const filtered = Object.values(f).some(Boolean)
  const query = qs({ tab, ...f, offset, limit: PAGE })
  const casesQ = useQuery({
    queryKey: ['mi-cases', query],
    queryFn: () => apiGet<MiCaseList>(`/market-intel/cases${query}`),
    placeholderData: keepPreviousData,
    retry: (n, e) => !notReady(e) && n < 1,
  })
  const data = casesQ.data
  const counts = data?.tabs || {}
  const issueTypes = (lists?.issue_types || []).filter((t) => t.kind === 'quality' || t.kind === 'packaging')

  if (casesQ.isError && notReady(casesQ.error)) return <NotAvailableYet what="Quality cases" />
  if (data && data.available === false) return <NotAvailableYet what="Quality cases" />

  return (
    <div>
      {tab !== 'safety' && (counts.safety || 0) > 0 && (
        <button
          type="button"
          onClick={() => { setTab('safety'); setOffset(0) }}
          className="mb-4 flex w-full items-center gap-3 rounded-2xl border border-[#f3c4cf] bg-[#fdecef] px-4 py-3 text-left text-[13.5px] text-[#9f1239] transition hover:bg-[#fbdbe1]"
        >
          <Siren size={18} className="shrink-0" aria-hidden="true" />
          <span className="flex-1">
            <span className="font-bold">
              {counts.safety} open safety report{counts.safety === 1 ? '' : 's'}.
            </span>{' '}
            Each is reviewed within one business day.
          </span>
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      )}

      <Tabs
        size="sm"
        value={tab}
        onChange={(v) => {
          setTab(v)
          setOffset(0)
        }}
        tabs={[
          { value: 'safety', label: 'Safety', count: counts.safety, urgent: true, icon: <Siren size={14} aria-hidden="true" /> },
          { value: 'new', label: 'New', count: counts.new, icon: <Inbox size={14} aria-hidden="true" /> },
          { value: 'need_info', label: 'Need info', count: counts.need_info, icon: <MessageSquareReply size={14} aria-hidden="true" /> },
          { value: 'identify', label: 'Photos to identify', count: counts.identify, icon: <PackageOpen size={14} aria-hidden="true" /> },
          { value: 'all', label: 'All', icon: <Layers size={14} aria-hidden="true" /> },
        ]}
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select aria-label="Status" value={f.status} onChange={(e) => set('status')(e.target.value)} className={SELECT}>
          <option value="">Any status</option>
          {CASE_STATUSES.map((s) => <option key={s} value={s}>{CASE_STATUS_LABEL[s]}</option>)}
        </select>
        <select aria-label="Issue" value={f.issue_type_id} onChange={(e) => set('issue_type_id')(e.target.value)} className={SELECT}>
          <option value="">Any issue</option>
          {issueTypes.map((t) => <option key={t.id} value={String(t.id)}>{t.label_en}{t.active === false ? ' (inactive)' : ''}</option>)}
        </select>
        <select aria-label="Supplier" value={f.supplier_id} onChange={(e) => set('supplier_id')(e.target.value)} className={SELECT}>
          <option value="">Any supplier</option>
          {(lists?.suppliers || []).map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
        </select>
        <select aria-label="Brand" value={f.brand_id} onChange={(e) => set('brand_id')(e.target.value)} className={SELECT}>
          <option value="">Any brand</option>
          {(lists?.brands || []).map((b) => <option key={b.id} value={String(b.id)}>{b.name}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
          From
          <input type="date" aria-label="From" value={f.from} onChange={(e) => set('from')(e.target.value)} className={cn(INPUT, 'w-auto')} />
        </label>
        <label className="flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
          To
          <input type="date" aria-label="To" value={f.to} onChange={(e) => set('to')(e.target.value)} className={cn(INPUT, 'w-auto')} />
        </label>
        {filtered && (
          <button type="button" onClick={() => { setF(NO_FILTERS); setOffset(0) }} className="h-10 rounded-xl px-3 text-[13px] font-semibold text-primary hover:bg-accent">
            Clear
          </button>
        )}
      </div>

      {casesQ.isError ? (
        <LoadError error={casesQ.error} onRetry={() => casesQ.refetch()} isRetrying={casesQ.isFetching} />
      ) : casesQ.isLoading || !data ? (
        <Loading />
      ) : !data.rows.length ? (
        <Empty icon={tab === 'safety' ? <CheckCircle2 size={18} aria-hidden="true" /> : <ClipboardCheck size={18} aria-hidden="true" />}>
          {filtered && tab !== 'all' ? 'Nothing in this tab matches these filters.' : EMPTY[tab]}
        </Empty>
      ) : (
        <>
          <ul className={cn('grid gap-2.5 lg:grid-cols-2', casesQ.isFetching && 'opacity-70')}>
            {data.rows.map((r) => (
              <li key={`${r.id}-${r.observation_id ?? ''}`}>
                <CaseCard r={r} />
              </li>
            ))}
          </ul>
          <Pager offset={offset} limit={PAGE} count={data.count ?? data.rows.length} onPage={setOffset} />
        </>
      )}
    </div>
  )
}

export function CaseCard({ r, compact = false }: { r: MiCaseRow; compact?: boolean }) {
  // a "Photos to identify" row may be a sighting: it opens its case when it has one
  const target = r.case_id ?? (r.observation_id ? null : r.id)
  const brand = nameOf(r.brand)
  const supplier = nameOf(r.supplier)
  const body = (
    <>
      <div className={cn('shrink-0 overflow-hidden rounded-xl bg-muted', compact ? 'h-14 w-14' : 'h-20 w-20')}>
        <Cover url={r.thumb_url} alt={r.title || 'Case photo'} />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          {r.safety && (
            <Badge tone="rose">
              <span className="inline-flex items-center gap-1"><Siren size={10} aria-hidden="true" /> Safety</span>
            </Badge>
          )}
          {r.status && <Badge tone={CASE_STATUS_TONE[r.status] || 'grey'}>{r.status_label || CASE_STATUS_LABEL[r.status] || r.status}</Badge>}
          {!compact && <Badge tone="grey">{r.evidence_label || evidenceLabel(r.evidence_level)}</Badge>}
          {r.issue_type?.label_en && <Badge tone="accent">{r.issue_type.label_en}</Badge>}
        </div>
        <div className="line-clamp-2 text-[14px] font-semibold leading-snug text-foreground">{r.title || 'Not named yet'}</div>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-muted-foreground">
          {target != null && <span className="tabular-nums">Case {target}</span>}
          {r.yq_item_code ? <span className="tabular-nums">YQ {r.yq_item_code}</span> : <span className="font-semibold text-[#96600d]">No product linked</span>}
          {(brand || supplier) && <span>{[brand, supplier].filter(Boolean).join(' · ')}</span>}
          {r.obs_count != null && (
            <span>
              {r.obs_count} report{r.obs_count === 1 ? '' : 's'}
              {r.shop_count ? ` · ${r.shop_count} shop${r.shop_count === 1 ? '' : 's'}` : ''}
            </span>
          )}
          {r.last_seen && <span>Last {shortDate(r.last_seen)}</span>}
          {r.due_date && <span className="font-semibold text-foreground">Due {dayText(r.due_date)}</span>}
          {r.assignee && <span>{r.assignee}</span>}
        </div>
      </div>
    </>
  )
  return target != null ? (
    <Link
      to={`/market-intel/cases/${target}`}
      className="flex h-full items-center gap-3 rounded-2xl border border-border bg-card p-3 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {body}
      <ChevronRight size={16} className="shrink-0 text-muted-foreground" aria-hidden="true" />
    </Link>
  ) : (
    <div className="flex h-full items-center gap-3 rounded-2xl border border-border bg-card p-3">{body}</div>
  )
}
