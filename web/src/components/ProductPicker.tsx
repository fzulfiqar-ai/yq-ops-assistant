import { useEffect, useState, type ReactNode } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { CircleQuestionMark, Loader2, PackageOpen, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { fetchProducts, notReady, productsQueryKey, readRecentProducts, type MiProduct } from '@/lib/marketIntel'

/**
 * "Which product?" (Market Intel v2, release r8m): the YQ catalog with its photos, so a rep names a
 * product by tapping it instead of typing a code. With nothing typed it lists what this shop bought
 * lately ("At this shop", when the API knows), the products this phone picked lately ("Recent"),
 * then the rest; typing searches the catalog (GET /market-intel/products, Arabic read into the
 * catalog's words). "Not sure / not ours" is always one tap away: a rep never has to invent a SKU.
 * The words come from the caller (the Spotted sheet speaks English or Arabic; the case page English).
 */

export interface PickerWords {
  search: string
  atShop: string
  recent: string
  products: string
  results: string
  none: string
  failed: string
  loading: string
  notSure?: string
}

const FIELD =
  'h-11 w-full rounded-xl border border-[#E2DCEA] bg-white px-3 text-[14px] text-[#1A1428] placeholder:text-[#9a93ad] focus:border-[#824FAB] focus:outline-none focus:ring-2 focus:ring-[#824FAB]/25'
const HEAD = 'mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[#6b6480]'

export function ProductPicker({
  words,
  onPick,
  onNotSure,
  shop,
  showRecent = true,
  autoFocus = false,
}: {
  words: PickerWords
  onPick: (p: MiProduct) => void
  onNotSure?: () => void
  shop?: { phone?: string | null; name?: string | null } | null
  showRecent?: boolean
  autoFocus?: boolean
}) {
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  // ask the API once the rep pauses typing, not on every letter
  useEffect(() => {
    const t = window.setTimeout(() => setDq(q.trim()), 250)
    return () => window.clearTimeout(t)
  }, [q])
  const productsQ = useQuery({
    queryKey: productsQueryKey(shop, dq),
    queryFn: () => fetchProducts(dq, shop),
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
    // a route not there yet (503 / 404) is not retried: "Not sure" and the typed name still work
    retry: (n, e) => !notReady(e) && n < 1,
  })
  const [recent] = useState<MiProduct[]>(() => (showRecent ? readRecentProducts() : []))

  const rows = productsQ.data || []
  const searching = dq !== ''
  // "at this shop" only means something when the sheet knows the shop (the case page has none)
  const shopKnown = Boolean(shop && (String(shop.phone || '').replace(/\D/g, '') || shop.name))
  const here = (p: MiProduct) => shopKnown && Boolean(p.at_shop)
  let sections: { key: string; title: string; rows: MiProduct[] }[]
  if (searching) {
    // what this shop buys first, otherwise the API's own order
    const hits = [...rows.filter(here), ...rows.filter((p) => !here(p))].slice(0, 12)
    sections = [{ key: 'results', title: words.results, rows: hits }]
  } else {
    const atShop = rows.filter(here).slice(0, 6)
    const seen = new Set(atShop.map((p) => p.code))
    const mine = recent.filter((p) => !seen.has(p.code)).slice(0, 4)
    mine.forEach((p) => seen.add(p.code))
    const rest = rows.filter((p) => !seen.has(p.code)).slice(0, 6)
    sections = [
      { key: 'shop', title: words.atShop, rows: atShop },
      { key: 'recent', title: words.recent, rows: mine },
      { key: 'rest', title: words.products, rows: rest },
    ]
  }
  const shown = sections.filter((s) => s.rows.length > 0)

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search size={16} className="pointer-events-none absolute start-3 top-1/2 -translate-y-1/2 text-[#9a93ad]" aria-hidden="true" />
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={words.search}
          aria-label={words.search}
          enterKeyHint="search"
          autoComplete="off"
          autoFocus={autoFocus}
          className={cn(FIELD, 'ps-9 pe-9')}
        />
        {productsQ.isFetching && (
          <Loader2 size={15} className="absolute end-3 top-1/2 -translate-y-1/2 animate-spin text-[#824FAB]" aria-label={words.loading} />
        )}
      </div>

      {onNotSure && words.notSure && (
        <button
          type="button"
          onClick={onNotSure}
          className="inline-flex h-11 items-center gap-2 rounded-full border border-dashed border-[#C9B7DA] bg-white px-4 text-[13.5px] font-semibold text-[#4B2C66] hover:bg-[#FAF7FC]"
        >
          <CircleQuestionMark size={16} aria-hidden="true" /> {words.notSure}
        </button>
      )}

      {productsQ.isError && !rows.length ? (
        <p className="rounded-xl bg-[#fdf3e3] px-3 py-2.5 text-[12.5px] font-medium text-[#7a4d0a]">{words.failed}</p>
      ) : productsQ.isLoading ? (
        <div className="grid h-16 place-items-center text-[#824FAB]">
          <Loader2 size={18} className="animate-spin" aria-label={words.loading} />
        </div>
      ) : shown.length === 0 ? (
        searching ? <p className="text-[12.5px] text-[#6b6480]">{words.none}</p> : null
      ) : (
        shown.map((s) => (
          <div key={s.key}>
            <div className={HEAD}>{s.title}</div>
            <ul className="space-y-1.5">
              {s.rows.map((p) => (
                <li key={`${s.key}-${p.code}`}>
                  <ProductRow p={p} badge={here(p) && s.key !== 'shop' ? words.atShop : null} onPick={() => onPick(p)} />
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  )
}

export function ProductRow({ p, badge, onPick, action }: { p: MiProduct; badge?: string | null; onPick?: () => void; action?: ReactNode }) {
  const body = (
    <>
      <ProductThumb url={p.thumb_url || p.package_thumb_url} />
      <span className="min-w-0 flex-1">
        {/* catalog names are English: kept left to right inside the Arabic sheet */}
        <span dir="ltr" className="block truncate text-[13.5px] font-semibold text-[#1A1428] rtl:text-right">
          {p.name}
        </span>
        <span dir="ltr" className="block truncate text-[11.5px] text-[#6b6480] rtl:text-right">
          {[p.code, p.brand].filter(Boolean).join(' · ')}
        </span>
      </span>
      {badge && <span className="shrink-0 rounded-full bg-[#e8f7ee] px-2 py-0.5 text-[10.5px] font-semibold text-[#137a48]">{badge}</span>}
      {action}
    </>
  )
  return onPick ? (
    <button
      type="button"
      onClick={onPick}
      className="flex min-h-[56px] w-full items-center gap-3 rounded-xl border border-[#EFE9F5] bg-white p-2 text-start transition hover:border-[#D9CCE6] hover:bg-[#FAF7FC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6D4091]/50"
    >
      {body}
    </button>
  ) : (
    <div className="flex min-h-[56px] w-full items-center gap-3 rounded-xl border border-[#E2DCEA] bg-white p-2">{body}</div>
  )
}

function ProductThumb({ url }: { url?: string | null }) {
  const [broken, setBroken] = useState(false)
  return url && !broken ? (
    <img src={url} alt="" loading="lazy" decoding="async" onError={() => setBroken(true)} className="h-11 w-11 shrink-0 rounded-lg border border-[#EFE9F5] bg-white object-contain" />
  ) : (
    <span className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-[#F3EDF8] text-[#824FAB]">
      <PackageOpen size={18} aria-hidden="true" />
    </span>
  )
}
