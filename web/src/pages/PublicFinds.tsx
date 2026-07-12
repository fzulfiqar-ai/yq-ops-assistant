import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Search, ImageOff } from 'lucide-react'
import { API_BASE } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Logo } from '@/components/Logo'

/** Management-facing product-finds board (no login): what the sales team spotted in the field.
 *  Reached via the tokenized share link. Photos are short-lived signed URLs from a private bucket. */

interface PubFind {
  id: number
  name?: string | null
  price_bhd?: number | null
  currency?: string | null
  note?: string | null
  category?: string | null
  status?: string
  image_url?: string | null
}
interface PubData { items: PubFind[]; categories: string[]; company: string; count: number }

export default function PublicFinds() {
  const { token } = useParams()
  const [data, setData] = useState<PubData | null>(null)
  const [err, setErr] = useState(false)
  const [cat, setCat] = useState('All')
  const [q, setQ] = useState('')

  useEffect(() => {
    if (!token) return
    fetch(`${API_BASE}/public/finds/${encodeURIComponent(token)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setData)
      .catch(() => setErr(true))
  }, [token])

  const items = useMemo(() => {
    let r = data?.items || []
    if (cat !== 'All') r = r.filter((i) => (i.category || 'OTHER') === cat)
    if (q.trim()) {
      const s = q.toLowerCase()
      r = r.filter((i) => (i.name || '').toLowerCase().includes(s) || (i.note || '').toLowerCase().includes(s))
    }
    return r
  }, [data, cat, q])

  if (err) {
    return (
      <div className="grid min-h-screen place-items-center bg-[#140f24] px-4 text-center text-white/80">
        <div>
          <Logo className="mx-auto h-14 w-14 rounded-2xl" />
          <p className="mt-4 text-sm">This link is no longer valid.<br />Please ask your YQ Bahrain contact for a new one.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#faf9fc]">
      <header className="sticky top-0 z-10 border-b bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
          <Logo className="h-10 w-10 rounded-xl" />
          <div>
            <div className="font-display text-base font-bold leading-tight text-[#1a1430]">YQ Bahrain — Product Finds</div>
            <div className="text-[11px] text-[#6b6480]">New &amp; unique products spotted in the field</div>
          </div>
          <div className="ml-auto hidden items-center gap-2 rounded-lg border bg-white px-3 sm:flex">
            <Search size={14} className="text-[#6b6480]" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…"
              className="h-9 w-44 bg-transparent text-sm outline-none" />
          </div>
        </div>
        <div className="mx-auto flex max-w-6xl gap-1.5 overflow-x-auto px-4 pb-2">
          {['All', ...(data?.categories || [])].map((c) => (
            <button key={c} onClick={() => setCat(c)}
              className={cn('shrink-0 rounded-full border px-3.5 py-1 text-[12px] font-medium capitalize transition',
                cat === c ? 'border-[#6d28d9] bg-[#6d28d9] text-white' : 'border-gray-200 bg-white text-[#6b6480]')}>
              {c.toLowerCase()}
            </button>
          ))}
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-5">
        {!data ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => <div key={i} className="aspect-[3/4] animate-pulse rounded-2xl bg-gray-100" />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {items.map((it) => (
              <div key={it.id} className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <div className="aspect-square bg-white">
                  {it.image_url ? (
                    <img src={it.image_url} alt={it.name || 'find'} loading="lazy" className="h-full w-full object-contain p-3" />
                  ) : (
                    <div className="grid h-full w-full place-items-center text-gray-300">
                      <ImageOff size={40} strokeWidth={1} />
                    </div>
                  )}
                </div>
                <div className="border-t p-3">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate font-display text-sm font-bold text-[#1a1430]">{it.name || 'New find'}</span>
                    {it.price_bhd != null && (
                      <span className="shrink-0 font-display text-sm font-extrabold tabular-nums text-[#6d28d9]">
                        BHD {Number(it.price_bhd).toFixed(3)}
                      </span>
                    )}
                  </div>
                  {it.note && <div className="mt-0.5 line-clamp-2 text-[12px] text-[#6b6480]">{it.note}</div>}
                  {it.category && (
                    <div className="mt-1 inline-block rounded bg-[#f1ecfb] px-1.5 py-0.5 text-[10px] font-medium uppercase text-[#6d28d9]">
                      {it.category}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        <footer className="py-8 text-center text-[11px] text-[#6b6480]">
          YQ Bahrain W.L.L · Product finds — internal reference.
        </footer>
      </main>
    </div>
  )
}
