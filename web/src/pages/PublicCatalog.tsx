import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Search, Package } from 'lucide-react'
import { API_BASE } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Logo } from '@/components/Logo'

/** Customer-facing catalog (no login): items, photos and RRP only.
 *  Reached via the tokenized share link salesmen send on WhatsApp. */

interface PubItem {
  item_code: string
  display_name?: string
  spec?: string
  category?: string
  brand?: string
  rrp?: number | null
  product_image_url?: string | null
  package_image_url?: string | null
}
interface PubData { items: PubItem[]; categories: string[]; company: string }

export default function PublicCatalog() {
  const { token } = useParams()
  const [data, setData] = useState<PubData | null>(null)
  const [err, setErr] = useState(false)
  const [cat, setCat] = useState('All')
  const [q, setQ] = useState('')

  useEffect(() => {
    if (!token) return
    fetch(`${API_BASE}/public/catalog/${encodeURIComponent(token)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setData)
      .catch(() => setErr(true))
  }, [token])

  const items = useMemo(() => {
    let r = data?.items || []
    if (cat !== 'All') r = r.filter((i) => (i.category || 'OTHER') === cat)
    if (q.trim()) {
      const s = q.toLowerCase()
      r = r.filter((i) => i.item_code.toLowerCase().includes(s) || (i.spec || '').toLowerCase().includes(s))
    }
    return r
  }, [data, cat, q])

  if (err) {
    return (
      <div className="grid min-h-screen place-items-center bg-[#140f24] px-4 text-center text-white/80">
        <div>
          <Logo className="mx-auto h-14 w-14 rounded-2xl" />
          <p className="mt-4 text-sm">This catalog link is no longer valid.<br />Please ask your YQ Bahrain contact for a new one.</p>
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
            <div className="font-display text-base font-bold leading-tight text-[#1a1430]">YQ Bahrain — VFAN Catalog</div>
            <div className="text-[11px] text-[#6b6480]">Mobile accessories · recommended retail prices</div>
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
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="aspect-[3/4] animate-pulse rounded-2xl bg-gray-100" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {items.map((it) => (
              <div key={it.item_code} className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <div className="aspect-square bg-white">
                  {it.product_image_url ? (
                    <img src={it.product_image_url} alt={it.item_code} loading="lazy" className="h-full w-full object-contain p-3" />
                  ) : (
                    <div className="grid h-full w-full place-items-center text-gray-300"><Package size={40} strokeWidth={1} /></div>
                  )}
                </div>
                <div className="border-t p-3">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-display text-sm font-bold text-[#1a1430]">{it.item_code}</span>
                    <span className="text-[10px] uppercase text-[#6b6480]">{it.brand || 'VFAN'}</span>
                  </div>
                  {it.spec && <div className="mt-0.5 line-clamp-2 whitespace-pre-line text-[12px] text-[#6b6480]">{it.spec}</div>}
                  {it.rrp != null && (
                    <div className="mt-2 border-t pt-2 text-right font-display text-base font-extrabold text-[#6d28d9]">
                      BHD {Number(it.rrp).toFixed(2)}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        <footer className="py-8 text-center text-[11px] text-[#6b6480]">
          YQ Bahrain W.L.L · Prices are recommended retail and may change without notice.
        </footer>
      </main>
    </div>
  )
}
