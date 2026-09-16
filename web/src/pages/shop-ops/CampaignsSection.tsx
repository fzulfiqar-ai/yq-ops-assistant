import { useRef, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, ImagePlus, Loader2, Megaphone, Pencil, Plus, Trash2, X } from 'lucide-react'
import { apiDelete, apiGet, apiPatch, apiPost, apiUpload, ApiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { DataTable, type Column } from '@/components/DataTable'

/**
 * Campaigns — the marketplace's promotions layer, scheduled here by the office. Each one is a
 * title, a line, an optional image and a link, shown where `placement` says: the home strip,
 * the desktop aside, the home hero (anonymous visitors only) or one category's shelf. A campaign
 * can be tied to a discount rule so its countdown is the rule's real end. Sponsored slots exist
 * for a supplier who pays for the space; merchants always see them labelled.
 */

type Placement = 'hero' | 'strip' | 'aside' | 'category'
type Audience = 'all' | 'recognized' | 'new'

interface Campaign {
  id: number
  title: string
  title_ar?: string | null
  line?: string | null
  line_ar?: string | null
  image_url?: string | null
  cta_label?: string | null
  cta_label_ar?: string | null
  cta_to: string
  placement: Placement[]
  category?: string | null
  audience: Audience
  rule_id?: number | null
  sponsored: boolean
  sponsor_name?: string | null
  starts_at?: string | null
  ends_at?: string | null
  is_active: boolean
  sort_order: number
  status?: 'live' | 'scheduled' | 'ended' | 'paused'
}
interface CampaignsResp { campaigns: Campaign[] }
interface RuleLite { id: number; name: string; kind: string; is_active: boolean; ends_at?: string | null }

const PLACEMENTS: { key: Placement; label: string; hint: string }[] = [
  { key: 'strip', label: 'Home strip', hint: 'Wide card under the categories (desktop) · swipe card (phone)' },
  { key: 'aside', label: 'Desktop spotlight', hint: 'Rotating panel under the running order, ≥1280px' },
  { key: 'hero', label: 'Home hero', hint: 'Replaces the product hero for new visitors only' },
  { key: 'category', label: 'Category shelf', hint: 'Slim banner at the top of one category' },
]
const AUDIENCES: { key: Audience; label: string }[] = [
  { key: 'all', label: 'Everyone' },
  { key: 'recognized', label: 'Shops that ordered before' },
  { key: 'new', label: 'New visitors' },
]
const CTA_PRESETS: { label: string; to: string }[] = [
  { label: 'Clearance', to: '/shop?f=clearance' },
  { label: 'Price drops', to: '/shop?f=drops' },
  { label: 'Offers', to: '/shop?f=offers' },
  { label: 'New arrivals', to: '/shop?f=new' },
  { label: 'Best sellers', to: '/shop?sort=popular' },
  { label: 'Quick order', to: '/quick' },
  { label: 'Cables', to: '/t/cable' },
  { label: 'Chargers', to: '/t/charger' },
  { label: 'Power banks', to: '/t/power-bank' },
]
const CATEGORIES = ['CABLE', 'CHARGER', 'CAR CHARGER', 'POWER BANK', 'EARPHONE', 'BLUETOOTH HEADSET', 'BLUETOOTH SPEAKER', 'CAR ACCESSORIES']

const STATUS_TONE: Record<string, BadgeTone> = { live: 'green', scheduled: 'accent', ended: 'grey', paused: 'amber' }

function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    try {
      const j = JSON.parse(e.body) as { detail?: string }
      if (j.detail) return j.detail
    } catch {
      /* not json */
    }
    return e.body.slice(0, 200) || fallback
  }
  return fallback
}
function isoToLocal(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function localToIso(local: string): string | null {
  if (!local) return null
  const d = new Date(local)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}
function windowLabel(a?: string | null, b?: string | null): string {
  const f = (s?: string | null) => (s ? new Date(s).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) : null)
  const x = f(a), y = f(b)
  if (x && y) return `${x} → ${y}`
  if (x) return `from ${x}`
  if (y) return `until ${y}`
  return 'Always'
}

const LABEL = 'mb-1 block text-xs font-semibold text-muted-foreground'
const SELECT = 'flex h-11 w-full rounded-lg border border-input bg-card px-3.5 text-sm text-foreground shadow-sm outline-none transition-colors focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring'

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className="inline-flex items-center gap-2 text-[12.5px] font-medium text-foreground">
      <span className={cn('relative inline-block h-5 w-9 rounded-full transition-colors', checked ? 'bg-primary' : 'bg-border')}>
        <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', checked ? 'left-[18px]' : 'left-0.5')} />
      </span>
      {label}
    </button>
  )
}

/* ───────────────────────── preview (what the merchant sees) ───────────────────────── */

function Preview({ c }: { c: Partial<Campaign> }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[#E9E4EF] bg-white shadow-sm">
      <div className="flex">
        {c.image_url ? (
          <img src={c.image_url} alt="" className="w-[120px] shrink-0 self-stretch object-cover" />
        ) : (
          <span className="grid w-[88px] shrink-0 place-items-center self-stretch bg-[#6D4091] text-white">
            <Megaphone size={22} />
          </span>
        )}
        <div className="min-w-0 flex-1 px-4 py-3.5">
          {c.sponsored && <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[#6b6478]">Sponsored · {c.sponsor_name || '…'}</div>}
          <div className="font-display text-[15px] font-bold leading-tight text-[#1A1428]">{c.title || 'Campaign title'}</div>
          {c.line && <div className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-[#6b6478]">{c.line}</div>}
          <div className="mt-1.5 text-[13px] font-semibold text-[#6D4091]">{c.cta_label || 'See more'} →</div>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────── dialog ───────────────────────── */

type Form = Omit<Campaign, 'id' | 'status'> & { starts_local: string; ends_local: string }
const EMPTY: Form = {
  title: '', title_ar: '', line: '', line_ar: '', image_url: '', cta_label: '', cta_label_ar: '', cta_to: '/shop?f=clearance',
  placement: ['strip'], category: '', audience: 'all', rule_id: null, sponsored: false, sponsor_name: '',
  starts_at: null, ends_at: null, is_active: true, sort_order: 100, starts_local: '', ends_local: '',
}

function CampaignDialog({ campaign, rules, onClose, onSaved }: { campaign: Campaign | null; rules: RuleLite[]; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [f, setF] = useState<Form>(() => (campaign ? { ...EMPTY, ...campaign, starts_local: isoToLocal(campaign.starts_at), ends_local: isoToLocal(campaign.ends_at) } : EMPTY))
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [err, setErr] = useState('')
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }))
  const togglePlacement = (p: Placement) => set('placement', f.placement.includes(p) ? f.placement.filter((x) => x !== p) : [...f.placement, p])
  const custom = !CTA_PRESETS.some((p) => p.to === f.cta_to)

  async function upload(file: File) {
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await apiUpload<{ url: string }>('/shop/campaigns/image', form)
      set('image_url', r.url)
    } catch (e) {
      toast(errorText(e, 'Upload failed.'), 'error')
    } finally {
      setUploading(false)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    const body = {
      title: f.title, title_ar: f.title_ar || null, line: f.line || null, line_ar: f.line_ar || null, image_url: f.image_url || null,
      cta_label: f.cta_label || null, cta_label_ar: f.cta_label_ar || null, cta_to: f.cta_to, placement: f.placement,
      category: f.category || null, audience: f.audience, rule_id: f.rule_id || null, sponsored: f.sponsored,
      sponsor_name: f.sponsor_name || null, starts_at: localToIso(f.starts_local), ends_at: localToIso(f.ends_local),
      is_active: f.is_active, sort_order: Number(f.sort_order) || 100,
    }
    try {
      if (campaign) await apiPatch(`/shop/campaigns/${campaign.id}`, body)
      else await apiPost('/shop/campaigns', body)
      toast(campaign ? 'Campaign updated.' : 'Campaign created.', 'success')
      onSaved()
      onClose()
    } catch (e2) {
      setErr(errorText(e2, 'Could not save the campaign.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label={campaign ? 'Edit campaign' : 'New campaign'}>
      <form onSubmit={submit} className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-t-2xl bg-card p-5 shadow-2xl sm:rounded-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-[18px] font-bold">{campaign ? 'Edit campaign' : 'New campaign'}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="grid h-10 w-10 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
            <X size={18} />
          </button>
        </div>

        {err && (
          <div className="mb-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-[13px] text-rose-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
          </div>
        )}

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className={LABEL}>Title *</span>
                <Input value={f.title} onChange={(e) => set('title', e.target.value)} placeholder="Clearance week — 10 cables at trade price" maxLength={80} />
              </label>
              <label className="block" dir="rtl">
                <span className={cn(LABEL, 'text-right')}>العنوان (عربي)</span>
                <Input value={f.title_ar || ''} onChange={(e) => set('title_ar', e.target.value)} placeholder="أسبوع التصفية" maxLength={160} />
              </label>
              <label className="block">
                <span className={LABEL}>Line</span>
                <Input value={f.line || ''} onChange={(e) => set('line', e.target.value)} placeholder="Last units, then gone." maxLength={160} />
              </label>
              <label className="block" dir="rtl">
                <span className={cn(LABEL, 'text-right')}>السطر (عربي)</span>
                <Input value={f.line_ar || ''} onChange={(e) => set('line_ar', e.target.value)} placeholder="آخر الوحدات" maxLength={160} />
              </label>
            </div>

            <div>
              <span className={LABEL}>Where it links *</span>
              <div className="flex flex-wrap gap-1.5">
                {CTA_PRESETS.map((p) => (
                  <button key={p.to} type="button" onClick={() => set('cta_to', p.to)} className={cn('rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition', f.cta_to === p.to ? 'border-primary bg-[#EEE8F4] text-[#6D4091]' : 'border-border text-muted-foreground hover:border-primary/40')}>
                    {p.label}
                  </button>
                ))}
                <button type="button" onClick={() => set('cta_to', '/p/')} className={cn('rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition', custom ? 'border-primary bg-[#EEE8F4] text-[#6D4091]' : 'border-border text-muted-foreground hover:border-primary/40')}>
                  Custom
                </button>
              </div>
              {custom && <Input className="mt-2" value={f.cta_to} onChange={(e) => set('cta_to', e.target.value)} placeholder="/p/X01 or https://…" />}
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className={LABEL}>Button text</span>
                  <Input value={f.cta_label || ''} onChange={(e) => set('cta_label', e.target.value)} placeholder="See more" maxLength={40} />
                </label>
                <label className="block" dir="rtl">
                  <span className={cn(LABEL, 'text-right')}>نص الزر</span>
                  <Input value={f.cta_label_ar || ''} onChange={(e) => set('cta_label_ar', e.target.value)} placeholder="اعرض المزيد" maxLength={40} />
                </label>
              </div>
            </div>

            <div>
              <span className={LABEL}>Placement *</span>
              <div className="grid gap-2 sm:grid-cols-2">
                {PLACEMENTS.map((p) => {
                  const on = f.placement.includes(p.key)
                  return (
                    <button key={p.key} type="button" onClick={() => togglePlacement(p.key)} aria-pressed={on} className={cn('rounded-xl border p-3 text-left transition', on ? 'border-primary bg-[#EEE8F4]' : 'border-border hover:border-primary/40')}>
                      <div className="flex items-center gap-2 text-[13px] font-semibold">
                        <span className={cn('grid h-4 w-4 place-items-center rounded border', on ? 'border-primary bg-primary text-white' : 'border-border')}>{on && <Check size={11} />}</span>
                        {p.label}
                      </div>
                      <div className="mt-0.5 text-[11.5px] text-muted-foreground">{p.hint}</div>
                    </button>
                  )
                })}
              </div>
              {f.placement.includes('category') && (
                <label className="mt-2 block">
                  <span className={LABEL}>Category *</span>
                  <select value={f.category || ''} onChange={(e) => set('category', e.target.value)} className={SELECT}>
                    <option value="">Choose…</option>
                    {CATEGORIES.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <label className="block">
                <span className={LABEL}>Audience</span>
                <select value={f.audience} onChange={(e) => set('audience', e.target.value as Audience)} className={SELECT}>
                  {AUDIENCES.map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className={LABEL}>Starts</span>
                <Input type="datetime-local" value={f.starts_local} onChange={(e) => set('starts_local', e.target.value)} />
              </label>
              <label className="block">
                <span className={LABEL}>Ends</span>
                <Input type="datetime-local" value={f.ends_local} onChange={(e) => set('ends_local', e.target.value)} />
              </label>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className={LABEL}>Tied to a discount rule (optional)</span>
                <select value={f.rule_id ?? ''} onChange={(e) => set('rule_id', e.target.value ? Number(e.target.value) : null)} className={SELECT}>
                  <option value="">None</option>
                  {rules.map((r) => (
                    <option key={r.id} value={r.id}>{r.name} · {r.kind}{r.is_active ? '' : ' (off)'}</option>
                  ))}
                </select>
                <span className="mt-1 block text-[11px] text-muted-foreground">The banner then ends exactly when the rule ends, and disappears if the rule is switched off.</span>
              </label>
              <label className="block">
                <span className={LABEL}>Order</span>
                <Input type="number" value={f.sort_order} onChange={(e) => set('sort_order', Number(e.target.value))} />
              </label>
            </div>

            <div className="rounded-xl border border-border p-3">
              <Toggle checked={f.sponsored} onChange={(v) => set('sponsored', v)} label="Sponsored slot (a supplier pays for this space)" />
              {f.sponsored && (
                <label className="mt-2 block">
                  <span className={LABEL}>Sponsor name * (merchants see “Sponsored · Name”)</span>
                  <Input value={f.sponsor_name || ''} onChange={(e) => set('sponsor_name', e.target.value)} placeholder="VFAN" maxLength={60} />
                </label>
              )}
            </div>

            <Toggle checked={f.is_active} onChange={(v) => set('is_active', v)} label={f.is_active ? 'Active' : 'Paused'} />
          </div>

          <div className="space-y-3">
            <span className={LABEL}>Image (optional, landscape)</span>
            <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading} className="flex h-28 w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border text-[13px] font-medium text-muted-foreground hover:border-primary/50">
              {uploading ? <Loader2 size={16} className="animate-spin" /> : <ImagePlus size={16} />} {f.image_url ? 'Replace image' : 'Upload image'}
            </button>
            {f.image_url && (
              <button type="button" onClick={() => set('image_url', '')} className="text-[12px] font-medium text-rose-700 hover:underline">
                Remove image
              </button>
            )}
            <span className={LABEL}>Preview</span>
            <Preview c={f} />
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} {campaign ? 'Save changes' : 'Create campaign'}
          </Button>
        </div>
      </form>
    </div>
  )
}

/* ───────────────────────── section ───────────────────────── */

export function CampaignsSection() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data, isLoading, isError } = useQuery({ queryKey: ['shop-campaigns'], queryFn: () => apiGet<CampaignsResp>('/shop/campaigns') })
  const rulesQ = useQuery({ queryKey: ['shop-rules'], queryFn: () => apiGet<{ rules: RuleLite[] }>('/shop/rules') })
  const [editing, setEditing] = useState<Campaign | 'new' | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['shop-campaigns'] })

  const toggleActive = useMutation({
    mutationFn: (c: Campaign) => apiPatch(`/shop/campaigns/${c.id}`, { is_active: !c.is_active }),
    onSuccess: refresh,
    onError: (e) => toast(errorText(e, 'Could not update the campaign.'), 'error'),
  })

  async function remove(c: Campaign) {
    if (!window.confirm(`Delete "${c.title}"? This can't be undone.`)) return
    try {
      await apiDelete(`/shop/campaigns/${c.id}`)
      toast('Campaign deleted.', 'success')
      refresh()
    } catch (e) {
      toast(errorText(e, 'Delete failed.'), 'error')
    }
  }

  const rows = data?.campaigns || []
  const cols: Column<Campaign>[] = [
    { key: 'title', label: 'Campaign', render: (_, c) => (
        <div className="flex items-center gap-3">
          {c.image_url ? <img src={c.image_url} alt="" className="h-10 w-16 rounded-md object-cover" /> : <span className="grid h-10 w-16 place-items-center rounded-md bg-[#EEE8F4] text-[#6D4091]"><Megaphone size={16} /></span>}
          <div>
            <div className="font-semibold">{c.title}</div>
            <div className="text-[11px] text-muted-foreground">{c.line || c.cta_to}</div>
          </div>
        </div>
      ) },
    { key: 'placement', label: 'Where', render: (_, c) => (
        <div className="flex flex-wrap gap-1">
          {c.placement.map((p) => <Badge key={p} tone="grey">{PLACEMENTS.find((x) => x.key === p)?.label || p}</Badge>)}
          {c.sponsored && <Badge tone="amber">Sponsored</Badge>}
        </div>
      ) },
    { key: 'audience', label: 'Audience', render: (_, c) => AUDIENCES.find((a) => a.key === c.audience)?.label || c.audience },
    { key: 'starts_at', label: 'Window', render: (_, c) => windowLabel(c.starts_at, c.ends_at) },
    { key: 'status', label: 'Status', render: (_, c) => <Badge tone={STATUS_TONE[c.status || 'live'] || 'grey'}>{c.status || 'live'}</Badge> },
    { key: 'is_active', label: 'Active', render: (_, c) => <Toggle checked={c.is_active} onChange={() => toggleActive.mutate(c)} label={c.is_active ? 'On' : 'Off'} /> },
    { key: 'id', label: '', align: 'right', render: (_, c) => (
        <div className="flex justify-end gap-1">
          <button type="button" onClick={() => setEditing(c)} aria-label="Edit" className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"><Pencil size={15} /></button>
          <button type="button" onClick={() => remove(c)} aria-label="Delete" className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground hover:bg-rose-50 hover:text-rose-700"><Trash2 size={15} /></button>
        </div>
      ) },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-[13px] text-muted-foreground">
          Promotions merchants see on the marketplace: the home strip, the desktop spotlight, the hero for new visitors, or one category. Only real offers — tie a campaign to a discount rule and its countdown is the rule's real end.
        </p>
        <Button onClick={() => setEditing('new')}><Plus size={15} /> New campaign</Button>
      </div>
      {isLoading ? (
        <Skeleton className="h-40 rounded-xl" />
      ) : isError ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">Could not load campaigns.</div>
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center">
          <Megaphone size={26} className="mx-auto text-muted-foreground" />
          <p className="mt-3 font-display text-[15px] font-bold">No campaigns yet</p>
          <p className="mt-1 text-[12.5px] text-muted-foreground">Start with the clearance shelf or a new-arrivals week — the marketplace shows it within a minute.</p>
        </div>
      ) : (
        <DataTable rows={rows} cols={cols} />
      )}
      {editing && <CampaignDialog campaign={editing === 'new' ? null : editing} rules={rulesQ.data?.rules || []} onClose={() => setEditing(null)} onSaved={refresh} />}
    </div>
  )
}
