import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Radar, Loader2, Phone, Globe, MapPin, Target, ClipboardCopy, ChevronRight, Sparkles,
  Pencil, Wand2, Mail, X, Check,
} from 'lucide-react'
import { apiGet, apiPost, apiSend } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { useToast } from '@/components/Toast'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface Lead {
  id: number; name: string; category: string | null; area: string | null
  phone: string | null; website: string | null; fit_score: number; status: string
  segment: string | null; brand: string | null
  email?: string | null; address?: string | null; contact_name?: string | null
  next_action?: string | null; notes?: string | null
}
const SEGMENTS = ['all', 'modern_trade', 'wholesale', 'mobile', 'electronics', 'general'] as const
const SEG_LABEL: Record<string, string> = {
  modern_trade: 'Modern trade', wholesale: 'Wholesale', mobile: 'Mobile', electronics: 'Electronics', general: 'General',
}
const SEG_COLOR: Record<string, string> = {
  modern_trade: 'bg-indigo-100 text-indigo-700', wholesale: 'bg-emerald-100 text-emerald-700',
  mobile: 'bg-violet-100 text-violet-700', electronics: 'bg-blue-100 text-blue-700', general: 'bg-slate-100 text-slate-600',
}
interface Pipeline { by_status: Record<string, { leads: number; avg_fit: number }>; total: number; stages: string[] }
interface LeadsData { pipeline: Pipeline; leads: Lead[]; attribution: string }

const STATUSES = ['new', 'contacted', 'visited', 'quoted', 'ordered', 'rejected'] as const
const NEXT: Record<string, { to: string; label: string }[]> = {
  new: [{ to: 'contacted', label: 'Contacted' }, { to: 'rejected', label: 'Reject' }],
  contacted: [{ to: 'visited', label: 'Visited' }, { to: 'rejected', label: 'Reject' }],
  visited: [{ to: 'quoted', label: 'Quoted' }, { to: 'rejected', label: 'Reject' }],
  quoted: [{ to: 'ordered', label: 'Ordered ✓' }, { to: 'rejected', label: 'Reject' }],
  ordered: [], rejected: [{ to: 'new', label: 'Reopen' }],
}
const STATUS_COLOR: Record<string, string> = {
  new: 'bg-violet-100 text-violet-700', contacted: 'bg-blue-100 text-blue-700',
  visited: 'bg-amber-100 text-amber-700', quoted: 'bg-indigo-100 text-indigo-700',
  ordered: 'bg-emerald-100 text-emerald-700', rejected: 'bg-slate-100 text-slate-500',
}

const opener = (l: Lead) =>
  `Hi ${l.name}, this is YQ Bahrain — we wholesale fast-moving mobile accessories (power banks, cables, earbuds) to retailers. Can I share our top sellers and trade prices?`

const EDIT_FIELDS: { key: keyof Lead & string; label: string; placeholder: string }[] = [
  { key: 'contact_name', label: 'Contact person', placeholder: 'e.g. Mohammed' },
  { key: 'phone', label: 'Phone', placeholder: '+973 …' },
  { key: 'email', label: 'Email', placeholder: 'shop@…' },
  { key: 'website', label: 'Website', placeholder: 'https://…' },
  { key: 'address', label: 'Address', placeholder: 'Shop 12, Road …' },
  { key: 'next_action', label: 'Next action', placeholder: 'e.g. visit Tuesday with samples' },
]

function LeadEditDialog({ lead, onClose, onSaved }: { lead: Lead; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [f, setF] = useState<Record<string, string>>(
    Object.fromEntries(EDIT_FIELDS.map(({ key }) => [key, String(lead[key] ?? '')])))
  const [busy, setBusy] = useState(false)
  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      const changes = Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== String(lead[k as keyof Lead] ?? '')))
      await apiSend('PATCH', `/leads/${lead.id}`, changes)
      toast('Lead updated.', 'success')
      onSaved()
    } catch { toast('Could not save.', 'error') }
    finally { setBusy(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <Card className="w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="font-display text-base font-semibold">Edit {lead.name}</div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
        </div>
        <form onSubmit={save} className="space-y-2.5">
          {EDIT_FIELDS.map(({ key, label, placeholder }) => (
            <label key={key} className="block">
              <span className="mb-1 block text-xs font-semibold text-muted-foreground">{label}</span>
              <Input value={f[key]} placeholder={placeholder}
                onChange={(e) => setF((s) => ({ ...s, [key]: e.target.value }))} />
            </label>
          ))}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy ? <Loader2 className="animate-spin" size={15} /> : <Check size={15} />} Save</Button>
          </div>
        </form>
      </Card>
    </div>
  )
}

export default function Leads() {
  const qc = useQueryClient()
  const [filter, setFilter] = useState<string>('new')
  const [seg, setSeg] = useState<string>('all')
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const { data } = useQuery({
    queryKey: ['leads', filter],
    queryFn: () => apiGet<LeadsData>(`/leads${filter ? `?status=${filter}` : ''}`),
  })

  async function discover() {
    setBusy(true)
    try {
      const r = await apiPost<{ found: number; new: number; refreshed: number; skipped_existing: number }>('/leads/discover')
      toast(`Found ${r.found} shops · ${r.new} new + ${r.refreshed} updated (${r.skipped_existing} already customers).`, 'success')
      qc.invalidateQueries({ queryKey: ['leads'] })
    } catch { toast('Discovery failed — try again in a minute.', 'error') }
    finally { setBusy(false) }
  }

  async function advance(id: number, to: string, label: string) {
    try {
      await apiPost(`/leads/${id}/status`, { status: to })
      toast(`Moved to ${label}.`, 'success')
      qc.invalidateQueries({ queryKey: ['leads'] })
    } catch { toast('Could not update the lead.', 'error') }
  }

  async function copyOpener(l: Lead) {
    try { await navigator.clipboard.writeText(opener(l)); toast('Opener copied — paste it to the shop.', 'success') }
    catch { toast('Could not copy.', 'error') }
  }

  async function enrich(l: Lead) {
    setEnriching(l.id)
    try {
      const r = await apiPost<{ ok: boolean; reason?: string; found?: Record<string, string> }>(`/leads/${l.id}/enrich`)
      if (!r.ok) toast(r.reason || 'Enrichment unavailable.', 'error')
      else if (r.found && Object.keys(r.found).length) {
        toast(`Found: ${Object.entries(r.found).map(([k, v]) => `${k} ${v}`).join(' · ')}`, 'success')
        qc.invalidateQueries({ queryKey: ['leads'] })
      } else toast('Nothing new found in public listings.', 'info')
    } catch { toast('Enrichment failed.', 'error') }
    finally { setEnriching(null) }
  }

  const [editing, setEditing] = useState<Lead | null>(null)
  const [enriching, setEnriching] = useState<number | null>(null)
  const pipe = data?.pipeline
  const leads = (data?.leads || []).filter((l) => seg === 'all' || l.segment === seg)

  return (
    <div>
      <PageHeader title="Leads" subtitle="Find new B2B buyers — discover, score, and work the pipeline (the agent drafts, you close)" />

      {/* Discover + pipeline */}
      <Card className="mb-5 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground shadow-lift"><Target size={20} /></div>
            <div>
              <div className="font-display text-base font-semibold">B2B prospecting</div>
              <div className="text-[13px] text-muted-foreground">{pipe?.total ?? 0} leads in pipeline · free, from OpenStreetMap</div>
            </div>
          </div>
          <button onClick={discover} disabled={busy}
            className="flex shrink-0 items-center gap-2 rounded-lg bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground shadow-soft transition hover:shadow-lift disabled:opacity-50">
            {busy ? <Loader2 className="animate-spin" size={15} /> : <Radar size={15} />} Discover leads
          </button>
        </div>
      </Card>

      {/* Stage filter */}
      <div className="mb-4 flex flex-wrap gap-1.5">
        {STATUSES.map((s) => {
          const n = pipe?.by_status?.[s]?.leads ?? 0
          return (
            <button key={s} onClick={() => setFilter(s)}
              className={cn('rounded-full border px-3 py-1.5 text-[12.5px] font-medium capitalize transition',
                filter === s ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
              {s} {n > 0 && <span className="opacity-70">· {n}</span>}
            </button>
          )
        })}
      </div>

      {/* Segment filter */}
      <div className="mb-4 flex flex-wrap gap-1.5">
        {SEGMENTS.map((s) => (
          <button key={s} onClick={() => setSeg(s)}
            className={cn('rounded-full border px-3 py-1 text-[11.5px] font-medium transition',
              seg === s ? 'border-foreground/30 bg-foreground/5 text-foreground' : 'border-transparent bg-card text-muted-foreground hover:text-foreground')}>
            {s === 'all' ? 'All segments' : SEG_LABEL[s]}
          </button>
        ))}
      </div>

      {/* Leads */}
      <div className="space-y-2.5">
        {leads.map((l) => (
          <Card key={l.id} className="p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="truncate font-display text-[15px] font-semibold">{l.name}</span>
                  <span className={cn('rounded-full px-2 py-0.5 text-[10.5px] font-semibold capitalize', STATUS_COLOR[l.status] || 'bg-accent')}>{l.status}</span>
                  {l.segment && <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-semibold', SEG_COLOR[l.segment] || 'bg-accent')}>{SEG_LABEL[l.segment] || l.segment}</span>}
                  {l.brand && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">{l.brand}</span>}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted-foreground">
                  {l.category && <span className="capitalize">{l.category.replace(/_/g, ' ')}</span>}
                  {(l.address || l.area) && <span className="inline-flex items-center gap-1"><MapPin size={11} />{l.address || l.area}</span>}
                  {l.contact_name && <span>👤 {l.contact_name}</span>}
                  {l.phone && <a href={`tel:${l.phone}`} className="inline-flex items-center gap-1 text-primary hover:underline"><Phone size={11} />{l.phone}</a>}
                  {l.email && <a href={`mailto:${l.email}`} className="inline-flex items-center gap-1 text-primary hover:underline"><Mail size={11} />{l.email}</a>}
                  {l.website && <a href={l.website} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline"><Globe size={11} />site</a>}
                </div>
                {l.next_action && <div className="mt-1 text-[12px] font-medium text-amber-700 dark:text-amber-400">Next: {l.next_action}</div>}
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <span className="inline-flex items-center gap-1 rounded-lg bg-accent px-2 py-1 text-[12px] font-bold text-accent-foreground"><Sparkles size={11} className="text-primary" />{l.fit_score}</span>
                <span className="text-[10px] text-muted-foreground">fit</span>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button onClick={() => copyOpener(l)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:border-primary/50 hover:bg-accent">
                <ClipboardCopy size={13} /> Copy opener
              </button>
              <button onClick={() => setEditing(l)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:border-primary/50 hover:bg-accent">
                <Pencil size={13} /> Edit
              </button>
              {(!l.email || !l.phone || !l.website) && (
                <button onClick={() => enrich(l)} disabled={enriching === l.id}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium transition hover:border-primary/50 hover:bg-accent disabled:opacity-50"
                  title="Find contact details from public listings">
                  {enriching === l.id ? <Loader2 className="animate-spin" size={13} /> : <Wand2 size={13} />} Find contacts
                </button>
              )}
              {(NEXT[l.status] || []).map((nx) => (
                <button key={nx.to} onClick={() => advance(l.id, nx.to, nx.label)}
                  className={cn('inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium transition',
                    nx.to === 'rejected' ? 'text-muted-foreground hover:text-rose-600' : 'bg-primary text-primary-foreground hover:opacity-90')}>
                  {nx.label} {nx.to !== 'rejected' && <ChevronRight size={13} />}
                </button>
              ))}
            </div>
          </Card>
        ))}
        {!leads.length && (
          <Card className="p-8 text-center text-sm text-muted-foreground">
            {filter === 'new' && seg === 'all'
              ? 'No new leads yet — tap “Discover leads” to find B2B buyers across Bahrain.'
              : `No ${seg === 'all' ? '' : SEG_LABEL[seg] + ' '}leads in “${filter}”.`}
          </Card>
        )}
      </div>

      <p className="mt-4 px-1 text-[11px] text-muted-foreground">{data?.attribution || 'Lead data © OpenStreetMap contributors (ODbL).'} · Business contacts for B2B outreach only.</p>

      {editing && (
        <LeadEditDialog lead={editing} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ['leads'] }) }} />
      )}
    </div>
  )
}
