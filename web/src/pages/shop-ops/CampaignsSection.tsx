import { useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type FormEvent, type KeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowRight, Check, Clock, ImagePlus, Layers, Loader2, Megaphone, Monitor, Pencil, Plus, Search,
  Smartphone, Trash2, X,
} from 'lucide-react'
import { apiDelete, apiGet, apiPatch, apiPost, apiUpload, ApiError } from '@/lib/api'
import type { BadgeKind, CampaignCanvas, CatalogPayload, ShopItem } from '@/lib/shopApi'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { DataTable, type Column } from '@/components/DataTable'

/**
 * Campaigns — the marketplace's promotions layer, scheduled here by the office. Each one is a
 * title, a line, a creative and a link, shown where `placement` says: the home promo slider (hero
 * first, then strip), the desktop spotlight or one category's shelf. The creative is either an
 * uploaded photo (framed with `image_fit`, contain by default — never cropped unless the office
 * says cover) or composed from up to three real products on a pastel/plum/night `canvas`. A
 * campaign can be tied to a discount rule so its countdown is the rule's real end. Sponsored slots
 * exist for a supplier who pays for the space; merchants always see them labelled.
 *
 * The preview below approximates the market's slide card (web/src/market/components/SlideCard.tsx)
 * in portal code — the market bundle is never imported here — with the market's tokens hard-coded.
 */

type Placement = 'hero' | 'strip' | 'aside' | 'category'
type Audience = 'all' | 'recognized' | 'new'
type Fit = 'contain' | 'cover'
type CreativeMode = 'upload' | 'compose'
type PreviewSize = 'phone' | 'hero'

interface Campaign {
  id: number
  title: string
  title_ar?: string | null
  line?: string | null
  line_ar?: string | null
  image_url?: string | null
  image_url_600?: string | null
  image_fit?: Fit | null
  product_codes?: string[] | null
  canvas?: CampaignCanvas | null
  cta_label?: string | null
  cta_label_ar?: string | null
  cta_to: string
  placement: Placement[]
  category?: string | null
  audience: Audience
  rule_id?: number | null
  sponsored: boolean
  sponsor_name?: string | null
  starts_at?: string | null
  ends_at?: string | null
  is_active: boolean
  sort_order: number
  status?: 'live' | 'scheduled' | 'ended' | 'paused'
}
interface CampaignsResp { campaigns: Campaign[] }
interface RuleLite { id: number; name: string; kind: string; is_active: boolean; ends_at?: string | null }

/** app/shop.py CAMPAIGN_MAX_PRODUCTS */
const MAX_PRODUCTS = 3

/** What Tab can reach inside the editor dialog (the file input is `hidden`, so it is filtered out by layout). */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

const PLACEMENTS: { key: Placement; label: string; hint: string }[] = [
  { key: 'hero', label: 'Home hero', hint: 'Leads the promo slider — first slide on phone, the big stage on desktop' },
  { key: 'strip', label: 'Home slider', hint: 'Joins the promo slider after hero campaigns (phone swipe · desktop stage)' },
  { key: 'aside', label: 'Desktop spotlight', hint: 'Rotating panel beside the running order, ≥1280px' },
  { key: 'category', label: 'Category shelf', hint: 'Header banner at the top of one category' },
]
const AUDIENCES: { key: Audience; label: string }[] = [
  { key: 'all', label: 'Everyone' },
  { key: 'recognized', label: 'Shops that ordered before' },
  { key: 'new', label: 'New visitors' },
]
/** Real Browse filters (web/src/market/lib/facets.ts) and routes — wholesale destinations first. */
const CTA_PRESETS: { label: string; to: string }[] = [
  { label: 'Stock-Up Deals', to: '/shop?f=deals' },
  { label: 'Last-chance stock', to: '/shop?f=clearance' },
  { label: 'Price drops', to: '/shop?f=drops' },
  { label: 'Restock essentials', to: '/shop?f=best' },
  { label: 'Moving fast', to: '/shop?f=moving' },
  { label: 'New arrivals', to: '/shop?f=new' },
  { label: 'Paste a list', to: '/quick' },
  { label: 'Cables', to: '/t/cable' },
  { label: 'Chargers', to: '/t/charger' },
  { label: 'Power banks', to: '/t/power-bank' },
]
const CATEGORIES = ['CABLE', 'CHARGER', 'CAR CHARGER', 'POWER BANK', 'EARPHONE', 'BLUETOOTH HEADSET', 'BLUETOOTH SPEAKER', 'CAR ACCESSORIES']

const STATUS_TONE: Record<string, BadgeTone> = { live: 'green', scheduled: 'accent', ended: 'grey', paused: 'amber' }

/* The market's canvases (web/src/market/market.css tokens --m-tile-*, --m-plum, --m-grad-night), hard-coded. */
interface CanvasDef { key: CampaignCanvas; label: string; hint: string; bg: string; rgb: string; dark: boolean }
const NIGHT_GRADIENT = 'linear-gradient(135deg, #2a1259, #140f24)'
const CANVASES: CanvasDef[] = [
  { key: 'lilac', label: 'Lilac', hint: 'Everyday promotions — soft lilac, ink text.', bg: '#EADCF9', rgb: '234,220,249', dark: false },
  { key: 'apricot', label: 'Apricot', hint: 'Deals and last-chance stock — warm apricot, ink text.', bg: '#FDE1C3', rgb: '253,225,195', dark: false },
  { key: 'mint', label: 'Mint', hint: 'Restock essentials and new lines — fresh mint, ink text.', bg: '#C9EDDD', rgb: '201,237,221', dark: false },
  { key: 'plum', label: 'Plum', hint: 'Brand moments — YQ plum with white text.', bg: '#6D4091', rgb: '109,64,145', dark: true },
  { key: 'night', label: 'Night', hint: 'Statement hero — the YQ night horizon with white text.', bg: NIGHT_GRADIENT, rgb: '20,15,36', dark: true },
]
const canvasOf = (key?: string | null): CanvasDef => CANVASES.find((c) => c.key === key) ?? CANVASES[0]
const INK = '#191424'
const INK_2 = '#5F556C'
const PLUM = '#6D4091'

/* ───────────────────────── helpers ───────────────────────── */

function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    try {
      const j = JSON.parse(e.body) as { detail?: unknown }
      if (typeof j.detail === 'string' && j.detail) return j.detail
      // FastAPI 422: a list of {loc, msg}
      if (Array.isArray(j.detail) && j.detail.length) {
        return j.detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d))).join(' · ')
      }
    } catch {
      /* not json */
    }
    return e.body.slice(0, 200) || fallback
  }
  return fallback
}
function isoToLocal(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function localToIso(local: string): string | null {
  if (!local) return null
  const d = new Date(local)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}
function windowLabel(a?: string | null, b?: string | null): string {
  const f = (s?: string | null) => (s ? new Date(s).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) : null)
  const x = f(a), y = f(b)
  if (x && y) return `${x} → ${y}`
  if (x) return `from ${x}`
  if (y) return `until ${y}`
  return 'Always'
}

const hasBadge = (i: ShopItem, b: BadgeKind) => Boolean(i.badges?.includes(b))
/**
 * A price drop the marketplace really prints: the old trade price is above today's price, which is
 * exactly when the card shows the struck-through anchor (`priceAnchor()`, web/src/market/lib/format.ts).
 * The `price_drop` badge alone is not enough — a card with no old price must never sit in a
 * "Price drops" campaign.
 */
const realDrop = (i: ShopItem) => {
  if (i.was_bhd == null || i.price_bhd == null) return false
  const was = Number(i.was_bhd), price = Number(i.price_bhd)
  return Number.isFinite(was) && Number.isFinite(price) && was > price
}
const hasPhoto = (i: ShopItem) => Boolean(i.thumb_urls || i.thumb_url || i.product_image_url)
const inStock = (i: ShopItem) => i.stock_status !== 'out_of_stock'
const thumbOf = (i: ShopItem, size: '160' | '320' = '160') => i.thumb_urls?.[size] || i.thumb_url || i.product_image_url || ''

/** A readable name for the picker: the display name, else the spec's first line, minus the code and "(VFAN)". */
function itemName(i: ShopItem): string {
  const code = i.item_code
  const dn = (i.display_name || '').trim()
  const raw = dn && dn.toUpperCase() !== code.toUpperCase() ? dn : (i.spec || '').split('\n')[0].trim()
  let s = raw.replace(/\s*\(VFAN\)\s*/gi, ' ').replace(/\s{2,}/g, ' ').trim()
  if (s.toUpperCase().startsWith(code.toUpperCase())) s = s.slice(code.length).replace(/^[\s\-–:·]+/, '')
  return s || code
}

interface CatalogIndex { items: ShopItem[]; byUpper: Map<string, ShopItem> }
type CatalogStatus = 'loading' | 'error' | 'ready'

function indexCatalog(items?: ShopItem[] | null): CatalogIndex {
  const list = items || []
  return { items: list, byUpper: new Map(list.map((i) => [i.item_code.toUpperCase(), i])) }
}

/** Composed art, as the market builds it: the codes the catalog knows, with a photo, at most three. */
function composedProducts(codes: string[], byUpper: Map<string, ShopItem>): ShopItem[] {
  return codes
    .map((c) => byUpper.get(c.trim().toUpperCase()))
    .filter((i): i is ShopItem => i != null && hasPhoto(i))
    .slice(0, MAX_PRODUCTS)
}

const LABEL = 'mb-1 block text-xs font-semibold text-muted-foreground'
const SELECT = 'flex h-11 w-full rounded-lg border border-input bg-card px-3.5 text-sm text-foreground shadow-sm outline-none transition-colors focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring'

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className="inline-flex items-center gap-2 text-[12.5px] font-medium text-foreground">
      <span className={cn('relative inline-block h-5 w-9 rounded-full transition-colors', checked ? 'bg-primary' : 'bg-border')}>
        <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', checked ? 'left-[18px]' : 'left-0.5')} />
      </span>
      {label}
    </button>
  )
}

/** Small two/three-way switch (preview size, language, fit). */
function Segmented<T extends string>({ value, options, onChange, label, className }: {
  value: T
  options: { value: T; label: string; icon?: typeof Monitor }[]
  onChange: (v: T) => void
  label: string
  className?: string
}) {
  return (
    <div role="group" aria-label={label} className={cn('inline-flex rounded-lg border bg-card p-0.5', className)}>
      {options.map((o) => {
        const on = o.value === value
        const Icon = o.icon
        return (
          <button key={o.value} type="button" aria-pressed={on} onClick={() => onChange(o.value)}
            className={cn('inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[12px] font-semibold transition',
              on ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
            {Icon && <Icon size={13} aria-hidden />}
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

/* ───────────────────────── creative art (portal approximation of the market) ───────────────────────── */

/** The portal's glow horizon, small: night gradient + four stacked arcs cresting in the lower third. */
function NightHorizon() {
  const arc = (extra: CSSProperties): CSSProperties => ({ position: 'absolute', inset: 0, borderRadius: '50%', display: 'block', ...extra })
  return (
    <span aria-hidden style={{ position: 'absolute', inset: 0, overflow: 'hidden', background: NIGHT_GRADIENT }}>
      <i style={arc({ transform: 'translateY(80%) scale(1.32)', background: '#fff', boxShadow: '0 -2px 12px 0 #ffffffb5' })} />
      <i style={arc({ transform: 'translateY(80%) scale(1.2)', background: '#A558FB', filter: 'blur(16px)' })} />
      <i style={arc({ transform: 'translateY(80%) scale(1.24)', background: '#4922E5', filter: 'blur(11px)' })} />
      <i style={arc({ transform: 'translateY(80%) scale(1.2)', background: '#1c0b3f', filter: 'blur(26px)' })} />
    </span>
  )
}

/** Disc layout per product count: centre (x, y in % of the art box), size in % of its height, rotation. */
const FAN: Record<number, { x: number; y: number; s: number; r: number; z: number }[]> = {
  1: [{ x: 50, y: 50, s: 76, r: 0, z: 3 }],
  2: [{ x: 34, y: 52, s: 60, r: -6, z: 3 }, { x: 69, y: 49, s: 54, r: 7, z: 2 }],
  3: [{ x: 50, y: 47, s: 60, r: 0, z: 3 }, { x: 22, y: 58, s: 46, r: -8, z: 2 }, { x: 78, y: 58, s: 46, r: 8, z: 1 }],
}

function Fan({ products, dark }: { products: ShopItem[]; dark: boolean }) {
  const spots = FAN[products.length] || []
  return (
    <>
      {products.map((p, k) => {
        const s = spots[k]
        return (
          <span key={p.item_code} aria-hidden style={{
            position: 'absolute', left: `${s.x}%`, top: `${s.y}%`, height: `${s.s}%`, aspectRatio: '1 / 1', zIndex: s.z,
            transform: `translate(-50%, -50%) rotate(${s.r}deg)`, borderRadius: 9999, background: '#fff', overflow: 'hidden',
            boxShadow: dark ? '0 10px 26px -8px rgba(0,0,0,.6)' : '0 10px 24px -10px rgba(25,20,36,.38)',
          }}>
            <img src={thumbOf(p, '320')} alt="" draggable={false} style={{ width: '100%', height: '100%', objectFit: 'contain', padding: '15%' }} />
          </span>
        )
      })}
    </>
  )
}

const TYPE: Record<PreviewSize, {
  ratio: string; radius: number; pad: string; copyW: string; art: CSSProperties
  kicker: string; title: string; line: string; lineGap: string; ctaGap: string; ctaH: string; ctaPx: string; ctaFont: string
}> = {
  // phone card ≈ 340 px wide in the market: 1cqw ≈ 3.4 px
  phone: {
    ratio: '2 / 1', radius: 12, pad: '4.7cqw', copyW: '57%', art: { top: '1.5%', bottom: '1.5%', right: '2.5%', width: '43%' },
    kicker: 'max(8px, 3cqw)', title: 'max(12px, 5.2cqw)', line: 'max(9px, 3.6cqw)', lineGap: '1.2cqw', ctaGap: '2.9cqw',
    ctaH: 'max(20px, 8.8cqw)', ctaPx: '3.5cqw', ctaFont: 'max(9px, 3.5cqw)',
  },
  // desktop hero stage ≈ 900 px wide: 1cqw ≈ 9 px
  hero: {
    ratio: '21 / 9', radius: 16, pad: '5.5cqw', copyW: '56%', art: { top: '7%', bottom: '7%', right: '3%', width: '42%' },
    kicker: 'max(7.5px, 1.45cqw)', title: 'max(14px, 4.4cqw)', line: 'max(8.5px, 1.8cqw)', lineGap: '1.3cqw', ctaGap: '2.6cqw',
    ctaH: 'max(20px, 4.9cqw)', ctaPx: '2.2cqw', ctaFont: 'max(9px, 1.7cqw)',
  },
}

interface PreviewProps {
  f: Form
  mode: CreativeMode
  byUpper: Map<string, ShopItem>
  size: PreviewSize
  lang: 'en' | 'ar'
  endsLabel: string | null
}

/** What the merchant sees: canvas, kicker, title, line, CTA pill and the art — uploaded photo (contain
 *  on the canvas, or cover behind a scrim) or up to three product photos fanned on white discs. */
function SlidePreview({ f, mode, byUpper, size, lang, endsLabel }: PreviewProps) {
  const canvas = canvasOf(f.canvas)
  const t = TYPE[size]
  const ar = lang === 'ar'
  const compose = mode === 'compose'
  const image = !compose && f.image_url ? { src: f.image_url_600 || f.image_url, fit: f.image_fit } : null
  const cover = image?.fit === 'cover'
  const products = compose ? composedProducts(f.product_codes, byUpper) : []
  const dark = canvas.dark
  const sponsor = f.sponsored ? (f.sponsor_name.trim() ? `Sponsored · ${f.sponsor_name.trim()}` : 'Sponsored') : null
  const title = (ar && f.title_ar.trim()) || f.title.trim() || 'Campaign title'
  const line = (ar && f.line_ar.trim()) || f.line.trim()
  const cta = (ar && f.cta_label_ar.trim()) || f.cta_label.trim() || 'See more'
  const scrim = cover
    ? `linear-gradient(90deg, rgba(${canvas.rgb},.94) 0%, rgba(${canvas.rgb},.8) 40%, rgba(${canvas.rgb},0) 74%)`
    : 'linear-gradient(90deg, rgba(20,15,36,.55) 0%, rgba(20,15,36,.2) 50%, rgba(20,15,36,0) 76%)'

  return (
    <div
      className="relative isolate w-full overflow-hidden shadow-[0_1px_2px_rgba(25,20,36,.06),0_12px_32px_-14px_rgba(25,20,36,.35)]"
      style={{ aspectRatio: t.ratio, borderRadius: t.radius, containerType: 'inline-size', background: canvas.bg, color: dark ? '#fff' : INK }}
      aria-hidden="true"
    >
      {canvas.key === 'night' ? (
        <NightHorizon />
      ) : (
        !cover && (
          <span aria-hidden style={{ position: 'absolute', inset: 0, background: dark ? 'rgba(255,255,255,.08)' : 'rgba(255,255,255,.4)', clipPath: 'polygon(66% 0, 100% 0, 100% 100%, 42% 100%)' }} />
        )
      )}
      {cover && image && <img src={image.src} alt="" draggable={false} className="absolute inset-0 h-full w-full object-cover" />}
      {(cover || canvas.key === 'night') && <span aria-hidden className="absolute inset-0" style={{ background: scrim }} />}

      {!cover && (
        <span aria-hidden className="absolute" style={t.art}>
          {image ? (
            <span className="block h-full w-full" style={{ padding: '4%' }}>
              <img src={image.src} alt="" draggable={false} className="h-full w-full object-contain" />
            </span>
          ) : products.length ? (
            <Fan products={products} dark={dark} />
          ) : (
            <span className="absolute grid place-items-center rounded-full bg-white" style={{ left: '50%', top: '50%', height: '62%', aspectRatio: '1 / 1', transform: 'translate(-50%, -50%)', color: PLUM, boxShadow: '0 10px 24px -10px rgba(25,20,36,.38)' }}>
              {compose ? <Layers style={{ width: '38%', height: '38%' }} strokeWidth={1.5} /> : <Megaphone style={{ width: '38%', height: '38%' }} strokeWidth={1.5} />}
            </span>
          )}
        </span>
      )}

      <span dir={ar ? 'rtl' : 'ltr'} className="absolute inset-y-0 left-0 flex flex-col items-start justify-center" style={{ width: t.copyW, paddingInlineStart: ar ? '1%' : t.pad, paddingInlineEnd: ar ? t.pad : '1%' }}>
        {(sponsor || endsLabel) && (
          <span className="flex min-w-0 max-w-full items-center gap-1.5" style={{ marginBottom: t.lineGap }}>
            {sponsor && (
              <span className="min-w-0 truncate font-semibold uppercase" style={{ fontSize: t.kicker, letterSpacing: '0.08em', color: dark ? 'rgba(255,255,255,.8)' : PLUM }}>{sponsor}</span>
            )}
            {endsLabel && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full font-semibold" style={{ fontSize: t.kicker, padding: '0.25em 0.7em', background: dark ? 'rgba(255,255,255,.15)' : 'rgba(255,255,255,.75)', color: dark ? '#fff' : INK }}>
                <Clock style={{ width: '1.1em', height: '1.1em' }} aria-hidden /> {endsLabel}
              </span>
            )}
          </span>
        )}
        <span dir="auto" className="line-clamp-2 max-w-full break-words" style={{ fontFamily: "Sora, 'Space Grotesk', Inter, sans-serif", fontWeight: 800, fontSize: t.title, lineHeight: 1.08, letterSpacing: '-0.012em', color: dark ? '#fff' : INK }}>
          {title}
        </span>
        {line && (
          <span dir="auto" className="line-clamp-2 max-w-full" style={{ marginTop: t.lineGap, fontSize: t.line, lineHeight: 1.3, color: dark ? 'rgba(255,255,255,.8)' : INK_2 }}>{line}</span>
        )}
        <span className="inline-flex max-w-full items-center whitespace-nowrap rounded-full font-semibold"
          style={{ marginTop: t.ctaGap, height: t.ctaH, padding: `0 ${t.ctaPx}`, gap: '0.4em', fontSize: t.ctaFont, background: dark ? '#fff' : INK, color: dark ? INK : '#fff' }}>
          <span dir="auto" className="truncate">{cta}</span>
          <ArrowRight aria-hidden className={cn('shrink-0', ar && '-scale-x-100')} style={{ width: '1.15em', height: '1.15em' }} />
        </span>
      </span>
    </div>
  )
}

/** The creative in one small tile for the campaigns table — same rules, no crop unless the campaign says cover. */
function CreativeThumb({ c, byUpper }: { c: Campaign; byUpper: Map<string, ShopItem> }) {
  const canvas = canvasOf(c.canvas)
  const products = c.image_url ? [] : composedProducts(c.product_codes || [], byUpper)
  // DOM order left · centre · right so the centre disc sits in the middle of the row
  const row = [products[1], products[0], products[2]].filter((p): p is ShopItem => p != null)
  return (
    <span className="relative block h-10 w-20 shrink-0 overflow-hidden rounded-md ring-1 ring-black/5" style={{ background: canvas.bg }} aria-hidden>
      {c.image_url ? (
        <img src={c.image_url_600 || c.image_url} alt="" className={cn('h-full w-full', c.image_fit === 'cover' ? 'object-cover' : 'object-contain p-0.5')} />
      ) : row.length ? (
        <span className="absolute inset-0 flex items-center justify-center">
          {row.map((p) => {
            const centre = p === products[0]
            const rot = centre ? 0 : p === products[1] ? -8 : 8
            return (
              <img key={p.item_code} src={thumbOf(p)} alt=""
                className={cn('-mx-1 rounded-full bg-white object-contain p-[3px] shadow-sm ring-1 ring-black/5', centre ? 'relative z-10 h-8 w-8' : 'h-6 w-6')}
                style={{ transform: `rotate(${rot}deg)` }} />
            )
          })}
        </span>
      ) : (
        <span className="grid h-full w-full place-items-center" style={{ color: canvas.dark ? '#fff' : PLUM }}>
          <Megaphone size={16} />
        </span>
      )}
    </span>
  )
}

/* ───────────────────────── product picker (compose) ───────────────────────── */

const QUICK_PICKS: { key: string; label: string; test: (i: ShopItem) => boolean }[] = [
  { key: 'clearance', label: 'Last-chance lines', test: (i) => hasBadge(i, 'clearance') },
  { key: 'best', label: 'Restock essentials', test: (i) => hasBadge(i, 'best_seller') },
  { key: 'drops', label: 'Price drops', test: realDrop },
  { key: 'new', label: 'New arrivals', test: (i) => hasBadge(i, 'new') },
  { key: 'moving', label: 'Moving fast', test: (i) => hasBadge(i, 'trending') || hasBadge(i, 'selling_fast') },
]

const BADGE_LABEL: Partial<Record<BadgeKind, { label: string; tone: BadgeTone }>> = {
  clearance: { label: 'Last chance', tone: 'amber' },
  price_drop: { label: 'Price drop', tone: 'rose' },
  best_seller: { label: 'Best seller', tone: 'ink' },
  new: { label: 'New', tone: 'green' },
}

function searchCatalog(items: ShopItem[], query: string, chosen: string[]): ShopItem[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!tokens.length) return []
  const taken = new Set(chosen.map((c) => c.toUpperCase()))
  const q = query.trim().toUpperCase()
  return items
    .filter((i) => !taken.has(i.item_code.toUpperCase()))
    .filter((i) => {
      const hay = `${i.item_code} ${itemName(i)} ${i.spec || ''} ${i.category || ''} ${i.brand || ''}`.toLowerCase()
      return tokens.every((t) => hay.includes(t))
    })
    .map((i, order) => {
      const code = i.item_code.toUpperCase()
      const match = code === q ? 0 : code.startsWith(q) || code.startsWith(tokens[0].toUpperCase()) ? 1 : 2
      return { i, order, rank: match * 4 + (hasPhoto(i) ? 0 : 2) + (inStock(i) ? 0 : 1) }
    })
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .slice(0, 8)
    .map((x) => x.i)
}

function ProductPicker({ catalog, status, codes, onChange }: {
  catalog: CatalogIndex
  status: CatalogStatus
  codes: string[]
  onChange: (codes: string[]) => void
}) {
  const [q, setQ] = useState('')
  const full = codes.length >= MAX_PRODUCTS
  const results = useMemo(() => (status === 'ready' ? searchCatalog(catalog.items, q, codes) : []), [catalog.items, q, codes, status])
  const picks = useMemo(() => {
    const pool = catalog.items.filter((i) => inStock(i) && hasPhoto(i))
    return QUICK_PICKS
      .map((p) => ({ ...p, codes: pool.filter(p.test).slice(0, MAX_PRODUCTS).map((i) => i.item_code) }))
      .filter((p) => p.codes.length > 0)
  }, [catalog.items])

  const add = (code: string) => {
    const c = code.trim()
    if (!c || full || codes.some((x) => x.toUpperCase() === c.toUpperCase())) return
    onChange([...codes, c])
    setQ('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return
    e.preventDefault() // never submit the campaign from the search box
    if (status === 'ready' && results[0]) add(results[0].item_code)
    else if (status === 'error') add(q)
  }

  const notes: { code: string; text: string }[] = []
  if (status === 'ready') {
    for (const code of codes) {
      const it = catalog.byUpper.get(code.toUpperCase())
      if (!it) notes.push({ code, text: 'is not on the marketplace — it will be skipped.' })
      else if (!hasPhoto(it)) notes.push({ code, text: 'has no photo — it is left out of the creative.' })
      else if (!inStock(it)) notes.push({ code, text: 'is out of stock — merchants can’t order it today.' })
    }
  }

  return (
    <div className="space-y-2.5">
      {codes.length > 0 && (
        <ol className="flex flex-wrap gap-1.5" aria-label="Products in the creative">
          {codes.map((code, k) => {
            const it = catalog.byUpper.get(code.toUpperCase())
            const warn = status === 'ready' && (!it || !hasPhoto(it) || !inStock(it))
            return (
              <li key={code} className={cn('flex h-10 items-center gap-2 rounded-full border bg-card py-1 pl-1 pr-1', warn ? 'border-amber-300' : 'border-border')}>
                <span className="grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-full bg-white ring-1 ring-black/5">
                  {it && hasPhoto(it) ? <img src={thumbOf(it)} alt="" className="h-full w-full object-contain p-0.5" /> : <Layers size={13} className="text-muted-foreground" aria-hidden />}
                </span>
                <span className="max-w-[9rem] truncate text-[12.5px] font-semibold" title={it ? itemName(it) : code}>{code}</span>
                {k === 0 && codes.length > 1 && <span className="text-[10.5px] font-medium text-muted-foreground">centre</span>}
                {warn && <AlertTriangle size={13} className="text-amber-600" aria-hidden />}
                <button type="button" onClick={() => onChange(codes.filter((x) => x !== code))} aria-label={`Remove ${code}`}
                  className="grid h-8 w-8 place-items-center rounded-full text-muted-foreground transition hover:bg-muted hover:text-foreground">
                  <X size={14} />
                </button>
              </li>
            )
          })}
        </ol>
      )}

      <div className="relative">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          disabled={full}
          placeholder={full ? `${MAX_PRODUCTS} chosen — remove one to swap` :status === 'error' ? 'Type an item code, then Enter' : 'Search code or name — UK04, 20W, power bank'}
          aria-label="Search products to add"
          className="pl-9"
          spellCheck={false}
        />
      </div>

      {status === 'loading' && <Skeleton className="h-10 rounded-lg" />}
      {status === 'error' && (
        <p className="text-[11.5px] text-muted-foreground">The product list could not load — type exact item codes instead (Enter adds one). Codes the marketplace doesn’t carry are skipped.</p>
      )}

      {status === 'ready' && q.trim() !== '' && !full && (
        results.length ? (
          <ul className="max-h-64 space-y-0.5 overflow-y-auto rounded-xl border bg-card p-1" aria-label="Matching products">
            {results.map((i) => {
              const badge = i.badges?.map((b) => BADGE_LABEL[b]).find(Boolean)
              return (
                <li key={i.item_code}>
                  <button type="button" onClick={() => add(i.item_code)}
                    className="flex min-h-[44px] w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition hover:bg-accent focus-visible:bg-accent focus-visible:outline-none">
                    <span className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-lg bg-white ring-1 ring-black/5">
                      {hasPhoto(i) ? <img src={thumbOf(i)} alt="" loading="lazy" className="h-full w-full object-contain p-1" /> : <Layers size={14} className="text-muted-foreground" aria-hidden />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-[13px] font-semibold">{i.item_code}</span>
                        {badge && <Badge tone={badge.tone}>{badge.label}</Badge>}
                      </span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {itemName(i)}
                        {!hasPhoto(i) ? ' · no photo' : !inStock(i) ? ' · out of stock' : ''}
                      </span>
                    </span>
                    <Plus size={15} className="shrink-0 text-primary" aria-hidden />
                  </button>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="rounded-lg border border-dashed px-3 py-2.5 text-[12px] text-muted-foreground">No live product matches “{q.trim()}”.</p>
        )
      )}

      {status === 'ready' && picks.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold text-muted-foreground">Quick picks</span>
          {picks.map((p) => {
            const on = p.codes.length === codes.length && p.codes.every((c, k) => c === codes[k])
            return (
              <button key={p.key} type="button" aria-pressed={on} onClick={() => onChange(p.codes)}
                className={cn('h-8 rounded-full border px-3 text-[12px] font-medium transition',
                  on ? 'border-primary bg-[#EEE8F4] text-[#6D4091]' : 'border-border text-muted-foreground hover:border-primary/40 hover:text-foreground')}>
                {p.label} · {p.codes.length}
              </button>
            )
          })}
        </div>
      )}

      {notes.length > 0 && (
        <ul className="space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-[12px] text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
          {notes.map((n) => <li key={n.code}><b>{n.code}</b> {n.text}</li>)}
        </ul>
      )}
    </div>
  )
}

/* ───────────────────────── dialog ───────────────────────── */

interface Form {
  title: string
  title_ar: string
  line: string
  line_ar: string
  image_url: string
  image_url_600: string
  image_fit: Fit
  product_codes: string[]
  canvas: CampaignCanvas
  cta_label: string
  cta_label_ar: string
  cta_to: string
  placement: Placement[]
  category: string
  audience: Audience
  rule_id: number | null
  sponsored: boolean
  sponsor_name: string
  is_active: boolean
  sort_order: number
  starts_local: string
  ends_local: string
}

const MODES: { key: CreativeMode; label: string; hint: string; Icon: typeof Layers }[] = [
  { key: 'upload', label: 'Upload photo', hint: 'Your own banner — Contain keeps it whole', Icon: ImagePlus },
  { key: 'compose', label: 'Compose from products', hint: 'Up to 3 product photos, fanned — never cropped', Icon: Layers },
]

function formFrom(c: Campaign | null): Form {
  return {
    title: c?.title || '',
    title_ar: c?.title_ar || '',
    line: c?.line || '',
    line_ar: c?.line_ar || '',
    image_url: c?.image_url || '',
    image_url_600: c?.image_url_600 || '',
    image_fit: c?.image_fit === 'cover' ? 'cover' : 'contain',
    product_codes: (c?.product_codes || []).slice(0, MAX_PRODUCTS),
    canvas: canvasOf(c?.canvas).key,
    cta_label: c?.cta_label || '',
    cta_label_ar: c?.cta_label_ar || '',
    cta_to: c?.cta_to || '/shop?f=deals',
    placement: c?.placement?.length ? c.placement : ['strip'],
    category: c?.category || '',
    audience: c?.audience || 'all',
    rule_id: c?.rule_id ?? null,
    sponsored: Boolean(c?.sponsored),
    sponsor_name: c?.sponsor_name || '',
    is_active: c ? c.is_active : true,
    sort_order: c?.sort_order ?? 100,
    starts_local: isoToLocal(c?.starts_at),
    ends_local: isoToLocal(c?.ends_at),
  }
}

function CampaignDialog({ campaign, rules, catalog, catalogStatus, onClose, onSaved }: {
  campaign: Campaign | null
  rules: RuleLite[]
  catalog: CatalogIndex
  catalogStatus: CatalogStatus
  onClose: () => void
  onSaved: () => void
}) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  const [f, setF] = useState<Form>(() => formFrom(campaign))
  // a new campaign starts composed (always on-brand, nothing to crop); an existing one keeps its creative
  const [mode, setMode] = useState<CreativeMode>(() => (campaign ? (campaign.image_url || !campaign.product_codes?.length ? 'upload' : 'compose') : 'compose'))
  const [size, setSize] = useState<PreviewSize>('phone')
  const [lang, setLang] = useState<'en' | 'ar'>('en')
  const [now] = useState(() => Date.now())
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [err, setErr] = useState('')
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }))
  const togglePlacement = (p: Placement) => set('placement', f.placement.includes(p) ? f.placement.filter((x) => x !== p) : [...f.placement, p])
  const custom = !CTA_PRESETS.some((p) => p.to === f.cta_to)
  const canvas = canvasOf(f.canvas)

  // the close handler is rebuilt by the parent on every render; the mount effect below must not be
  useEffect(() => { closeRef.current = onClose }, [onClose])
  /* Modal manners (WAI-ARIA dialog pattern): focus moves into the dialog on open, Escape closes it
     from anywhere on the page (not only once the office has clicked inside), and focus returns to
     whatever opened it. Tab is kept inside by `trapTab` below. */
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    // structural type: `KeyboardEvent` in this file is React's synthetic one
    const onKey = (e: { key: string }) => { if (e.key === 'Escape') closeRef.current() }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener?.isConnected) opener.focus()
    }
  }, [])

  const trapTab = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab') return
    const node = dialogRef.current
    if (!node) return
    const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null)
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || active === node)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && active === last) {
      e.preventDefault()
      first.focus()
    }
  }

  // the market's countdown: the campaign's own end, else the tied rule's real end — shown within 7 days only
  const endIso = localToIso(f.ends_local) || rules.find((r) => r.id === f.rule_id)?.ends_at || null
  const endMs = endIso ? new Date(endIso).getTime() : NaN
  const endsLabel = Number.isFinite(endMs) && endMs > now && endMs - now <= 7 * 864e5
    ? `Ends ${new Date(endMs).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })}`
    : null

  async function upload(file: File) {
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await apiUpload<{ url: string; url_600?: string | null }>('/shop/campaigns/image', form)
      setF((s) => ({ ...s, image_url: r.url, image_url_600: r.url_600 || '' }))
    } catch (e) {
      toast(errorText(e, 'Upload failed.'), 'error')
    } finally {
      setUploading(false)
    }
  }
  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files?.[0]
    if (file && !uploading) void upload(file)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setErr('')
    const compose = mode === 'compose'
    const codes = compose ? f.product_codes.map((c) => c.trim()).filter(Boolean) : []
    if (compose && codes.length === 0) {
      setErr('Pick at least one product for the composed creative — or switch to Upload photo.')
      return
    }
    setBusy(true)
    const body = {
      title: f.title, title_ar: f.title_ar || null, line: f.line || null, line_ar: f.line_ar || null,
      // one creative per campaign: composing clears the uploaded photo (the market shows a photo over products)
      image_url: compose ? null : f.image_url || null,
      image_url_600: compose || !f.image_url ? null : f.image_url_600 || null,
      image_fit: f.image_fit,
      product_codes: codes.length ? codes : null,
      canvas: f.canvas,
      cta_label: f.cta_label || null, cta_label_ar: f.cta_label_ar || null, cta_to: f.cta_to, placement: f.placement,
      category: f.category || null, audience: f.audience, rule_id: f.rule_id || null, sponsored: f.sponsored,
      sponsor_name: f.sponsor_name || null, starts_at: localToIso(f.starts_local), ends_at: localToIso(f.ends_local),
      is_active: f.is_active, sort_order: Number(f.sort_order) || 100,
    }
    try {
      if (campaign) await apiPatch(`/shop/campaigns/${campaign.id}`, body)
      else await apiPost('/shop/campaigns', body)
      toast(campaign ? 'Campaign updated.' : 'Campaign created.', 'success')
      onSaved()
      onClose()
    } catch (e2) {
      setErr(errorText(e2, 'Could not save the campaign.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      ref={dialogRef} tabIndex={-1}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 outline-none sm:items-center sm:p-4"
      role="dialog" aria-modal="true" aria-label={campaign ? 'Edit campaign' : 'New campaign'}
      onKeyDown={trapTab}
    >
      <form onSubmit={submit} className="max-h-[92vh] w-full max-w-5xl overflow-y-auto rounded-t-2xl bg-card p-5 shadow-2xl sm:rounded-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-[18px] font-bold">{campaign ? 'Edit campaign' : 'New campaign'}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="grid h-10 w-10 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
            <X size={18} />
          </button>
        </div>

        {err && (
          <div role="alert" className="mb-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-[13px] text-rose-700">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {err}
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className={LABEL}>Title *</span>
                <Input value={f.title} onChange={(e) => set('title', e.target.value)} placeholder="Last-chance cables at trade price" maxLength={80} />
              </label>
              <label className="block" dir="rtl">
                <span className={cn(LABEL, 'text-right')}>العنوان (عربي)</span>
                <Input value={f.title_ar} onChange={(e) => set('title_ar', e.target.value)} placeholder="مخزون الفرصة الأخيرة" maxLength={160} />
              </label>
              <label className="block">
                <span className={LABEL}>Line</span>
                <Input value={f.line} onChange={(e) => set('line', e.target.value)} placeholder="Lines we’re clearing — full retail margin for your shop." maxLength={160} />
              </label>
              <label className="block" dir="rtl">
                <span className={cn(LABEL, 'text-right')}>السطر (عربي)</span>
                <Input value={f.line_ar} onChange={(e) => set('line_ar', e.target.value)} placeholder="بسعر الجملة حتى نفاد الكمية" maxLength={160} />
              </label>
            </div>

            {/* creative */}
            <section className="rounded-2xl border border-border p-4" aria-labelledby="creative-heading">
              <div className="mb-3 flex items-baseline justify-between gap-2">
                <h3 id="creative-heading" className="text-[13.5px] font-semibold">Creative</h3>
                <span className="text-[11px] text-muted-foreground">Nothing is ever cropped unless you choose Cover.</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Creative type">
                {MODES.map((m) => {
                  const on = mode === m.key
                  return (
                    <button key={m.key} type="button" aria-pressed={on} onClick={() => setMode(m.key)}
                      className={cn('flex items-start gap-3 rounded-xl border p-3 text-left transition', on ? 'border-primary bg-[#EEE8F4] dark:bg-primary/10' : 'border-border hover:border-primary/40')}>
                      <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-lg', on ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground')}>
                        <m.Icon size={17} aria-hidden />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-semibold text-foreground">{m.label}</span>
                        <span className="mt-0.5 block text-[11.5px] leading-snug text-muted-foreground">{m.hint}</span>
                      </span>
                    </button>
                  )
                })}
              </div>

              <div className="mt-4">
                {mode === 'upload' ? (
                  <div className="space-y-3">
                    <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden"
                      onChange={(e) => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = '' }} />
                    <div onDragOver={(e) => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
                      {f.image_url ? (
                        <div className={cn('flex items-center gap-3 rounded-xl border p-2.5 transition', dragging ? 'border-primary bg-primary/5' : 'border-border')}>
                          <span className="block aspect-[2/1] w-36 shrink-0 overflow-hidden rounded-lg ring-1 ring-black/5" style={{ background: canvas.bg }}>
                            <img src={f.image_url_600 || f.image_url} alt="Uploaded campaign photo" className={cn('h-full w-full', f.image_fit === 'cover' ? 'object-cover' : 'object-contain p-1')} />
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="text-[12.5px] font-medium">Photo uploaded</div>
                            <div className="text-[11px] text-muted-foreground">{f.image_url_600 ? '600 + 1200 px versions ready' : '1200 px version'}</div>
                            <div className="mt-1.5 flex flex-wrap gap-2">
                              <Button type="button" size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading}>
                                {uploading ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />} Replace
                              </Button>
                              <Button type="button" size="sm" variant="ghost" onClick={() => setF((s) => ({ ...s, image_url: '', image_url_600: '' }))} className="text-rose-700 hover:bg-rose-50 hover:text-rose-700">
                                Remove
                              </Button>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading}
                          className={cn('flex h-28 w-full flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed text-[13px] font-medium transition',
                            dragging ? 'border-primary bg-primary/5 text-foreground' : 'border-border text-muted-foreground hover:border-primary/50')}>
                          <span className="inline-flex items-center gap-2">
                            {uploading ? <Loader2 size={16} className="animate-spin" /> : <ImagePlus size={16} />} {uploading ? 'Uploading…' : 'Upload or drop a photo'}
                          </span>
                          <span className="text-[11px] font-normal">JPG, PNG or WEBP · landscape, 1200 px wide or more · optional</span>
                        </button>
                      )}
                    </div>
                    <div>
                      <span className={LABEL}>Image fit</span>
                      <Segmented label="Image fit" value={f.image_fit} onChange={(v) => set('image_fit', v)}
                        options={[{ value: 'contain', label: 'Contain' }, { value: 'cover', label: 'Cover' }]} />
                      <span className="mt-1 block text-[11px] text-muted-foreground">
                        {f.image_fit === 'contain'
                          ? 'The whole photo sits on the canvas beside the text — never cropped (recommended).'
                          : 'The photo fills the card behind a canvas-coloured scrim — its edges can be cropped on some screens.'}
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <span className={LABEL}>Products (up to {MAX_PRODUCTS}; the first sits in the centre)</span>
                    <ProductPicker catalog={catalog} status={catalogStatus} codes={f.product_codes} onChange={(codes) => set('product_codes', codes)} />
                    {f.image_url && (
                      <p className="text-[11.5px] text-muted-foreground">Saving as a composed creative removes the uploaded photo from this campaign.</p>
                    )}
                  </div>
                )}
              </div>

              <div className="mt-4">
                <span className={LABEL}>Canvas</span>
                <div className="grid grid-cols-5 gap-2" role="group" aria-label="Canvas">
                  {CANVASES.map((c) => {
                    const on = f.canvas === c.key
                    return (
                      <button key={c.key} type="button" aria-pressed={on} onClick={() => set('canvas', c.key)} title={c.hint}
                        className="flex flex-col items-center gap-1.5 rounded-xl p-1 text-[11.5px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        <span className={cn('grid h-11 w-full place-items-center rounded-lg ring-1 ring-black/10 transition', on && 'ring-2 ring-primary ring-offset-2 ring-offset-card')} style={{ background: c.bg }}>
                          {on && <Check size={16} strokeWidth={2.5} style={{ color: c.dark ? '#fff' : INK }} aria-hidden />}
                        </span>
                        <span className={on ? 'text-foreground' : 'text-muted-foreground'}>{c.label}</span>
                      </button>
                    )
                  })}
                </div>
                <span className="mt-1 block text-[11px] text-muted-foreground">{canvas.hint}</span>
              </div>
            </section>

            <div>
              <span className={LABEL}>Where it links *</span>
              <div className="flex flex-wrap gap-1.5">
                {CTA_PRESETS.map((p) => (
                  <button key={p.to} type="button" aria-pressed={f.cta_to === p.to} onClick={() => set('cta_to', p.to)} className={cn('rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition', f.cta_to === p.to ? 'border-primary bg-[#EEE8F4] text-[#6D4091]' : 'border-border text-muted-foreground hover:border-primary/40')}>
                    {p.label}
                  </button>
                ))}
                <button type="button" aria-pressed={custom} onClick={() => set('cta_to', '/p/')} className={cn('rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition', custom ? 'border-primary bg-[#EEE8F4] text-[#6D4091]' : 'border-border text-muted-foreground hover:border-primary/40')}>
                  Custom
                </button>
              </div>
              {custom && <Input className="mt-2" value={f.cta_to} onChange={(e) => set('cta_to', e.target.value)} placeholder="/p/X01 or https://…" />}
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className={LABEL}>Button text</span>
                  <Input value={f.cta_label} onChange={(e) => set('cta_label', e.target.value)} placeholder="See more" maxLength={40} />
                </label>
                <label className="block" dir="rtl">
                  <span className={cn(LABEL, 'text-right')}>نص الزر</span>
                  <Input value={f.cta_label_ar} onChange={(e) => set('cta_label_ar', e.target.value)} placeholder="اعرض المزيد" maxLength={40} />
                </label>
              </div>
            </div>

            <div>
              <span className={LABEL}>Placement *</span>
              <div className="grid gap-2 sm:grid-cols-2">
                {PLACEMENTS.map((p) => {
                  const on = f.placement.includes(p.key)
                  return (
                    <button key={p.key} type="button" onClick={() => togglePlacement(p.key)} aria-pressed={on} className={cn('rounded-xl border p-3 text-left transition', on ? 'border-primary bg-[#EEE8F4] dark:bg-primary/10' : 'border-border hover:border-primary/40')}>
                      <div className="flex items-center gap-2 text-[13px] font-semibold">
                        <span className={cn('grid h-4 w-4 place-items-center rounded border', on ? 'border-primary bg-primary text-white' : 'border-border')}>{on && <Check size={11} />}</span>
                        {p.label}
                      </div>
                      <div className="mt-0.5 text-[11.5px] text-muted-foreground">{p.hint}</div>
                    </button>
                  )
                })}
              </div>
              <p className="mt-2 text-[11.5px] text-muted-foreground">A campaign shows once per page — hero campaigns are kept out of the strip automatically.</p>
              {f.placement.includes('category') && (
                <label className="mt-2 block">
                  <span className={LABEL}>Category *</span>
                  <select value={f.category} onChange={(e) => set('category', e.target.value)} className={SELECT}>
                    <option value="">Choose…</option>
                    {CATEGORIES.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <label className="block">
                <span className={LABEL}>Audience</span>
                <select value={f.audience} onChange={(e) => set('audience', e.target.value as Audience)} className={SELECT}>
                  {AUDIENCES.map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className={LABEL}>Starts</span>
                <Input type="datetime-local" value={f.starts_local} onChange={(e) => set('starts_local', e.target.value)} />
              </label>
              <label className="block">
                <span className={LABEL}>Ends</span>
                <Input type="datetime-local" value={f.ends_local} onChange={(e) => set('ends_local', e.target.value)} />
              </label>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className={LABEL}>Tied to a discount rule (optional)</span>
                <select value={f.rule_id ?? ''} onChange={(e) => set('rule_id', e.target.value ? Number(e.target.value) : null)} className={SELECT}>
                  <option value="">None</option>
                  {rules.map((r) => (
                    <option key={r.id} value={r.id}>{r.name} · {r.kind}{r.is_active ? '' : ' (off)'}</option>
                  ))}
                </select>
                <span className="mt-1 block text-[11px] text-muted-foreground">The banner then ends exactly when the rule ends, and disappears if the rule is switched off.</span>
              </label>
              <label className="block">
                <span className={LABEL}>Order</span>
                <Input type="number" value={f.sort_order} onChange={(e) => set('sort_order', Number(e.target.value))} />
              </label>
            </div>

            <div className="rounded-xl border border-border p-3">
              <Toggle checked={f.sponsored} onChange={(v) => set('sponsored', v)} label="Sponsored slot (a supplier pays for this space)" />
              {f.sponsored && (
                <label className="mt-2 block">
                  <span className={LABEL}>Sponsor name * (merchants see “Sponsored · Name”)</span>
                  <Input value={f.sponsor_name} onChange={(e) => set('sponsor_name', e.target.value)} placeholder="VFAN" maxLength={60} />
                </label>
              )}
            </div>

            <Toggle checked={f.is_active} onChange={(v) => set('is_active', v)} label={f.is_active ? 'Active' : 'Paused'} />
          </div>

          {/* live preview — first on small screens, a sticky column on large ones */}
          <aside className="order-first space-y-3 lg:sticky lg:top-0 lg:order-last lg:self-start" aria-label="Preview">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">What merchants see</span>
              <div className="flex gap-1.5">
                <Segmented label="Preview size" value={size} onChange={setSize}
                  options={[{ value: 'phone', label: 'Phone', icon: Smartphone }, { value: 'hero', label: 'Desktop', icon: Monitor }]} />
                <Segmented label="Preview language" value={lang} onChange={setLang}
                  options={[{ value: 'en', label: 'EN' }, { value: 'ar', label: 'ع' }]} />
              </div>
            </div>
            <div className="rounded-2xl bg-[#F9F7F3] p-3 ring-1 ring-black/5 dark:bg-[#F9F7F3]/95">
              <SlidePreview f={f} mode={mode} byUpper={catalog.byUpper} size={size} lang={lang} endsLabel={endsLabel} />
            </div>
            <div className="space-y-2 rounded-xl border border-dashed border-border p-3 text-[11.5px] leading-relaxed text-muted-foreground">
              <div className="flex flex-wrap items-center gap-1">
                <span className="font-semibold text-foreground">Shows in</span>
                {f.placement.length
                  ? f.placement.map((p) => <Badge key={p} tone="accent">{PLACEMENTS.find((x) => x.key === p)?.label || p}</Badge>)
                  : <Badge tone="rose">No placement yet</Badge>}
              </div>
              <p>
                A close approximation of the marketplace card — the real one scales its type to the screen and
                {size === 'phone' ? ' peeks the next slide in the phone slider.' : ' crossfades on the desktop hero stage.'}
              </p>
            </div>
          </aside>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy || uploading}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} {campaign ? 'Save changes' : 'Create campaign'}
          </Button>
        </div>
      </form>
    </div>
  )
}

/* ───────────────────────── section ───────────────────────── */

export function CampaignsSection() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data, isLoading, isError } = useQuery({ queryKey: ['shop-campaigns'], queryFn: () => apiGet<CampaignsResp>('/shop/campaigns') })
  const rulesQ = useQuery({ queryKey: ['shop-rules'], queryFn: () => apiGet<{ rules: RuleLite[] }>('/shop/rules') })
  // the staff catalog (same items the marketplace composes from): product picker + creative thumbs
  const catalogQ = useQuery({
    queryKey: ['shop-staff-catalog'],
    queryFn: () => apiGet<CatalogPayload>('/shop/catalog'),
    staleTime: 5 * 60_000,
    retry: 1,
  })
  const catalog = useMemo(() => indexCatalog(catalogQ.data?.items), [catalogQ.data])
  const catalogStatus: CatalogStatus = catalogQ.isError ? 'error' : catalogQ.data ? 'ready' : 'loading'
  const [editing, setEditing] = useState<Campaign | 'new' | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['shop-campaigns'] })

  const toggleActive = useMutation({
    mutationFn: (c: Campaign) => apiPatch(`/shop/campaigns/${c.id}`, { is_active: !c.is_active }),
    onSuccess: refresh,
    onError: (e) => toast(errorText(e, 'Could not update the campaign.'), 'error'),
  })

  async function remove(c: Campaign) {
    if (!window.confirm(`Delete "${c.title}"? This can't be undone.`)) return
    try {
      await apiDelete(`/shop/campaigns/${c.id}`)
      toast('Campaign deleted.', 'success')
      refresh()
    } catch (e) {
      toast(errorText(e, 'Delete failed.'), 'error')
    }
  }

  const rows = data?.campaigns || []
  const cols: Column<Campaign>[] = [
    { key: 'title', label: 'Campaign', render: (_, c) => (
        <div className="flex items-center gap-3">
          <CreativeThumb c={c} byUpper={catalog.byUpper} />
          <div className="min-w-0">
            <div className="font-semibold">{c.title}</div>
            <div className="text-[11px] text-muted-foreground">
              {c.line || c.cta_to}
              {!c.image_url && c.product_codes?.length ? ` · composed from ${c.product_codes.length}` : ''}
            </div>
          </div>
        </div>
      ) },
    { key: 'placement', label: 'Where', render: (_, c) => (
        <div className="flex flex-wrap gap-1">
          {c.placement.map((p) => <Badge key={p} tone="grey">{PLACEMENTS.find((x) => x.key === p)?.label || p}</Badge>)}
          {c.sponsored && <Badge tone="amber">Sponsored</Badge>}
        </div>
      ) },
    { key: 'audience', label: 'Audience', render: (_, c) => AUDIENCES.find((a) => a.key === c.audience)?.label || c.audience },
    { key: 'starts_at', label: 'Window', render: (_, c) => windowLabel(c.starts_at, c.ends_at) },
    { key: 'status', label: 'Status', render: (_, c) => <Badge tone={STATUS_TONE[c.status || 'live'] || 'grey'}>{c.status || 'live'}</Badge> },
    { key: 'is_active', label: 'Active', render: (_, c) => <Toggle checked={c.is_active} onChange={() => toggleActive.mutate(c)} label={c.is_active ? 'On' : 'Off'} /> },
    { key: 'id', label: '', align: 'right', render: (_, c) => (
        <div className="flex justify-end gap-1">
          <button type="button" onClick={() => setEditing(c)} aria-label="Edit" className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"><Pencil size={15} /></button>
          <button type="button" onClick={() => remove(c)} aria-label="Delete" className="grid h-9 w-9 place-items-center rounded-lg text-muted-foreground hover:bg-rose-50 hover:text-rose-700"><Trash2 size={15} /></button>
        </div>
      ) },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-[13px] text-muted-foreground">
          Promotions merchants see on the marketplace: the home promo slider, the desktop spotlight or one category’s shelf. Only real offers — tie a campaign to a discount rule and its countdown is the rule’s real end.
        </p>
        <Button onClick={() => setEditing('new')}><Plus size={15} /> New campaign</Button>
      </div>
      {isLoading ? (
        <Skeleton className="h-40 rounded-xl" />
      ) : isError ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">Could not load campaigns.</div>
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center">
          <Megaphone size={26} className="mx-auto text-muted-foreground" />
          <p className="mt-3 font-display text-[15px] font-bold">No campaigns yet</p>
          <p className="mx-auto mt-1 max-w-md text-[12.5px] text-muted-foreground">Start with last-chance stock or restock essentials — compose it from three products, no design work needed. The marketplace shows it within a minute.</p>
        </div>
      ) : (
        <DataTable rows={rows} cols={cols} />
      )}
      {editing && (
        <CampaignDialog
          campaign={editing === 'new' ? null : editing}
          rules={rulesQ.data?.rules || []}
          catalog={catalog}
          catalogStatus={catalogStatus}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
    </div>
  )
}
