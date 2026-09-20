import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, ClipboardPaste, Plus, RotateCcw, Save, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { fetchOrderCached } from '../hooks/useRecentOrders'
import { useMarket } from '../MarketContext'
import { lastQty, rememberedOrders } from '../lib/device'
import { track } from '../lib/events'
import { bhd, fmtDateShort, minQtyOf, normalizeQty, stepOf, unitAt, productName } from '../lib/format'
import { bestSellers, orderLines, regularStock } from '../lib/home'
import { parseList, resolveQuery } from '../lib/quickParse'
import { PageBar, usePageTitle, useShell } from '../shell/ShellContext'
import { MarketCard } from '../components/MarketCard'
import { Spotlight } from '../components/Spotlight'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Input, Label, Textarea } from '../ui/Field'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { SectionHeader } from '../ui/SectionHeader'
import { Sheet } from '../ui/Sheet'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'

/**
 * /quick — mission mode. Rows of [code or name][qty]; Enter moves to the next row; paste a
 * WhatsApp list and it becomes rows; load the last order or a saved list; "Add all" puts every
 * resolved row in the cart at once and opens it for review. Device-saved lists (max 5) are
 * written so they can migrate to a customer object later.
 *
 * The total is shown once: the sticky bar carries it on a phone, the aside card on desktop. With
 * nothing composed yet there is no summary card and no disabled CTA — the page shows the paste
 * action, the merchant's own lists and Popular restocks instead of empty canvas.
 */

interface Row {
  id: number
  query: string
  item: ShopItem | null
  qty: number
  candidates: ShopItem[]
  suggestions: ShopItem[]
}
interface SavedList {
  name: string
  ts: number
  lines: { item_code: string; qty: number }[]
}

const LISTS_KEY = 'yq-lists'
let nextId = 1
const newRow = (): Row => ({ id: nextId++, query: '', item: null, qty: 0, candidates: [], suggestions: [] })

function readLists(): SavedList[] {
  try {
    const raw = localStorage.getItem(LISTS_KEY)
    return raw ? (JSON.parse(raw) as SavedList[]) : []
  } catch {
    return []
  }
}
function writeLists(lists: SavedList[]) {
  try {
    localStorage.setItem(LISTS_KEY, JSON.stringify(lists.slice(0, 5)))
  } catch {
    /* ignore */
  }
}

export default function QuickOrderPage() {
  const m = useMarket()
  const { viewport } = useShell()
  const navigate = useNavigate()
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState<Row[]>(() => [newRow()])
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')
  const [lists, setLists] = useState<SavedList[]>(() => readLists())
  const [saveOpen, setSaveOpen] = useState(false)
  const [listName, setListName] = useState('')
  const inputs = useRef(new Map<number, HTMLInputElement>())
  usePageTitle(S.quick.title, true, `${S.quick.title} · ${S.brand}`)
  useEffect(() => {
    void m.ensureIndex()
  }, [m])
  const last = rememberedOrders()[0] || null

  const setRow = useCallback((id: number, patch: Partial<Row>) => setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r))), [])
  const lockRow = useCallback(
    (id: number, item: ShopItem, qty?: number) => {
      // a row must never land at 0 — that is a line the merchant cannot order
      const q = normalizeQty(item, qty && qty > 0 ? qty : lastQty(item.item_code) || minQtyOf(item) || 1)
      setRows((rs) => {
        const next = rs.map((r) => (r.id === id ? { ...r, item, query: productName(item), qty: q, candidates: [], suggestions: [] } : r))
        return next.some((r) => !r.item) ? next : [...next, newRow()]
      })
    },
    [],
  )

  /* load templates from ?load= */
  const loadedRef = useRef<string | null>(null)
  useEffect(() => {
    const load = params.get('load')
    if (!load || loadedRef.current === load || !m.items.length) return
    loadedRef.current = load
    const apply = (lines: { item: ShopItem; qty: number }[]) => {
      if (!lines.length) return
      setRows([...lines.map((l) => ({ ...newRow(), item: l.item, query: productName(l.item), qty: normalizeQty(l.item, l.qty) })), newRow()])
    }
    if (load === 'last' && last) fetchOrderCached(last.token).then((o) => apply(orderLines(o, m.itemsByCode)))
    else if (load === 'regular') {
      Promise.all(rememberedOrders().slice(0, 3).map((o) => fetchOrderCached(o.token))).then((os) => apply(regularStock(os.filter((o): o is NonNullable<typeof o> => Boolean(o)), m.itemsByCode, new Set())))
    }
    const next = new URLSearchParams(params)
    next.delete('load')
    setParams(next, { replace: true })
  }, [params, setParams, m.items.length, m.itemsByCode, last])

  const onQuery = (row: Row, value: string) => {
    if (!value.trim()) {
      setRow(row.id, { query: value, item: null, suggestions: [], candidates: [] })
      return
    }
    const { item, candidates } = resolveQuery(value, m.items, m.index)
    const exact = item && item.item_code.replace(/[\s-]/g, '').toLowerCase() === value.replace(/[\s-]/g, '').toLowerCase()
    const hits = m.index ? m.index.mini.search(value, { prefix: true, fuzzy: 0.2 }).slice(0, 6) : []
    const suggestions = hits.map((h) => m.itemsByCode.get(String(h.id))).filter((x): x is ShopItem => Boolean(x))
    // typing a code resolves the line: lock it the way picking it does — with a quantity and a
    // fresh row underneath. Without this the row read "0" and the order could not be sent.
    if (exact && item) {
      lockRow(row.id, item, row.qty)
      // the typed row's input unmounts with it: carry the caret to the fresh row so codes can run on
      window.setTimeout(() => Array.from(inputs.current.values()).pop()?.focus(), 0)
      return
    }
    setRow(row.id, { query: value, item: null, suggestions: suggestions.length ? suggestions : candidates, candidates })
  }
  const onKey = (row: Row, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const pick = row.item || row.suggestions[0] || row.candidates[0]
      if (pick) {
        lockRow(row.id, pick)
        const idx = rows.findIndex((r) => r.id === row.id)
        const target = rows[idx + 1]
        window.setTimeout(() => (target ? inputs.current.get(target.id)?.focus() : Array.from(inputs.current.values()).pop()?.focus()), 0)
      }
    } else if (e.key === 'Escape') {
      setRow(row.id, { suggestions: [] })
    }
  }
  const remove = (id: number) => setRows((rs) => (rs.length === 1 ? [newRow()] : rs.filter((r) => r.id !== id)))

  const applyPaste = () => {
    const parsed = parseList(pasteText, m.items, m.index)
    if (!parsed.length) return
    const made = parsed.map((p) => ({ ...newRow(), query: p.item ? productName(p.item) : p.query, item: p.item, qty: p.item ? normalizeQty(p.item, p.qty || lastQty(p.item.item_code) || minQtyOf(p.item) || 1) : 0, candidates: p.candidates, suggestions: p.item ? [] : p.candidates }))
    setRows((rs) => [...rs.filter((r) => r.item || r.query.trim()), ...made, newRow()])
    track('search', { meta: { rail: 'quick_paste', results: made.filter((r) => r.item).length, count: made.length } })
    setPasteText('')
    setPasteOpen(false)
  }

  const resolved = useMemo(() => rows.filter((r): r is Row & { item: ShopItem } => Boolean(r.item) && r.qty > 0), [rows])
  const units = resolved.reduce((s, r) => s + r.qty, 0)
  const total = resolved.reduce((s, r) => s + (unitAt(r.item, r.qty) ?? 0) * r.qty, 0)
  const unresolved = rows.filter((r) => !r.item && r.query.trim()).length

  const addAll = () => {
    const n = m.addMany(
      resolved.map((r) => ({ item: r.item, qty: r.qty })),
      'quick',
    )
    track('search', { meta: { rail: 'quick_add', count: n, results: unresolved } })
    navigate('/cart')
  }
  const saveList = () => {
    const name = listName.trim() || `${S.quick.lists} ${lists.length + 1}`
    const next = [{ name, ts: Date.now(), lines: resolved.map((r) => ({ item_code: r.item.item_code, qty: r.qty })) }, ...lists.filter((l) => l.name !== name)].slice(0, 5)
    setLists(next)
    writeLists(next)
    setSaveOpen(false)
    setListName('')
    toast(S.quick.listSaved(name), 'success')
  }
  const loadList = (l: SavedList) => {
    const lines = l.lines.map((x) => ({ item: m.itemsByCode.get(x.item_code)!, qty: x.qty })).filter((x) => x.item)
    setRows([...lines.map((x) => ({ ...newRow(), item: x.item, query: productName(x.item), qty: normalizeQty(x.item, x.qty) })), newRow()])
  }
  const deleteList = (name: string) => {
    const next = lists.filter((l) => l.name !== name)
    setLists(next)
    writeLists(next)
  }
  const desktop = viewport === 'desktop' || viewport === 'wide'

  // the sticky bar owns the total on a phone, this card owns it on desktop — never both at once
  const barShown = !desktop && resolved.length > 0
  const summary = (compact: boolean) => (
    <div className="rounded-lg border border-line bg-surface p-4">
      {!compact && (
        <div className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate text-sm text-ink-2">{S.cart.summary(resolved.length, units)}</span>
          <span className="shrink-0 whitespace-nowrap font-display text-xl font-extrabold tnum text-ink">≈ {bhd(total)}</span>
        </div>
      )}
      {unresolved > 0 && <p className={cn('text-xs text-warn', !compact && 'mt-1')}>{S.quick.unresolved(unresolved)}</p>}
      {!compact && (
        <Button size="lg" full className="mt-4" onClick={addAll} icon={<ArrowRight size={16} aria-hidden="true" />}>
          {S.quick.addAll(resolved.length)}
        </Button>
      )}
      <Button variant="ghost" full className={cn((!compact || unresolved > 0) && 'mt-2')} onClick={() => setSaveOpen(true)} icon={<Save size={15} aria-hidden="true" />}>
        {S.quick.saveList}
      </Button>
    </div>
  )
  // nothing typed yet: the page fills with what a merchant can actually restock from
  const composeEmpty = !resolved.length && !rows.some((r) => r.query.trim())
  const popular = useMemo(() => (composeEmpty ? bestSellers(m.items).slice(0, 6) : []), [composeEmpty, m.items])

  return (
    <div className="px-gutter lg:px-0 lg:pt-4">
      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start lg:gap-6">
        <div>
          <div>
            {/* the phone header carries the title; the intro frames the page everywhere */}
            <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.quick.title}</h1>
            <p className="text-sm leading-snug text-ink-2 lg:mt-1">{S.quick.intro}</p>
          </div>

          {/* templates */}
          <div className="mt-3 flex flex-wrap items-center gap-2 lg:mt-4">
            {/* the page is paste-first: its strongest affordance must look like it */}
            <Button variant="primary" size="lg" className="w-full sm:w-auto" onClick={() => setPasteOpen(true)} icon={<ClipboardPaste size={17} aria-hidden="true" />}>
              {S.quick.paste}
            </Button>
            {last && (
              <Button variant="secondary" size="sm" onClick={() => { loadedRef.current = null; setParams({ load: 'last' }) }} icon={<RotateCcw size={15} aria-hidden="true" />}>
                {S.quick.lastOrderShort(fmtDateShort(new Date(last.ts).toISOString()) || '')}
              </Button>
            )}
            {lists.map((l) => (
              <span key={l.name} className="inline-flex items-center overflow-hidden rounded-sm border border-line bg-surface">
                <button type="button" onClick={() => loadList(l)} className="h-10 px-3 text-sm font-semibold text-ink hover:bg-plum-wash">
                  {l.name} <span className="text-xs font-normal tnum text-ink-3">· {l.lines.length}</span>
                </button>
                <button type="button" onClick={() => deleteList(l.name)} aria-label={S.quick.deleteList(l.name)} className="grid h-10 w-9 place-items-center border-s border-line text-ink-3 hover:bg-bad-soft hover:text-bad">
                  <X size={14} aria-hidden="true" />
                </button>
              </span>
            ))}
          </div>

          {/* rows */}
          <ol className="mt-4 space-y-2">
            {rows.map((row) => (
              <li key={row.id} className={cn('relative rounded-lg', row.item && 'border border-plum/40 bg-surface p-2.5')}>
                {row.item ? (
                  <div className="flex items-center gap-3">
                    <div className="h-12 w-12 shrink-0 overflow-hidden rounded-sm border border-line-2">
                      <ProductImage item={row.item} alt="" sizes={SIZES_THUMB} size={48} imgClassName="p-1" iconSize={16} showCaption={false} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold text-ink">{productName(row.item)}</div>
                      <div className="text-xs tnum text-ink-2">
                        {row.item.item_code} · {bhd(unitAt(row.item, row.qty))} {S.cart.each}
                        {stepOf(row.item) > 1 && row.qty % stepOf(row.item) === 0 ? ` · ${S.card.packs(stepOf(row.item))}` : ''}
                      </div>
                    </div>
                    {/* one delete control per line: the stepper removes the row at its minimum, the
                        way the restock rows do — a second trash beside it read as two ways to delete */}
                    <div className="flex items-center gap-2">
                      <span className="hidden text-sm font-semibold tnum text-ink sm:block">{bhd((unitAt(row.item, row.qty) ?? 0) * row.qty)}</span>
                      <Stepper value={row.qty} step={stepOf(row.item)} min={minQtyOf(row.item)} size="sm" label={productName(row.item)} onChange={(n) => setRow(row.id, { qty: n })} onRemove={() => remove(row.id)} />
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="flex items-center gap-2">
                      <Input
                        ref={(el) => {
                          if (el) inputs.current.set(row.id, el)
                          else inputs.current.delete(row.id)
                        }}
                        value={row.query}
                        onChange={(e) => onQuery(row, e.target.value)}
                        onKeyDown={(e) => onKey(row, e)}
                        placeholder={S.quick.row}
                        aria-label={S.quick.row}
                        autoCapitalize="characters"
                        autoCorrect="off"
                        spellCheck={false}
                        tall={false}
                        className="flex-1"
                      />
                      {row.query.trim() && (
                        <button type="button" onClick={() => remove(row.id)} aria-label={S.quick.remove} className="grid h-9 w-9 shrink-0 place-items-center rounded-xs text-ink-3 hover:bg-bad-soft hover:text-bad">
                          <X size={15} aria-hidden="true" />
                        </button>
                      )}
                    </div>
                    {row.suggestions.length > 0 && (
                      <ul className="mt-2 divide-y divide-line-2 overflow-hidden rounded-sm border border-line" role="listbox">
                        {row.suggestions.map((s) => (
                          <li key={s.item_code}>
                            <button type="button" role="option" aria-selected={false} onClick={() => lockRow(row.id, s)} className="flex w-full items-center gap-2.5 px-2.5 py-2 text-start text-sm hover:bg-plum-wash">
                              <span className="h-8 w-8 shrink-0 overflow-hidden rounded-xs border border-line-2">
                                <ProductImage item={s} alt="" sizes={SIZES_THUMB} size={32} imgClassName="p-0.5" iconSize={12} showCaption={false} />
                              </span>
                              <span className="min-w-0 flex-1 truncate font-medium text-ink">{productName(s)}</span>
                              <span className="text-xs tnum text-ink-2">{s.item_code}</span>
                              <span className="whitespace-nowrap text-xs font-semibold tnum text-plum-ink">{s.price_bhd != null ? bhd(s.price_bhd) : ''}</span>
                              <Plus size={14} className="text-plum" aria-hidden="true" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {row.query.trim() && !row.suggestions.length && m.index && <p className="mt-1.5 text-xs text-warn">{S.quick.notFound}</p>}
                  </div>
                )}
              </li>
            ))}
          </ol>
          <Button variant="ghost" className="mt-2" onClick={() => setRows((rs) => [...rs, newRow()])} icon={<Plus size={15} aria-hidden="true" />}>
            {S.quick.addLine}
          </Button>
          {resolved.length > 0 && <div className="mt-4 lg:hidden">{summary(barShown)}</div>}

          {/* an empty compose card used to leave half the screen bare: fill it with real stock */}
          {composeEmpty && popular.length > 0 && (
            <section aria-labelledby="quick-popular" className="mt-6">
              <SectionHeader id="quick-popular" title={S.search.popularRestocks} seeAllTo="/shop?f=best" />
              <div className="mt-2 rounded-lg bg-surface px-3 shadow-1 ring-1 ring-line [&>article:last-child]:border-b-0">
                {popular.map((it) => (
                  <MarketCard key={it.item_code} item={it} variant="list" from="quick_popular" />
                ))}
              </div>
            </section>
          )}
        </div>
        <div className="hidden lg:sticky lg:top-[calc(var(--m-sticky-h,var(--m-header-h))+16px)] lg:block">
          {resolved.length > 0 && summary(false)}
          {/* desktop only: on a phone this column is display:none, so a mounted carousel would only idle */}
          {desktop && <Spotlight className="mt-4" />}
        </div>
      </div>

      {!desktop && resolved.length > 0 && (
        <PageBar>
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs text-ink-2">{S.cart.summary(resolved.length, units)}</div>
              {/* the money never wraps: at 320–390px it steps down instead */}
              <div className="whitespace-nowrap font-display text-lg font-extrabold leading-tight tnum text-ink min-[400px]:text-xl">≈ {bhd(total)}</div>
            </div>
            <Button size="lg" className="shrink-0 px-5" onClick={addAll} icon={<ArrowRight size={16} aria-hidden="true" />}>
              {S.quick.addAll(resolved.length)}
            </Button>
          </div>
        </PageBar>
      )}

      {pasteOpen && (
        <Sheet
          open
          onClose={() => setPasteOpen(false)}
          variant="dialog"
          title={S.quick.paste}
          subtitle={S.quick.pasteHint}
          footer={
            <Button size="lg" full onClick={applyPaste} disabled={!pasteText.trim()}>
              {S.quick.pasteApply}
            </Button>
          }
        >
          <div className="px-4 py-4 md:px-5">
            <Textarea value={pasteText} onChange={(e) => setPasteText(e.target.value)} rows={7} autoFocus placeholder={S.quick.pastePlaceholder} aria-label={S.quick.paste} />
          </div>
        </Sheet>
      )}
      {saveOpen && (
        <Sheet
          open
          onClose={() => setSaveOpen(false)}
          variant="dialog"
          title={S.quick.saveList}
          footer={
            <Button size="lg" full onClick={saveList}>
              {S.quick.saveList}
            </Button>
          }
        >
          <div className="px-4 py-4 md:px-5">
            <Label htmlFor="yq-list-name">{S.quick.listName}</Label>
            <Input id="yq-list-name" value={listName} onChange={(e) => setListName(e.target.value)} autoFocus placeholder={S.quick.listPlaceholder} onKeyDown={(e) => e.key === 'Enter' && saveList()} />
          </div>
        </Sheet>
      )}
    </div>
  )
}
