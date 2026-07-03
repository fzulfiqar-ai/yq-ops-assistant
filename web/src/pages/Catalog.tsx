import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'motion/react'
import {
  Search, Download, Share2, Plus, Pencil, X, Loader2, Check,
  ImagePlus, Copy, MessageCircle, RefreshCw, Package, Camera,
} from 'lucide-react'
import { apiGet, apiPost, apiUpload, apiDownload, apiSend } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

export interface CatalogItem {
  item_code: string
  display_name?: string
  spec?: string
  category?: string
  brand?: string
  division?: string
  dealer_price?: number | null
  roadshow_price?: number | null
  rrp?: number | null
  standard_rate?: number | null
  product_image_url?: string | null
  package_image_url?: string | null
  sort_order?: number | null
  is_active?: boolean
}
interface CatalogData { items: CatalogItem[]; categories: string[]; count: number }
interface ShareLink { token: string; url: string }

const price = (v?: number | null) => (v == null ? '—' : bhd(Number(v)))

function Tier({ label, value, strong }: { label: string; value?: number | null; strong?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('truncate text-[13px] tabular-nums', strong ? 'font-bold text-primary' : 'font-medium')}>{price(value)}</div>
    </div>
  )
}

function ItemCard({ it, isAdmin, onEdit }: { it: CatalogItem; isAdmin: boolean; onEdit: (i: CatalogItem) => void }) {
  const [side, setSide] = useState<'product' | 'package'>('product')
  const img = side === 'product' ? it.product_image_url : it.package_image_url
  return (
    <motion.div layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      className={cn('group overflow-hidden rounded-2xl border bg-card shadow-soft transition-shadow hover:shadow-lift', it.is_active === false && 'opacity-60')}>
      <div className="relative aspect-square bg-white">
        {img ? (
          <img src={img} alt={it.item_code} loading="lazy" className="h-full w-full object-contain p-3" />
        ) : (
          <div className="grid h-full w-full place-items-center text-muted-foreground"><Package size={40} strokeWidth={1} /></div>
        )}
        {it.package_image_url && (
          <button onClick={() => setSide((s) => (s === 'product' ? 'package' : 'product'))}
            className="absolute bottom-2 right-2 rounded-full border bg-background/90 px-2.5 py-1 text-[11px] font-medium shadow-sm backdrop-blur transition hover:border-primary/50"
            title="Flip product / package photo">
            {side === 'product' ? 'Box' : 'Item'}
          </button>
        )}
        {it.is_active === false && (
          <span className="absolute left-2 top-2 rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase text-muted-foreground">hidden</span>
        )}
        {isAdmin && (
          <button onClick={() => onEdit(it)}
            className="absolute right-2 top-2 rounded-full border bg-background/90 p-1.5 opacity-0 shadow-sm backdrop-blur transition group-hover:opacity-100"
            title="Edit item">
            <Pencil size={13} />
          </button>
        )}
      </div>
      <div className="border-t p-3">
        <div className="flex items-baseline justify-between gap-2">
          <span className="font-display text-sm font-bold">{it.item_code}</span>
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{it.brand || 'VFAN'}</span>
        </div>
        {it.spec && <div className="mt-0.5 line-clamp-2 whitespace-pre-line text-[12px] leading-snug text-muted-foreground">{it.spec}</div>}
        <div className="mt-2 grid grid-cols-4 gap-2 border-t pt-2">
          <Tier label="Dealer" value={it.dealer_price} />
          <Tier label="Roadshow" value={it.roadshow_price} />
          <Tier label="RRP" value={it.rrp} strong />
          <Tier label="Book" value={it.standard_rate} />
        </div>
      </div>
    </motion.div>
  )
}

function EditDialog({ item, onClose, onSaved }: { item: Partial<CatalogItem>; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const isNew = !item.item_code
  const [f, setF] = useState<Partial<CatalogItem>>({ brand: 'VFAN', is_active: true, ...item })
  const [busy, setBusy] = useState(false)
  const set = (k: keyof CatalogItem, v: unknown) => setF((s) => ({ ...s, [k]: v }))

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!f.item_code?.trim()) return toast('Item code is required.', 'error')
    setBusy(true)
    try {
      await apiPost('/catalog/item', {
        ...f,
        item_code: f.item_code!.trim().toUpperCase(),
        dealer_price: f.dealer_price === undefined || f.dealer_price === null ? undefined : Number(f.dealer_price),
        roadshow_price: f.roadshow_price == null ? undefined : Number(f.roadshow_price),
        rrp: f.rrp == null ? undefined : Number(f.rrp),
      })
      toast(isNew ? 'Item added to the catalog.' : 'Item updated.', 'success')
      onSaved()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Save failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function photo(kind: 'product' | 'package', file: File | null) {
    if (!file || !f.item_code) return
    const form = new FormData()
    form.append('file', file)
    try {
      const r = await apiUpload<{ url: string }>(`/catalog/${encodeURIComponent(f.item_code)}/image?kind=${kind}`, form)
      set(kind === 'product' ? 'product_image_url' : 'package_image_url', r.url)
      toast(`${kind === 'product' ? 'Product' : 'Package'} photo updated.`, 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Upload failed', 'error')
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <Card className="max-h-[90vh] w-full max-w-lg overflow-auto p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <div className="font-display text-base font-semibold">{isNew ? 'Add catalog item' : `Edit ${f.item_code}`}</div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
        </div>
        <form onSubmit={save} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Item code *</span>
              <Input value={f.item_code || ''} disabled={!isNew} onChange={(e) => set('item_code', e.target.value)} placeholder="e.g. X21" /></label>
            <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Category</span>
              <Input value={f.category || ''} onChange={(e) => set('category', e.target.value.toUpperCase())} placeholder="CABLE" /></label>
          </div>
          <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Spec / description</span>
            <textarea value={f.spec || ''} onChange={(e) => set('spec', e.target.value)} rows={3}
              className="w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:border-primary/50" /></label>
          <div className="grid grid-cols-3 gap-3">
            <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Dealer</span>
              <Input inputMode="decimal" value={f.dealer_price ?? ''} onChange={(e) => set('dealer_price', e.target.value)} /></label>
            <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">Roadshow</span>
              <Input inputMode="decimal" value={f.roadshow_price ?? ''} onChange={(e) => set('roadshow_price', e.target.value)} /></label>
            <label className="block"><span className="mb-1 block text-xs font-semibold text-muted-foreground">RRP</span>
              <Input inputMode="decimal" value={f.rrp ?? ''} onChange={(e) => set('rrp', e.target.value)} /></label>
          </div>
          {!isNew && (
            <div className="grid grid-cols-2 gap-3">
              {(['product', 'package'] as const).map((kind) => (
                <label key={kind} className="flex cursor-pointer items-center gap-2 rounded-xl border border-dashed px-3 py-2.5 text-sm text-muted-foreground transition hover:border-primary/50">
                  {kind === 'product' ? <Camera size={16} /> : <ImagePlus size={16} />}
                  {kind === 'product' ? 'Product photo' : 'Package photo'}
                  <input type="file" accept="image/*" className="hidden" onChange={(e) => photo(kind, e.target.files?.[0] ?? null)} />
                </label>
              ))}
            </div>
          )}
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={f.is_active !== false} onChange={(e) => set('is_active', e.target.checked)} />
            Visible in the catalog
          </label>
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy ? <Loader2 className="animate-spin" size={15} /> : <Check size={15} />} Save</Button>
          </div>
        </form>
      </Card>
    </div>
  )
}

function ShareDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const { me } = useAuth()
  const { data } = useQuery({ queryKey: ['catalog-share'], queryFn: () => apiGet<ShareLink>('/catalog/share-link') })
  const rotate = useMutation({
    mutationFn: () => apiSend<ShareLink>('POST', '/catalog/share-link/rotate'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['catalog-share'] }); toast('New link created — old links no longer work.', 'success') },
  })
  const url = data?.url?.startsWith('http') ? data.url : data ? `${window.location.origin}${data.url}` : ''
  const waText = encodeURIComponent(`Hello! Here is the latest YQ Bahrain VFAN accessories catalog with prices: ${url}`)
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <Card className="w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between">
          <div className="font-display text-base font-semibold">Share with customers</div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={16} /></button>
        </div>
        <p className="mb-4 text-sm text-muted-foreground">
          Customers see items, photos and <b>RRP only</b> — never your dealer or roadshow pricing.
        </p>
        {url ? (
          <>
            <div className="flex items-center gap-2 rounded-xl border bg-secondary/40 px-3 py-2">
              <span className="min-w-0 flex-1 truncate text-[13px]">{url}</span>
              <button onClick={() => { navigator.clipboard?.writeText(url); toast('Link copied.', 'success') }}
                className="rounded-lg border bg-card p-1.5 hover:border-primary/40" title="Copy"><Copy size={14} /></button>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <a href={`https://wa.me/?text=${waText}`} target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-[13px] font-semibold text-white transition hover:bg-emerald-700">
                <MessageCircle size={15} /> Share on WhatsApp
              </a>
              <a href={url} target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-[13px] font-medium transition hover:border-primary/40">
                Preview
              </a>
              {me?.role === 'admin' && (
                <Button variant="outline" size="sm" className="ml-auto" onClick={() => rotate.mutate()} disabled={rotate.isPending}>
                  <RefreshCw size={13} className={rotate.isPending ? 'animate-spin' : ''} /> New link
                </Button>
              )}
            </div>
          </>
        ) : (
          <Skeleton className="h-10" />
        )}
      </Card>
    </div>
  )
}

export default function Catalog() {
  const { me } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const isAdmin = me?.role === 'admin'
  const { data, isLoading } = useQuery({ queryKey: ['catalog'], queryFn: () => apiGet<CatalogData>('/catalog') })
  const [cat, setCat] = useState<string>('All')
  const [q, setQ] = useState('')
  const [needsPhoto, setNeedsPhoto] = useState(false)
  const [edit, setEdit] = useState<Partial<CatalogItem> | null>(null)
  const [share, setShare] = useState(false)
  const [exporting, setExporting] = useState(false)

  const noPhotoCount = useMemo(() => (data?.items || []).filter((i) => !i.product_image_url).length, [data])
  const items = useMemo(() => {
    let r = data?.items || []
    if (cat !== 'All') r = r.filter((i) => (i.category || 'OTHER') === cat)
    if (needsPhoto) r = r.filter((i) => !i.product_image_url)
    if (q.trim()) {
      const s = q.toLowerCase()
      r = r.filter((i) => i.item_code.toLowerCase().includes(s) || (i.spec || '').toLowerCase().includes(s))
    }
    return r
  }, [data, cat, q, needsPhoto])

  async function exportXlsx() {
    setExporting(true)
    try {
      await apiDownload('/catalog/export', {}, 'YQ-VFAN-Catalog.xlsx')
    } catch {
      toast('Export failed — try again.', 'error')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div>
      <PageHeader title="Catalog" subtitle="VFAN item master — live prices, photos, ready to share" />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 rounded-lg border bg-card px-3 shadow-sm focus-within:border-primary/40">
          <Search size={15} className="text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search code or spec…"
            className="h-9 w-40 bg-transparent text-sm outline-none sm:w-56" />
        </div>
        <span className="text-xs text-muted-foreground">{items.length} of {data?.count ?? 0} items</span>
        <div className="ml-auto flex flex-wrap gap-2">
          {isAdmin && (
            <Button variant="outline" size="sm" onClick={() => setEdit({})}><Plus size={15} /> Add item</Button>
          )}
          <Button variant="outline" size="sm" onClick={exportXlsx} disabled={exporting}>
            {exporting ? <Loader2 className="animate-spin" size={15} /> : <Download size={15} />} Excel
          </Button>
          <Button size="sm" onClick={() => setShare(true)}><Share2 size={15} /> Share</Button>
        </div>
      </div>

      <div className="mb-4 flex gap-1.5 overflow-x-auto pb-1">
        {['All', ...(data?.categories || [])].map((c) => (
          <button key={c} onClick={() => setCat(c)}
            className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium capitalize transition',
              cat === c ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40')}>
            {c.toLowerCase()}
          </button>
        ))}
        {isAdmin && noPhotoCount > 0 && (
          <button onClick={() => setNeedsPhoto((v) => !v)}
            className={cn('shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium transition',
              needsPhoto ? 'border-amber-500 bg-amber-500 text-white' : 'border-amber-300 bg-amber-50 text-amber-700 hover:border-amber-400 dark:bg-amber-500/10 dark:text-amber-300')}>
            📷 needs photo · {noPhotoCount}
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="aspect-[3/4] rounded-2xl" />)}
        </div>
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">No items match.</Card>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((it) => (
            <ItemCard key={it.item_code} it={it} isAdmin={!!isAdmin} onEdit={setEdit} />
          ))}
        </div>
      )}

      {edit && (
        <EditDialog item={edit} onClose={() => setEdit(null)}
          onSaved={() => { setEdit(null); qc.invalidateQueries({ queryKey: ['catalog'] }) }} />
      )}
      {share && <ShareDialog onClose={() => setShare(false)} />}
    </div>
  )
}
