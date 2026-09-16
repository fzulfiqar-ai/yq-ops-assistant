import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ClipboardList, Loader2, MessageCircle, Phone, Search, ShoppingBag, Users, X } from 'lucide-react'
import type { StaffCustomer } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { fmtDate } from '@/lib/format'
import { initials, setSelectedCustomer, telLink, useCustomers, waLink } from './lib'

/**
 * /customers — the shops this salesman has served, searchable, each one tap from a new order
 * (the catalog opens with the shop pre-selected), their orders, a call or a WhatsApp.
 */
export default function Customers() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useCustomers()
  const [q, setQ] = useState('')
  const rows = useMemo(() => {
    const all = data || []
    const s = q.trim().toLowerCase()
    if (!s) return all
    return all.filter((c) => [c.shop, c.name, c.area, c.phone].some((v) => (v || '').toLowerCase().includes(s)))
  }, [data, q])

  const orderFor = (c: StaffCustomer) => {
    setSelectedCustomer(c)
    navigate('/shop')
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-4 lg:px-8 lg:py-8">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-[22px] font-bold leading-tight tracking-tight lg:text-[28px]">Customers</h1>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">{data ? `${data.length} shops you have served` : 'Shops you have served'}</p>
        </div>
      </div>

      <div className="mt-4 flex h-12 items-center gap-2 rounded-2xl border border-border bg-card px-3.5 focus-within:border-primary lg:max-w-md">
        <Search size={16} className="text-muted-foreground" aria-hidden="true" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search shop, name, area, phone…" aria-label="Search customers" className="h-full w-full bg-transparent text-[16px] outline-none placeholder:text-muted-foreground/70" />
        {q && (
          <button type="button" onClick={() => setQ('')} aria-label="Clear" className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
            <X size={15} />
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="grid h-40 place-items-center text-muted-foreground">
          <Loader2 size={20} className="animate-spin" />
        </div>
      ) : isError ? (
        <p className="mt-6 text-[13px] text-destructive">Could not load your customers. Pull to refresh or try again in a moment.</p>
      ) : rows.length === 0 ? (
        <div className="mt-6 rounded-2xl border border-border bg-card px-6 py-12 text-center">
          <Users size={28} className="mx-auto text-muted-foreground" aria-hidden="true" />
          <p className="mt-3 font-display text-[15px] font-bold">{q ? 'No shop matches' : 'No customers yet'}</p>
          <p className="mt-1 text-[12.5px] text-muted-foreground">{q ? 'Try another word or a phone number.' : 'Shops appear here after their first order — from your link or placed by you.'}</p>
          {!q && (
            <button type="button" onClick={() => navigate('/shop')} className="mt-5 inline-flex h-11 items-center gap-2 rounded-xl bg-primary px-4 text-[13px] font-semibold text-primary-foreground">
              <ShoppingBag size={15} aria-hidden="true" /> Place the first order
            </button>
          )}
        </div>
      ) : (
        <ul className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((c, i) => {
            const title = c.shop || c.name
            const wa = waLink(c.phone, `Hello ${c.name || ''},`)
            const tel = telLink(c.phone)
            return (
              <li key={`${c.phone || c.name}-${i}`} className="rounded-2xl border border-border bg-card p-3.5">
                <div className="flex items-start gap-3">
                  <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-accent font-display text-[13px] font-bold text-accent-foreground">{initials(title)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[15px] font-semibold leading-tight">{title}</div>
                    <div className="mt-0.5 truncate text-[12px] text-muted-foreground">{[c.shop ? c.name : null, c.area, c.phone].filter(Boolean).join(' · ')}</div>
                    <div className="mt-1 text-[11.5px] text-muted-foreground">
                      {c.orders ? `${c.orders} order${c.orders === 1 ? '' : 's'}` : 'No orders yet'}
                      {c.last_order_at ? ` · last ${fmtDate(c.last_order_at)}` : ''}
                    </div>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-[1fr_auto_auto_auto] gap-1.5">
                  <button type="button" onClick={() => orderFor(c)} className="inline-flex h-10 items-center justify-center gap-1.5 rounded-xl bg-primary px-3 text-[12.5px] font-semibold text-primary-foreground">
                    <ShoppingBag size={14} aria-hidden="true" /> New order
                  </button>
                  <button type="button" onClick={() => navigate(`/shop-orders?q=${encodeURIComponent(c.phone || c.name || '')}&bucket=done`)} aria-label="Their orders" title="Their orders" className="grid h-10 w-10 place-items-center rounded-xl border border-border bg-card text-foreground hover:bg-muted">
                    <ClipboardList size={16} aria-hidden="true" />
                  </button>
                  <a href={wa || '#'} target="_blank" rel="noreferrer" aria-label="WhatsApp" title="WhatsApp" className={cn('grid h-10 w-10 place-items-center rounded-xl border border-border bg-card text-[#1d9e50] hover:bg-muted', !wa && 'pointer-events-none opacity-40')}>
                    <MessageCircle size={16} aria-hidden="true" />
                  </a>
                  <a href={tel || '#'} aria-label="Call" title="Call" className={cn('grid h-10 w-10 place-items-center rounded-xl border border-border bg-card text-foreground hover:bg-muted', !tel && 'pointer-events-none opacity-40')}>
                    <Phone size={16} aria-hidden="true" />
                  </a>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
