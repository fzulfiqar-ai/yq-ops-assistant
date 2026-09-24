import { useLayoutEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  Moon, Sun, ShieldCheck, User as UserIcon, KeyRound, Check, Loader2, Calculator, Bot, Store,
  Activity, AlertTriangle, ArrowDown, ArrowUp, BadgeCheck, Clock, MessageCircle, Package, Plus, RotateCcw, Tag, Trash2, Truck, Zap,
  type LucideIcon,
} from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import { useToast } from '@/components/Toast'
import { changeOwnPassword } from '@/lib/password'
import { apiGet, apiSend, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type Costing = Record<string, number>

/** The server's own `detail` line for a failed call (FastAPI 400s carry the reason there), never "API 400: {…}". */
function apiErrorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    try {
      const j = JSON.parse(e.body) as { detail?: unknown }
      if (typeof j.detail === 'string' && j.detail) return j.detail
      if (Array.isArray(j.detail) && j.detail.length) {
        return j.detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d))).join(' · ')
      }
    } catch {
      /* not json — fall through to the raw text */
    }
    return e.body.slice(0, 200) || fallback
  }
  return e instanceof Error && e.message ? e.message : fallback
}

const COSTING_FIELDS: { key: string; label: string; hint: string }[] = [
  { key: 'fx_rmb_usd', label: 'RMB per USD', hint: 'Order costing exchange leg' },
  { key: 'fx_usd_bhd', label: 'BHD per USD', hint: 'Order costing exchange leg' },
  { key: 'dealer_discount', label: 'Dealer discount', hint: '0.18 = net price is list ÷ 1.18' },
  { key: 'landing_vat_pct', label: 'Landing + VAT uplift', hint: '0.30 = 20% landing + 10% VAT' },
  { key: 'target_markup', label: 'Target markup', hint: '0.70 = sell at landed × 1.70' },
  { key: 'monthly_sales_target_bhd', label: 'Monthly accessories target (BHD)', hint: 'Mobile Accessories only — SIM sales never count · 0 = no target' },
]

function CostingCard() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['costing'], queryFn: () => apiGet<Costing>('/settings/costing') })
  const [draft, setDraft] = useState<Record<string, string>>({})
  const save = useMutation({
    mutationFn: () => {
      const changes: Costing = {}
      for (const [k, v] of Object.entries(draft)) {
        const n = parseFloat(v)
        if (!Number.isNaN(n) && n !== data?.[k]) changes[k] = n
      }
      return apiSend<Costing>('PUT', '/settings/costing', changes)
    },
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['costing'] }); toast('Business settings saved.', 'success') },
    onError: (e: Error) => toast(e.message, 'error'),
  })
  if (!data) return null
  return (
    <Card className="mb-4 p-6">
      <div className="mb-1 flex items-center gap-2 font-display text-base font-semibold">
        <Calculator size={18} className="text-primary" /> Business settings
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        The costing chain used across order verification, reorder proposals and pricing:
        RMB ÷ (1 + discount) ÷ RMB/USD × BHD/USD → base cost · × (1 + landing) → landed · × (1 + markup) → sell.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {COSTING_FIELDS.map((f) => (
          <label key={f.key} className="block">
            <span className="mb-1 block text-xs font-semibold text-muted-foreground">{f.label}</span>
            <Input inputMode="decimal" value={draft[f.key] ?? String(data[f.key] ?? '')}
              onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
            <span className="mt-0.5 block text-[11px] text-muted-foreground">{f.hint}</span>
          </label>
        ))}
      </div>
      <Button className="mt-4" onClick={() => save.mutate()} disabled={save.isPending || Object.keys(draft).length === 0}>
        {save.isPending ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />} Save settings
      </Button>
    </Card>
  )
}

/** AI-agent data scope — owner rule: agents focus on mobile accessories; SIM /
 *  starter packs stay out of their analysis until this is switched back on. */
function AgentScopeCard() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['agent-scope'],
    queryFn: () => apiGet<{ exclude_sim: boolean }>('/settings/agents'),
  })
  const save = useMutation({
    mutationFn: (exclude_sim: boolean) => apiSend('PUT', '/settings/agents', { exclude_sim }),
    onSuccess: (_r, exclude_sim) => {
      qc.invalidateQueries({ queryKey: ['agent-scope'] })
      toast(exclude_sim
        ? 'AI agents now ignore SIM / starter packs.'
        : 'AI agents now include SIM / starter packs.', 'success')
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })
  if (!data) return null
  return (
    <Card className="mb-4 p-6">
      <div className="mb-1 flex items-center gap-2 font-display text-base font-semibold">
        <Bot size={18} className="text-primary" /> AI agent scope
      </div>
      <p className="mb-3 text-sm text-muted-foreground">
        What data the AI agents (reorders, trends, outreach, forecasts…) analyse.
        Dashboards always show everything — this only scopes the agents.
      </p>
      <label className="flex cursor-pointer items-center gap-3 rounded-xl border bg-card px-4 py-3 text-sm font-medium transition hover:border-primary/40">
        <input type="checkbox" checked={data.exclude_sim}
          disabled={save.isPending}
          onChange={(e) => save.mutate(e.target.checked)} />
        Mobile accessories only — agents ignore the SIM / starter-pack division
        <span className="ml-auto text-xs text-muted-foreground">
          {data.exclude_sim ? 'SIM excluded' : 'SIM included'}
        </span>
      </label>
    </Card>
  )
}

function Toggle({ checked, onChange, label, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; id?: string }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className="flex items-center gap-2 text-sm font-medium">
      <span className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors', checked ? 'bg-primary' : 'bg-muted')}>
        {/* left-0: a <button> centres its text, which moved the knob's static position to the middle of the track */}
        <span className={cn('absolute left-0 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-4' : 'translate-x-0.5')} />
      </span>
      {label}
    </button>
  )
}

type ShopSettings = Record<string, string>
type ShopFieldType = 'number' | 'text' | 'toggle' | 'select' | 'percent'
/** One choice of a 'select' field; `hint` explains that choice under the field while it is picked. */
interface ShopOption { value: string; label: string; hint?: string }
interface ShopField { key: string; label: string; hint: string; type: ShopFieldType; options?: ShopOption[] }

/** The recommended wholesale minimum (owner decision 17-Sep-2026) — offered as a one-tap value, never forced. */
const RECOMMENDED_MIN_BHD = 20

const SMALL_ORDER_OPTIONS: ShopOption[] = [
  { value: 'request', label: 'Request — small orders go to the rep', hint: 'Merchants can still send the order as a small order request; your rep confirms it case by case (the handling fee below applies).' },
  { value: 'allow', label: 'Allow', hint: 'Orders under the minimum go through like any order — still tagged as small orders, no handling fee.' },
  { value: 'block', label: 'Block below minimum', hint: 'Checkout stays closed until the restock reaches the minimum.' },
]

const SHOP_FIELDS: ShopField[] = [
  { key: 'shop_min_order_bhd', label: 'Wholesale minimum (BHD)', hint: `Wholesale minimum — merchants see how far they are from it; BHD ${RECOMMENDED_MIN_BHD} recommended. 0 = no minimum.`, type: 'number' },
  { key: 'shop_free_delivery_threshold_bhd', label: 'Free delivery threshold (BHD)', hint: 'Order value at which delivery becomes free.', type: 'number' },
  { key: 'shop_small_order_mode', label: 'Under the minimum', hint: 'Merchants always see how far they are from the minimum and get one-tap add-ons to complete it.', type: 'select', options: SMALL_ORDER_OPTIONS },
  { key: 'shop_small_order_fee_bhd', label: 'Small-order handling fee (BHD)', hint: 'Shown to merchants under the minimum in request mode; the rep applies it when confirming. 0 = none.', type: 'number' },
  { key: 'shop_gap_suggestions', label: 'Gap suggestions', hint: 'How many one-tap add-ons merchants get to complete a wholesale order.', type: 'number' },
  { key: 'shop_low_stock_units', label: 'Low-stock units', hint: 'Units remaining at/below which an item shows "Only a few left."', type: 'number' },
  { key: 'shop_low_stock_days_cover', label: 'Low-stock days cover', hint: 'Days of stock cover at/below which an item is flagged low stock.', type: 'number' },
  { key: 'shop_min_margin_pct', label: 'Minimum margin', hint: 'Margin floor over landed cost — no rule or coupon can price below this.', type: 'percent' },
  { key: 'shop_allow_backorder', label: 'Allow backorder', hint: 'Let customers order out-of-stock items; a salesman confirms the ETA.', type: 'toggle' },
  { key: 'shop_show_retail_compare', label: 'Show retail price + merchant margin', hint: 'Show the price-book retail price and the merchant’s margin per piece next to the trade price. Last-chance lines show it either way.', type: 'toggle' },
  { key: 'shop_social_proof_min_customers', label: 'Social proof minimum', hint: 'Minimum shops ordering an item this month before showing social proof.', type: 'number' },
  { key: 'shop_default_salesman', label: 'Default salesman', hint: 'Credited for orders placed with no referral link.', type: 'select' },
  { key: 'shop_order_prefix', label: 'Order number prefix', hint: 'e.g. YQ → order numbers look like YQ-2609-0001.', type: 'text' },
  { key: 'shop_best_seller_top_n', label: 'Best-seller count', hint: 'How many top sellers get the "Best seller" badge.', type: 'number' },
  { key: 'shop_trending_growth_pct', label: 'Trending growth %', hint: 'Sales growth that qualifies an item as "Trending."', type: 'number' },
  { key: 'shop_new_days', label: 'New item window (days)', hint: 'Days since first sale that an item is tagged "New."', type: 'number' },
]

function ShopSettingsCard() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['settings-shop'], queryFn: () => apiGet<{ settings: ShopSettings }>('/settings/shop') })
  const { data: salesmenData } = useQuery({
    queryKey: ['shop-salesmen-names'],
    queryFn: () => apiGet<{ salesmen: { name: string }[] }>('/shop/salesmen'),
    staleTime: 5 * 60_000,
  })
  const [draft, setDraft] = useState<Record<string, string>>({})
  const settings = data?.settings
  const salesmenNames = (salesmenData?.salesmen || []).map((s) => s.name)

  const save = useMutation({
    mutationFn: () => apiSend<{ settings: ShopSettings }>('PUT', '/settings/shop', { settings: draft }),
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['settings-shop'] }); toast('Shop settings saved.', 'success') },
    onError: (e: unknown) => toast(apiErrorText(e, 'Could not save the shop settings.'), 'error'),
  })

  if (!settings) return null
  const val = (k: string) => draft[k] ?? settings[k] ?? ''
  const setVal = (k: string, v: string) => setDraft((d) => ({ ...d, [k]: v }))
  const dirty = Object.keys(draft).length > 0
  /** A select's choices: the field's own options, else (default salesman) the live roster. A stored
   *  value the list does not know stays visible as "(current)" instead of silently showing another choice. */
  const optionsFor = (f: ShopField): ShopOption[] => {
    const base = f.options ?? [{ value: '', label: '— none —' }, ...salesmenNames.map((n) => ({ value: n, label: n }))]
    const v = val(f.key)
    return v && !base.some((o) => o.value === v) ? [...base, { value: v, label: `${v} (current)` }] : base
  }

  return (
    <Card className="mb-4 p-6">
      <div className="mb-1 flex items-center gap-2 font-display text-base font-semibold">
        <Store size={18} className="text-primary" /> Shop settings
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        Rules the marketplace and its ordering flow follow — the wholesale minimum, small orders,
        delivery, stock thresholds and badge rules.
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {SHOP_FIELDS.map((f) => {
          const id = `shop-setting-${f.key}`
          const hintId = `${id}-hint`
          const options = f.type === 'select' ? optionsFor(f) : []
          const picked = options.find((o) => o.value === val(f.key))
          const minimum = f.key === 'shop_min_order_bhd'
          const atRecommended = minimum && val(f.key) !== '' && Number(val(f.key)) === RECOMMENDED_MIN_BHD
          return (
            <div key={f.key}>
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <label htmlFor={id} className="block text-xs font-semibold text-muted-foreground">{f.label}</label>
                {minimum && !atRecommended && (
                  <button type="button" onClick={() => setVal(f.key, String(RECOMMENDED_MIN_BHD))}
                    className="rounded-full px-2 py-0.5 text-[11px] font-semibold text-primary transition hover:bg-primary/10">
                    Use BHD {RECOMMENDED_MIN_BHD}.000
                  </button>
                )}
              </div>
              {f.type === 'toggle' ? (
                <div className="flex h-11 items-center">
                  <Toggle id={id} checked={val(f.key) === '1'} onChange={(v) => setVal(f.key, v ? '1' : '0')} label={val(f.key) === '1' ? 'On' : 'Off'} />
                </div>
              ) : f.type === 'select' ? (
                <select
                  id={id}
                  aria-describedby={hintId}
                  value={val(f.key)}
                  onChange={(e) => setVal(f.key, e.target.value)}
                  className="flex h-11 w-full rounded-lg border border-input bg-card px-3.5 text-sm text-foreground shadow-sm outline-none transition-colors focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {options.map((o) => <option key={o.value || '_none'} value={o.value}>{o.label}</option>)}
                </select>
              ) : f.type === 'percent' ? (
                <div className="relative">
                  <Input
                    id={id}
                    aria-describedby={hintId}
                    inputMode="decimal"
                    value={val(f.key) === '' ? '' : String(Number(val(f.key)) * 100)}
                    onChange={(e) => {
                      const n = parseFloat(e.target.value)
                      setVal(f.key, Number.isNaN(n) ? '' : String(n / 100))
                    }}
                    className="pr-8"
                  />
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">%</span>
                </div>
              ) : (
                <Input id={id} aria-describedby={hintId} inputMode={f.type === 'number' ? 'decimal' : 'text'} value={val(f.key)} onChange={(e) => setVal(f.key, e.target.value)} />
              )}
              <span id={hintId} className="mt-0.5 block text-[11px] text-muted-foreground">
                {f.hint}
                {picked?.hint && <span className="mt-1 block font-medium text-foreground/80">{picked.hint}</span>}
              </span>
            </div>
          )
        })}
      </div>
      <Button className="mt-4" onClick={() => save.mutate()} disabled={save.isPending || !dirty}>
        {save.isPending ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />} Save shop settings
      </Button>
    </Card>
  )
}

/* ───────────────────────── promise bar (shop_market_promises) ─────────────────────────
   The four wholesale promises under the marketplace header (phone strip + desktop utility bar).
   Stored as a JSON list of {key, en, ar, icon, to}; app/shop.py validate_promises() is the
   authority (400 with a readable reason) — this editor mirrors its rules so a bad row is caught
   before the round trip, and never lets a broken value blank the bar. */

interface PromiseRow { uid: string; key: string; en: string; ar: string; icon: string; to: string }

/** Exactly the keys (and glyphs) of the market's web/src/market/lib/icons.ts PROMISE_ICONS — keep the two in step; unknown → shield. */
const PROMISE_ICON_OPTIONS: { value: string; label: string; Icon: LucideIcon }[] = [
  { value: 'truck', label: 'Truck', Icon: Truck },
  { value: 'tag', label: 'Price tag', Icon: Tag },
  { value: 'pulse', label: 'Pulse', Icon: Activity },
  { value: 'shield', label: 'Shield', Icon: ShieldCheck },
  { value: 'package', label: 'Package', Icon: Package },
  { value: 'clock', label: 'Clock', Icon: Clock },
  { value: 'check', label: 'Check badge', Icon: BadgeCheck },
  { value: 'chat', label: 'Chat', Icon: MessageCircle },
  { value: 'zap', label: 'Lightning bolt', Icon: Zap },
  { value: 'store', label: 'Store', Icon: Store },
]
const promiseIcon = (icon: string): LucideIcon =>
  PROMISE_ICON_OPTIONS.find((o) => o.value === icon.trim().toLowerCase())?.Icon ?? ShieldCheck

const PROMISE_LINKS = ['/about#delivery', '/about#trade', '/about#how', '/shop?f=instock', '/shop?f=deals', '/quick']
const PROMISES_SHOWN = 4

/** app/shop.py SETTING_DEFAULTS["shop_market_promises"] — what "Restore defaults" puts back. */
const DEFAULT_PROMISES: Omit<PromiseRow, 'uid'>[] = [
  { key: 'delivery', en: 'Free delivery across Bahrain', ar: 'توصيل مجاني في كل البحرين', icon: 'truck', to: '/about#delivery' },
  { key: 'trade', en: 'Trade prices for shops', ar: 'أسعار الجملة للمحلات', icon: 'tag', to: '/about#trade' },
  { key: 'stock', en: 'Real warehouse stock', ar: 'مخزون حقيقي من المستودع', icon: 'pulse', to: '/shop?f=instock' },
  { key: 'rep', en: 'Every order confirmed by your rep', ar: 'كل طلب يؤكده مندوبك', icon: 'shield', to: '/about#how' },
]

function storedPromises(raw: string | undefined): { rows: PromiseRow[]; problem: string | null } {
  let data: unknown
  try {
    data = JSON.parse(raw || '[]')
  } catch {
    return { rows: [], problem: 'The saved promise list is not valid JSON, so the marketplace shows no promise bar. Rebuild it below or restore the defaults.' }
  }
  if (!Array.isArray(data)) {
    return { rows: [], problem: 'The saved promise list is not a list, so the marketplace shows no promise bar. Rebuild it below or restore the defaults.' }
  }
  const text = (x: unknown) => (typeof x === 'string' ? x : '')
  const rows = data
    .filter((r): r is Record<string, unknown> => Boolean(r) && typeof r === 'object' && !Array.isArray(r))
    .map((r, i) => ({ uid: `saved-${i}`, key: text(r.key), en: text(r.en), ar: text(r.ar), icon: text(r.icon), to: text(r.to) }))
  return { rows, problem: null }
}

/** Rows → the JSON string to PUT, or the first problem. The JSON is parsed back and compared before
 *  it is sent, so what reaches the server is exactly what the editor shows. */
function promisesJson(rows: PromiseRow[]): { json: string; error: null } | { json: null; error: string } {
  const fail = (error: string) => ({ json: null, error })
  if (rows.length === 0) return fail('Add at least one promise — an empty list hides the bar.')
  if (rows.length > PROMISES_SHOWN) return fail(`The bar shows ${PROMISES_SHOWN} promises — remove ${rows.length - PROMISES_SHOWN}.`)
  const seen = new Set<string>()
  const clean: Record<string, string>[] = []
  for (const [n, r] of rows.entries()) {
    const key = r.key.trim()
    const en = r.en.trim()
    const to = r.to.trim()
    if (!key) return fail(`Promise ${n + 1} needs a key (a short id such as delivery).`)
    if (!en) return fail(`Promise ${n + 1} (${key}) needs English text.`)
    if (seen.has(key.toLowerCase())) return fail(`Promise keys must be unique — “${key}” appears twice.`)
    seen.add(key.toLowerCase())
    if (to && !(to.startsWith('/') || to.startsWith('https://'))) {
      return fail(`Promise ${n + 1} (${key}): the link must be a marketplace path such as /about#delivery, or an https:// address.`)
    }
    const row: Record<string, string> = { key, en }
    if (r.ar.trim()) row.ar = r.ar.trim()
    if (r.icon.trim()) row.icon = r.icon.trim()
    if (to) row.to = to
    clean.push(row)
  }
  const json = JSON.stringify(clean)
  try {
    const back: unknown = JSON.parse(json)
    if (!Array.isArray(back) || back.length !== clean.length || JSON.stringify(back) !== json) throw new Error('mismatch')
  } catch {
    return fail('Could not encode the promise list — check the text for unusual characters.')
  }
  return { json, error: null }
}

/** React keys (and focus ids) for rows the office adds — never sent to the server. */
let addedPromises = 0
const newPromiseUid = () => `new-${(addedPromises += 1)}`

function PromiseBarCard() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['settings-shop'], queryFn: () => apiGet<{ settings: ShopSettings }>('/settings/shop') })
  const settings = data?.settings
  const stored = useMemo(() => storedPromises(settings?.shop_market_promises), [settings?.shop_market_promises])
  const [draft, setDraft] = useState<PromiseRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lang, setLang] = useState<'en' | 'ar'>('en')
  const rows = draft ?? stored.rows

  const save = useMutation({
    mutationFn: (json: string) =>
      apiSend<{ settings: ShopSettings }>('PUT', '/settings/shop', { settings: { shop_market_promises: json } }),
    onSuccess: (r) => {
      if (r?.settings) qc.setQueryData(['settings-shop'], { settings: r.settings })
      setDraft(null)
      setError(null)
      qc.invalidateQueries({ queryKey: ['settings-shop'] })
      toast('Promise bar saved — merchants see it on their next visit.', 'success')
    },
    onError: (e: unknown) => {
      const msg = apiErrorText(e, 'Could not save the promise bar.')
      setError(msg)
      toast(msg, 'error')
    },
  })

  /* Keyboard focus survives a reorder or a remove. Every control that can lose its node registers
     itself as `<what>:<uid>`; an editing action names the controls it would like focus on, best
     first, and the layout effect lands on the first one that is mounted and enabled. */
  const controls = useRef(new Map<string, HTMLElement>())
  const wanted = useRef<string[] | null>(null)
  const addRef = useRef<HTMLButtonElement>(null)
  const hold = (id: string) => (el: HTMLElement | null) => {
    if (el) controls.current.set(id, el)
    else controls.current.delete(id)
  }
  const want = (ids: string[]) => { wanted.current = ids }
  useLayoutEffect(() => {
    const ids = wanted.current
    if (!ids) return
    wanted.current = null
    for (const id of ids) {
      const el = id === 'add' ? addRef.current : controls.current.get(id)
      if (el && !(el instanceof HTMLButtonElement && el.disabled)) {
        el.focus()
        return
      }
    }
  })

  if (!settings) return null

  const edit = (next: PromiseRow[]) => {
    setDraft(next)
    setError(null)
  }
  const setField = (uid: string, k: keyof Omit<PromiseRow, 'uid'>, v: string) =>
    edit(rows.map((r) => (r.uid === uid ? { ...r, [k]: v } : r)))
  const move = (i: number, by: -1 | 1) => {
    const j = i + by
    if (j < 0 || j >= rows.length) return
    const next = [...rows]
    ;[next[i], next[j]] = [next[j], next[i]]
    // React moves the focused node when the row moves, so name where focus goes: the same arrow on
    // the row that moved, or the other one when the move used that arrow up (top / bottom of the list).
    const uid = rows[i].uid
    want(by === -1 ? [`up:${uid}`, `down:${uid}`] : [`down:${uid}`, `up:${uid}`])
    edit(next)
  }
  const remove = (i: number) => {
    const next = rows.filter((_, n) => n !== i)
    const neighbour = next[i] || next[i - 1] // the row that takes its place, else the one above
    want(neighbour ? [`remove:${neighbour.uid}`, `up:${neighbour.uid}`] : ['add'])
    edit(next)
  }
  const add = () => {
    const uid = newPromiseUid()
    want([`key:${uid}`]) // the Add button disables itself on the last promise — land in the new row instead
    edit([...rows, { uid, key: '', en: '', ar: '', icon: 'shield', to: '' }])
  }
  const restore = () => edit(DEFAULT_PROMISES.map((p, i) => ({ ...p, uid: `default-${Date.now()}-${i}` })))
  const submit = () => {
    const out = promisesJson(rows)
    if (out.json === null) {
      setError(out.error)
      return
    }
    save.mutate(out.json)
  }

  // the market rewrites the delivery line while a free-delivery threshold is set (app/shop.py promises_payload)
  const threshold = Number(settings.shop_free_delivery_threshold_bhd) || 0
  const shownText = (r: PromiseRow) => {
    if (r.key.trim() === 'delivery' && threshold > 0) {
      return lang === 'ar' ? `توصيل مجاني للطلبات فوق ${threshold.toFixed(3)} د.ب` : `Free delivery over BHD ${threshold.toFixed(3)}`
    }
    return (lang === 'ar' ? r.ar.trim() || r.en.trim() : r.en.trim()) || '…'
  }
  const preview = rows.filter((r) => r.en.trim()).slice(0, PROMISES_SHOWN)
  const LABEL = 'mb-1 block text-[11px] font-semibold text-muted-foreground'
  const SELECT = 'flex h-11 w-full rounded-lg border border-input bg-card px-3 text-sm text-foreground shadow-sm outline-none transition-colors focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring'

  return (
    <Card className="mb-4 p-6">
      <div className="mb-1 flex items-center gap-2 font-display text-base font-semibold">
        <BadgeCheck size={18} className="text-primary" /> Promise bar
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        The wholesale promises under the marketplace header — the phone strip and the desktop utility bar.
        Only claims the business backs every day.
      </p>

      {/* live bar, as merchants read it (market colours: canvas, ink, plum) */}
      <div className="mb-4">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Preview</span>
          <div role="group" aria-label="Preview language" className="inline-flex rounded-lg border bg-card p-0.5">
            {(['en', 'ar'] as const).map((l) => (
              <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)}
                className={cn('h-7 rounded-md px-2.5 text-[12px] font-semibold transition', lang === l ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}>
                {l === 'en' ? 'English' : 'العربية'}
              </button>
            ))}
          </div>
        </div>
        <div dir={lang === 'ar' ? 'rtl' : 'ltr'} className="rounded-xl border border-[#E9E4EF] bg-[#F9F7F3] px-4 py-2.5">
          {preview.length ? (
            <ul className="flex flex-wrap items-center justify-center gap-x-6 gap-y-1.5">
              {preview.map((r) => {
                const Icon = promiseIcon(r.icon)
                return (
                  <li key={r.uid} className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-[#191424]">
                    <Icon size={14} strokeWidth={2} className="shrink-0 text-[#6D4091]" aria-hidden />
                    {shownText(r)}
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="text-center text-[12.5px] text-[#5F556C]">No promises — the bar is hidden.</p>
          )}
        </div>
        {threshold > 0 && rows.some((r) => r.key.trim() === 'delivery') && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            The delivery line reads “Free delivery over BHD {threshold.toFixed(3)}” while a free-delivery threshold is set.
          </p>
        )}
      </div>

      {stored.problem && draft === null && (
        <div className="mb-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[13px] text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden /> {stored.problem}
        </div>
      )}

      <ol className="space-y-2.5">
        {rows.map((r, i) => {
          const Icon = promiseIcon(r.icon)
          const iconKnown = PROMISE_ICON_OPTIONS.some((o) => o.value === r.icon)
          const base = `promise-${r.uid}`
          return (
            <li key={r.uid} className="rounded-xl border bg-card p-3 sm:p-3.5">
              <div className="mb-2.5 flex items-center gap-2.5">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[#EEE8F4] text-[#6D4091]" aria-hidden>
                  <Icon size={16} />
                </span>
                <span className="text-[13px] font-semibold">Promise {i + 1}</span>
                {i >= PROMISES_SHOWN && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10.5px] font-semibold text-amber-800">Not shown — the bar holds {PROMISES_SHOWN}</span>
                )}
                <div className="ml-auto flex items-center gap-0.5">
                  <button type="button" ref={hold(`up:${r.uid}`)} onClick={() => move(i, -1)} disabled={i === 0} aria-label={`Move promise ${i + 1} up`}
                    className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-35">
                    <ArrowUp size={15} />
                  </button>
                  <button type="button" ref={hold(`down:${r.uid}`)} onClick={() => move(i, 1)} disabled={i === rows.length - 1} aria-label={`Move promise ${i + 1} down`}
                    className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-35">
                    <ArrowDown size={15} />
                  </button>
                  <button type="button" ref={hold(`remove:${r.uid}`)} onClick={() => remove(i)} aria-label={`Remove promise ${i + 1}`}
                    className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground transition hover:bg-rose-50 hover:text-rose-700 dark:hover:bg-rose-500/10">
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
              <div className="grid gap-2.5 sm:grid-cols-[8.5rem_minmax(0,1fr)_9.5rem]">
                <div>
                  <label htmlFor={`${base}-key`} className={LABEL}>Key</label>
                  <Input id={`${base}-key`} ref={hold(`key:${r.uid}`)} value={r.key} onChange={(e) => setField(r.uid, 'key', e.target.value)} placeholder="delivery" maxLength={24} spellCheck={false} className="font-mono text-[13px]" />
                </div>
                <div>
                  <label htmlFor={`${base}-en`} className={LABEL}>English</label>
                  <Input id={`${base}-en`} value={r.en} onChange={(e) => setField(r.uid, 'en', e.target.value)} placeholder="Free delivery across Bahrain" maxLength={60} />
                </div>
                <div>
                  <label htmlFor={`${base}-icon`} className={LABEL}>Icon</label>
                  <select id={`${base}-icon`} value={r.icon} onChange={(e) => setField(r.uid, 'icon', e.target.value)} className={SELECT}>
                    {PROMISE_ICON_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    {!iconKnown && <option value={r.icon}>{r.icon ? `${r.icon} (shows a shield)` : '— none (shield) —'}</option>}
                  </select>
                </div>
              </div>
              <div className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
                <div dir="rtl">
                  <label htmlFor={`${base}-ar`} className={cn(LABEL, 'text-right')}>العربية</label>
                  <Input id={`${base}-ar`} dir="rtl" lang="ar" value={r.ar} onChange={(e) => setField(r.uid, 'ar', e.target.value)} placeholder="توصيل مجاني في كل البحرين" maxLength={60} className="text-right" />
                </div>
                <div>
                  <label htmlFor={`${base}-to`} className={LABEL}>Link (optional)</label>
                  <Input id={`${base}-to`} value={r.to} onChange={(e) => setField(r.uid, 'to', e.target.value)} placeholder="/about#delivery" list="promise-links" spellCheck={false} maxLength={200} />
                </div>
              </div>
            </li>
          )
        })}
      </ol>
      <datalist id="promise-links">
        {PROMISE_LINKS.map((l) => <option key={l} value={l} />)}
      </datalist>

      {error && (
        <div role="alert" className="mt-3 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-[13px] text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden /> {error}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button type="button" ref={addRef} variant="outline" onClick={add} disabled={rows.length >= PROMISES_SHOWN}>
          <Plus size={16} /> Add promise
        </Button>
        <Button type="button" variant="ghost" onClick={restore}>
          <RotateCcw size={15} /> Restore defaults
        </Button>
        {rows.length >= PROMISES_SHOWN && <span className="text-[11px] text-muted-foreground">The bar holds {PROMISES_SHOWN} promises.</span>}
        <div className="ml-auto flex items-center gap-2">
          {draft !== null && (
            <Button type="button" variant="ghost" onClick={() => { setDraft(null); setError(null) }} disabled={save.isPending}>
              Discard
            </Button>
          )}
          <Button type="button" onClick={submit} disabled={save.isPending || draft === null}>
            {save.isPending ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />} Save promise bar
          </Button>
        </div>
      </div>
    </Card>
  )
}

export default function Settings() {
  const { me } = useAuth()
  const { theme, toggle } = useTheme()
  const toast = useToast()
  const [p1, setP1] = useState('')
  const [p2, setP2] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function changePassword(e: FormEvent) {
    e.preventDefault()
    if (p1.length < 8) return setMsg({ ok: false, text: 'Password must be at least 8 characters.' })
    if (p1 !== p2) return setMsg({ ok: false, text: 'Passwords do not match.' })
    setBusy(true); setMsg(null)
    const error = await changeOwnPassword(p1)
    setBusy(false)
    if (error) { setMsg({ ok: false, text: error }); toast(error, 'error') }
    else { setP1(''); setP2(''); setMsg(null); toast('Password updated successfully.', 'success') }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Settings" subtitle="Your profile, appearance and security" />

      <Card className="mb-4 p-6">
        <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
          <UserIcon size={18} className="text-primary" /> Profile
        </div>
        <div className="flex items-center gap-4">
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-primary text-xl font-bold text-primary-foreground">
            {(me?.full_name || me?.email || 'U')[0].toUpperCase()}
          </div>
          <div>
            <div className="text-lg font-semibold">{me?.full_name || me?.email?.split('@')[0]}</div>
            <div className="text-sm text-muted-foreground">{me?.email}</div>
            <div className="mt-1 inline-flex items-center gap-1.5 rounded-full bg-accent px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-accent-foreground">
              <ShieldCheck size={12} /> {me?.role}
            </div>
          </div>
        </div>
        {me?.role !== 'admin' && (me?.features?.length ?? 0) > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {me!.features!.map((f) => (
              <span key={f} className="rounded-full border px-2.5 py-0.5 text-[12px] text-muted-foreground">{f}</span>
            ))}
          </div>
        )}
      </Card>

      {me?.role === 'admin' && <CostingCard />}
      {me?.role === 'admin' && <ShopSettingsCard />}
      {me?.role === 'admin' && <PromiseBarCard />}
      {me?.role === 'admin' && <AgentScopeCard />}

      <Card className="mb-4 p-6">
        <div className="mb-4 font-display text-base font-semibold">Appearance</div>
        <button onClick={toggle} className="flex items-center gap-3 rounded-xl border bg-card px-4 py-3 text-sm font-medium transition hover:border-primary/40">
          {theme === 'dark' ? <Moon size={18} className="text-primary" /> : <Sun size={18} className="text-amber-500" />}
          {theme === 'dark' ? 'Dark mode' : 'Light mode'}
          <span className="ml-auto text-xs text-muted-foreground">Click to switch</span>
        </button>
      </Card>

      <Card className="p-6">
        <div className="mb-4 flex items-center gap-2 font-display text-base font-semibold">
          <KeyRound size={18} className="text-primary" /> Change password
        </div>
        <form onSubmit={changePassword} className="max-w-sm space-y-3">
          <Input type="password" placeholder="New password" value={p1} onChange={(e) => setP1(e.target.value)} />
          <Input type="password" placeholder="Confirm new password" value={p2} onChange={(e) => setP2(e.target.value)} />
          {msg && (
            <div className={cn('rounded-lg px-3 py-2 text-sm', msg.ok ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300' : 'bg-destructive/10 text-destructive')}>
              {msg.text}
            </div>
          )}
          <Button type="submit" disabled={busy}>
            {busy ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />} Update password
          </Button>
        </form>
      </Card>
    </div>
  )
}
