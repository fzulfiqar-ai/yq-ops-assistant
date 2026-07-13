import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'motion/react'
import {
  Search, Camera, Loader2, Plus, X, Share2, Copy, RefreshCw, Check,
  Trash2, ArrowUpRight, Send, ImageOff, Pencil,
} from 'lucide-react'
import { apiGet, apiPost, apiPatch, apiDelete, apiUpload, apiSend } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

interface Find {
  id: number
  name?: string | null
  price_bhd?: number | null
  currency?: string | null
  note?: string | null
  category?: string | null
  source?: string | null
  status: string
  promoted_item_code?: string | null
  posted_by?: string | null
  posted_at?: string | null
  image_url?: string | null
}
interface ShareLink { token: string; url: string }
interface Draft { file: File; preview: string; name: string; price: string; note: string; category: string }

const STATUSES = ['new', 'reviewing', 'promoted', 'archived'] as const
const STATUS_STYLE: Record<string, string> = {
  new: 'bg-violet-100 text-violet-700', reviewing: 'bg-amber-100 text-amber-700',
  promoted: 'bg-emerald-100 text-emerald-700', archived: 'bg-secondary text-muted-foreground',
}

const bd = (v?: number | null) =>
  v == null ? null : `BD ${Number(v).toFixed(3).replace(/0+$/, '').replace(/\.$/, '')}`

function relTime(iso?: string | null) {
  if (!iso) return ''
  const h = Math.floor((Date.now() - new Date(iso).getTime()) / 3.6e6)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

/** Primary intake — snap/choose one or many photos, add a price + a note, post to the board.
 *  Replaces the WhatsApp-group habit: pick several at once for a batch. */
function AddFinds({ onDone }: { onDone: () => void }) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [busy, setBusy] = useState(false)

  function addFiles(files: FileList | null) {
    if (!files?.length) return
    const next = Array.from(files).map((file) => ({
      file, preview: URL.createObjectURL(file), name: '', price: '', note: '', category: '',
    }))
    setDrafts((d) => [...d, ...next])
  }
  const update = (i: number, patch: Partial<Draft>) =>
    setDrafts((d) => d.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  function remove(i: number) {
    setDrafts((d) => { const t = d[i]; if (t) URL.revokeObjectURL(t.preview); return d.filter((_, j) => j !== i) })
  }
  function clearAll() { drafts.forEach((d) => URL.revokeObjectURL(d.preview)); setDrafts([]) }

  async function saveAll() {
    if (!drafts.length || busy) return
    setBusy(true)
    try {
      const items = await Promise.all(drafts.map(async (d) => {
        const form = new FormData(); form.append('file', d.file)
        const up = await apiUpload<{ image_path?: string; error?: string }>('/finds/photo', form)
        if (up.error || !up.image_path) throw new Error(up.error || 'upload failed')
        return {
          image_path: up.image_path,
          name: d.name.trim() || undefined,
          price_bhd: d.price.trim() ? Number(d.price) : undefined,
          note: d.note.trim() || undefined,
          category: d.category.trim() || undefined,
          source: 'field',
        }
      }))
      const r = await apiPost<{ ok: boolean; count?: number }>('/finds/bulk', { items })
      if (!r.ok) throw new Error('save failed')
      clearAll()
      toast(`${r.count} find${r.count === 1 ? '' : 's'} added to the board.`, 'success')
      onDone()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not save.', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="p-5">
      <input ref={fileRef} type="file" accept="image/*" capture="environment" multiple className="hidden"
        onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
      {drafts.length === 0 ? (
        <button onClick={() => fileRef.current?.click()}
          className="flex w-full flex-col items-center justify-center gap-1.5 rounded-xl border-2 border-dashed py-10 text-muted-foreground transition hover:border-primary/50 hover:text-foreground">
          <Camera size={26} />
          <span className="text-sm font-medium">Take or choose product photos</span>
          <span className="max-w-md text-center text-[12px]">Snap what you spotted, add a price &amp; a short note — it posts straight to the board. Pick several at once for a batch.</span>
        </button>
      ) : (
        <>
          <div className="mb-3 flex items-center justify-between">
            <div className="font-display text-sm font-semibold">{drafts.length} photo{drafts.length === 1 ? '' : 's'} to add</div>
            <button onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-[13px] font-medium text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
              <Plus size={14} /> Add more
            </button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {drafts.map((d, i) => (
              <div key={i} className="flex gap-3 rounded-xl border p-2.5">
                <div className="relative shrink-0">
                  <img src={d.preview} alt="" className="h-24 w-24 rounded-lg border object-cover" />
                  <button onClick={() => remove(i)} title="Remove"
                    className="absolute -right-2 -top-2 grid h-6 w-6 place-items-center rounded-full bg-foreground text-background shadow"><X size={12} /></button>
                </div>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex gap-1.5">
                    <Input value={d.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Name (optional)" className="h-8 text-[13px]" />
                    <Input value={d.price} onChange={(e) => update(i, { price: e.target.value })} placeholder="Price BD" inputMode="decimal" className="h-8 w-24 text-[13px]" />
                  </div>
                  <Input value={d.category} onChange={(e) => update(i, { category: e.target.value })} placeholder="Category (optional)" className="h-8 text-[13px]" />
                  <Input value={d.note} onChange={(e) => update(i, { note: e.target.value })} placeholder="Comment — price details, where seen, MOQ…" className="h-8 text-[13px]" />
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" onClick={clearAll} disabled={busy}>Clear</Button>
            <Button onClick={saveAll} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" size={16} /> : <Send size={16} />} Add {drafts.length} to board
            </Button>
          </div>
        </>
      )}
    </Card>
  )
}

function PromoteDialog({ f, onClose, onDone }: { f: Find; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  async function go() {
    if (!code.trim()) return toast('Enter an item code (SKU).', 'error')
    setBusy(true)
    try {
      const r = await apiPost<{ ok: boolean }>(`/finds/${f.id}/promote`, { item_code: code.trim().toUpperCase() })
      if (!r.ok) throw new Error()
      toast(`Added to catalog as ${code.trim().toUpperCase()} — hidden until its SKU is in the price book.`, 'success')
      onDone()
    } catch { toast('Promote failed.', 'error') } finally { setBusy(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <Card className="w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between">
          <div className="font-display text-base font-semibold">Promote to catalog</div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
        </div>
        <p className="mb-3 text-[13px] text-muted-foreground">
          Creates a catalog item (with this photo) as <b>hidden</b>. It goes live automatically once its
          SKU appears in your MA price-book upload.
        </p>
        <Input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="Item code / SKU (e.g. X21)" autoFocus />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={go} disabled={busy}>{busy ? <Loader2 className="animate-spin" size={15} /> : <Check size={15} />} Promote</Button>
        </div>
      </Card>
    </div>
  )
}

/** Edit a find after the fact — add/change the price, comment, name or category. Available to
 *  anyone with the feature (reps annotate the seeded photos), backed by PATCH /finds/{id}. */
function EditFindDialog({ f, onClose, onSaved }: { f: Find; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [name, setName] = useState(f.name || '')
  const [price, setPrice] = useState(f.price_bhd != null ? String(f.price_bhd) : '')
  const [category, setCategory] = useState(f.category || '')
  const [note, setNote] = useState(f.note || '')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      const payload: Record<string, unknown> = { name: name.trim(), category: category.trim(), note: note.trim() }
      if (price.trim()) payload.price_bhd = Number(price)   // empty leaves the price unchanged
      await apiPatch(`/finds/${f.id}`, payload)
      toast('Find updated.', 'success')
      onSaved()
    } catch { toast('Update failed.', 'error') } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <Card className="max-h-[90vh] w-full max-w-md overflow-auto p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="font-display text-base font-semibold">Edit find</div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
        </div>
        {f.image_url && <img src={f.image_url} alt="" className="mx-auto mb-3 max-h-40 rounded-xl border object-contain p-2" />}
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Name</span>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Product name" /></label>
            <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Price (BD)</span>
              <Input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" placeholder="e.g. 1.250" /></label>
          </div>
          <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Category</span>
            <Input value={category} onChange={(e) => setCategory(e.target.value.toUpperCase())} placeholder="e.g. CABLE" /></label>
          <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Comment</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3}
              placeholder="Price details, where it was seen, MOQ, competitor…"
              className="w-full resize-none rounded-lg border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary/50" /></label>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={busy}>{busy ? <Loader2 className="animate-spin" size={15} /> : <Check size={15} />} Save</Button>
        </div>
      </Card>
    </div>
  )
}

function FindCard({ f, isAdmin, onChange }: { f: Find; isAdmin: boolean; onChange: () => void }) {
  const toast = useToast()
  const [promoteOpen, setPromoteOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const price = bd(f.price_bhd)

  async function setStatus(status: string) {
    try { await apiPatch(`/finds/${f.id}`, { status }); onChange() } catch { toast('Update failed', 'error') }
  }
  async function del() {
    if (!window.confirm('Delete this find? This cannot be undone.')) return
    try { await apiDelete(`/finds/${f.id}`); onChange() } catch { toast('Delete failed', 'error') }
  }

  return (
    <motion.div layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      className="group flex flex-col overflow-hidden rounded-2xl border bg-card shadow-soft transition-shadow hover:shadow-lift">
      <a href={f.image_url || undefined} target="_blank" rel="noreferrer" className="relative block aspect-square bg-white">
        {f.image_url
          ? <img src={f.image_url} alt={f.name || 'find'} loading="lazy" className="h-full w-full object-contain p-2" />
          : <div className="grid h-full w-full place-items-center text-muted-foreground"><ImageOff size={34} strokeWidth={1} /></div>}
        <span className={cn('absolute left-2 top-2 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase', STATUS_STYLE[f.status] || STATUS_STYLE.new)}>{f.status}</span>
      </a>
      <div className="flex flex-1 flex-col border-t p-3">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate font-display text-sm font-semibold">{f.name || 'Unnamed find'}</span>
          {price && <span className="shrink-0 font-display text-sm font-extrabold tabular-nums text-primary">{price}</span>}
        </div>
        {f.note && <p className="mt-0.5 line-clamp-2 text-[12px] text-muted-foreground">{f.note}</p>}
        <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {f.category && <span className="rounded bg-secondary px-1.5 py-0.5 uppercase">{f.category}</span>}
          <span className="ml-auto truncate">{f.posted_by ? `${f.posted_by.split('@')[0]} · ` : ''}{relTime(f.posted_at)}</span>
        </div>
        {f.promoted_item_code && <div className="mt-1 text-[11px] font-medium text-emerald-600">In catalog as {f.promoted_item_code}</div>}
        <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t pt-2">
          <button onClick={() => setEditOpen(true)}
            className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium transition hover:border-primary/50">
            <Pencil size={12} /> Edit
          </button>
          {isAdmin && (
            <>
              {f.status !== 'promoted' && (
                <button onClick={() => setPromoteOpen(true)}
                  className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium transition hover:border-primary/50">
                  <ArrowUpRight size={12} /> Promote
                </button>
              )}
              {f.status === 'new' && <button onClick={() => setStatus('reviewing')} className="rounded-lg border px-2 py-1 text-[11px] transition hover:border-primary/50">Reviewing</button>}
              {f.status !== 'archived'
                ? <button onClick={() => setStatus('archived')} className="rounded-lg border px-2 py-1 text-[11px] transition hover:border-primary/50">Archive</button>
                : <button onClick={() => setStatus('new')} className="rounded-lg border px-2 py-1 text-[11px] transition hover:border-primary/50">Restore</button>}
              <button onClick={del} title="Delete" className="ml-auto rounded-lg border px-2 py-1 text-[11px] text-rose-600 transition hover:border-rose-400"><Trash2 size={12} /></button>
            </>
          )}
        </div>
      </div>
      {editOpen && <EditFindDialog f={f} onClose={() => setEditOpen(false)} onSaved={() => { setEditOpen(false); onChange() }} />}
      {promoteOpen && <PromoteDialog f={f} onClose={() => setPromoteOpen(false)} onDone={() => { setPromoteOpen(false); onChange() }} />}
    </motion.div>
  )
}

function ShareDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const { me } = useAuth()
  const { data } = useQuery({ queryKey: ['finds-share'], queryFn: () => apiGet<ShareLink>('/finds/share-link') })
  const rotate = useMutation({
    mutationFn: () => apiSend<ShareLink>('POST', '/finds/share-link/rotate'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['finds-share'] }); toast('New link created — old links no longer work.', 'success') },
  })
  const url = data?.url?.startsWith('http') ? data.url : data ? `${window.location.origin}${data.url}` : ''
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <Card className="w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between">
          <div className="font-display text-base font-semibold">Share the finds board</div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
        </div>
        <p className="mb-4 text-sm text-muted-foreground">
          Anyone with this link can browse the product finds — <b>no login</b>. Rotate it any time to revoke old links.
        </p>
        {url ? (
          <>
            <div className="flex items-center gap-2 rounded-xl border bg-secondary/40 px-3 py-2">
              <span className="min-w-0 flex-1 truncate text-[13px]">{url}</span>
              <button onClick={() => { navigator.clipboard?.writeText(url); toast('Link copied.', 'success') }}
                className="rounded-lg border bg-card p-1.5 hover:border-primary/40" title="Copy"><Copy size={14} /></button>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <a href={url} target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-[13px] font-medium transition hover:border-primary/40">Preview</a>
              {me?.role === 'admin' && (
                <Button variant="outline" size="sm" className="ml-auto" onClick={() => rotate.mutate()} disabled={rotate.isPending}>
                  <RefreshCw size={13} className={rotate.isPending ? 'animate-spin' : ''} /> New link
                </Button>
              )}
            </div>
          </>
        ) : <Skeleton className="h-10" />}
      </Card>
    </div>
  )
}

export default function Finds() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const isAdmin = me?.role === 'admin'
  const [status, setStatus] = useState<string>('all')
  const [q, setQ] = useState('')
  const [share, setShare] = useState(false)
  const { data, isLoading } = useQuery({ queryKey: ['finds'], queryFn: () => apiGet<Find[]>('/finds') })

  const refresh = () => qc.invalidateQueries({ queryKey: ['finds'] })
  const items = useMemo(() => {
    let r = data || []
    r = status === 'all' ? r.filter((f) => f.status !== 'archived') : r.filter((f) => f.status === status)
    if (q.trim()) {
      const s = q.toLowerCase()
      r = r.filter((f) => (f.name || '').toLowerCase().includes(s)
        || (f.note || '').toLowerCase().includes(s) || (f.category || '').toLowerCase().includes(s))
    }
    return r
  }, [data, status, q])

  return (
    <div>
      <PageHeader title="Product Finds" subtitle="New & unique products the team spots in the field — one place, ready to review" />

      <div className="mb-4"><AddFinds onDone={refresh} /></div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 rounded-lg border bg-card px-3 shadow-sm focus-within:border-primary/40">
          <Search size={15} className="text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search finds…"
            className="h-9 w-40 bg-transparent text-sm outline-none sm:w-56" />
        </div>
        <span className="text-xs text-muted-foreground">{items.length} shown</span>
        <div className="ml-auto"><Button size="sm" onClick={() => setShare(true)}><Share2 size={15} /> Share</Button></div>
      </div>

      <div className="mb-4 flex gap-1.5 overflow-x-auto pb-1">
        {['all', ...STATUSES].map((s) => (
          <button key={s} onClick={() => setStatus(s)}
            className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium capitalize transition',
              status === s ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
            {s}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="aspect-[3/4] rounded-2xl" />)}
        </div>
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">No finds here yet — add the first one above.</Card>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((f) => <FindCard key={f.id} f={f} isAdmin={!!isAdmin} onChange={refresh} />)}
        </div>
      )}

      {share && <ShareDialog onClose={() => setShare(false)} />}
    </div>
  )
}
