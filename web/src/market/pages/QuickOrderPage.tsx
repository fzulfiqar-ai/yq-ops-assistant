import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, ClipboardPaste, Plus, RotateCcw, Save, Trash2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { fetchOrderCached } from '../hooks/useRecentOrders'
import { useMarket } from '../MarketContext'
import { lastQty, rememberedOrders } from '../lib/device'
import { track } from '../lib/events'
import { bhd, fmtDateShort, minQtyOf, money, normalizeQty, stepOf, unitAt, productName } from '../lib/format'
import { orderLines, regularStock } from '../lib/home'
import { parseList, resolveQuery } from '../lib/quickParse'
import { PageBar, usePageTitle, useShell } from '../shell/ShellContext'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Input, Label, Textarea } from '../ui/Field'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { Sheet } from '../ui/Sheet'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'

/**
 * /quick — mission mode. Rows of [code or name][qty]; Enter moves to the next row; paste a
 * WhatsApp list and it becomes rows; load the last order or a saved list; "Add all" puts every
 * resolved row in the cart at once and opens it for review. Device-saved lists (max 5) are
 * written so they can migrate to a customer object later.
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
      const q = normalizeQty(item, qty && qty > 0 ? qty : lastQty(item.item_code) || minQtyOf(item))
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
    setRow(row.id, { query: value, item: exact ? item : null, suggestions: exact ? [] : suggestions.length ? suggestions : candidates, candidates })
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
    const made = parsed.map((p) => ({ ...newRow(), query: p.item ? productName(p.item) : p.query, item: p.item, qty: p.item ? normalizeQty(p.item, p.qty || lastQty(p.item.item_code) || minQtyOf(p.item)) : 0, candidates: p.candidates, suggestions: p.item ? [] : p.candidates }))
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
    toast(`${S.quick.saveList}: ${name}`, 'success')
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

  const summary = (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="flex items-baseline justify-between">
        <span className="text-sm text-ink-2">{S.cart.summary(resolved.length, units)}</span>
        <span className="font-display text-xl font-extrabold tnum text-ink">≈ {bhd(total)}</span>
      </div>
      {unresolved > 0 && <p className="mt-1 text-xs text-warn">{unresolved} × {S.quick.notFound}</p>}
      <Button size="lg" full className="mt-4" disabled={!resolved.length} onClick={addAll} icon={<ArrowRight size={16} aria-hidden="true" />}>
        {S.quick.addAll(resolved.length)}
      </Button>
      <Button variant="ghost" full className="mt-2" disabled={!resolved.length} onClick={() => setSaveOpen(true)} icon={<Save size={15} aria-hidden="true" />}>
        {S.quick.saveList}
      </Button>
    </div>
  )

  return (
    <div className="px-gutter lg:px-0">
      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start lg:gap-6">
        <div>
          <div className="hidden lg:block">
            <h1 className="font-display text-2xl font-bold text-ink">{S.quick.title}</h1>
            <p className="mt-1 text-sm text-ink-2">{S.quick.intro}</p>
          </div>

          {/* templates */}
          <div className="mt-1 flex flex-wrap gap-2 lg:mt-4">
            <Button variant="secondary" size="sm" onClick={() => setPasteOpen(true)} icon={<ClipboardPaste size={15} aria-hidden="true" />}>
              {S.quick.paste}
            </Button>
            {last && (
              <Button variant="secondary" size="sm" onClick={() => { loadedRef.current = null; setParams({ load: 'last' }) }} icon={<RotateCcw size={15} aria-hidden="true" />}>
                {S.quick.lastOrder(fmtDateShort(new Date(last.ts).toISOString()) || '', 0).replace(' · 0 products', '')}
              </Button>
            )}
            {lists.map((l) => (
              <span key={l.name} className="inline-flex items-center overflow-hidden rounded-sm border border-line bg-surface">
                <button type="button" onClick={() => loadList(l)} className="h-10 px-3 text-sm font-semibold text-ink hover:bg-plum-wash">
                  {l.name} <span className="text-xs font-normal tnum text-ink-3">· {l.lines.length}</span>
                </button>
                <button type="button" onClick={() => deleteList(l.name)} aria-label={`Delete ${l.name}`} className="grid h-10 w-9 place-items-center border-s border-line text-ink-3 hover:bg-bad-soft hover:text-bad">
                  <X size={14} aria-hidden="true" />
                </button>
              </span>
            ))}
          </div>

          {/* rows */}
          <ol className="mt-4 space-y-2">
            {rows.map((row) => (
              <li key={row.id} className={cn('relative rounded-lg border bg-surface p-2.5', row.item ? 'border-plum/40' : 'border-line')}>
                {row.item ? (
                  <div className="flex items-center gap-3">
                    <div className="h-12 w-12 shrink-0 overflow-hidden rounded-sm border border-line-2">
                      <ProductImage item={row.item} alt="" sizes={SIZES_THUMB} size={48} imgClassName="p-1" iconSize={16} showCaption={false} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold text-ink">{productName(row.item)}</div>
                      <div className="text-xs tnum text-ink-2">
                        {row.item.item_code} · {money(unitAt(row.item, row.qty))} {S.cart.each}
                        {stepOf(row.item) > 1 && row.qty % stepOf(row.item) === 0 ? ` · ${S.card.packs(stepOf(row.item))}` : ''}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="hidden text-sm font-semibold tnum text-ink sm:block">{bhd((unitAt(row.item, row.qty) ?? 0) * row.qty)}</span>
                      <Stepper value={row.qty} step={stepOf(row.item)} min={minQtyOf(row.item)} size="sm" label={productName(row.item)} onChange={(n) => setRow(row.id, { qty: n })} onRemove={() => remove(row.id)} />
                      <button type="button" onClick={() => remove(row.id)} aria-label={S.quick.remove} className="grid h-9 w-9 place-items-center rounded-xs text-ink-3 hover:bg-bad-soft hover:text-bad">
                        <Trash2 size={15} aria-hidden="true" />
                      </button>
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
                              <span className="text-xs font-semibold tnum text-plum-ink">{s.price_bhd != null ? money(s.price_bhd) : ''}</span>
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
          {rows.length === 1 && !rows[0].query && !rows[0].item && <p className="mt-3 text-sm text-ink-2">{S.quick.empty}</p>}
          <Button variant="ghost" className="mt-3" onClick={() => setRows((rs) => [...rs, newRow()])} icon={<Plus size={15} aria-hidden="true" />}>
            {S.card.add}
          </Button>
          <div className="mt-4 lg:hidden">{summary}</div>
        </div>
        <div className="hidden lg:sticky lg:top-[calc(var(--m-header-h)+16px)] lg:block">{summary}</div>
      </div>

      {!desktop && resolved.length > 0 && (
        <PageBar>
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-xs text-ink-2">{S.cart.summary(resolved.length, units)}</div>
              <div className="font-display text-xl font-extrabold leading-tight tnum text-ink">≈ {bhd(total)}</div>
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
            <Textarea value={pasteText} onChange={(e) => setPasteText(e.target.value)} rows={7} autoFocus placeholder={'24 x C18\n12 UK15\ntws 6'} aria-label={S.quick.paste} />
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
            <Input id="yq-list-name" value={listName} onChange={(e) => setListName(e.target.value)} autoFocus placeholder="Weekly cables" onKeyDown={(e) => e.key === 'Enter' && saveList()} />
          </div>
        </Sheet>
      )}
    </div>
  )
}
