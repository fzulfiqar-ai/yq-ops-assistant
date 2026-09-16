import { useMemo, useState } from 'react'
import { ChevronDown, Plus, Search, Store, X } from 'lucide-react'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import type { StaffCustomer } from '@/lib/shopApi'
import { initials, setSelectedCustomer, useCustomers, useSelectedCustomer } from '@/pages/sales/lib'

/**
 * Salesman catalog, top of page: who is this order for?
 * Picking the shop first means the checkout is already filled in when the cart is ready,
 * and the salesman can see whose order he is building while he adds lines. Optional — he can
 * still leave it blank and type a new shop at checkout.
 */
export function CustomerBar() {
  const sel = useSelectedCustomer()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState(String())
  const { data, isLoading } = useCustomers(open)
  const rows = useMemo(() => {
    const all = data || []
    const s = q.trim().toLowerCase()
    if (!s) return all
    return all.filter((c) => [c.shop, c.name, c.area, c.phone].some((v) => (v || String()).toLowerCase().includes(s)))
  }, [data, q])

  const pick = (c: StaffCustomer | null) => {
    setSelectedCustomer(c)
    setOpen(false)
    setQ(String())
  }

  return (
    <>
      <div className="mx-auto max-w-6xl px-4 pb-2 pt-3">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-haspopup="dialog"
          className={cn(
            'flex h-12 w-full items-center gap-3 rounded-2xl border px-3 text-left transition-colors duration-150',
            sel ? 'border-[#6D4091]/40 bg-[#EEE8F4]' : 'border-dashed border-[#CFC3DE] bg-white hover:border-[#6D4091]',
          )}
        >
          {sel ? (
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#6D4091] font-display text-[11.5px] font-bold text-white">{initials(sel.shop || sel.name)}</span>
          ) : (
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#EEE8F4] text-[#6D4091]">
              <Store size={15} aria-hidden="true" />
            </span>
          )}
          <span className="min-w-0 flex-1 leading-tight">
            {sel ? (
              <>
                <span className="block truncate text-[13.5px] font-semibold text-[#1A1428]">{sel.shop || sel.name}</span>
                <span className="block truncate text-[11.5px] text-[#6b6478]">{[sel.shop ? sel.name : null, sel.area, sel.phone].filter(Boolean).join(' · ')}</span>
              </>
            ) : (
              <>
                <span className="block text-[13.5px] font-semibold text-[#1A1428]">Who is this order for?</span>
                <span className="block text-[11.5px] text-[#6b6478]">Pick a shop now, or fill it in at checkout</span>
              </>
            )}
          </span>
          <span className="shrink-0 text-[12px] font-semibold text-[#6D4091]">{sel ? 'Change' : 'Choose'}</span>
          <ChevronDown size={15} className="shrink-0 text-[#6D4091]" aria-hidden="true" />
        </button>
      </div>

      <Sheet open={open} onClose={() => setOpen(false)} title="Order for which shop?" variant="dialog">
        <div className="space-y-3 p-4">
          <div className="flex h-12 items-center gap-2 rounded-2xl border border-[#E2DCEA] bg-white px-3.5 focus-within:border-[#6D4091]">
            <Search size={16} className="text-[#6b6478]" aria-hidden="true" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search shop, name, area, phone…" aria-label="Search customers" className="h-full w-full bg-transparent text-[16px] outline-none placeholder:text-[#a8a2bb]" />
            {q && (
              <button type="button" onClick={() => setQ(String())} aria-label="Clear" className="grid h-9 w-9 place-items-center rounded-lg text-[#6b6478]">
                <X size={15} />
              </button>
            )}
          </div>
          <button type="button" onClick={() => pick(null)} className="flex h-12 w-full items-center gap-3 rounded-xl border border-dashed border-[#CFC3DE] bg-white px-3 text-left text-[13.5px] font-semibold text-[#1A1428] hover:border-[#6D4091]">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-[#EEE8F4] text-[#6D4091]">
              <Plus size={15} aria-hidden="true" />
            </span>
            New shop — type details at checkout
          </button>
          <ul className="max-h-[50vh] divide-y divide-[#E9E4EF] overflow-y-auto overscroll-contain rounded-xl border border-[#E9E4EF] bg-white">
            {isLoading && <li className="px-3 py-6 text-center text-[12.5px] text-[#6b6478]">Loading your shops…</li>}
            {!isLoading && rows.length === 0 && <li className="px-3 py-6 text-center text-[12.5px] text-[#6b6478]">{q ? 'No shop matches.' : 'No customers yet — they appear after their first order.'}</li>}
            {rows.map((c, i) => {
              const on = sel != null && (sel.phone || sel.name) === (c.phone || c.name)
              return (
                <li key={`${c.phone || c.name}-${i}`}>
                  <button type="button" onClick={() => pick(c)} className={cn('flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-[#F9F7F3]', on && 'bg-[#EEE8F4]')}>
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#EEE8F4] font-display text-[12px] font-bold text-[#5A3478]">{initials(c.shop || c.name)}</span>
                    <span className="min-w-0 flex-1 leading-tight">
                      <span className="block truncate text-[13.5px] font-semibold text-[#1A1428]">{c.shop || c.name}</span>
                      <span className="block truncate text-[11.5px] text-[#6b6478]">{[c.shop ? c.name : null, c.area, c.phone].filter(Boolean).join(' · ')}</span>
                    </span>
                    {c.orders ? <span className="shrink-0 text-[11px] tabular-nums text-[#6b6478]">{c.orders} orders</span> : null}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      </Sheet>
    </>
  )
}
