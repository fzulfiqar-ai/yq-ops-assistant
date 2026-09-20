import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, X, Check, Loader2, Eye, AlertTriangle, Tag, Percent, Boxes, Megaphone } from 'lucide-react'
import { CampaignsSection } from '@/pages/shop-ops/CampaignsSection'
import { apiGet, apiPost, apiPatch, apiDelete, ApiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { bhd, num } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { DataTable, Stat, type Column } from '@/components/DataTable'

// ── types (kept close to docs/SHOP.md — fields we're not 100% certain of stay optional) ──

type RuleKind = 'qty_tier' | 'cart_value' | 'coupon' | 'bundle_price' | 'salesman_offer'
type DiscountType = 'pct' | 'amount' | 'fixed'

interface RuleScope {
  item_codes?: string[] | null
  categories?: string[] | null
  referral_codes?: string[] | null
}

interface DiscountRule {
  id: number
  name: string
  kind: string
  scope?: RuleScope | null
  min_qty?: number | null
  min_value_bhd?: number | null
  pct_off?: number | null
  amount_off_bhd?: number | null
  fixed_price_bhd?: number | null
  coupon_code?: string | null
  stackable?: boolean | null
  starts_at?: string | null
  ends_at?: string | null
  max_uses?: number | null
  uses?: number | null
  priority?: number | null
  is_active: boolean
  summary?: string | null
  status?: string | null
}
interface RulesResp { rules: DiscountRule[] }

interface RuleBreach { item_code: string; unit_bhd: number; floor_bhd: number }
interface RuleImpact { items: number; breach_count: number; breaches: RuleBreach[] }
interface RuleSaveResp extends DiscountRule { impact?: RuleImpact | null }
interface RulePreviewResp { rule?: Partial<DiscountRule>; summary?: string; impact: RuleImpact }

interface MarginRow {
  item_code: string
  spec?: string | null
  category?: string | null
  price_incl_vat_bhd?: number | null
  price_ex_vat_bhd?: number | null
  landed_cost_bhd?: number | null
  profit_bhd?: number | null
  margin_pct?: number | null // fraction, e.g. 0.234 == 23.4%
  markup_pct?: number | null // fraction
  floor_bhd?: number | null
  stock_status?: string | null
  sold_90d?: number | null
  status?: string | null // ok | below_floor | no_cost | no_price
}
interface MarginSummary { items: number; with_cost: number; below_floor: number; vat_rate: number; min_margin_pct: number }
interface MarginsResp { rows: MarginRow[]; summary: MarginSummary }

interface UnpricedRow {
  item_name?: string | null
  warehouse_name?: string | null
  stock_qty?: number | null
  value_bhd?: number | null
  matched_code?: string | null
  match_source?: string | null
  as_of_date?: string | null
}
interface UnpricedResp { rows: UnpricedRow[] }

// ── constants ──────────────────────────────────────────────────────────────────

const RULE_KINDS: { value: RuleKind; label: string }[] = [
  { value: 'qty_tier', label: 'Quantity tier' },
  { value: 'cart_value', label: 'Cart value' },
  { value: 'coupon', label: 'Coupon' },
  { value: 'bundle_price', label: 'Bundle price' },
  { value: 'salesman_offer', label: 'Salesman offer' },
]
const KIND_LABEL: Record<string, string> = Object.fromEntries(RULE_KINDS.map((k) => [k.value, k.label]))

const DISCOUNT_TYPES: { value: DiscountType; label: string }[] = [
  { value: 'pct', label: '% off' },
  { value: 'amount', label: 'BHD off' },
  { value: 'fixed', label: 'Fixed price/unit' },
]

const RULE_STATUS_STYLE: Record<string, string> = {
  live: 'bg-emerald-100 text-emerald-700',
  scheduled: 'bg-violet-100 text-violet-700',
  expired: 'bg-secondary text-muted-foreground',
  exhausted: 'bg-secondary text-muted-foreground',
  inactive: 'bg-muted text-muted-foreground/70',
}

const STOCK_LABEL: Record<string, string> = { in_stock: 'In stock', low_stock: 'Only a few left', out_of_stock: 'Sold out' }
const STOCK_STYLE: Record<string, string> = {
  in_stock: 'bg-emerald-100 text-emerald-700',
  low_stock: 'bg-amber-100 text-amber-700',
  out_of_stock: 'bg-rose-100 text-rose-700',
}

const MARGIN_STATUS_LABEL: Record<string, string> = { ok: 'OK', below_floor: 'Below floor', no_cost: 'No cost', no_price: 'No price' }
const MARGIN_STATUS_TONE: Record<string, BadgeTone> = { ok: 'green', below_floor: 'amber', no_cost: 'grey', no_price: 'grey' }

// ── small pure helpers ────────────────────────────────────────────────────────

/** BHD money, null-safe: an unknown/missing cost must read as "—", never as a misleading 0. */
function money3(n: number | null | undefined): string {
  return n == null ? '—' : bhd(n, 3)
}

/** margin_pct / markup_pct / vat_rate / min_margin_pct all come back as fractions (0.234), not
 * whole percentages — confirmed against app/shop.py::margin_health (margin = profit/ex_vat). */
function pctFrac(n: number | null | undefined, dp = 1): string {
  return n == null ? '—' : `${(Number(n) * 100).toFixed(dp)}%`
}

function fmtDateTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return String(iso)
  }
}

function windowLabel(startsAt?: string | null, endsAt?: string | null): string {
  if (!startsAt && !endsAt) return 'always'
  if (startsAt && !endsAt) return `From ${fmtDateTime(startsAt)}`
  if (!startsAt && endsAt) return `Until ${fmtDateTime(endsAt)}`
  return `${fmtDateTime(startsAt)} – ${fmtDateTime(endsAt)}`
}

function usesLabel(r: DiscountRule): string {
  const u = r.uses ?? 0
  return r.max_uses != null ? `${num(u)} / ${num(r.max_uses)}` : `${num(u)} · no limit`
}

/** datetime-local <-> ISO. Both round-trip through the browser's local timezone via Date. */
function isoToLocalInput(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
function localInputToIso(local: string): string {
  if (!local) return ''
  const d = new Date(local)
  return Number.isNaN(d.getTime()) ? '' : d.toISOString()
}

function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    try {
      const parsed = JSON.parse(e.body) as { detail?: unknown }
      if (parsed && typeof parsed.detail === 'string') return parsed.detail
    } catch {
      /* body wasn't JSON — fall through to the raw text below */
    }
    return e.body ? e.body.slice(0, 200) : e.message
  }
  if (e instanceof Error) return e.message
  return fallback
}

// item codes: users type "T02, X24 CC  UK04" — split on comma OR whitespace.
function splitCodes(raw: string): string[] {
  return Array.from(new Set(raw.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)))
}
// categories can contain spaces ("BLUETOOTH HEADSET") — comma-separated only.
function splitCategories(raw: string): string[] {
  return Array.from(new Set(raw.split(',').map((s) => s.trim()).filter(Boolean)))
}
function splitReferrals(raw: string): string[] {
  return Array.from(new Set(raw.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean)))
}

function numOr0(s: string): number {
  const n = parseFloat(s)
  return Number.isFinite(n) ? n : 0
}
function intOr0(s: string): number {
  const n = parseInt(s, 10)
  return Number.isFinite(n) ? n : 0
}

// ── small UI atoms ────────────────────────────────────────────────────────────

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" onClick={() => onChange(!checked)} className="flex items-center gap-2 text-[13px] font-medium">
      <span className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors', checked ? 'bg-primary' : 'bg-muted')}>
        {/* left-0: a <button> centres its text, which moved the knob's static position to the middle of the track */}
        <span className={cn('absolute left-0 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-4' : 'translate-x-0.5')} />
      </span>
      {label}
    </button>
  )
}

function RuleStatusPill({ status }: { status?: string | null }) {
  const s = status || 'inactive'
  return (
    <span className={cn('inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase', RULE_STATUS_STYLE[s] || 'bg-secondary text-muted-foreground')}>
      {s}
    </span>
  )
}

function StockPill({ status }: { status?: string | null }) {
  const s = status || ''
  return (
    <span className={cn('inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase', STOCK_STYLE[s] || 'bg-secondary text-muted-foreground')}>
      {STOCK_LABEL[s] || s || '—'}
    </span>
  )
}

function ScopeChips({ scope }: { scope?: RuleScope | null }) {
  const items = scope?.item_codes || []
  const cats = scope?.categories || []
  const refs = scope?.referral_codes || []
  if (!items.length && !cats.length && !refs.length) return <Badge tone="grey">All items</Badge>
  const MAX = 3
  return (
    <div className="flex max-w-[260px] flex-wrap gap-1">
      {items.slice(0, MAX).map((c) => <Badge key={`i-${c}`} tone="accent">{c}</Badge>)}
      {items.length > MAX && <Badge tone="accent">+{items.length - MAX}</Badge>}
      {cats.slice(0, MAX).map((c) => <Badge key={`c-${c}`} tone="grey">{c}</Badge>)}
      {cats.length > MAX && <Badge tone="grey">+{cats.length - MAX}</Badge>}
      {refs.slice(0, MAX).map((c) => <Badge key={`r-${c}`} tone="ink">@{c}</Badge>)}
      {refs.length > MAX && <Badge tone="ink">+{refs.length - MAX}</Badge>}
    </div>
  )
}

function ErrorPanel({ error }: { error?: unknown }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-6 text-sm text-rose-700">
      <AlertTriangle size={16} className="shrink-0" />
      <span>{error ? errorText(error, 'Could not load this from the server.') : 'Could not load this from the server. Try again in a moment.'}</span>
    </div>
  )
}

// ── rule form (New / Edit dialog) ─────────────────────────────────────────────

interface RuleForm {
  id?: number
  name: string
  kind: RuleKind
  discountType: DiscountType
  pctOff: string
  amountOff: string
  fixedPrice: string
  minQty: string
  minValue: string
  couponCode: string
  itemCodes: string
  categories: string
  referralCodes: string
  stackable: boolean
  startsAt: string
  endsAt: string
  maxUses: string
  priority: string
  isActive: boolean
}

function emptyForm(): RuleForm {
  return {
    name: '', kind: 'qty_tier', discountType: 'pct',
    pctOff: '', amountOff: '', fixedPrice: '',
    minQty: '', minValue: '', couponCode: '',
    itemCodes: '', categories: '', referralCodes: '',
    stackable: false, startsAt: '', endsAt: '', maxUses: '', priority: '100', isActive: true,
  }
}

function formFromRule(r: DiscountRule): RuleForm {
  const discountType: DiscountType = r.fixed_price_bhd ? 'fixed' : r.amount_off_bhd ? 'amount' : 'pct'
  const kindOk = RULE_KINDS.some((k) => k.value === r.kind)
  return {
    id: r.id,
    name: r.name || '',
    kind: (kindOk ? r.kind : 'qty_tier') as RuleKind,
    discountType,
    pctOff: r.pct_off ? String(r.pct_off) : '',
    amountOff: r.amount_off_bhd ? String(r.amount_off_bhd) : '',
    fixedPrice: r.fixed_price_bhd ? String(r.fixed_price_bhd) : '',
    minQty: r.min_qty ? String(r.min_qty) : '',
    minValue: r.min_value_bhd ? String(r.min_value_bhd) : '',
    couponCode: r.coupon_code || '',
    itemCodes: (r.scope?.item_codes || []).join(', '),
    categories: (r.scope?.categories || []).join(', '),
    referralCodes: (r.scope?.referral_codes || []).join(', '),
    stackable: !!r.stackable,
    startsAt: isoToLocalInput(r.starts_at),
    endsAt: isoToLocalInput(r.ends_at),
    maxUses: r.max_uses ? String(r.max_uses) : '',
    priority: r.priority != null ? String(r.priority) : '100',
    isActive: r.is_active !== false,
  }
}

/**
 * The backend parses the body with Pydantic's `model_dump(exclude_none=True)` (app/shop_api.py),
 * so a JSON `null` is stripped before it ever reaches the update logic — sending `null` for an
 * unchosen discount field (or a date you want to clear) silently keeps whatever was saved before,
 * it does NOT clear it. The only values that reliably overwrite a previous save are a real 0 for
 * numeric fields (min_qty/max_uses fall through `_i(x) or None`, min_value_bhd is checked with
 * `> 0` everywhere downstream) and an empty string for starts_at/ends_at (the validator only skips
 * blank dates when the value is exactly `""`, not `None`). So every field below is always present
 * with an explicit "cleared" value rather than being omitted — required for Edit to work when the
 * rule kind or discount type changes. See the report for the full trace.
 */
function buildPayload(f: RuleForm) {
  return {
    name: f.name.trim(),
    kind: f.kind,
    scope: {
      item_codes: splitCodes(f.itemCodes),
      categories: splitCategories(f.categories),
      referral_codes: splitReferrals(f.referralCodes),
    },
    pct_off: f.discountType === 'pct' ? numOr0(f.pctOff) : 0,
    amount_off_bhd: f.discountType === 'amount' ? numOr0(f.amountOff) : 0,
    fixed_price_bhd: f.discountType === 'fixed' ? numOr0(f.fixedPrice) : 0,
    min_qty: f.kind === 'qty_tier' ? intOr0(f.minQty) : 0,
    min_value_bhd: f.kind === 'cart_value' || f.kind === 'coupon' ? numOr0(f.minValue) : 0,
    coupon_code: f.kind === 'coupon' ? f.couponCode.trim().toUpperCase() : '',
    stackable: f.stackable,
    starts_at: f.startsAt ? localInputToIso(f.startsAt) : '',
    ends_at: f.endsAt ? localInputToIso(f.endsAt) : '',
    max_uses: f.maxUses ? intOr0(f.maxUses) : 0,
    priority: f.priority ? intOr0(f.priority) : 100,
    is_active: f.isActive,
  }
}

function RuleDialog({ rule, onClose, onSaved }: { rule: DiscountRule | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const isNew = rule == null
  const [f, setF] = useState<RuleForm>(() => (rule ? formFromRule(rule) : emptyForm()))
  const [busy, setBusy] = useState<'save' | 'preview' | null>(null)
  const [preview, setPreview] = useState<RuleImpact | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  function set<K extends keyof RuleForm>(key: K, value: RuleForm[K]) {
    setF((prev) => ({ ...prev, [key]: value }))
    setPreview(null)
    setFormError(null)
  }

  function clientError(): string | null {
    if (!f.name.trim()) return 'Give the rule a name.'
    const discountVal = f.discountType === 'pct' ? numOr0(f.pctOff) : f.discountType === 'amount' ? numOr0(f.amountOff) : numOr0(f.fixedPrice)
    if (discountVal <= 0) return 'Set a discount value.'
    if (f.kind === 'qty_tier' && intOr0(f.minQty) < 2) return 'Set a minimum quantity of 2 or more.'
    if ((f.kind === 'cart_value' || f.kind === 'coupon') && numOr0(f.minValue) <= 0) return 'Set a minimum cart value.'
    if (f.kind === 'coupon' && !f.couponCode.trim()) return 'Give the coupon a code.'
    if (f.kind === 'salesman_offer' && !splitReferrals(f.referralCodes).length) return 'Add at least one referral code for a salesman offer.'
    if (f.kind === 'bundle_price' && !splitCodes(f.itemCodes).length) return 'Add the item codes this bundle price applies to.'
    return null
  }

  async function doPreview() {
    const err = clientError()
    if (err) { setFormError(err); return }
    setBusy('preview'); setFormError(null)
    try {
      const res = await apiPost<RulePreviewResp>('/shop/rules/preview', buildPayload(f))
      setPreview(res?.impact || { items: 0, breach_count: 0, breaches: [] })
    } catch (e) {
      setFormError(errorText(e, 'Could not preview this rule.'))
    } finally {
      setBusy(null)
    }
  }

  async function doSave() {
    const err = clientError()
    if (err) { setFormError(err); return }
    setBusy('save'); setFormError(null)
    try {
      const payload = buildPayload(f)
      const saved = isNew
        ? await apiPost<RuleSaveResp>('/shop/rules', payload)
        : await apiPatch<RuleSaveResp>(`/shop/rules/${f.id}`, payload)
      const n = saved?.impact?.items ?? 0
      const breaches = saved?.impact?.breach_count ?? 0
      toast(
        breaches > 0
          ? `Rule saved — touches ${n} item${n === 1 ? '' : 's'}, ${breaches} clamped at the margin floor.`
          : `Rule saved — touches ${n} item${n === 1 ? '' : 's'}.`,
        breaches > 0 ? 'info' : 'success',
      )
      onSaved()
    } catch (e) {
      setFormError(errorText(e, 'Could not save this rule.'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="flex min-h-full items-start justify-center py-4 sm:items-center sm:py-8">
        <Card className="w-full max-w-2xl p-0" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between border-b px-5 py-4">
            <div className="font-display text-base font-semibold">{isNew ? 'New rule' : `Edit ${rule?.name || 'rule'}`}</div>
            <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-accent"><X size={18} /></button>
          </div>

          <div className="max-h-[65vh] space-y-4 overflow-y-auto px-5 py-4">
            {formError && (
              <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-[13px] text-rose-700">
                <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                <span>{formError}</span>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <label className="col-span-2 block sm:col-span-1">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Name *</span>
                <Input value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="Weekend cable deal" />
              </label>
              <label className="col-span-2 block sm:col-span-1">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Kind *</span>
                <select
                  value={f.kind}
                  onChange={(e) => set('kind', e.target.value as RuleKind)}
                  className="flex h-11 w-full rounded-lg border border-input bg-card px-3.5 text-sm text-foreground shadow-sm outline-none transition-colors focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {RULE_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
                </select>
              </label>
            </div>

            <div>
              <span className="mb-1 block text-xs font-semibold text-muted-foreground">Discount *</span>
              <div className="grid grid-cols-3 gap-2">
                {DISCOUNT_TYPES.map((t) => (
                  <button
                    key={t.value}
                    type="button"
                    onClick={() => set('discountType', t.value)}
                    className={cn(
                      'rounded-lg border px-2.5 py-2 text-[12.5px] font-medium transition',
                      f.discountType === t.value ? 'border-primary bg-[#f1ecfb] text-[#6d28d9]' : 'border-border text-muted-foreground hover:border-primary/40',
                    )}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <div className="relative mt-2">
                {f.discountType === 'pct' && (
                  <Input inputMode="decimal" value={f.pctOff} onChange={(e) => set('pctOff', e.target.value)} placeholder="5" className="pr-12" />
                )}
                {f.discountType === 'amount' && (
                  <Input inputMode="decimal" value={f.amountOff} onChange={(e) => set('amountOff', e.target.value)} placeholder="1.000" className="pr-14" />
                )}
                {f.discountType === 'fixed' && (
                  <Input inputMode="decimal" value={f.fixedPrice} onChange={(e) => set('fixedPrice', e.target.value)} placeholder="2.500" className="pr-14" />
                )}
                <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">
                  {f.discountType === 'pct' ? '%' : 'BHD'}
                </span>
              </div>
            </div>

            {f.kind === 'qty_tier' && (
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Minimum quantity *</span>
                <Input inputMode="numeric" value={f.minQty} onChange={(e) => set('minQty', e.target.value)} placeholder="12" />
              </label>
            )}

            {(f.kind === 'cart_value' || f.kind === 'coupon') && (
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Minimum cart value (BHD) *</span>
                <Input inputMode="decimal" value={f.minValue} onChange={(e) => set('minValue', e.target.value)} placeholder="100.000" />
              </label>
            )}

            {f.kind === 'coupon' && (
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Coupon code *</span>
                <Input
                  value={f.couponCode}
                  onChange={(e) => set('couponCode', e.target.value.toUpperCase().replace(/[^A-Z0-9-]/g, ''))}
                  placeholder="WELCOME5"
                />
              </label>
            )}

            <div className="space-y-3 rounded-xl border bg-secondary/30 p-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Scope</div>
              <label className="block">
                <span className="mb-1 block text-[12px] text-muted-foreground">
                  Item codes{f.kind === 'bundle_price' ? ' — required for a bundle price' : ''}
                </span>
                <Input value={f.itemCodes} onChange={(e) => set('itemCodes', e.target.value)} placeholder="T02, X24 CC, UK04" />
              </label>
              <label className="block">
                <span className="mb-1 block text-[12px] text-muted-foreground">Categories</span>
                <Input value={f.categories} onChange={(e) => set('categories', e.target.value)} placeholder="CABLE, BLUETOOTH HEADSET" />
              </label>
              <label className="block">
                <span className="mb-1 block text-[12px] text-muted-foreground">
                  Referral codes{f.kind === 'salesman_offer' ? ' — required for a salesman offer' : ''}
                </span>
                <Input value={f.referralCodes} onChange={(e) => set('referralCodes', e.target.value)} placeholder="furqan, ali" />
              </label>
              <p className="text-[11px] text-muted-foreground">Comma-separated. Leave all three blank to apply to every item and every customer.</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Starts</span>
                <Input type="datetime-local" value={f.startsAt} onChange={(e) => set('startsAt', e.target.value)} />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Ends</span>
                <Input type="datetime-local" value={f.endsAt} onChange={(e) => set('endsAt', e.target.value)} />
              </label>
            </div>
            <p className="-mt-2 text-[11px] text-muted-foreground">Leave either blank for an offer with no start or no end.</p>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Max uses</span>
                <Input inputMode="numeric" value={f.maxUses} onChange={(e) => set('maxUses', e.target.value)} placeholder="No limit" />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-muted-foreground">Priority</span>
                <Input inputMode="numeric" value={f.priority} onChange={(e) => set('priority', e.target.value)} placeholder="100" />
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-4">
              <Toggle checked={f.stackable} onChange={(v) => set('stackable', v)} label="Stackable with other offers" />
              <Toggle checked={f.isActive} onChange={(v) => set('isActive', v)} label={f.isActive ? 'Active' : 'Inactive'} />
            </div>

            {preview && (
              <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3">
                <div className="flex items-center gap-2 text-[13px] font-semibold text-amber-800">
                  <AlertTriangle size={15} className="shrink-0" />
                  {preview.items} item{preview.items === 1 ? '' : 's'} touched
                  {preview.breach_count > 0 ? ` — ${preview.breach_count} would be clamped by the margin floor` : ' — none below the margin floor'}
                </div>
                {preview.breaches.length > 0 && (
                  <ul className="space-y-1 text-[12.5px] text-amber-800">
                    {preview.breaches.map((b) => (
                      <li key={b.item_code}>
                        would sell <b>{b.item_code}</b> at {bhd(b.unit_bhd, 3)} — below the floor {bhd(b.floor_bhd, 3)}; it will be clamped
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 border-t px-5 py-4">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="button" variant="outline" onClick={doPreview} disabled={busy !== null}>
              {busy === 'preview' ? <Loader2 className="animate-spin" size={15} /> : <Eye size={15} />} Preview impact
            </Button>
            <Button type="button" onClick={doSave} disabled={busy !== null}>
              {busy === 'save' ? <Loader2 className="animate-spin" size={15} /> : <Check size={15} />} Save
            </Button>
          </div>
        </Card>
      </div>
    </div>
  )
}

// ── section 1: Rules & coupons ────────────────────────────────────────────────

function RulesSection() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['shop-rules'], queryFn: () => apiGet<RulesResp>('/shop/rules') })
  const [editing, setEditing] = useState<DiscountRule | 'new' | null>(null)

  const refresh = () => qc.invalidateQueries({ queryKey: ['shop-rules'] })

  const toggleActive = useMutation({
    mutationFn: (r: DiscountRule) => apiPatch(`/shop/rules/${r.id}`, { is_active: !r.is_active }),
    onSuccess: refresh,
    onError: (e) => toast(errorText(e, 'Could not update the rule.'), 'error'),
  })

  async function remove(r: DiscountRule) {
    if (!window.confirm(`Delete "${r.name}"? This can't be undone.`)) return
    try {
      await apiDelete(`/shop/rules/${r.id}`)
      toast('Rule deleted.', 'success')
      refresh()
    } catch (e) {
      toast(errorText(e, 'Delete failed.'), 'error')
    }
  }

  const rows = data?.rules || []

  const cols: Column<DiscountRule>[] = [
    { key: 'name', label: 'Name', render: (_, r) => (
        <div>
          <div className="font-semibold">{r.name}</div>
          <div className="text-[11px] text-muted-foreground">{KIND_LABEL[r.kind] || r.kind}</div>
        </div>
      ) },
    { key: 'summary', label: 'Summary', render: (_, r) => <span className="block max-w-[240px] whitespace-normal">{r.summary || '—'}</span> },
    { key: 'scope', label: 'Scope', render: (_, r) => <ScopeChips scope={r.scope} /> },
    { key: 'starts_at', label: 'Window', render: (_, r) => windowLabel(r.starts_at, r.ends_at) },
    { key: 'uses', label: 'Uses', align: 'right', render: (_, r) => usesLabel(r) },
    { key: 'status', label: 'Status', render: (_, r) => <RuleStatusPill status={r.status} /> },
    { key: 'is_active', label: 'Active', render: (_, r) => (
        <Toggle checked={r.is_active} onChange={() => toggleActive.mutate(r)} label={r.is_active ? 'On' : 'Off'} />
      ) },
    { key: 'id', label: '', align: 'right', render: (_, r) => (
        <div className="flex justify-end gap-1.5">
          <Button type="button" variant="outline" size="sm" onClick={() => setEditing(r)}><Pencil size={13} /></Button>
          <Button type="button" variant="destructive" size="sm" onClick={() => remove(r)}><Trash2 size={13} /></Button>
        </div>
      ) },
  ]

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">
          Quantity tiers, cart-value discounts, coupons, bundle prices and salesman-only offers — every
          discount is clamped to the margin floor automatically.
        </p>
        <Button size="sm" onClick={() => setEditing('new')} className="shrink-0"><Plus size={15} /> New rule</Button>
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : isError ? (
        <ErrorPanel error={error} />
      ) : (
        <DataTable rows={rows} cols={cols} exportName="yq-shop-rules"
          empty="No rules yet — add a quantity tier, coupon or cart-value discount to get started." />
      )}

      {editing != null && (
        <RuleDialog
          rule={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh() }}
        />
      )}
    </div>
  )
}

// ── section 2: Margin health ──────────────────────────────────────────────────

function MarginsSection() {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['shop-margins'], queryFn: () => apiGet<MarginsResp>('/shop/margins') })
  const [filter, setFilter] = useState<'all' | 'below_floor' | 'no_cost'>('all')

  const filtered = useMemo(() => {
    const rows = data?.rows || []
    return filter === 'all' ? rows : rows.filter((r) => r.status === filter)
  }, [data, filter])
  const emptyMsg =
    filter === 'below_floor' ? 'Nothing below the margin floor right now.'
      : filter === 'no_cost' ? 'Every priced item has a landed cost on file.'
        : 'No priced items yet.'

  const cols: Column<MarginRow>[] = [
    { key: 'item_code', label: 'Code', render: (_, r) => <span className="font-semibold">{r.item_code}</span> },
    { key: 'spec', label: 'Spec', render: (_, r) => <span className="block max-w-[220px] truncate" title={r.spec || ''}>{r.spec || '—'}</span> },
    { key: 'category', label: 'Category', render: (_, r) => r.category || '—' },
    { key: 'price_incl_vat_bhd', label: 'Price incl. VAT', align: 'right', render: (_, r) => money3(r.price_incl_vat_bhd) },
    { key: 'price_ex_vat_bhd', label: 'Price ex. VAT', align: 'right', render: (_, r) => money3(r.price_ex_vat_bhd) },
    { key: 'landed_cost_bhd', label: 'Landed cost', align: 'right', render: (_, r) => money3(r.landed_cost_bhd) },
    { key: 'profit_bhd', label: 'Profit/unit', align: 'right', render: (_, r) => money3(r.profit_bhd) },
    { key: 'margin_pct', label: 'Margin %', align: 'right', render: (_, r) => (
        <span className={cn(
          r.status === 'below_floor' && ((r.margin_pct ?? 0) < 0 ? 'font-semibold text-rose-600' : 'font-semibold text-amber-600'),
        )}
        >
          {pctFrac(r.margin_pct)}
        </span>
      ) },
    { key: 'markup_pct', label: 'Markup %', align: 'right', render: (_, r) => pctFrac(r.markup_pct) },
    { key: 'floor_bhd', label: 'Floor', align: 'right', render: (_, r) => money3(r.floor_bhd) },
    { key: 'stock_status', label: 'Stock', render: (_, r) => <StockPill status={r.stock_status} /> },
    { key: 'sold_90d', label: 'Sold 90d', align: 'right', render: (_, r) => num(r.sold_90d) },
    { key: 'status', label: 'Status', render: (_, r) => (
        <Badge tone={MARGIN_STATUS_TONE[r.status || ''] || 'grey'}>{MARGIN_STATUS_LABEL[r.status || ''] || r.status || '—'}</Badge>
      ) },
  ]

  return (
    <div>
      {data?.summary && (
        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Stat label="Items" value={num(data.summary.items)} />
          <Stat label="With cost" value={num(data.summary.with_cost)} />
          <Stat label="Below floor" value={num(data.summary.below_floor)} tone={data.summary.below_floor > 0 ? 'amber' : undefined} />
          <Stat label="VAT rate" value={pctFrac(data.summary.vat_rate, 0)} />
          <Stat label="Min margin" value={pctFrac(data.summary.min_margin_pct, 0)} tone="violet" />
        </div>
      )}

      <div className="mb-3 flex gap-1.5 overflow-x-auto pb-1">
        {(['all', 'below_floor', 'no_cost'] as const).map((k) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            className={cn(
              'shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] font-medium transition',
              filter === k ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40',
            )}
          >
            {k === 'all' ? 'All' : k === 'below_floor' ? 'Below floor' : 'No cost'}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : isError ? (
        <ErrorPanel error={error} />
      ) : (
        <DataTable rows={filtered} cols={cols} exportName="yq-margin-health" empty={emptyMsg} />
      )}
    </div>
  )
}

// ── section 3: Unpriced stock ─────────────────────────────────────────────────

function UnpricedSection() {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['shop-unpriced-stock'], queryFn: () => apiGet<UnpricedResp>('/shop/unpriced-stock') })
  const rows = data?.rows || []
  const totals = useMemo(() => {
    const list = data?.rows || []
    return {
      value: list.reduce((s, r) => s + Number(r.value_bhd || 0), 0),
      units: list.reduce((s, r) => s + Number(r.stock_qty || 0), 0),
    }
  }, [data])

  const cols: Column<UnpricedRow>[] = [
    { key: 'item_name', label: 'Item', render: (_, r) => r.item_name || '—' },
    { key: 'warehouse_name', label: 'Warehouse', render: (_, r) => r.warehouse_name || '—' },
    { key: 'stock_qty', label: 'Units', align: 'right', render: (_, r) => num(r.stock_qty) },
    { key: 'value_bhd', label: 'Value', align: 'right', render: (_, r) => money3(r.value_bhd) },
    { key: 'matched_code', label: 'Matched code', render: (_, r) => (
        r.matched_code
          ? <code className="rounded bg-secondary px-1.5 py-0.5 text-[12px]">{r.matched_code}</code>
          : <span className="text-muted-foreground">—</span>
      ) },
    { key: 'match_source', label: 'Match source', render: (_, r) => r.match_source || '—' },
  ]

  return (
    <div>
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">
        Stock in the warehouse with no active price-book code — it cannot be sold online until it is
        added to the MA price book.
      </p>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:w-fit">
        <Stat label="Value at risk" value={bhd(totals.value, 3)} tone="amber" />
        <Stat label="Units" value={num(totals.units)} />
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : isError ? (
        <ErrorPanel error={error} />
      ) : (
        <DataTable rows={rows} cols={cols} exportName="yq-unpriced-stock"
          empty="Every warehouse item has an active price-book code — nothing to fix." />
      )}
    </div>
  )
}

// ── page ───────────────────────────────────────────────────────────────────────

const SECTIONS = [
  { key: 'rules', label: 'Rules & coupons', icon: Tag },
  { key: 'campaigns', label: 'Campaigns', icon: Megaphone },
  { key: 'margins', label: 'Margin health', icon: Percent },
  { key: 'unpriced', label: 'Unpriced stock', icon: Boxes },
] as const
type SectionKey = (typeof SECTIONS)[number]['key']

export default function ShopRules() {
  const [tab, setTab] = useState<SectionKey>('rules')

  return (
    <div>
      <PageHeader title="Offers & Rules" subtitle="Discount rules, coupons, marketplace campaigns and the live health of every margin behind the shop" />

      <div className="mb-5 flex gap-1.5 overflow-x-auto pb-1">
        {SECTIONS.map((s) => (
          <button
            key={s.key}
            onClick={() => setTab(s.key)}
            className={cn(
              'flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[13px] font-medium transition',
              tab === s.key ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40',
            )}
          >
            <s.icon size={14} /> {s.label}
          </button>
        ))}
      </div>

      {tab === 'rules' && <RulesSection />}
      {tab === 'campaigns' && <CampaignsSection />}
      {tab === 'margins' && <MarginsSection />}
      {tab === 'unpriced' && <UnpricedSection />}
    </div>
  )
}
