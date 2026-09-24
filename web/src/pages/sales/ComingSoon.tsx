import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, MessageCircle, PackagePlus, Share2, Users } from 'lucide-react'
import { apiGet, apiPost } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { relTime, waLink } from './lib'

/**
 * The rep's "Coming soon" section on Today: the published cards with a share-to-WhatsApp button
 * per model (the brand page link carries his referral code, so the interest lands on him), and
 * "Shops interested from your link" — EVERY shop that tapped "Notify me", each with its own
 * WhatsApp link (expand a model to see them), and "Mark as told" per model once he has messaged
 * them. On arrival the rep tells those shops himself; there is no mass message. The whole
 * section hides while the office has the feature paused (upcoming_enabled = 0).
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

interface ListResp {
  items: Item[]
  settings?: Record<string, string>
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
  ids: number[]
}

function originOf(link: string): string {
  try {
    return new URL(link).origin
  } catch {
    return ''
  }
}

const ICON_BTN = 'grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-border hover:bg-muted disabled:opacity-50'

export function ComingSoon({ link }: { link: string }) {
  const qc = useQueryClient()
  const toast = useToast()
  const listQ = useQuery({ queryKey: ['shop-upcoming'], queryFn: () => apiGet<ListResp>('/shop/upcoming'), staleTime: 60_000, retry: 1 })
  const interestQ = useQuery({ queryKey: ['shop-upcoming-interest'], queryFn: () => apiGet<{ interest: Interest[] }>('/shop/upcoming/interest'), staleTime: 60_000, retry: 1 })
  const [openId, setOpenId] = useState<number | null>(null)
  const [telling, setTelling] = useState<number | null>(null)
  // the kill switch: the office paused the feature — nothing to share, nothing to answer
  if (listQ.data?.settings?.upcoming_enabled === '0') return null
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

  const message = (g: Interest) => `Hello, you asked to be told when ${g.brand || brand} ${g.model_code || ''} (${g.name_en || ''}) lands at YQ. It is ${g.status === 'arrived' ? 'in now' : 'on its way'} — shall I add it to your next order?`

  /** "Mark as told": the restock resolve route, for this model's requests only */
  const told = async (g: Interest) => {
    if (!g.ids.length || telling !== null) return
    setTelling(g.upcoming_id)
    try {
      await apiPost('/shop/restock/resolve', { ids: g.ids })
      qc.invalidateQueries({ queryKey: ['shop-upcoming-interest'] })
      qc.invalidateQueries({ queryKey: ['shop-upcoming'] })
      toast('Marked as told', 'success')
    } catch {
      toast('Could not update', 'error')
    } finally {
      setTelling(null)
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
                <button type="button" onClick={() => share(it)} aria-label={`Share ${it.model_code} on WhatsApp`} title="Share on WhatsApp" className={cn(ICON_BTN, 'text-[#1d9e50]')}>
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
              const open = openId === g.upcoming_id
              const listId = `upcoming-interest-${g.upcoming_id}`
              return (
                <li key={g.upcoming_id}>
                  <div className="flex items-center gap-3 px-4 py-2.5">
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="font-display text-[13.5px] font-bold">{g.model_code}</span>
                        <span className="truncate text-[13px]">{g.name_en}</span>
                        {g.status === 'arrived' ? <Badge tone="green">Arrived</Badge> : null}
                      </span>
                      <span className="block text-[11.5px] text-muted-foreground">
                        {g.count} {g.count === 1 ? 'shop' : 'shops'}
                        {g.qty_interest ? ` · ${g.qty_interest} pcs in mind` : ''}
                        {` · ${g.phones.length} ${g.phones.length === 1 ? 'number' : 'numbers'}`} · since {relTime(g.first_at)}
                      </span>
                    </span>
                    <button type="button" onClick={() => setOpenId(open ? null : g.upcoming_id)} aria-expanded={open} aria-controls={listId} aria-label={open ? 'Hide the shops' : 'Show the shops to message'} title={open ? 'Hide' : 'Show the shops'} className={cn(ICON_BTN, 'text-muted-foreground hover:text-foreground')}>
                      <ChevronDown size={16} className={cn('transition-transform', open && 'rotate-180')} aria-hidden="true" />
                    </button>
                    <button type="button" onClick={() => told(g)} disabled={telling !== null} aria-label={`Mark ${g.model_code} as told`} title="Mark as told — clears these requests once you have messaged the shops" className={cn(ICON_BTN, 'text-muted-foreground hover:text-foreground')}>
                      <Check size={16} aria-hidden="true" />
                    </button>
                  </div>
                  {open && (
                    <ul id={listId} className="border-t border-border bg-muted/30 px-4 py-1">
                      {g.phones.map((p) => {
                        const wa = waLink(p, message(g))
                        return (
                          <li key={p} className="flex items-center justify-between gap-3 py-1.5">
                            <span className="text-[13px] tabular-nums">{p}</span>
                            {wa && (
                              <a href={wa} target="_blank" rel="noreferrer" aria-label={`WhatsApp ${p}`} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 text-[12.5px] font-semibold text-[#1d9e50] hover:bg-muted">
                                <MessageCircle size={14} aria-hidden="true" /> WhatsApp
                              </a>
                            )}
                          </li>
                        )
                      })}
                      {!g.phones.length && <li className="py-1.5 text-[12px] text-muted-foreground">No phone number was left with these requests.</li>}
                    </ul>
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
