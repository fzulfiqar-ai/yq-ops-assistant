import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { NotebookPen, Loader2, Send, Camera, X } from 'lucide-react'
import { apiGet, apiPost, apiUpload } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { useToast } from '@/components/Toast'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

interface Note { id: number; note: string; category: string; created_by?: string; created_at: string; image_url?: string | null }

const CATS = [
  { v: 'demand', label: 'Demand signal' },
  { v: 'competitor_price', label: 'Competitor price' },
  { v: 'stockout', label: 'Stock-out seen' },
  { v: 'new_product', label: 'New product ask' },
  { v: 'complaint', label: 'Complaint' },
  { v: 'other', label: 'Other' },
]
const CAT_LABEL: Record<string, string> = Object.fromEntries(CATS.map((c) => [c.v, c.label]))
const CAT_STYLE: Record<string, string> = {
  demand: 'bg-emerald-100 text-emerald-700', competitor_price: 'bg-amber-100 text-amber-700',
  stockout: 'bg-rose-100 text-rose-700', new_product: 'bg-violet-100 text-violet-700',
  complaint: 'bg-orange-100 text-orange-700', other: 'bg-secondary text-muted-foreground',
}

function relTime(iso: string) {
  const h = Math.floor((Date.now() - new Date(iso).getTime()) / 3.6e6)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export default function FieldNotes() {
  const qc = useQueryClient()
  const { data: notes } = useQuery({ queryKey: ['field-notes'], queryFn: () => apiGet<Note[]>('/field-notes') })
  const [note, setNote] = useState('')
  const [cat, setCat] = useState('demand')
  const [busy, setBusy] = useState(false)
  const [photo, setPhoto] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const toast = useToast()

  function pickPhoto(f: File | null) {
    if (preview) URL.revokeObjectURL(preview)
    setPhoto(f)
    setPreview(f ? URL.createObjectURL(f) : null)
  }

  async function submit() {
    if ((!note.trim() && !photo) || busy) return
    setBusy(true)
    try {
      let image_path: string | undefined
      if (photo) {
        const form = new FormData()
        form.append('file', photo)
        const up = await apiUpload<{ image_path?: string; error?: string }>('/field-notes/photo', form)
        if (up.error || !up.image_path) throw new Error(up.error || 'upload failed')
        image_path = up.image_path
      }
      await apiPost('/field-notes', { note: note.trim(), category: cat, image_path })
      setNote('')
      pickPhoto(null)
      toast('Field note saved — the AI assistant will recall it.', 'success')
      qc.invalidateQueries({ queryKey: ['field-notes'] })
    } catch {
      toast('Could not save the note.', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <PageHeader title="Field Notes" subtitle="What the team sees on the ground — feeds the AI's market intelligence" />

      {/* Composer — full width across the top */}
      <Card className="p-5">
        <div className="mb-3 flex flex-wrap gap-1.5">
          {CATS.map((c) => (
            <button key={c.v} onClick={() => setCat(c.v)}
              className={cn('rounded-full px-3 py-1 text-[12.5px] font-medium transition',
                cat === c.v ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}>
              {c.label}
            </button>
          ))}
        </div>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3}
          placeholder="e.g. Competitor selling the X27 cable at BD 1.2 in Manama · customers keep asking for 65W chargers · we ran out of F30 power banks on the Causeway route…"
          className="w-full resize-none rounded-xl border bg-background px-3.5 py-2.5 text-sm outline-none transition focus:border-primary/40 focus:ring-4 focus:ring-primary/10" />

        {/* hidden capture input — `capture=environment` opens the rear camera on phones */}
        <input ref={fileRef} type="file" accept="image/*" capture="environment" className="hidden"
          onChange={(e) => pickPhoto(e.target.files?.[0] || null)} />

        {/* photo preview */}
        {preview && (
          <div className="relative mt-3 inline-block">
            <img src={preview} alt="attachment preview" className="h-28 w-28 rounded-xl border object-cover shadow-soft" />
            <button type="button" onClick={() => pickPhoto(null)} title="Remove photo"
              className="absolute -right-2 -top-2 grid h-6 w-6 place-items-center rounded-full bg-foreground text-background shadow-lift transition hover:scale-105">
              <X size={13} />
            </button>
          </div>
        )}

        <div className="mt-3 flex items-center justify-between gap-2">
          <button type="button" onClick={() => fileRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-[13px] font-medium text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
            <Camera size={15} /> {photo ? 'Change photo' : 'Add photo'}
          </button>
          <div className="flex items-center gap-3">
            <span className="hidden text-[11px] text-muted-foreground sm:inline">Recalled by the assistant as context.</span>
            <Button onClick={submit} disabled={(!note.trim() && !photo) || busy}>
              {busy ? <Loader2 className="animate-spin" size={16} /> : <Send size={16} />} Save note
            </Button>
          </div>
        </div>
      </Card>

      {/* Recent notes — a grid below that fills the full width */}
      <div className="mt-6">
        <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Recent notes</div>
        {!notes?.length ? (
          <Card className="flex flex-col items-center justify-center gap-2 p-10 text-center text-sm text-muted-foreground">
            <NotebookPen size={26} className="text-muted-foreground/50" />
            No field notes yet — add the first observation above.
          </Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {notes.map((n) => (
              <Card key={n.id} className="p-4">
                <div className="mb-1.5 flex items-center gap-2">
                  <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', CAT_STYLE[n.category] || CAT_STYLE.other)}>
                    {CAT_LABEL[n.category] || n.category}
                  </span>
                  <span className="ml-auto text-[11px] text-muted-foreground">
                    {n.created_by ? `${n.created_by.split('@')[0]} · ` : ''}{relTime(n.created_at)}
                  </span>
                </div>
                {n.note && <p className="text-sm leading-relaxed">{n.note}</p>}
                {n.image_url && (
                  <a href={n.image_url} target="_blank" rel="noreferrer" className="mt-2 inline-block">
                    <img src={n.image_url} alt="field note attachment"
                      className="max-h-44 rounded-xl border object-cover shadow-soft transition hover:shadow-lift" />
                  </a>
                )}
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
