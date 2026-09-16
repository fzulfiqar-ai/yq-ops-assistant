import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Clock, MessageCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { RING } from '@/pages/shop/shared'
import { useMarket } from '../MarketContext'
import { BTN_SECONDARY, CategoryChips, EmptyState, QtySheet, SearchBox } from '../components/Bits'
import { BottomNav, CartBar, Page, TopBar } from '../components/Chrome'
import { MarketCard } from '../components/MarketCard'
import { recentSearches, rememberSearch } from '../lib/device'
import { track, trackSearch } from '../lib/events'
import { searchItems, suggest } from '../lib/search'
import { S } from '../strings'

/** Fuzzy, synonym-aware search over the whole catalog (plan §Q). */
export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const m = useMarket()
  const { items, index, categories, cart, rep } = m
  const [q, setQ] = useState(params.get('q') || '')
  const [cat, setCat] = useState<string>(S.categories.all)
  const [keypad, setKeypad] = useState<ShopItem | null>(null)
  const [recent, setRecent] = useState<string[]>(() => recentSearches())

  useEffect(() => {
    document.title = q.trim() ? `${q.trim()} · ${S.brand}` : `${S.nav.search} · ${S.brand}`
  }, [q])

  // keep the URL in step so a search survives a refresh and can be shared
  useEffect(() => {
    const cur = params.get('q') || ''
    if (cur !== q) setParams(q.trim() ? { q: q.trim() } : {}, { replace: true })
  }, [q, params, setParams])

  const results = useMemo(() => {
    if (!index || !q.trim()) return []
    const r = searchItems(index, items, q)
    return cat === S.categories.all ? r : r.filter((i) => (i.category || 'OTHER') === cat)
  }, [index, items, q, cat])
  const hints = useMemo(() => (index && q.trim() && !results.length ? suggest(index, q) : []), [index, q, results.length])

  useEffect(() => {
    if (!q.trim() || !index) return
    trackSearch(q, results.length)
    const id = window.setTimeout(() => {
      rememberSearch(q)
      setRecent(recentSearches())
    }, 1200)
    return () => window.clearTimeout(id)
  }, [q, results.length, index])

  const openItem = (code: string) => navigate(`/p/${encodeURIComponent(code)}`)
  const askUrl = rep?.whatsapp_url && q.trim() ? `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(`Hello ${rep.first_name || ''}, do you have "${q.trim()}"?`)}` : null

  return (
    <Page withCartBar>
      <TopBar title={S.nav.search} />
      <div className="sticky top-0 z-20 border-b border-[#ece9f3] bg-[#faf9fc]/92 backdrop-blur-md">
        <div className="mx-auto max-w-6xl px-4 pb-2 pt-2">
          <SearchBox value={q} onChange={setQ} autoFocus />
          {q.trim() && (
            <div className="mt-2">
              <CategoryChips categories={categories} active={cat} onPick={setCat} />
            </div>
          )}
        </div>
      </div>
      <main className="mx-auto max-w-6xl px-4 pt-4">
        {!q.trim() ? (
          <>
            {recent.length > 0 && (
              <section aria-label={S.states.recent}>
                <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">{S.states.recent}</h2>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {recent.map((r) => (
                    <button key={r} type="button" onClick={() => setQ(r)} className={cn('inline-flex h-9 items-center gap-1.5 rounded-full border border-[#e4e0ee] bg-white px-3.5 text-[12.5px] text-[#1a1430] hover:bg-[#f7f5fb]', RING)}>
                      <Clock size={13} className="text-[#a8a2bb]" aria-hidden="true" /> {r}
                    </button>
                  ))}
                </div>
              </section>
            )}
            <section className="mt-6" aria-label="Browse by category">
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">Categories</h2>
              <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {categories.map((c) => (
                  <button key={c} type="button" onClick={() => navigate(`/t/${encodeURIComponent(c.toLowerCase())}`)} className={cn('h-14 rounded-[16px] border border-[#ece9f3] bg-white px-3 text-left font-display text-[13.5px] font-bold capitalize text-[#1a1430] hover:border-[#e2ddef] hover:shadow-[0_10px_24px_-16px_rgba(24,16,48,.32)]', RING)}>
                    {c.toLowerCase()}
                    <span className="mt-0.5 block text-[11px] font-normal text-[#6b6480]">{items.filter((i) => (i.category || 'OTHER') === c).length} products</span>
                  </button>
                ))}
              </div>
            </section>
          </>
        ) : results.length === 0 ? (
          <EmptyState
            title={S.states.noMatch(q.trim())}
            hint={S.states.noMatchHint}
            action={
              <div className="flex flex-col items-center gap-3">
                {hints.length > 0 && (
                  <div className="flex flex-wrap justify-center gap-1.5">
                    <span className="self-center text-[12px] text-[#6b6480]">{S.states.didYouMean}</span>
                    {hints.map((h) => (
                      <button key={h} type="button" onClick={() => setQ(h)} className={cn('h-9 rounded-full border border-[#e4e0ee] bg-white px-3.5 text-[12.5px] font-semibold text-[#6d28d9] hover:bg-[#f7f5fb]', RING)}>{h}</button>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap justify-center gap-1.5">
                  {categories.map((c) => (
                    <button key={c} type="button" onClick={() => navigate(`/t/${encodeURIComponent(c.toLowerCase())}`)} className={cn('h-9 rounded-full border border-[#e4e0ee] bg-white px-3.5 text-[12px] capitalize text-[#1a1430] hover:bg-[#f7f5fb]', RING)}>{c.toLowerCase()}</button>
                  ))}
                </div>
                {askUrl && (
                  <a href={askUrl} target="_blank" rel="noreferrer" className={cn(BTN_SECONDARY, 'text-[#137a48]')}>
                    <MessageCircle size={15} aria-hidden="true" /> {S.cart.ask(rep!.first_name || 'us')}
                  </a>
                )}
              </div>
            }
          />
        ) : (
          <>
            <p aria-live="polite" className="text-[12px] text-[#6b6480]"><b className="font-semibold tabular-nums text-[#1a1430]">{results.length}</b> {results.length === 1 ? 'product' : 'products'}</p>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4 xl:grid-cols-5">
              {results.map((it, i) => (
                <MarketCard
                  key={it.item_code}
                  item={it}
                  qty={cart.qtyOf(it.item_code)}
                  defaultQty={m.defaultQty(it)}
                  allowBackorder={m.allowBackorder}
                  showCompare={m.showCompare}
                  publicTiers={m.publicTiers}
                  rep={rep}
                  eagerImage={i < 4}
                  onOpen={() => { track('item', { item_code: it.item_code, meta: { q: q.trim().slice(0, 60), pos: i } }); openItem(it.item_code) }}
                  onAdd={() => m.add(it, undefined, 'search')}
                  onSetQty={(n) => m.setQty(it, n)}
                  onRemove={() => m.remove(it.item_code)}
                  onKeypad={() => setKeypad(it)}
                />
              ))}
            </div>
          </>
        )}
      </main>
      <CartBar />
      <BottomNav />
      {keypad && <QtySheet item={keypad} value={cart.qtyOf(keypad.item_code)} onApply={(n) => m.setQty(keypad, n)} onRemove={() => m.remove(keypad.item_code)} onClose={() => setKeypad(null)} />}
    </Page>
  )
}
