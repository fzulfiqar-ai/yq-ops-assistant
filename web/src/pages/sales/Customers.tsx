import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ClipboardList, MessageCircle, Phone, RotateCcw, Search, Send, ShoppingBag, Users, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { CreditChip, StatusBadge } from '@/pages/shop/ShopPicker'
import { ShopSheet } from '@/pages/shop/ShopSheet'
import { HIT, SKELETON } from '@/pages/shop/shared'
import { LoadFailed } from './LoadFailed'
import { customerOf, dayLabel, initials, phoneLabel, setSelectedCustomer, telLink, useFollowups, useOrderAgain, useShopBook, waLink, type BookShop } from './lib'
import { FollowupRow } from './FollowUps'

/**
 * /customers — the rep's book. "Shops" (Sprint 5) is the merged book: his Focus regulars and the
 * shops he has ordered for in the app, one card per shop, due first — each one tap from a new
 * order, "Order again" (its usual order in the cart), a restock link, a call or a WhatsApp.
 *
 * R3a: two more views built from Focus sales — "Due" (shops past their usual rhythm, most valuable
 * first) and "Lapsed" (quiet for months, win-back). ?view=due|lapsed deep-links from Today.
 */

type View = 'all' | 'due' | 'lapsed'

function norm(s?: string | null): string {
  return String(s || '').toLowerCase().replace(/[^0-9a-z؀-ۿ]+/g, '')
}

const ICON = cn('relative grid h-10 w-10 place-items-center rounded-xl border border-border bg-card hover:bg-muted', HIT)

function ShopCard({ s, onRestock }: { s: BookShop; onRestock: (s: BookShop) => void }) {
  const navigate = useNavigate()
  const orderAgain = useOrderAgain()
  const wa = waLink(s.phone, `Hello${s.contact_name ? ` ${s.contact_name.split(' ')[0]}` : ''}, YQ here.`)
  const tel = telLink(s.phone)
  return (
    <li className="min-w-0 rounded-2xl border border-border bg-card p-3.5">
      <div className="flex items-start gap-3">
        <span className={cn('grid h-11 w-11 shrink-0 place-items-center rounded-full font-display text-[13px] font-bold', s.due ? 'bg-primary text-primary-foreground' : 'bg-accent text-accent-foreground')}>{initials(s.name)}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="min-w-0 truncate text-[15px] font-semibold leading-tight">{s.name}</span>
            <StatusBadge shop={s} />
          </div>
          <div className="mt-0.5 truncate text-[12px] text-muted-foreground">{[s.contact_name, s.area, s.phone ? phoneLabel(s.phone) : 'no phone on file'].filter(Boolean).join(' · ')}</div>
          <div className="mt-1 text-[11.5px] text-muted-foreground">
            {s.last_order_date ? `Last order ${dayLabel(s.last_order_date)}` : 'No order yet'}
            {s.app_orders ? ` · ${s.app_orders} in the app` : ''}
            {s.sources.length === 1 && s.sources[0] === 'app' ? ' · not in Focus yet' : ''}
          </div>
          {s.credit && <CreditChip credit={s.credit} className="mt-1.5" />}
        </div>
      </div>
      {/* 40 px buttons, 44 px under a finger (HIT; the 6 px gaps take the 2 px each side reaches) */}
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] gap-1.5">
        <button type="button" onClick={() => orderAgain(customerOf(s))} className={cn('relative inline-flex h-10 min-w-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl bg-primary px-2 text-[12.5px] font-semibold text-primary-foreground', HIT)}>
          {/* the text clips itself: overflow-hidden on the button would cut its own hit area */}
          <RotateCcw size={14} aria-hidden="true" className="shrink-0" /> <span className="min-w-0 truncate">Order again</span>
        </button>
        <button
          type="button"
          onClick={() => {
            setSelectedCustomer(customerOf(s))
            navigate('/shop')
          }}
          aria-label={`New order for ${s.name}`}
          title="New order"
          className={cn(ICON, 'text-primary')}
        >
          <ShoppingBag size={16} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => onRestock(s)} aria-label={`Send ${s.name} a restock link`} title="Send restock link" className={cn(ICON, 'text-[#137a48]')}>
          <Send size={16} aria-hidden="true" />
        </button>
        <a href={wa || '#'} target="_blank" rel="noreferrer" aria-label="WhatsApp" title="WhatsApp" className={cn(ICON, 'text-[#1d9e50]', !wa && 'pointer-events-none opacity-40')}>
          <MessageCircle size={16} aria-hidden="true" />
        </a>
        <a href={tel || '#'} aria-label="Call" title="Call" className={cn(ICON, 'text-foreground', !tel && 'pointer-events-none opacity-40')}>
          <Phone size={16} aria-hidden="true" />
        </a>
      </div>
      {s.sources.includes('app') && s.phone && (
        <button type="button" onClick={() => navigate(`/shop-orders?q=${encodeURIComponent(s.phone || '')}&bucket=done`)} className={cn('relative mt-2 inline-flex h-9 items-center gap-1.5 rounded-lg px-1 text-[12px] font-semibold text-primary hover:underline', HIT)}>
          <ClipboardList size={13} aria-hidden="true" /> Their app orders
        </button>
      )}
    </li>
  )
}

/** The book while it loads: shop cards (a circle, three lines, the action row) in the one loading look. */
function CardsSkeleton() {
  return (
    <div role="status">
      <span className="sr-only">Loading your shops…</span>
      <ul aria-hidden="true" className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <li key={i} className="min-w-0 rounded-2xl border border-border bg-card p-3.5">
            <div className="flex items-start gap-3">
              <div className={cn(SKELETON, 'h-11 w-11 shrink-0 rounded-full')} />
              <div className="min-w-0 flex-1 space-y-2 pt-1">
                <div className={cn(SKELETON, 'h-3.5 w-2/3 rounded')} />
                <div className={cn(SKELETON, 'h-3 w-1/2 rounded')} />
                <div className={cn(SKELETON, 'h-2.5 w-1/3 rounded')} />
              </div>
            </div>
            <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] gap-1.5">
              <div className={cn(SKELETON, 'h-10 rounded-xl')} />
              {[0, 1, 2, 3].map((n) => (
                <div key={n} className={cn(SKELETON, 'h-10 w-10 rounded-xl')} />
              ))}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Due / Lapsed while they load: the list's rows (a name, a reason, the product chips, the actions). */
function RowsSkeleton() {
  return (
    <div role="status">
      <span className="sr-only">Loading your Focus book…</span>
      <ul aria-hidden="true" className="mt-4 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
        {Array.from({ length: 4 }, (_, i) => (
          <li key={i} className="space-y-2 px-4 py-3.5">
            <div className={cn(SKELETON, 'h-3.5 w-1/2 rounded')} />
            <div className={cn(SKELETON, 'h-3 w-3/4 rounded')} />
            <div className={cn(SKELETON, 'mt-2.5 h-10 rounded-xl')} />
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function Customers() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const view: View = params.get('view') === 'due' ? 'due' : params.get('view') === 'lapsed' ? 'lapsed' : 'all'
  const setView = (v: View) => setParams(v === 'all' ? {} : { view: v }, { replace: true })
  const book = useShopBook()
  const fu = useFollowups()
  const [q, setQ] = useState('')
  const [restock, setRestock] = useState<BookShop | null>(null)
  const rows = useMemo(() => {
    const all = book.data?.shops || []
    const words = q.toLowerCase().split(/\s+/).map(norm).filter(Boolean)
    if (!words.length) return all
    return all.filter((s) => {
      const hay = [s.name, s.focus_name, s.contact_name, s.area].map(norm).join(' ')
      return words.every((w) => hay.includes(w) || (/^\d{3,}$/.test(w) && String(s.phone || '').includes(w)))
    })
  }, [book.data, q])
  const focusRows = useMemo(() => {
    const list = view === 'due' ? fu.data?.due || [] : view === 'lapsed' ? fu.data?.lapsed || [] : []
    const s = q.trim().toLowerCase()
    return s ? list.filter((r) => r.shop.toLowerCase().includes(s) || r.top_skus.some((k) => k.item_code.toLowerCase().includes(s))) : list
  }, [fu.data, view, q])

  const counts = fu.data?.counts
  const tabs: { key: View; label: string; n?: number }[] = [
    { key: 'all', label: 'Shops', n: book.data?.count },
    { key: 'due', label: 'Due', n: counts?.due ?? book.data?.counts?.due },
    { key: 'lapsed', label: 'Lapsed', n: counts?.lapsed },
  ]

  return (
    <div className="mx-auto max-w-6xl px-4 py-4 lg:px-8 lg:py-8">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-[22px] font-bold leading-tight tracking-tight lg:text-[28px]">Shops</h1>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">
            {view === 'all'
              ? book.data ? `${book.data.count} shops — your Focus regulars and your app customers, due first` : 'Your Focus regulars and your app customers'
              : view === 'due'
                ? 'Past their usual rhythm — most valuable first'
                : 'Quiet for months — worth a visit'}
          </p>
        </div>
      </div>

      {/* view switch: the merged book · due · lapsed (Focus) */}
      <div role="tablist" aria-label="Which shops" className="mt-4 inline-grid grid-cols-3 rounded-xl border border-border bg-card p-1">
        {tabs.map((t) => (
          <button key={t.key} role="tab" type="button" aria-selected={view === t.key} onClick={() => setView(t.key)}
            className={cn('relative inline-flex h-9 items-center justify-center gap-1.5 rounded-lg px-3 text-[13px] font-semibold', HIT, view === t.key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted')}>
            {t.label}
            {t.n != null && <span className={cn('rounded-full px-1.5 text-[11px] tabular-nums', view === t.key ? 'bg-primary-foreground/20' : 'bg-muted')}>{t.n}</span>}
          </button>
        ))}
      </div>

      <div className="mt-3 flex h-12 items-center gap-2 rounded-2xl border border-border bg-card px-3.5 focus-within:border-primary lg:max-w-md">
        <Search size={16} className="text-muted-foreground" aria-hidden="true" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={view === 'all' ? 'Shop, contact, area or phone…' : 'Search shop or item code…'} aria-label="Search shops" className="h-full w-full bg-transparent text-[16px] outline-none placeholder:text-muted-foreground/70" />
        {q && (
          <button type="button" onClick={() => setQ('')} aria-label="Clear" className={cn('relative grid h-9 w-9 place-items-center rounded-lg text-muted-foreground hover:bg-muted', HIT)}>
            <X size={15} />
          </button>
        )}
      </div>

      {view !== 'all' ? (
        fu.isLoading ? (
          <RowsSkeleton />
        ) : fu.isError ? (
          <LoadFailed className="mt-6" title="Your Focus book did not load" onRetry={() => void fu.refetch()} retrying={fu.isFetching} />
        ) : fu.data?.hint ? (
          <p className="mt-6 rounded-2xl border border-border bg-card px-4 py-5 text-[13px] text-muted-foreground">{fu.data.hint}</p>
        ) : focusRows.length === 0 ? (
          <div className="mt-6 rounded-2xl border border-border bg-card px-6 py-12 text-center">
            <Users size={28} className="mx-auto text-muted-foreground" aria-hidden="true" />
            <p className="mt-3 font-display text-[15px] font-bold">{q ? 'No shop matches' : view === 'due' ? 'Nothing due right now' : 'No lapsed shops'}</p>
            <p className="mt-1 text-[12.5px] text-muted-foreground">{q ? 'Try another word or an item code.' : 'Built from your Focus sales — it changes as new sales load.'}</p>
          </div>
        ) : (
          <>
            <ul className="mt-4 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
              {focusRows.map((r) => <FollowupRow key={r.shop} r={r} kind={view} />)}
            </ul>
            <p className="mt-2 px-1 text-[11.5px] text-muted-foreground">
              From your Focus sales{fu.data?.data_through ? ` to ${dayLabel(fu.data.data_through)}` : ''}. A cadence and an age, never a promised date.
              A random fifth of shops is held back from this list so the office can measure whether it helps.
            </p>
          </>
        )
      ) : book.isLoading ? (
        <CardsSkeleton />
      ) : book.isError ? (
        <LoadFailed className="mt-6" title="Your shops did not load" onRetry={() => void book.refetch()} retrying={book.isFetching} />
      ) : (
        <>
          {book.data?.error && <p className="mt-4 rounded-xl bg-[#fdf3e3] px-3 py-2 text-[12.5px] text-[#96600d]">{book.data.error}</p>}
          {book.data?.hint && <p className="mt-4 rounded-xl border border-border bg-card px-3 py-2 text-[12.5px] text-muted-foreground">{book.data.hint}</p>}
          {rows.length === 0 ? (
            <div className="mt-6 rounded-2xl border border-border bg-card px-6 py-12 text-center">
              <Users size={28} className="mx-auto text-muted-foreground" aria-hidden="true" />
              <p className="mt-3 font-display text-[15px] font-bold">{q ? 'No shop matches' : 'No shops yet'}</p>
              <p className="mt-1 text-[12.5px] text-muted-foreground">{q ? 'Try another word or a phone number.' : 'Your Focus regulars appear once your login has a Focus name; app customers after their first order.'}</p>
              {!q && (
                <button type="button" onClick={() => navigate('/shop')} className="mt-5 inline-flex h-11 items-center gap-2 rounded-xl bg-primary px-4 text-[13px] font-semibold text-primary-foreground">
                  <ShoppingBag size={15} aria-hidden="true" /> Place the first order
                </button>
              )}
            </div>
          ) : (
            <ul className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {rows.map((s) => <ShopCard key={s.key} s={s} onRestock={setRestock} />)}
            </ul>
          )}
          {book.data?.data_through && (
            <p className="mt-2 px-1 text-[11.5px] text-muted-foreground">
              Focus sales to {dayLabel(book.data.data_through)}. Due = past the shop's own rhythm. The over-90 chip is the Focus ageing — collect tactfully.
            </p>
          )}
        </>
      )}
      <ShopSheet shop={restock} open={Boolean(restock)} onClose={() => setRestock(null)} />
    </div>
  )
}
