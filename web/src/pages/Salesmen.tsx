import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Copy, ExternalLink, QrCode, X, Check, Loader2, UserX } from 'lucide-react'
import { apiGet, apiPost, apiPatch, apiDelete, ApiError, API_BASE } from '@/lib/api'
import { getSessionSafe } from '@/lib/supabase'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { num } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, type Column } from '@/components/DataTable'
import { AttainmentTab, StatementsTab } from './SalesmenRollups'
import { useAuth } from '@/lib/auth'

interface Salesman {
  id: number
  name: string
  phone?: string | null
  email?: string | null
  whatsapp?: string | null
  user_email?: string | null
  focus_name?: string | null
  referral_code: string
  is_active: boolean
  sort_order?: number | null
  notify_email?: boolean
  notify_whatsapp?: boolean
  link?: string | null
  orders_30d?: number | null
  // marketplace storefront card (/{referral_code})
  title?: string | null
  photo_url?: string | null
  public_profile?: boolean
  public_whatsapp?: boolean
  // distinct orders / merchants / kickback statements that point at this rep (all time).
  // null = the API could not count them. A referenced rep is never deleted (the server
  // refuses too): deactivate instead. `statements` arrives with the R1 API (older API: absent).
  references?: { orders: number; merchants: number; statements?: number } | null
}
interface SalesmenResp { salesmen: Salesman[] }
interface TeamUsersResp { users: { email: string }[] }

/** True when the API could not count what references this rep (or is an older API without the
 *  counts): the row must not claim history as a fact, nor offer Delete. */
function refsUnknown(r: Salesman): boolean {
  return r.references === undefined || r.references === null
}

/** Why a rep cannot be deleted, or null when nothing references them. Unknown counts read as
 *  "keep" — the server would refuse anyway, and a wrong Delete button is worse than a missing one. */
function keepReason(r: Salesman): string | null {
  const refs = r.references
  if (refs === undefined || refs === null) return 'Could not check this rep\'s orders — deactivate instead of deleting.'
  const statements = refs.statements ?? 0
  if (refs.orders + refs.merchants + statements === 0) return null
  const parts = [
    refs.orders ? `${refs.orders} order${refs.orders === 1 ? '' : 's'}` : '',
    refs.merchants ? `${refs.merchants} merchant${refs.merchants === 1 ? '' : 's'}` : '',
    statements ? `${statements} kickback statement${statements === 1 ? '' : 's'}` : '',
  ].filter(Boolean)
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0]
  return `Has ${list} — reps with history are deactivated, never deleted, so their orders and statements keep their name.`
}

/** The API's error detail (FastAPI wraps it as {"detail": "…"}); falls back to the raw body. */
function apiMessage(e: unknown, fallback: string): string {
  if (!(e instanceof ApiError)) return fallback
  try {
    const d = (JSON.parse(e.body) as { detail?: unknown }).detail
    if (typeof d === 'string' && d) return d
  } catch { /* not JSON */ }
  return e.body.slice(0, 160) || fallback
}

/** Fetch a protected binary endpoint (the QR PNG needs the bearer token) and hand back an
 *  object URL — plain <img src> can't carry an Authorization header. */
function useAuthedBlob(url: string | null | undefined) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    let active = true
    let created: string | null = null
    // Every setState call below lives inside this async callback (not the effect's
    // synchronous body) so React doesn't schedule an extra render on commit.
    ;(async () => {
      setBlobUrl(null)
      if (!url) return
      setLoading(true)
      try {
        const session = await getSessionSafe()
        const token = session?.access_token
        const res = await fetch(`${API_BASE}${url}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
        if (!res.ok) throw new Error(String(res.status))
        const blob = await res.blob()
        if (!active) return
        created = URL.createObjectURL(blob)
        setBlobUrl(created)
      } catch {
        if (active) setBlobUrl(null)
      } finally {
        if (active) setLoading(false)
      }
    })()
    return () => {
      active = false
      if (created) URL.revokeObjectURL(created)
    }
  }, [url])
  return { blobUrl, loading }
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" onClick={() => onChange(!checked)} className="flex items-center gap-2 text-[13px] font-medium">
      <span className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors', checked ? 'bg-primary' : 'bg-muted')}>
        <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-4' : 'translate-x-0.5')} />
      </span>
      {label}
    </button>
  )
}

/** first-name slug, lowercase [a-z0-9-] — used to seed referral_code from name on create only */
function slugify(s: string): string {
  const first = s.trim().split(/\s+/)[0] || ''
  return first.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}
function sanitizeCode(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9-]/g, '')
}

function QrDialog({ salesman, onClose }: { salesman: Salesman; onClose: () => void }) {
  const { blobUrl, loading } = useAuthedBlob(`/shop/salesmen/${salesman.id}/qr.png`)
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <Card className="w-full max-w-xs p-5 text-center" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="font-display text-base font-semibold">{salesman.name}'s QR</div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
        </div>
        {loading ? (
          <Skeleton className="mx-auto h-48 w-48" />
        ) : blobUrl ? (
          <img src={blobUrl} alt="Referral QR code" className="mx-auto h-48 w-48 rounded-xl border bg-white p-2" />
        ) : (
          <p className="py-12 text-sm text-muted-foreground">Could not load the QR code.</p>
        )}
        <p className="mt-3 text-[12px] text-muted-foreground">Customers scan this to open {salesman.name}'s catalog link.</p>
      </Card>
    </div>
  )
}

function SalesmanDialog({
  s, onClose, onSaved, emailOptions,
}: {
  s: Partial<Salesman> | null
  onClose: () => void
  onSaved: () => void
  emailOptions: string[]
}) {
  const toast = useToast()
  const isNew = !s?.id
  const [f, setF] = useState<Partial<Salesman>>({ is_active: true, notify_email: true, notify_whatsapp: true, ...s })
  const [codeTouched, setCodeTouched] = useState(!isNew)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof Salesman, v: unknown) => setF((prev) => ({ ...prev, [k]: v }))

  function onNameChange(v: string) {
    set('name', v)
    if (isNew && !codeTouched) set('referral_code', slugify(v))
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!f.name?.trim()) return toast('Name is required.', 'error')
    setBusy(true)
    try {
      const payload = {
        name: f.name.trim(),
        phone: f.phone?.trim() || undefined,
        email: f.email?.trim() || undefined,
        whatsapp: f.whatsapp?.trim() || f.phone?.trim() || undefined,
        user_email: f.user_email?.trim() || undefined,
        focus_name: f.focus_name?.trim() || undefined,
        referral_code: f.referral_code?.trim() || undefined,
        is_active: f.is_active !== false,
        notify_email: f.notify_email !== false,
        notify_whatsapp: f.notify_whatsapp !== false,
        title: f.title?.trim() || undefined,
        photo_url: f.photo_url?.trim() || undefined,
        public_profile: f.public_profile !== false,
        public_whatsapp: f.public_whatsapp === true,
      }
      if (isNew) await apiPost('/shop/salesmen', payload)
      else await apiPatch(`/shop/salesmen/${f.id}`, payload)
      toast(isNew ? 'Salesman added.' : 'Salesman updated.', 'success')
      onSaved()
    } catch (err) {
      toast(err instanceof ApiError ? err.body.slice(0, 160) : 'Save failed.', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="flex min-h-full items-center justify-center">
        <Card className="w-full max-w-lg p-5" onClick={(e) => e.stopPropagation()}>
          <div className="mb-4 flex items-center justify-between">
            <div className="font-display text-base font-semibold">{isNew ? 'Add salesman' : `Edit ${f.name}`}</div>
            <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
          </div>
          <form onSubmit={save} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Name *</span>
                <Input value={f.name || ''} onChange={(e) => onNameChange(e.target.value)} placeholder="Full name" /></label>
              <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Phone</span>
                <Input value={f.phone || ''} onChange={(e) => set('phone', e.target.value)} placeholder="973XXXXXXXX" /></label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Email</span>
                <Input type="email" value={f.email || ''} onChange={(e) => set('email', e.target.value)} placeholder="name@yq.bh" /></label>
              <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">WhatsApp</span>
                <Input value={f.whatsapp || ''} onChange={(e) => set('whatsapp', e.target.value)} placeholder={f.phone || 'defaults to phone'} /></label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Linked login (user email)</span>
                <Input list="salesmen-user-emails" value={f.user_email || ''} onChange={(e) => set('user_email', e.target.value)} placeholder="teammate@yq.bh" />
                <datalist id="salesmen-user-emails">
                  {emailOptions.map((e) => <option key={e} value={e} />)}
                </datalist>
              </label>
              <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Focus name</span>
                <Input value={f.focus_name || ''} onChange={(e) => set('focus_name', e.target.value)} placeholder="Name as it appears in Focus" /></label>
            </div>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-muted-foreground">Referral code</span>
              <Input value={f.referral_code || ''}
                onChange={(e) => { setCodeTouched(true); set('referral_code', sanitizeCode(e.target.value)) }}
                placeholder="e.g. furqan" />
              <span className="mt-0.5 block text-[11px] text-muted-foreground">Used in the shared link — lowercase letters, numbers and dashes only.</span>
            </label>
            <div className="rounded-xl border p-3">
              <div className="mb-2 text-xs font-semibold text-muted-foreground">Marketplace storefront · /{f.referral_code || 'code'}</div>
              <div className="grid grid-cols-2 gap-3">
                <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Title shown to merchants</span>
                  <Input value={f.title || ''} onChange={(e) => set('title', e.target.value)} placeholder="YQ sales representative" /></label>
                <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Photo URL (https)</span>
                  <Input value={f.photo_url || ''} onChange={(e) => set('photo_url', e.target.value)} placeholder="https://…/ahmed.jpg" /></label>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-4">
                <Toggle checked={f.public_profile !== false} onChange={(v) => set('public_profile', v)} label="Show my card on my storefront" />
                <Toggle checked={f.public_whatsapp === true} onChange={(v) => set('public_whatsapp', v)} label="Show my WhatsApp button to merchants" />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-4 pt-1">
              <Toggle checked={f.is_active !== false} onChange={(v) => set('is_active', v)} label="Active" />
              <Toggle checked={f.notify_email !== false} onChange={(v) => set('notify_email', v)} label="Notify by email" />
              <Toggle checked={f.notify_whatsapp !== false} onChange={(v) => set('notify_whatsapp', v)} label="Notify by WhatsApp" />
            </div>
            <p className="rounded-lg bg-secondary/50 px-3 py-2 text-[12px] text-muted-foreground">
              Contact details are stored in the database only — never committed to the repo.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={busy}>{busy ? <Loader2 className="animate-spin" size={15} /> : <Check size={15} />} Save</Button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  )
}

type Tab = 'reps' | 'attainment' | 'statements'

export default function Salesmen() {
  const qc = useQueryClient()
  const toast = useToast()
  const { me } = useAuth()
  const isAdmin = me?.role === 'admin'
  const [tab, setTab] = useState<Tab>('reps')
  const { data, isLoading } = useQuery({ queryKey: ['shop-salesmen'], queryFn: () => apiGet<SalesmenResp>('/shop/salesmen') })
  const { data: teamData } = useQuery({ queryKey: ['team-emails'], queryFn: () => apiGet<TeamUsersResp>('/team'), retry: false })
  const [edit, setEdit] = useState<Partial<Salesman> | null>(null)
  const [qr, setQr] = useState<Salesman | null>(null)

  const refresh = () => qc.invalidateQueries({ queryKey: ['shop-salesmen'] })

  const toggleActive = useMutation({
    mutationFn: (r: Salesman) => apiPatch(`/shop/salesmen/${r.id}`, { is_active: !r.is_active }),
    onSuccess: refresh,
    onError: () => toast('Could not update.', 'error'),
  })

  async function remove(r: Salesman) {
    // Only a rep nobody references reaches here (the row shows Deactivate otherwise); the
    // server refuses with 409 regardless, and that reason is what the toast shows.
    if (!window.confirm(`Remove ${r.name}? Their referral link will stop working. This cannot be undone.`)) return
    try {
      await apiDelete(`/shop/salesmen/${r.id}`)
      toast('Salesman removed.', 'success')
      refresh()
    } catch (e) {
      toast(apiMessage(e, 'Delete failed.'), 'error')
      refresh()
    }
  }

  function deactivate(r: Salesman) {
    if (!window.confirm(`Deactivate ${r.name}? Their link and storefront stop working; their orders and merchants keep their name.`)) return
    toggleActive.mutate(r)
  }

  const emailOptions = (teamData?.users || []).map((u) => u.email)
  const rows = data?.salesmen || []

  const cols: Column<Salesman>[] = [
    { key: 'name', label: 'Name', render: (_, r) => <span className="font-semibold">{r.name}</span> },
    { key: 'phone', label: 'Phone', render: (_, r) => r.phone || '—' },
    { key: 'email', label: 'Email', render: (_, r) => r.email || '—' },
    { key: 'referral_code', label: 'Code', render: (_, r) => <code className="rounded bg-secondary px-1.5 py-0.5 text-[12px]">{r.referral_code}</code> },
    { key: 'link', label: 'Link', render: (_, r) => (
        <div className="flex items-center gap-1">
          <button type="button" title="Copy link"
            onClick={() => { if (r.link) { navigator.clipboard?.writeText(r.link); toast('Link copied.', 'success') } }}
            className="rounded-lg border p-1.5 transition hover:border-primary/40"><Copy size={13} /></button>
          {r.link && (
            <a href={r.link} target="_blank" rel="noreferrer" title="Open" className="rounded-lg border p-1.5 transition hover:border-primary/40">
              <ExternalLink size={13} />
            </a>
          )}
          <button type="button" title="Show QR" onClick={() => setQr(r)} className="rounded-lg border p-1.5 transition hover:border-primary/40">
            <QrCode size={13} />
          </button>
        </div>
      ) },
    { key: 'user_email', label: 'Linked login', render: (_, r) => r.user_email || <span className="text-muted-foreground">Not linked</span> },
    { key: 'focus_name', label: 'Focus name', render: (_, r) => r.focus_name || '—' },
    { key: 'is_active', label: 'Active', render: (_, r) => (
        <Toggle checked={r.is_active} onChange={() => toggleActive.mutate(r)} label={r.is_active ? 'Active' : 'Inactive'} />
      ) },
    { key: 'orders_30d', label: 'Orders (30d)', align: 'right', render: (_, r) => num(r.orders_30d) },
    { key: 'id', label: '', align: 'right', render: (_, r) => {
        const keep = keepReason(r)
        return (
          <div className="flex justify-end gap-1.5">
            <Button type="button" variant="outline" size="sm" onClick={() => setEdit(r)}><Pencil size={13} /></Button>
            {keep === null ? (
              <Button type="button" variant="destructive" size="sm" title="Remove this rep (nothing references them)" onClick={() => remove(r)}>
                <Trash2 size={13} />
              </Button>
            ) : r.is_active ? (
              <Button type="button" variant="outline" size="sm" title={keep} onClick={() => deactivate(r)}>
                <UserX size={13} /> Deactivate
              </Button>
            ) : (
              <span className="self-center text-[12px] text-muted-foreground" title={keep}>
                {refsUnknown(r) ? 'Kept (could not check history)' : 'Kept (has history)'}
              </span>
            )}
          </div>
        )
      } },
  ]

  // R3a: the rollups. Attainment is the Salesmen page's business; statements are money → admin only.
  const tabs: { key: Tab; label: string }[] = [
    { key: 'reps', label: 'Reps' },
    { key: 'attainment', label: 'Attainment' },
    ...(isAdmin ? [{ key: 'statements' as Tab, label: 'Statements' }] : []),
  ]

  return (
    <div>
      <PageHeader title="Salesmen" subtitle="Referral links, QR codes, Focus mapping, monthly attainment and kickback statements"
        actions={tab === 'reps' ? <Button size="sm" onClick={() => setEdit({})}><Plus size={15} /> Add salesman</Button> : undefined} />

      <div role="tablist" aria-label="Salesmen views" className="mb-4 inline-grid auto-cols-fr grid-flow-col rounded-xl border border-border bg-card p-1">
        {tabs.map((t) => (
          <button key={t.key} role="tab" type="button" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
            className={cn('h-9 rounded-lg px-4 text-[13px] font-semibold', tab === t.key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted')}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'attainment' ? <AttainmentTab /> : tab === 'statements' ? <StatementsTab /> : (
        <>
          <p className="mb-4 text-sm text-muted-foreground">
            Each salesman gets a personal storefront link and QR code — orders placed through it are credited
            to them automatically, and they see their own orders under Shop Orders. Set the marketplace URL in
            Settings → Shop (<code>shop_market_url</code>) so links and QR codes point at <code>/{'{code}'}</code> on the
            marketplace instead of the token link. Contact details are stored in the database only. A rep with
            orders, merchants or a kickback statement can only be <strong>deactivated</strong> — their orders keep their name for kickback and returns.
          </p>

          {isLoading ? (
            <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
          ) : (
            <DataTable rows={rows} cols={cols} exportName="yq-salesmen"
              empty="No salesmen yet — add your first one to generate a referral link and QR code." />
          )}
        </>
      )}

      {edit && (
        <SalesmanDialog s={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); refresh() }} emailOptions={emailOptions} />
      )}
      {qr && <QrDialog salesman={qr} onClose={() => setQr(null)} />}
    </div>
  )
}
