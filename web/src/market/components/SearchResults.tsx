import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Clock, Hash, MessageCircle, TrendingUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { recentSearches } from '../lib/device'
import { categorySlug, niceCategory } from '../lib/format'
import { popularTerms, type Grouped } from '../lib/searchGroups'
import { S } from '../strings'
import { AnchorButton } from '../ui/Button'
import { Skeleton } from '../ui/Skeleton'
import { MarketCard } from './MarketCard'

/**
 * Search, shared by the Search tab and the desktop palette.
 *
 * Empty query: recent searches, "Popular right now" (client-derived from best sellers and the
 * merchant vocabulary until the backend exposes real terms), category shortcuts.
 * Typing: instant grouped results — Codes (exact/prefix, ≤3) · Products (≤20, list rows with
 * inline Add so a mission shopper never leaves) · Categories (≤2).
 */

export function SearchEmpty({ onPick }: { onPick: (q: string) => void }) {
  const { items, categories } = useMarket()
  const [recent] = useState<string[]>(() => recentSearches())
  const popular = useMemo(() => popularTerms(items), [items])
  const chip = 'inline-flex h-9 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm text-ink transition duration-1 ease-m hover:border-ink/25 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'
  return (
    <div className="space-y-6">
      <p className="flex items-center gap-1.5 text-xs text-ink-2">
        <Hash size={13} aria-hidden="true" /> {S.search.codeHint}
      </p>
      {recent.length > 0 && (
        <section aria-label={S.search.recent}>
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.recent}</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {recent.map((r) => (
              <button key={r} type="button" onClick={() => onPick(r)} className={chip}>
                <Clock size={13} className="text-ink-3" aria-hidden="true" /> {r}
              </button>
            ))}
          </div>
        </section>
      )}
      {popular.length > 0 && (
        <section aria-label={S.search.popular}>
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.popular}</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {popular.map((r) => (
              <button key={r} type="button" onClick={() => onPick(r)} className={chip}>
                <TrendingUp size={13} className="text-plum" aria-hidden="true" /> {r}
              </button>
            ))}
          </div>
        </section>
      )}
      <section aria-label={S.categories.title}>
        <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.categories}</h2>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {categories.map((c) => (
            <Link key={c} to={`/t/${categorySlug(c)}`} className="flex h-12 items-center justify-between rounded-md border border-line bg-surface px-3 text-sm font-semibold text-ink transition duration-1 ease-m hover:border-ink/15 hover:shadow-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              {niceCategory(c)}
              <span className="text-xs font-normal tnum text-ink-3">{items.filter((i) => (i.category || 'OTHER') === c).length}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  )
}

export function SearchGroups({ q, grouped, hints, onPick, activeCode, from = 'search_row', className }: { q: string; grouped: Grouped; hints: string[]; onPick: (q: string) => void; activeCode?: string | null; from?: string; className?: string }) {
  const { categories, items, rep } = useMarket()
  const total = grouped.codes.length + grouped.products.length
  const askUrl = rep?.whatsapp_url && q.trim() ? `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(`Hello ${rep.first_name || ''}, do you have "${q.trim()}"?`)}` : null
  const chip = 'h-9 rounded-full border border-line bg-surface px-3.5 text-sm text-ink hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'

  if (total === 0 && grouped.categories.length === 0) {
    return (
      <div className={cn('rounded-lg border border-line bg-surface px-5 py-10 text-center', className)}>
        <p className="font-display text-base font-bold text-ink">{S.states.noMatch(q.trim())}</p>
        <p className="mt-1 text-sm text-ink-2">{S.states.noMatchHint}</p>
        {hints.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center justify-center gap-1.5">
            <span className="text-xs text-ink-2">{S.states.didYouMean}</span>
            {hints.map((h) => (
              <button key={h} type="button" onClick={() => onPick(h)} className={cn(chip, 'font-semibold text-plum')}>
                {h}
              </button>
            ))}
          </div>
        )}
        <div className="mt-4 flex flex-wrap justify-center gap-1.5">
          {categories.map((c) => (
            <Link key={c} to={`/t/${categorySlug(c)}`} className={chip}>
              {niceCategory(c)}
            </Link>
          ))}
        </div>
        {askUrl && (
          <AnchorButton href={askUrl} target="_blank" rel="noreferrer" variant="wa" className="mt-5" icon={<MessageCircle size={15} aria-hidden="true" />}>
            {S.cart.ask(rep!.first_name || 'us')}
          </AnchorButton>
        )}
      </div>
    )
  }

  return (
    <div className={className}>
      {grouped.codes.length > 0 && (
        <section aria-label={S.search.codes}>
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.codes}</h2>
          <div className="mt-1">
            {grouped.codes.map((it) => (
              <div key={it.item_code} data-code={it.item_code} className={cn('rounded-sm transition-colors', activeCode === it.item_code && 'bg-plum-wash ring-1 ring-plum/20')}>
                <MarketCard item={it} variant="list" from={from} />
              </div>
            ))}
          </div>
        </section>
      )}
      {grouped.products.length > 0 && (
        <section aria-label={S.search.products} className={cn(grouped.codes.length > 0 && 'mt-5')}>
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">
            {S.search.products} <span className="tnum text-ink-3">· {grouped.products.length}</span>
          </h2>
          <div className="mt-1">
            {grouped.products.map((it) => (
              <div key={it.item_code} data-code={it.item_code} className={cn('rounded-sm transition-colors', activeCode === it.item_code && 'bg-plum-wash ring-1 ring-plum/20')}>
                <MarketCard item={it} variant="list" from={from} />
              </div>
            ))}
          </div>
        </section>
      )}
      {grouped.categories.length > 0 && (
        <section aria-label={S.search.categories} className="mt-5">
          <h2 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.search.categories}</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {grouped.categories.map((c) => (
              <Link key={c} to={`/t/${categorySlug(c)}`} className={cn(chip, 'inline-flex items-center font-semibold')}>
                {niceCategory(c)} <span className="ms-1 text-xs font-normal tnum text-ink-3">{items.filter((i) => (i.category || 'OTHER') === c).length}</span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

export function SearchPreparing() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label={S.search.preparing}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="h-14 w-14" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-3/5" />
            <Skeleton className="h-3 w-2/5" />
          </div>
          <Skeleton className="h-10 w-28" />
        </div>
      ))}
    </div>
  )
}
