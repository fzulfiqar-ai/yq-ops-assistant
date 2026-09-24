import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Check, Eye, EyeOff, Link2, Loader2, PackageCheck, Users } from 'lucide-react'
import { apiGet, apiPatch, ApiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

/**
 * /upcoming — the office's "Coming soon" desk (plan §6b). The cards scripts/import_upcoming.py
 * inserted as drafts: publish or withdraw each one, set the arrival month (the marketplace label
 * derives from it and drops to "Arriving soon" once the month has passed), fix copy, and on
 * arrival link the catalog code — which retires the card automatically while that item is live.
 * The interest count is what the "Notify me" button collected. No price is set here: the price
 * arrives with the price book, like every other SKU.
 */

type Status = 'draft' | 'published' | 'arrived' | 'withdrawn'

interface Variant {
  label: string
  label_ar?: string | null
}

interface Item {
  id: number
  brand: string
  model_code: string
  category?: string | null
  name_en: string
  name_ar?: string | null
  spec_en?: string | null
  spec_ar?: string | null
  variants: Variant[]
  photo_url?: string | null
  photo_thumb_urls?: Record<string, string> | null
  box_url?: string | null
  shipment_ref?: string | null
  expected_month?: string | null
  expected_label_en?: string | null
  expected_label_ar?: string | null
  status: Status
  catalog_item_code?: string | null
  sort_order?: number | null
  retired?: boolean
  interest_count?: number
  interest_shops?: number
}

interface Resp {
  items: Item[]
  can_edit: boolean
  settings?: Record<string, string>
}

const STATUS_TONE: Record<Status, BadgeTone> = { draft: 'grey', published: 'green', arrived: 'accent', withdrawn: 'amber' }
const STATUS_LABEL: Record<Status, string> = { draft: 'Draft', published: 'Published', arrived: 'Arrived', withdrawn: 'Withdrawn' }
const FILTERS: (Status | 'all')[] = ['all', 'draft', 'published', 'arrived', 'withdrawn']

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    try {
      const j = JSON.parse(e.body) as { detail?: string }
      if (j.detail) return j.detail
    } catch {
      /* plain text */
    }
    return e.body || e.message
  }
  return e instanceof Error ? e.message : 'Could not save'
}

export default function Upcoming() {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['shop-upcoming'], queryFn: () => apiGet<Resp>('/shop/upcoming') })
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => apiPatch<Item>(`/shop/upcoming/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['shop-upcoming'] })
      toast('Saved', 'success')
    },
    onError: (e) => toast(errorText(e), 'error'),
  })
  const [filter, setFilter] = useState<Status | 'all'>('all')
  const [month, setMonth] = useState('')
  const [bulkBusy, setBulkBusy] = useState(false)
  const items = useMemo(() => q.data?.items || [], [q.data])
  const canEdit = q.data?.can_edit !== false
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length }
    for (const it of items) c[it.status] = (c[it.status] || 0) + 1
    return c
  }, [items])
  const shown = filter === 'all' ? items : items.filter((i) => i.status === filter)
  const interest = items.reduce((n, i) => n + (i.interest_count || 0), 0)

  /** one PATCH per card — the API has no bulk route on purpose (every change is audited per item) */
  const bulk = async (pick: (i: Item) => boolean, body: Record<string, unknown>, label: string) => {
    const targets = items.filter(pick)
    if (!targets.length) return
    if (!window.confirm(`${label} ${targets.length} card${targets.length === 1 ? '' : 's'}?`)) return
    setBulkBusy(true)
    let ok = 0
    for (const it of targets) {
      try {
        await apiPatch(`/shop/upcoming/${it.id}`, body)
        ok += 1
      } catch (e) {
        toast(`${it.model_code}: ${errorText(e)}`, 'error')
      }
    }
    setBulkBusy(false)
    qc.invalidateQueries({ queryKey: ['shop-upcoming'] })
    toast(`${label}: ${ok} of ${targets.length}`, ok === targets.length ? 'success' : 'error')
  }

  return (
    <div>
      <PageHeader
        title="Coming soon"
        subtitle="Announced ranges before they land — publish the cards, set the arrival month, link each one to its catalog code when the stock is in."
        actions={
          canEdit ? (
            <>
              <Button variant="outline" size="sm" disabled={bulkBusy || !counts.draft} onClick={() => bulk((i) => i.status === 'draft', { status: 'published' }, 'Publish')}>
                {bulkBusy ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />} Publish all drafts
              </Button>
            </>
          ) : undefined
        }
      />

      {/* the numbers */}
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          { k: 'Cards', v: counts.all || 0 },
          { k: 'Published', v: counts.published || 0 },
          { k: 'Drafts', v: counts.draft || 0 },
          { k: 'Notify-me requests', v: interest },
        ].map((s) => (
          <Card key={s.k} className="px-4 py-3">
            <div className="text-[11px] text-muted-foreground">{s.k}</div>
            <div className="mt-0.5 font-display text-[20px] font-bold tabular-nums leading-tight">{q.isLoading ? '—' : s.v}</div>
          </Card>
        ))}
      </div>

      {/* filters + the month for every published card */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button key={f} type="button" onClick={() => setFilter(f)} aria-pressed={filter === f} className={cn('h-9 rounded-full border px-3.5 text-[12.5px] font-semibold transition', filter === f ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:text-foreground')}>
            {f === 'all' ? 'All' : STATUS_LABEL[f]} <span className="tabular-nums opacity-70">{counts[f] || 0}</span>
          </button>
        ))}
        {canEdit && (
          <div className="ml-auto flex items-center gap-2">
            <CalendarClock size={15} className="text-muted-foreground" aria-hidden="true" />
            <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="h-9 w-[160px]" aria-label="Arrival month for every published card" />
            <Button variant="outline" size="sm" disabled={!month || bulkBusy} onClick={() => bulk((i) => i.status !== 'withdrawn', { expected_month: month }, 'Set month on')}>
              Apply month to all
            </Button>
          </div>
        )}
      </div>

      {q.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-2xl" />
          ))}
        </div>
      ) : q.isError ? (
        <Card className="p-6 text-sm text-muted-foreground">Could not load the upcoming cards. Has scripts/shop_upcoming_migration.sql been applied?</Card>
      ) : shown.length === 0 ? (
        <Card className="p-6 text-sm text-muted-foreground">
          Nothing here yet. Run <code className="rounded bg-muted px-1">python -m scripts.import_upcoming</code>, review the sheet, then <code className="rounded bg-muted px-1">--commit --yes</code> to load the drafts.
        </Card>
      ) : (
        <ul className="space-y-3">
          {shown.map((it) => (
            <UpcomingRow key={it.id} item={it} canEdit={canEdit} busy={patch.isPending && patch.variables?.id === it.id} onPatch={(body) => patch.mutate({ id: it.id, body })} />
          ))}
        </ul>
      )}
    </div>
  )
}

function UpcomingRow({ item, canEdit, busy, onPatch }: { item: Item; canEdit: boolean; busy: boolean; onPatch: (body: Record<string, unknown>) => void }) {
  const [code, setCode] = useState(item.catalog_item_code || '')
  const [month, setMonth] = useState((item.expected_month || '').slice(0, 7))
  const [editing, setEditing] = useState(false)
  const [copy, setCopy] = useState({ name_en: item.name_en, name_ar: item.name_ar || '', spec_en: item.spec_en || '', spec_ar: item.spec_ar || '' })
  const thumb = item.photo_thumb_urls?.['160'] || item.photo_url || item.box_url || null
  const status = item.status
  const monthDirty = month !== (item.expected_month || '').slice(0, 7)
  const codeDirty = code.trim().toUpperCase() !== (item.catalog_item_code || '')

  return (
    <li>
      <Card className={cn('overflow-hidden', item.retired && 'border-emerald-300/60')}>
        <div className="flex gap-4 p-4">
          <div className="grid h-20 w-20 shrink-0 place-items-center overflow-hidden rounded-xl border border-border bg-white">
            {thumb ? <img src={thumb} alt="" width={80} height={80} loading="lazy" className="h-full w-full object-contain p-1" /> : <span className="text-[10px] text-muted-foreground">No photo</span>}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-display text-[15px] font-bold">{item.model_code}</span>
              <span className="text-[12px] text-muted-foreground">
                {item.brand}
                {item.category ? ` · ${item.category}` : ''}
              </span>
              <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>
              {item.retired && (
                <Badge tone="green" dot>
                  Live in catalog · card retired
                </Badge>
              )}
              {(item.interest_count || 0) > 0 && (
                <Badge tone="accent">
                  <Users size={11} className="mr-0.5 inline" aria-hidden="true" /> {item.interest_count} notify-me{item.interest_shops ? ` · ${item.interest_shops} phone${item.interest_shops === 1 ? '' : 's'}` : ''}
                </Badge>
              )}
            </div>
            {editing ? (
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <Input value={copy.name_en} onChange={(e) => setCopy({ ...copy, name_en: e.target.value })} placeholder="Name (EN)" />
                <Input value={copy.name_ar} onChange={(e) => setCopy({ ...copy, name_ar: e.target.value })} placeholder="الاسم (AR)" dir="rtl" />
                <Input value={copy.spec_en} onChange={(e) => setCopy({ ...copy, spec_en: e.target.value })} placeholder="Spec (EN)" />
                <Input value={copy.spec_ar} onChange={(e) => setCopy({ ...copy, spec_ar: e.target.value })} placeholder="المواصفات (AR)" dir="rtl" />
                <div className="flex gap-2 sm:col-span-2">
                  <Button size="sm" disabled={busy || !copy.name_en.trim()} onClick={() => { onPatch(copy); setEditing(false) }}>
                    <Check size={14} /> Save copy
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="mt-1 text-[14px] font-semibold leading-tight">{item.name_en}</div>
                {item.name_ar && (
                  <div className="text-[13px] text-muted-foreground" dir="rtl">
                    {item.name_ar}
                  </div>
                )}
                {item.spec_en && <div className="mt-0.5 text-[12.5px] text-muted-foreground">{item.spec_en}</div>}
              </>
            )}
            {item.variants.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {item.variants.map((v) => (
                  <Badge key={v.label} tone="grey">
                    {v.label}
                  </Badge>
                ))}
              </div>
            )}
            <div className="mt-2 text-[12px] text-muted-foreground">
              Shows as <b className="text-foreground">{item.expected_label_en}</b>
              {item.expected_label_ar ? ` · ${item.expected_label_ar}` : ''}
              {item.shipment_ref ? ` · shipment ${item.shipment_ref}` : ''} · price label “Price on arrival”
            </div>
          </div>
        </div>

        {canEdit && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border bg-muted/40 px-4 py-3">
            {status !== 'published' && status !== 'arrived' && (
              <Button size="sm" disabled={busy} onClick={() => onPatch({ status: 'published' })}>
                <Eye size={14} /> Publish
              </Button>
            )}
            {status === 'published' && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onPatch({ status: 'withdrawn' })}>
                <EyeOff size={14} /> Withdraw
              </Button>
            )}
            {status !== 'arrived' && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onPatch({ status: 'arrived' })}>
                <PackageCheck size={14} /> Arrived
              </Button>
            )}
            {!editing && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(true)}>
                Edit copy
              </Button>
            )}
            <span className="mx-1 hidden h-6 w-px bg-border sm:block" aria-hidden="true" />
            <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
              Month
              <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="h-9 w-[150px]" />
            </label>
            {monthDirty && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onPatch({ expected_month: month || null })}>
                <Check size={14} /> Set
              </Button>
            )}
            <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
              <Link2 size={13} aria-hidden="true" /> Catalog code
              <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="e.g. WK-WS55" className="h-9 w-[150px] uppercase" />
            </label>
            {codeDirty && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onPatch({ catalog_item_code: code.trim().toUpperCase() || null })}>
                <Check size={14} /> Link
              </Button>
            )}
            {busy && <Loader2 size={14} className="animate-spin text-muted-foreground" />}
          </div>
        )}
      </Card>
    </li>
  )
}
