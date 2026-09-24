import { useQuery } from '@tanstack/react-query'
import { MessageCircle, PackagePlus, Share2, Users } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { relTime, waLink } from './lib'

/**
 * The rep's "Coming soon" section on Today: the published cards with a share-to-WhatsApp button
 * per model (the brand page link carries his referral code, so the interest lands on him), and
 * "Shops interested from your link" — the shops that tapped "Notify me", with the phone to
 * message when it lands. On arrival the rep tells those shops himself; there is no mass message.
 */

interface Item {
  id: number
  brand: string
  model_code: string
  category?: string | null
  name_en: string
  spec_en?: string | null
  variants: { label: string }[]
  photo_thumb_urls?: Record<string, string> | null
  photo_url?: string | null
  expected_label_en?: string | null
  status: string
  retired?: boolean
}

interface Interest {
  upcoming_id: number
  brand?: string | null
  model_code?: string | null
  name_en?: string | null
  status?: string | null
  count: number
  qty_interest: number
  phones: string[]
  first_at: string
}

function originOf(link: string): string {
  try {
    return new URL(link).origin
  } catch {
    return ''
  }
}

export function ComingSoon({ link }: { link: string }) {
  const listQ = useQuery({ queryKey: ['shop-upcoming'], queryFn: () => apiGet<{ items: Item[] }>('/shop/upcoming'), staleTime: 60_000, retry: 1 })
  const interestQ = useQuery({ queryKey: ['shop-upcoming-interest'], queryFn: () => apiGet<{ interest: Interest[] }>('/shop/upcoming/interest'), staleTime: 60_000, retry: 1 })
  const items = (listQ.data?.items || []).filter((i) => i.status === 'published' && !i.retired)
  const interest = interestQ.data?.interest || []
  if (!items.length && !interest.length) return null
  const brand = items[0]?.brand || interest[0]?.brand || 'WEKOME'
  const origin = originOf(link)
  const slug = link.replace(/\/+$/, '').split('/').pop() || ''
  const pageLink = origin ? `${origin}/brands/${brand.toLowerCase()}${slug ? `?ref=${encodeURIComponent(slug)}` : ''}` : ''

  const share = async (it: Item) => {
    const text = `${it.brand} ${it.model_code} — ${it.name_en}${it.spec_en ? ` (${it.spec_en})` : ''}. ${it.expected_label_en || 'Arriving soon'} at YQ, price on arrival.${pageLink ? ` See the range: ${pageLink}` : ''}`
    try {
      if (navigator.share) await navigator.share({ title: `${it.brand} ${it.model_code}`, text, url: pageLink || undefined })
      else window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, '_blank', 'noreferrer')
    } catch {
      /* dismissed */
    }
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="flex items-center gap-2 px-4 py-3">
        <PackagePlus size={16} className="text-primary" aria-hidden="true" />
        <h2 className="font-display text-[15px] font-bold">Coming soon · {brand}</h2>
        <span className="text-[12px] text-muted-foreground">{items.length} to pre-sell</span>
        {pageLink && (
          <a href={pageLink} target="_blank" rel="noreferrer" className="ml-auto inline-flex h-9 items-center gap-1.5 rounded-lg px-2 text-[12.5px] font-semibold text-primary hover:bg-muted">
            <Share2 size={13} aria-hidden="true" /> The range
          </a>
        )}
      </div>

      {items.length > 0 && (
        <ul className="divide-y divide-border border-t border-border">
          {items.slice(0, 12).map((it) => {
            const thumb = it.photo_thumb_urls?.['160'] || it.photo_url || null
            return (
              <li key={it.id} className="flex items-center gap-3 px-4 py-2.5">
                <span className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-lg border border-border bg-white">
                  {thumb ? <img src={thumb} alt="" width={40} height={40} loading="lazy" className="h-full w-full object-contain" /> : null}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="font-display text-[13.5px] font-bold">{it.model_code}</span>
                    <span className="truncate text-[13px]">{it.name_en}</span>
                  </span>
                  <span className="block truncate text-[11.5px] text-muted-foreground">
                    {[it.expected_label_en, it.variants.length ? it.variants.map((v) => v.label).join(' · ') : null].filter(Boolean).join(' · ')} · price on arrival
                  </span>
                </span>
                <button type="button" onClick={() => share(it)} aria-label={`Share ${it.model_code} on WhatsApp`} title="Share on WhatsApp" className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-border text-[#1d9e50] hover:bg-muted">
                  <MessageCircle size={16} aria-hidden="true" />
                </button>
              </li>
            )
          })}
          {items.length > 12 && <li className="px-4 py-2 text-[12px] text-muted-foreground">+{items.length - 12} more on the range page</li>}
        </ul>
      )}

      {interest.length > 0 && (
        <div className="border-t border-border">
          <div className="flex items-center gap-2 bg-muted/60 px-4 py-2">
            <Users size={14} className="text-primary" aria-hidden="true" />
            <h3 className="text-[13px] font-bold">Shops interested from your link</h3>
            <span className="text-[11.5px] text-muted-foreground">{interest.reduce((n, g) => n + g.count, 0)} notify-me requests · no commitment</span>
          </div>
          <ul className="divide-y divide-border">
            {interest.slice(0, 10).map((g) => {
              const wa = waLink(g.phones[0], `Hello, you asked to be told when ${g.brand || brand} ${g.model_code || ''} (${g.name_en || ''}) lands at YQ. It is ${g.status === 'arrived' ? 'in now' : 'on its way'} — shall I add it to your next order?`)
              return (
                <li key={g.upcoming_id} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="font-display text-[13.5px] font-bold">{g.model_code}</span>
                      <span className="truncate text-[13px]">{g.name_en}</span>
                      {g.status === 'arrived' ? <Badge tone="green">Arrived</Badge> : null}
                    </span>
                    <span className="block text-[11.5px] text-muted-foreground">
                      {g.count} {g.count === 1 ? 'shop' : 'shops'}
                      {g.qty_interest ? ` · ${g.qty_interest} pcs in mind` : ''}
                      {g.phones.length ? ` · ${g.phones.length} with a number` : ''} · since {relTime(g.first_at)}
                    </span>
                  </span>
                  {wa && (
                    <a href={wa} target="_blank" rel="noreferrer" aria-label="WhatsApp the shop" className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-border text-[#1d9e50] hover:bg-muted">
                      <MessageCircle size={16} aria-hidden="true" />
                    </a>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </section>
  )
}
