import { useMemo, useState } from 'react'
import { ChevronDown, Loader2, Plus, Search, Store, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import type { StaffCustomer } from '@/lib/shopApi'
import { customerOf, dayLabel, initials, phoneLabel, useShopBook, type BookShop, type CreditChip as Credit } from '@/pages/sales/lib'
import { RING } from './shared'

/**
 * The one shop picker (Sprint 5) — it replaces the old CustomerBar and the checkout's "recent
 * customers" chips. It lists the rep's merged book (GET /shop/me/book): his Focus regulars and the
 * shops he has ordered for in the app, one row per shop, the ones due first. Each row says when the
 * shop last bought, whether it is due, and whether its Focus account carries a balance over 90 days.
 */

/** "Over 90 days: BHD 45.500" — shown wherever a shop is picked, never hidden in a tooltip. */
export function CreditChip({ credit, className }: { credit?: Credit | null; className?: string }) {
  if (!credit) return null
  return (
    <Badge tone="rose" className={className} title={`Owes BHD ${credit.outstanding_bhd} in all${credit.as_of ? ` (ageing of ${dayLabel(credit.as_of)})` : ''}`}>
      Over 90 days · BHD {credit.over_90_bhd}
    </Badge>
  )
}

export function StatusBadge({ shop }: { shop: Pick<BookShop, 'status' | 'due'> }) {
  if (shop.due) return <Badge tone="accent">Due</Badge>
  if (shop.status === 'lapsed') return <Badge tone="grey">Win back</Badge>
  return null
}

function metaLine(s: Pick<BookShop, 'contact_name' | 'area' | 'last_order_date' | 'focus_name' | 'sources'>): string {
  return [
    s.contact_name,
    s.area,
    s.last_order_date ? `last ${dayLabel(s.last_order_date)}` : 'no order yet',
    s.sources?.length === 1 && s.sources[0] === 'app' ? 'app only' : null,
  ]
    .filter(Boolean)
    .join(' · ')
}

/** The header of the salesman catalog: who this order is for. */
export function ShopPill({
  selected,
  row,
  onOpen,
  className,
}: {
  selected: StaffCustomer | null
  /** the selected shop's book row, when known (due / credit / last order) */
  row?: BookShop | null
  onOpen: () => void
  className?: string
}) {
  const title = selected ? selected.shop || selected.name : ''
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      className={cn(
        'flex min-h-[3.25rem] w-full items-center gap-3 rounded-2xl border px-3 py-2 text-left transition-colors duration-150',
        RING,
        selected ? 'border-[#6D4091]/35 bg-[#EEE8F4]' : 'border-dashed border-[#CFC3DE] bg-white hover:border-[#6D4091]',
        className,
      )}
    >
      {selected ? (
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#6D4091] font-display text-[12px] font-bold text-white">{initials(title) || 'YQ'}</span>
      ) : (
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#EEE8F4] text-[#6D4091]">
          <Store size={16} aria-hidden="true" />
        </span>
      )}
      <span className="min-w-0 flex-1 leading-tight">
        {selected ? (
          <>
            <span className="flex items-center gap-1.5">
              <span className="truncate text-[14px] font-semibold text-[#1A1428]">{title}</span>
              {row && <StatusBadge shop={row} />}
            </span>
            <span className="mt-0.5 block truncate text-[11.5px] text-[#6b6480]">
              {row ? metaLine(row) : [selected.shop ? selected.name : null, selected.area, phoneLabel(selected.phone)].filter(Boolean).join(' · ') || 'New shop'}
            </span>
            {row?.credit && <CreditChip credit={row.credit} className="mt-1" />}
          </>
        ) : (
          <>
            <span className="block text-[14px] font-semibold text-[#1A1428]">Who is this order for?</span>
            <span className="block text-[11.5px] text-[#6b6480]">Pick a shop — its usual order is one tap away</span>
          </>
        )}
      </span>
      <span className="flex shrink-0 items-center gap-1 text-[12px] font-semibold text-[#6D4091]">
        {selected ? 'Shop' : 'Choose'}
        <ChevronDown size={15} aria-hidden="true" />
      </span>
    </button>
  )
}

function norm(s?: string | null): string {
  return String(s || '').toLowerCase().replace(/[^0-9a-z؀-ۿ]+/g, '')
}

/** Every word of the query must hit the name, the contact, the area or the phone. */
function matches(s: BookShop, q: string): boolean {
  const words = q.toLowerCase().split(/\s+/).map(norm).filter(Boolean)
  if (!words.length) return true
  const hay = [s.name, s.focus_name, s.contact_name, s.area].map(norm).join(' ')
  const digits = String(s.phone || '')
  return words.every((w) => hay.includes(w) || (/^\d{3,}$/.test(w) && digits.includes(w)))
}

export function ShopPickerSheet({
  open,
  onClose,
  onPick,
  selectedKey,
}: {
  open: boolean
  onClose: () => void
  /** null = "a new shop, typed at checkout" */
  onPick: (c: StaffCustomer | null, row: BookShop | null) => void
  selectedKey?: string
}) {
  const [q, setQ] = useState('')
  const book = useShopBook(open)
  const all = useMemo(() => book.data?.shops || [], [book.data])
  const rows = useMemo(() => all.filter((s) => matches(s, q)), [all, q])
  const due = q ? [] : rows.filter((s) => s.due)
  const rest = q ? rows : rows.filter((s) => !s.due)
  // a desk with a keyboard types straight away; a phone keeps its keyboard down until asked
  const fine = typeof window !== 'undefined' && window.matchMedia?.('(pointer: fine)').matches

  const pick = (s: BookShop | null) => {
    onPick(s ? customerOf(s) : null, s)
    setQ('')
    onClose()
  }

  const row = (s: BookShop) => {
    const on = s.key === selectedKey
    return (
      <li key={s.key}>
        <button type="button" onClick={() => pick(s)} aria-pressed={on} className={cn('flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors duration-150 hover:bg-[#F9F7F3]', on && 'bg-[#EEE8F4]')}>
          <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-full font-display text-[12px] font-bold', s.due ? 'bg-[#6D4091] text-white' : 'bg-[#EEE8F4] text-[#5A3478]')}>{initials(s.name) || '·'}</span>
          <span className="min-w-0 flex-1 leading-tight">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-[13.5px] font-semibold text-[#1A1428]">{s.name}</span>
              <StatusBadge shop={s} />
            </span>
            <span className="mt-0.5 block truncate text-[11.5px] text-[#6b6480]">{metaLine(s)}</span>
            {s.credit && <CreditChip credit={s.credit} className="mt-1" />}
          </span>
        </button>
      </li>
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title="Order for which shop?" subtitle={book.data ? `${book.data.count} shops in your book` : undefined} variant="dialog">
      <div className="space-y-3 p-4">
        <div className="flex h-12 items-center gap-2 rounded-2xl border border-[#E2DCEA] bg-white px-3.5 focus-within:border-[#6D4091]">
          <Search size={16} className="text-[#6b6480]" aria-hidden="true" />
          <input
            autoFocus={fine}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Shop, contact, area or phone…"
            aria-label="Search your shops"
            className="h-full w-full bg-transparent text-[16px] outline-none placeholder:text-[#a8a2bb]"
          />
          {q && (
            <button type="button" onClick={() => setQ('')} aria-label="Clear" className="grid h-9 w-9 place-items-center rounded-lg text-[#6b6480]">
              <X size={15} />
            </button>
          )}
        </div>
        <button type="button" onClick={() => pick(null)} className="flex h-12 w-full items-center gap-3 rounded-xl border border-dashed border-[#CFC3DE] bg-white px-3 text-left text-[13.5px] font-semibold text-[#1A1428] hover:border-[#6D4091]">
          <span className="grid h-8 w-8 place-items-center rounded-full bg-[#EEE8F4] text-[#6D4091]">
            <Plus size={15} aria-hidden="true" />
          </span>
          A new shop — type its details at checkout
        </button>
        {book.data?.error && <p className="rounded-xl bg-[#fdf3e3] px-3 py-2 text-[12px] text-[#96600d]">{book.data.error}</p>}
        {book.data?.hint && <p className="rounded-xl bg-[#F9F7F3] px-3 py-2 text-[12px] text-[#6b6480]">{book.data.hint}</p>}
        <div className="max-h-[52vh] overflow-y-auto overscroll-contain rounded-xl border border-[#E9E4EF] bg-white">
          {book.isLoading ? (
            <p className="flex items-center justify-center gap-2 px-3 py-8 text-[12.5px] text-[#6b6480]">
              <Loader2 size={15} className="animate-spin" aria-hidden="true" /> Loading your shops…
            </p>
          ) : book.isError ? (
            <p className="px-3 py-8 text-center text-[12.5px] text-[#9f1239]">Your shops did not load. Close this and try again.</p>
          ) : rows.length === 0 ? (
            <p className="px-3 py-8 text-center text-[12.5px] text-[#6b6480]">{q ? 'No shop matches.' : 'No shops yet — they appear after your first order.'}</p>
          ) : (
            <>
              {due.length > 0 && (
                <>
                  <h3 className="sticky top-0 z-[1] border-b border-[#F3F0F6] bg-[#F9F7F3] px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6D4091]">Due now · {due.length}</h3>
                  <ul className="divide-y divide-[#F3F0F6]">{due.map(row)}</ul>
                </>
              )}
              {rest.length > 0 && (
                <>
                  {due.length > 0 && <h3 className="sticky top-0 z-[1] border-y border-[#F3F0F6] bg-[#F9F7F3] px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">All shops · latest first</h3>}
                  <ul className="divide-y divide-[#F3F0F6]">{rest.map(row)}</ul>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </Sheet>
  )
}
