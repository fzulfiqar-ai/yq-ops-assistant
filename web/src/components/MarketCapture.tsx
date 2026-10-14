import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Camera,
  CheckCircle2,
  CircleDot,
  Copy,
  Flame,
  ImagePlus,
  Languages,
  Loader2,
  MapPin,
  MessageSquare,
  Mic,
  MicOff,
  Minus,
  Package,
  PackageCheck,
  Plus,
  RefreshCw,
  ShieldAlert,
  Siren,
  Sparkles,
  Store,
  Swords,
  Tag,
  WifiOff,
  X,
} from 'lucide-react'
import { Sheet } from '@/components/ui/sheet'
import { ProductPicker, ProductRow } from '@/components/ProductPicker'
import { isReadOnly, useAuth } from '@/lib/auth'
import { useDictation, type DictationLang } from '@/lib/dictation'
import { CAPTURE_STRINGS, readCaptureLang, saveCaptureLang, type CaptureLang, type CaptureStrings, type CaptureTile } from '@/lib/marketCaptureStrings'
import { cn } from '@/lib/utils'
import {
  categoryNames,
  DEMAND_LEVELS,
  discardCapture,
  fetchLists,
  fetchProducts,
  issueTypesFor,
  matchBrand,
  MAX_PHOTOS,
  newUuid,
  notReady,
  observedAtIso,
  photoRoles,
  preparePhoto,
  productsQueryKey,
  rememberProduct,
  retryWaiting,
  submitCapture,
  useCaptureQueue,
  type MiCard,
  type MiIssueType,
  type MiKind,
  type MiPhotoRole,
  type MiProduct,
  type MiReportBasis,
  type ReadyPhoto,
} from '@/lib/marketIntel'
import { useCustomers, useSelectedCustomer } from '@/pages/sales/lib'

/**
 * "Spotted" — the one-tap market capture (release R7b, plan §18.1; v2 in release r8m, plan F3). A
 * floating camera button on Today, the Catalog, Customers and Market Intel (and, for the office, on
 * the Market Intel pages): the camera opens at once; the sheet behind it asks, all optional:
 *
 *   What is it?  six big tiles (Quality problem · Packaging · Customer asked for · Competitor ·
 *                New product / trend · Other)
 *   Quality / Packaging  → Which product? (the catalog with photos, "Not sure / not ours") → the
 *                admin's issue chips → how many, "Customer said" / "I saw it", a label / batch photo,
 *                and a safety chip that alerts the manager. These become quality CASES on the server.
 *   Customer asked for   → the product (or what they asked for), how much they want.
 *   Competitor / New / Other → v1's quick fields (name, brand, price, category, barcode).
 *
 * The note can be spoken (en-US / ar-SA), the shop pill he is ordering for comes along by itself, and
 * Save answers with the office's result card ("Manager alerted" when a safety report e-mailed it).
 * The sheet reads English or Arabic (its own small table, right to left in Arabic). Targets: a photo
 * only in 30 s, a complaint in 60 s.
 *
 * Photos are resized on the phone (1600 px, WebP q0.75) and carry no EXIF. The time of the shot goes
 * with the capture (client_observed_at), so a sighting that waits in the queue for signal keeps the
 * time the rep was in the shop. With no signal the capture waits under its client UUID and is
 * retried while the app is open; the new fields travel in the same queue.
 */

const SHOW_ON = ['/today', '/shop', '/customers', '/market-intel']
/** The office (AppShell, r8m): Spotted floats on the Market Intel pages only; a desk has no tab bar and no shop pill. */
const OFFICE_SHOW_ON = ['/market-intel']
/** When the catalog's category master cannot be read (an API without the lists route). */
const CATEGORIES = ['CABLE', 'CHARGER', 'CAR CHARGER', 'POWER BANK', 'EARPHONE', 'BLUETOOTH HEADSET', 'BLUETOOTH SPEAKER', 'CAR ACCESSORIES']

/** Which quick fields each kind asks for (all optional); the field titles are in the strings table. */
const FIELDS: Record<MiKind, { brand?: boolean; price?: boolean; category?: boolean; barcode?: boolean; demand?: boolean }> = {
  new_product: { brand: true, price: true, category: true, barcode: true },
  competitor_price: { brand: true, price: true, category: true },
  promotion: { brand: true, price: true },
  shop_asked: { demand: true, category: true },
  complaint: {},
  other: {},
}

const TILES: { value: CaptureTile; icon: ReactNode }[] = [
  { value: 'quality', icon: <ShieldAlert size={21} aria-hidden="true" /> },
  { value: 'packaging', icon: <Package size={21} aria-hidden="true" /> },
  { value: 'asked', icon: <MessageSquare size={21} aria-hidden="true" /> },
  { value: 'competitor', icon: <Swords size={21} aria-hidden="true" /> },
  { value: 'trend', icon: <Sparkles size={21} aria-hidden="true" /> },
  { value: 'other', icon: <CircleDot size={21} aria-hidden="true" /> },
]
/** The kind the sighting is stored under (a competitor's promotion is picked inside the tile). */
const TILE_KIND: Record<CaptureTile, MiKind> = {
  quality: 'complaint',
  packaging: 'complaint',
  asked: 'shop_asked',
  competitor: 'competitor_price',
  trend: 'new_product',
  other: 'other',
}
/** Quality and packaging reports carry an issue type from the admin's list, and become cases. */
const ISSUE_KIND: Partial<Record<CaptureTile, 'quality' | 'packaging'>> = { quality: 'quality', packaging: 'packaging' }

const RESULT: Record<MiCard['result'], { icon: ReactNode; tone: string }> = {
  already_in_yq: { icon: <PackageCheck size={22} aria-hidden="true" />, tone: 'bg-[#e8f7ee] text-[#137a48]' },
  seen_before: { icon: <Copy size={22} aria-hidden="true" />, tone: 'bg-[#EEE8F4] text-[#6D4091]' },
  new_find: { icon: <Sparkles size={22} aria-hidden="true" />, tone: 'bg-[#6D4091] text-white' },
  saved_for_review: { icon: <CheckCircle2 size={22} aria-hidden="true" />, tone: 'bg-[#f4f3f8] text-[#4B2C66]' },
}

/**
 * Where the floating button sits: above the phone tab bar, and clear of whatever fixed furniture
 * the page publishes (the catalog's StaffCatalog.useDockRoom) — above its order bar only while that
 * bar shows (--yq-dock-bottom), and left of its docked order slip from 1280 px (--yq-dock-right),
 * so it never covers Total / Place order. Every other page publishes nothing: 16 px from the edge
 * on a phone, 32 px from 1024 px (--yq-capture-edge, set by the button's classes).
 */
const CAPTURE_POSITION: CSSProperties = {
  bottom: 'calc(var(--yq-tabbar, 88px) + env(safe-area-inset-bottom, 0px) + var(--yq-dock-bottom, 0px) + 14px)',
  right: 'max(var(--yq-dock-right, 0px), var(--yq-capture-edge, 16px))',
}
/** What the button covers at the foot of a page (52 px + 14 px): the shell pads its main by it. */
const CAPTURE_ROOM = '66px'

const CHIP =
  'inline-flex h-11 items-center gap-1.5 rounded-full px-4 text-[13.5px] font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6D4091]/60 disabled:cursor-default'
const CHIP_OFF = 'bg-[#F3EDF8] text-[#4B2C66] hover:bg-[#EADFF3]'
const CHIP_ON = 'bg-[#6D4091] text-white shadow-sm'
const FIELD =
  'h-11 w-full rounded-xl border border-[#E2DCEA] bg-white px-3 text-[14px] text-[#1A1428] placeholder:text-[#9a93ad] focus:border-[#824FAB] focus:outline-none focus:ring-2 focus:ring-[#824FAB]/25'
/** Arabic labels: no letter-spacing (it breaks the joined letters on iPhones) and a size Arabic reads at */
const LABEL = 'mb-1.5 block text-[11.5px] font-semibold uppercase tracking-[0.06em] text-[#6b6480] rtl:text-[13px] rtl:tracking-normal'
const PANEL = 'space-y-4 rounded-2xl border border-[#EFE9F5] bg-[#FCFAFE] p-3'

interface Shop {
  name: string
  area: string
  phone: string
}

function titleCase(s: string): string {
  return s.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase())
}

function issueLabel(t: MiIssueType, lang: CaptureLang): string {
  return lang === 'ar' && t.label_ar ? t.label_ar : t.label_en
}

export function MarketCapture({ office = false }: { office?: boolean }) {
  const { me } = useAuth()
  const { pathname } = useLocation()
  const qc = useQueryClient()
  const selected = useSelectedCustomer()
  const queue = useCaptureQueue()
  const cameraRef = useRef<HTMLInputElement>(null)
  const galleryRef = useRef<HTMLInputElement>(null)
  const labelRef = useRef<HTMLInputElement>(null)

  const [open, setOpen] = useState(false)
  const [stage, setStage] = useState<'edit' | 'saving' | 'result' | 'queued'>('edit')
  const [photos, setPhotos] = useState<ReadyPhoto[]>([])
  const [preparing, setPreparing] = useState(0)
  const [note, setNote] = useState('')
  const [tile, setTile] = useState<CaptureTile | null>(null)
  const [compKind, setCompKind] = useState<'competitor_price' | 'promotion'>('competitor_price')
  const [title, setTitle] = useState('')
  const [brand, setBrand] = useState('')
  const [price, setPrice] = useState('')
  const [category, setCategory] = useState('')
  const [barcode, setBarcode] = useState('')
  const [demand, setDemand] = useState('')
  const [qty, setQty] = useState('')
  const [product, setProduct] = useState<MiProduct | null>(null)
  const [notSure, setNotSure] = useState(false)
  const [issueId, setIssueId] = useState<number | null>(null)
  const [safety, setSafety] = useState(false)
  const [affected, setAffected] = useState(1)
  const [basis, setBasis] = useState<MiReportBasis | null>(null)
  const [batch, setBatch] = useState('')
  const [packVersion, setPackVersion] = useState('')
  const [shop, setShop] = useState<Shop | null>(null)
  const [pickShop, setPickShop] = useState(false)
  const [card, setCard] = useState<MiCard | null>(null)
  const [savedSafety, setSavedSafety] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uuid, setUuid] = useState(newUuid)
  const [openedAt, setOpenedAt] = useState(0)
  const [lang, setLang] = useState<CaptureLang>(readCaptureLang)
  const [voiceLang, setVoiceLang] = useState<DictationLang>(() => (readCaptureLang() === 'ar' ? 'ar-SA' : 'en-US'))
  const dict = useDictation()
  const S = CAPTURE_STRINGS[lang]

  const customersQ = useCustomers(open && pickShop)
  // the admin's lists (issue chips, brands, categories): an API or a database without them leaves the v1 sheet
  const listsQ = useQuery({ queryKey: ['mi-lists'], queryFn: () => fetchLists(), enabled: open, staleTime: 10 * 60_000, retry: (n, e) => !notReady(e) && n < 1 })
  const lists = listsQ.data?.available ? listsQ.data : null
  const allowed = !!me && (me.role === 'admin' || (me.features || []).includes('Market Intel')) && !isReadOnly(me)
  const showOn = office ? OFFICE_SHOW_ON : SHOW_ON
  const visible = allowed && showOn.some((p) => pathname === p || pathname.startsWith(`${p}/`))
  const waiting = queue.filter((q) => q.state !== 'failed').length
  const failed = queue.filter((q) => q.state === 'failed')

  // the photo previews are object URLs: let them go when the component does
  const photosRef = useRef<ReadyPhoto[]>([])
  useEffect(() => {
    photosRef.current = photos
  }, [photos])
  useEffect(() => () => photosRef.current.forEach((p) => URL.revokeObjectURL(p.url)), [])

  // while the button floats, the page's last lines can still scroll out from under it (SalesmanShell)
  useEffect(() => {
    if (!visible) return
    const root = document.documentElement
    root.style.setProperty('--yq-capture-room', CAPTURE_ROOM)
    return () => {
      root.style.removeProperty('--yq-capture-room')
    }
  }, [visible])

  if (!visible && !open) return null

  function fromSelected(): Shop | null {
    if (!selected) return null
    return { name: String(selected.shop || selected.name || ''), area: String(selected.area || ''), phone: String(selected.phone || '') }
  }

  function reset(keepPhotos = false) {
    if (!keepPhotos) photos.forEach((p) => URL.revokeObjectURL(p.url))
    setPhotos(keepPhotos ? photos : [])
    setNote('')
    setTile(null)
    setCompKind('competitor_price')
    setTitle('')
    setBrand('')
    setPrice('')
    setCategory('')
    setBarcode('')
    setDemand('')
    setQty('')
    setProduct(null)
    setNotSure(false)
    setIssueId(null)
    setSafety(false)
    setAffected(1)
    setBasis(null)
    setBatch('')
    setPackVersion('')
    setCard(null)
    setSavedSafety(false)
    setError(null)
    setPickShop(false)
    setStage('edit')
    setUuid(newUuid())
    setOpenedAt(Date.now())
    setShop(fromSelected())
    if (dict.listening) dict.stop()
  }

  function start() {
    if (stage === 'result' || stage === 'queued') reset()
    else if (!open) {
      setShop(fromSelected())
      if (!photos.length && !note.trim()) setOpenedAt(Date.now())
    }
    setOpen(true)
    // "Which product?" opens with the list already there: warm it while the camera is up
    const s = fromSelected()
    const hint = s ? { phone: s.phone } : null
    void qc.prefetchQuery({ queryKey: productsQueryKey(hint, ''), queryFn: () => fetchProducts('', hint), staleTime: 5 * 60_000 })
    // the camera opens in the same tap (a user gesture), the sheet waits behind it
    if (photos.length < MAX_PHOTOS) cameraRef.current?.click()
  }

  function close() {
    if (stage === 'saving') return
    if (dict.listening) dict.stop()
    setOpen(false)
  }

  async function addFiles(list: FileList | null, role: MiPhotoRole = 'product') {
    if (!list?.length) return
    const room = MAX_PHOTOS - photos.length
    const files = Array.from(list).slice(0, Math.max(0, room))
    if (list.length > room) setError(S.upTo(MAX_PHOTOS))
    setPreparing((n) => n + files.length)
    for (const [i, f] of files.entries()) {
      try {
        const ready = await preparePhoto(f, photos.length + i)
        setPhotos((cur) => (cur.length < MAX_PHOTOS ? [...cur, { ...ready, role }] : cur))
      } catch (e) {
        setError(e instanceof Error ? e.message : S.photoUnreadable)
      } finally {
        setPreparing((n) => n - 1)
      }
    }
  }

  function removePhoto(i: number) {
    setPhotos((cur) => {
      const gone = cur[i]
      if (gone) URL.revokeObjectURL(gone.url)
      return cur.filter((_, j) => j !== i)
    })
  }

  function pickTile(t: CaptureTile) {
    // the product stays when he switches between Quality and Packaging; the issue chip does not
    setIssueId(null)
    setTile(tile === t ? null : t)
  }

  function pickProduct(p: MiProduct) {
    setProduct(p)
    setNotSure(false)
    rememberProduct(p)
  }

  function toggleLang() {
    const next: CaptureLang = lang === 'en' ? 'ar' : 'en'
    saveCaptureLang(next)
    setLang(next)
    setVoiceLang(next === 'ar' ? 'ar-SA' : 'en-US')
  }

  function toggleVoice() {
    if (dict.listening) {
      dict.stop()
      return
    }
    const base = note.trim()
    dict.start(voiceLang, (text) => setNote((base ? `${base} ${text}` : text).slice(0, 1000)))
  }

  const issueKind = tile ? ISSUE_KIND[tile] : undefined
  const quality = Boolean(issueKind)
  const issueTypes = issueKind ? issueTypesFor(lists, issueKind) : []
  const chosen = issueTypes.find((t) => t.id === issueId) || null
  const typeFields = chosen?.fields || []
  const safetyOn = quality && (safety || Boolean(chosen?.safety_default))
  const kind: MiKind | null = tile ? (tile === 'competitor' ? compKind : TILE_KIND[tile]) : null
  const wantsProduct = quality || tile === 'asked'
  const quick = kind && !quality ? FIELDS[kind] : null
  const listCats = categoryNames(lists)
  const categories = listCats.length ? listCats : CATEGORIES
  const brandNames = (lists?.brands || []).filter((b) => b.active !== false && !b.merged_into).map((b) => b.name)
  const hasLabelPhoto = photos.some((p) => p.role === 'label_batch')
  // the products route knows the shop by its phone only (GET /market-intel/products?shop_phone=)
  const shopHint = shop ? { phone: shop.phone } : null

  const canSave =
    stage === 'edit' &&
    preparing === 0 &&
    (photos.length > 0 || note.trim() !== '' || title.trim() !== '' || barcode.trim() !== '' || product != null || issueId != null)

  async function save() {
    if (!canSave) return
    if (dict.listening) dict.stop()
    setError(null)
    setStage('saving')
    const k: MiKind = kind || 'other'
    const f = quick
    const picked = wantsProduct ? product : null
    const brandText = picked ? picked.brand || '' : f?.brand ? brand.trim() : ''
    const linked = matchBrand(lists, brandText)
    const fields: Record<string, string> = {
      note: note.trim(),
      kind: k,
      title: picked ? picked.name : title.trim(),
      yq_item_code: picked ? picked.code : '',
      brand: brandText,
      brand_id: linked ? String(linked.id) : '',
      supplier_id: linked?.supplier_id ? String(linked.supplier_id) : '',
      price_bhd: f?.price ? price.trim() : '',
      category: f?.category && !picked ? category : '',
      barcode: f?.barcode ? barcode.replace(/\D/g, '') : '',
      demand_level: f?.demand ? demand : '',
      demand_qty: f?.demand ? qty.trim() : '',
      issue_type_id: quality && chosen ? String(chosen.id) : '',
      report_basis: quality && basis ? basis : '',
      qty_affected: quality && typeFields.includes('qty') ? String(Math.max(1, affected)) : '',
      batch_ref: quality && typeFields.includes('batch_ref') ? batch.trim() : '',
      packaging_version: quality && typeFields.includes('packaging_version') ? packVersion.trim() : '',
      safety_flag: safetyOn ? 'true' : '',
      // when the shot was taken, fixed now: a retry an hour later still says when he was in the shop
      client_observed_at: observedAtIso(photos, openedAt),
      photo_roles: photos.length ? photoRoles(photos) : '',
      shop_name: shop?.name || '',
      shop_phone: shop?.phone || '',
      area: shop?.area || '',
    }
    const draft = { client_uuid: uuid, fields, photos: photos.map((p) => ({ ...p })) }
    try {
      const got = await submitCapture(draft)
      setSavedSafety(safetyOn)
      if (got) {
        setCard(got)
        setStage('result')
      } else {
        setStage('queued')
      }
      setPhotos([]) // the queue owns the photos now (it lets their URLs go once sent)
    } catch (e) {
      discardCapture(uuid, true) // the sheet keeps the draft and its photos to fix and save again
      setError(e instanceof Error ? e.message : S.saveFailed)
      setStage('edit')
    }
  }

  const busy = stage === 'saving'

  const footer =
    stage === 'result' || stage === 'queued' ? (
      <div className="flex gap-2">
        <button type="button" onClick={() => { reset(); cameraRef.current?.click() }} className="flex h-12 flex-1 items-center justify-center gap-2 rounded-xl border border-[#E2DCEA] bg-white text-[14px] font-semibold text-[#4B2C66] hover:bg-[#F7F3FA]">
          <Camera size={17} aria-hidden="true" /> {S.spotAnother}
        </button>
        <button type="button" onClick={() => { setOpen(false); reset() }} className="flex h-12 flex-1 items-center justify-center rounded-xl bg-[#6D4091] text-[14px] font-semibold text-white hover:bg-[#5A3478]">
          {S.done}
        </button>
      </div>
    ) : (
      <button type="button" onClick={save} disabled={!canSave} className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[15px] font-semibold text-white transition hover:bg-[#5A3478] disabled:opacity-45">
        {busy ? <Loader2 size={18} className="animate-spin" aria-hidden="true" /> : <CheckCircle2 size={18} aria-hidden="true" />}
        {busy ? S.saving : preparing ? S.preparing : S.save}
      </button>
    )

  return (
    <>
      <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => { void addFiles(e.target.files); e.target.value = '' }} />
      <input ref={galleryRef} type="file" accept="image/*" multiple className="hidden" onChange={(e) => { void addFiles(e.target.files); e.target.value = '' }} />
      <input ref={labelRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => { void addFiles(e.target.files, 'label_batch'); e.target.value = '' }} />

      {visible && (
        <button
          type="button"
          onClick={start}
          aria-label={waiting ? S.buttonWaiting(waiting) : S.buttonAria}
          className={`fixed z-30 flex h-[52px] items-center gap-2 rounded-full bg-gradient-to-br from-[#824FAB] to-[#6D4091] pl-4 pr-5 text-[14px] font-bold text-white shadow-[0_12px_28px_-10px_rgba(109,64,145,.75)] ring-1 ring-white/20 transition active:scale-[.97] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[#824FAB]/40 [--yq-capture-edge:16px] lg:[--yq-capture-edge:32px]${office ? ' [--yq-tabbar:0px]' : ''}`}
          style={CAPTURE_POSITION}
        >
          <Camera size={20} strokeWidth={2.2} aria-hidden="true" />
          {S.spotted}
          {waiting > 0 && (
            <span className="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-[#F4B740] px-1 text-[11px] font-bold text-[#1A1428] ring-2 ring-white">{waiting}</span>
          )}
        </button>
      )}

      <Sheet
        open={open}
        onClose={close}
        dir={S.dir}
        lang={lang}
        title={stage === 'result' ? S.titleResult : stage === 'queued' ? S.titleQueued : S.titleEdit}
        subtitle={stage === 'edit' || stage === 'saving' ? S.subtitle : undefined}
        footer={footer}
      >
        <div className="space-y-5 px-4 py-4 sm:px-5">
          <div className="-mb-2 flex justify-end">
            <button
              type="button"
              onClick={toggleLang}
              aria-label={S.switchAria}
              className="inline-flex h-10 items-center gap-1.5 rounded-full border border-[#E2DCEA] bg-white px-3.5 text-[13px] font-semibold text-[#4B2C66] hover:bg-[#F7F3FA]"
            >
              <Languages size={14} aria-hidden="true" />
              <span lang={lang === 'en' ? 'ar' : 'en'}>{S.switchTo}</span>
            </button>
          </div>

          {stage === 'result' && card && <ResultCard card={card} S={S} lang={lang} safety={savedSafety} />}

          {stage === 'queued' && (
            <div className="space-y-2">
              <div className="flex gap-3 rounded-2xl bg-[#fdf3e3] p-4 text-[#7a4d0a]">
                <WifiOff size={22} className="mt-0.5 shrink-0" aria-hidden="true" />
                <p className="text-[13.5px] leading-snug">
                  <span className="font-bold">{S.queuedTitle}</span> {S.queuedBody}
                </p>
              </div>
              {savedSafety && <p className="rounded-xl bg-[#fdecef] px-3 py-2.5 text-[13px] font-semibold text-[#9f1239]">{S.safetyQueued}</p>}
            </div>
          )}

          {(stage === 'edit' || stage === 'saving') && (
            <>
              {/* photos (pt-2: the scroll box clips, and the remove badges sit above each photo's corner) */}
              <div className="flex gap-2 overflow-x-auto pb-1 pt-2">
                {photos.map((p, i) => (
                  <div key={p.url} className="relative shrink-0">
                    <img src={p.url} alt={S.photoAlt(i + 1)} className="h-24 w-24 rounded-xl border border-[#E2DCEA] object-cover" />
                    {p.role === 'label_batch' && (
                      <span className="absolute bottom-1 start-1 rounded-full bg-[#1A1428]/75 px-1.5 py-0.5 text-[10px] font-bold text-white">{S.labelBadge}</span>
                    )}
                    <button type="button" onClick={() => removePhoto(i)} aria-label={S.removePhoto(i + 1)} className="absolute -end-1.5 -top-1.5 grid h-7 w-7 place-items-center rounded-full bg-[#1A1428] text-white shadow ring-2 ring-white after:absolute after:-inset-2">
                      <X size={13} aria-hidden="true" />
                    </button>
                  </div>
                ))}
                {preparing > 0 && (
                  <div className="grid h-24 w-24 shrink-0 place-items-center rounded-xl border border-dashed border-[#C9B7DA] text-[#824FAB]">
                    <Loader2 size={20} className="animate-spin" aria-label={S.preparingPhoto} />
                  </div>
                )}
                {photos.length + preparing < MAX_PHOTOS && (
                  <>
                    <button type="button" onClick={() => cameraRef.current?.click()} className="grid h-24 w-24 shrink-0 place-items-center rounded-xl border-2 border-dashed border-[#C9B7DA] bg-[#FAF7FC] text-[#6D4091] hover:bg-[#F3EDF8]">
                      <span className="flex flex-col items-center gap-1 text-[12px] font-semibold">
                        <Camera size={22} aria-hidden="true" /> {photos.length ? S.another : S.camera}
                      </span>
                    </button>
                    <button type="button" onClick={() => galleryRef.current?.click()} className="grid h-24 w-20 shrink-0 place-items-center rounded-xl border border-[#E2DCEA] bg-white text-[#6b6480] hover:bg-[#F7F3FA]">
                      <span className="flex flex-col items-center gap-1 text-[11.5px] font-semibold">
                        <ImagePlus size={19} aria-hidden="true" /> {S.gallery}
                      </span>
                    </button>
                    {quality && (
                      <button type="button" onClick={() => labelRef.current?.click()} className="grid h-24 w-20 shrink-0 place-items-center rounded-xl border border-[#E2DCEA] bg-white text-[#6b6480] hover:bg-[#F7F3FA]">
                        <span className="flex flex-col items-center gap-1 px-1 text-center text-[11.5px] font-semibold leading-tight">
                          <Tag size={19} aria-hidden="true" /> {S.labelPhoto}
                        </span>
                      </button>
                    )}
                  </>
                )}
              </div>

              {/* what is it? */}
              <div>
                <span className={LABEL} id="mi-what">{S.whatIsIt}</span>
                <div role="radiogroup" aria-labelledby="mi-what" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {TILES.map((t) => {
                    const on = tile === t.value
                    return (
                      <button
                        key={t.value}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        onClick={() => pickTile(t.value)}
                        className={cn(
                          'flex min-h-[64px] items-center gap-2.5 rounded-2xl border px-3 py-2 text-start text-[13.5px] font-semibold leading-tight transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6D4091]/60',
                          on ? 'border-[#6D4091] bg-[#6D4091] text-white shadow-sm' : 'border-[#E2DCEA] bg-white text-[#2A1A3D] hover:bg-[#F7F3FA]',
                        )}
                      >
                        <span className={cn('grid h-10 w-10 shrink-0 place-items-center rounded-xl', on ? 'bg-white/20 text-white' : 'bg-[#F3EDF8] text-[#6D4091]')}>{t.icon}</span>
                        <span className="min-w-0">{S.tiles[t.value]}</span>
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* quality / packaging: product, issue, how many, who saw it, label photo, safety */}
              {quality && (
                <div className={PANEL}>
                  <WhichProduct S={S} product={product} notSure={notSure} name={title} onName={setTitle} onPick={pickProduct} onChange={() => { setProduct(null); setNotSure(false) }} onNotSure={() => setNotSure(true)} shop={shopHint} namePlaceholder={S.notSureName} />

                  <div>
                    <span className={LABEL} id="mi-issue">{S.whatsWrong}</span>
                    {issueTypes.length > 0 ? (
                      <div role="radiogroup" aria-labelledby="mi-issue" className="flex flex-wrap gap-2">
                        {issueTypes.map((t) => {
                          const on = issueId === t.id
                          return (
                            <button
                              key={t.id}
                              type="button"
                              role="radio"
                              aria-checked={on}
                              onClick={() => setIssueId(on ? null : t.id)}
                              className={cn(CHIP, t.safety_default ? (on ? 'bg-[#9f1239] text-white' : 'bg-[#fdecef] text-[#9f1239] hover:bg-[#fbdbe1]') : on ? CHIP_ON : CHIP_OFF)}
                            >
                              {t.safety_default && <Flame size={14} aria-hidden="true" />}
                              {issueLabel(t, lang)}
                            </button>
                          )
                        })}
                      </div>
                    ) : listsQ.isLoading ? (
                      <p className="text-[12.5px] text-[#6b6480]">{S.loading}</p>
                    ) : (
                      <p className="text-[12.5px] text-[#6b6480]">{S.noIssueTypes}</p>
                    )}
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={safetyOn}
                      disabled={Boolean(chosen?.safety_default)}
                      onClick={() => setSafety((v) => !v)}
                      className={cn(CHIP, 'mt-2', safetyOn ? 'bg-[#9f1239] text-white' : 'border border-[#f3c4cf] bg-white text-[#9f1239] hover:bg-[#fdecef]')}
                    >
                      <Siren size={15} aria-hidden="true" /> {S.safetyChip}
                    </button>
                    {safetyOn && <p className="mt-1.5 text-[12px] font-semibold text-[#9f1239]">{S.safetyHint}</p>}
                  </div>

                  {typeFields.includes('qty') && (
                    <div>
                      <span className={LABEL} id="mi-qty">{S.howMany}</span>
                      <QtyStepper value={affected} onChange={setAffected} labelledBy="mi-qty" less={S.qtyLess} more={S.qtyMore} />
                    </div>
                  )}

                  <div>
                    <span className={LABEL} id="mi-basis">{S.basis}</span>
                    <div role="radiogroup" aria-labelledby="mi-basis" className="grid grid-cols-2 gap-2">
                      {(['customer_said', 'rep_saw'] as const).map((b) => {
                        const on = basis === b
                        return (
                          <button key={b} type="button" role="radio" aria-checked={on} onClick={() => setBasis(on ? null : b)} className={cn('h-11 rounded-xl text-[13.5px] font-semibold transition-colors', on ? CHIP_ON : CHIP_OFF)}>
                            {b === 'customer_said' ? S.customerSaid : S.iSaw}
                          </button>
                        )
                      })}
                    </div>
                  </div>

                  {(typeFields.includes('batch_ref') || typeFields.includes('packaging_version')) && (
                    <div className="grid grid-cols-2 gap-3">
                      {typeFields.includes('batch_ref') && (
                        <div className={typeFields.includes('packaging_version') ? '' : 'col-span-2'}>
                          <label htmlFor="mi-batch" className={LABEL}>{S.batch}</label>
                          <input id="mi-batch" dir="ltr" value={batch} onChange={(e) => setBatch(e.target.value)} maxLength={60} className={FIELD} placeholder={S.optional} autoComplete="off" />
                        </div>
                      )}
                      {typeFields.includes('packaging_version') && (
                        <div className={typeFields.includes('batch_ref') ? '' : 'col-span-2'}>
                          <label htmlFor="mi-pack" className={LABEL}>{S.packVersion}</label>
                          <input id="mi-pack" dir="ltr" value={packVersion} onChange={(e) => setPackVersion(e.target.value)} maxLength={60} className={FIELD} placeholder={S.optional} autoComplete="off" />
                        </div>
                      )}
                    </div>
                  )}

                  {typeFields.includes('label_photo') && !hasLabelPhoto && photos.length + preparing < MAX_PHOTOS && (
                    <button type="button" onClick={() => labelRef.current?.click()} className="flex min-h-11 w-full items-center gap-2 rounded-xl border border-dashed border-[#C9B7DA] bg-white px-3 py-2 text-start text-[13px] font-medium text-[#4B2C66] hover:bg-[#FAF7FC]">
                      <Tag size={16} className="shrink-0" aria-hidden="true" /> {S.labelPhotoAsk}
                    </button>
                  )}
                </div>
              )}

              {/* customer asked for: the product (or what they asked for) and how much */}
              {tile === 'asked' && (
                <div className={PANEL}>
                  <WhichProduct S={S} product={product} notSure={notSure} name={title} onName={setTitle} onPick={pickProduct} onChange={() => { setProduct(null); setNotSure(false) }} onNotSure={() => setNotSure(true)} shop={shopHint} namePlaceholder={S.fieldTitle.shop_asked} />
                  <div>
                    <span className={LABEL} id="mi-demand">{S.howMuch}</span>
                    <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-labelledby="mi-demand">
                      {DEMAND_LEVELS.map((d) => {
                        const on = demand === d.value
                        return (
                          <button key={d.value} type="button" role="radio" aria-checked={on} onClick={() => setDemand(on ? '' : d.value)} className={cn(CHIP, on ? CHIP_ON : CHIP_OFF)}>
                            {S.demand[d.value]}
                          </button>
                        )
                      })}
                      <input aria-label={S.qty} inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ''))} maxLength={6} className={cn(FIELD, 'w-24 tabular-nums')} placeholder={S.qty} />
                    </div>
                  </div>
                  {!product && (
                    <div>
                      <label htmlFor="mi-cat" className={LABEL}>{S.category}</label>
                      <select id="mi-cat" value={category} onChange={(e) => setCategory(e.target.value)} className={FIELD}>
                        <option value="">{S.anyCategory}</option>
                        {categories.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
                      </select>
                    </div>
                  )}
                </div>
              )}

              {/* competitor: their price or their promotion */}
              {tile === 'competitor' && (
                <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label={S.tiles.competitor}>
                  {(['competitor_price', 'promotion'] as const).map((k) => {
                    const on = compKind === k
                    return (
                      <button key={k} type="button" role="radio" aria-checked={on} onClick={() => setCompKind(k)} className={cn('h-11 rounded-xl text-[13.5px] font-semibold transition-colors', on ? CHIP_ON : CHIP_OFF)}>
                        {k === 'competitor_price' ? S.compPrice : S.compPromo}
                      </button>
                    )
                  })}
                </div>
              )}

              {/* v1's quick fields: competitor, new product / trend, other */}
              {quick && !wantsProduct && kind && (
                <div className="space-y-3 rounded-2xl border border-[#EFE9F5] bg-[#FCFAFE] p-3">
                  <div>
                    <label htmlFor="mi-title" className={LABEL}>{S.fieldTitle[kind]}</label>
                    <input id="mi-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} className={FIELD} placeholder={S.optional} autoComplete="off" />
                  </div>
                  {quick.brand && (
                    <div>
                      <label htmlFor="mi-brand" className={LABEL}>{S.brand}</label>
                      <input id="mi-brand" list="mi-brands" value={brand} onChange={(e) => setBrand(e.target.value)} maxLength={60} className={FIELD} placeholder={S.pickOrType} autoComplete="off" />
                      <datalist id="mi-brands">
                        {brandNames.map((b) => (
                          <option key={b} value={b} />
                        ))}
                      </datalist>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    {quick.price && (
                      <div>
                        <label htmlFor="mi-price" className={LABEL}>{S.price}</label>
                        {/* the amount box is left to right in both languages: the currency and the digits share one side
                            (in Arabic it reads "0.000 د.ب"); rtl right-alignment put the digits under the currency */}
                        <div className="relative" dir="ltr">
                          <span className="pointer-events-none absolute start-3 top-1/2 -translate-y-1/2 text-[12px] font-semibold text-[#6b6480]">{S.currency}</span>
                          <input id="mi-price" dir="ltr" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ''))} maxLength={10} className={cn(FIELD, 'ps-12 tabular-nums')} placeholder="0.000" />
                        </div>
                      </div>
                    )}
                    {quick.category && (
                      <div className={quick.price ? '' : 'col-span-2'}>
                        <label htmlFor="mi-cat2" className={LABEL}>{S.category}</label>
                        <select id="mi-cat2" value={category} onChange={(e) => setCategory(e.target.value)} className={FIELD}>
                          <option value="">{S.anyCategory}</option>
                          {categories.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
                        </select>
                      </div>
                    )}
                  </div>
                  {quick.barcode && (
                    <div>
                      <label htmlFor="mi-barcode" className={LABEL}>{S.barcode}</label>
                      <input id="mi-barcode" dir="ltr" inputMode="numeric" value={barcode} onChange={(e) => setBarcode(e.target.value.replace(/[^0-9 ]/g, ''))} maxLength={24} className={cn(FIELD, 'tabular-nums rtl:text-right')} placeholder={S.barcodeHint} />
                    </div>
                  )}
                </div>
              )}

              {/* note: typed or spoken */}
              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <label htmlFor="mi-note" className={cn(LABEL, 'mb-0')}>{S.note}</label>
                  {dict.supported && (
                    <button
                      type="button"
                      onClick={() => setVoiceLang((v) => (v === 'en-US' ? 'ar-SA' : 'en-US'))}
                      aria-label={`${S.voiceLang}: ${voiceLang === 'en-US' ? 'English' : 'العربية'}`}
                      title={S.voiceLang}
                      className="inline-flex h-10 min-w-12 items-center justify-center gap-1.5 rounded-full border border-[#E2DCEA] bg-white px-3 text-[12px] font-bold text-[#4B2C66] hover:bg-[#F7F3FA]"
                    >
                      <Mic size={13} className="text-[#824FAB]" aria-hidden="true" />
                      {voiceLang === 'en-US' ? 'EN' : 'AR'}
                    </button>
                  )}
                </div>
                <div className="relative">
                  <textarea
                    id="mi-note"
                    rows={2}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    maxLength={1000}
                    placeholder={S.notePlaceholder}
                    className={cn(FIELD, 'h-auto min-h-[76px] resize-none py-2.5 leading-snug', dict.supported && 'pe-14')}
                  />
                  {dict.supported && (
                    <button
                      type="button"
                      onClick={toggleVoice}
                      aria-pressed={dict.listening}
                      aria-label={dict.listening ? S.stopSpeaking : S.speak}
                      className={cn('absolute bottom-2 end-2 grid h-11 w-11 place-items-center rounded-xl transition-colors', dict.listening ? 'bg-[#e11d48] text-white' : 'bg-[#F3EDF8] text-[#6D4091] hover:bg-[#EADFF3]')}
                    >
                      {dict.listening ? <MicOff size={19} aria-hidden="true" /> : <Mic size={19} aria-hidden="true" />}
                    </button>
                  )}
                </div>
                {dict.listening && <p className="mt-1 text-[12px] font-semibold text-[#e11d48]" aria-live="polite">{S.listening}</p>}
              </div>

              {/* the shop */}
              <div>
                <span className={LABEL}>{S.shop}</span>
                {shop && !pickShop ? (
                  <div className="flex items-center gap-2 rounded-xl border border-[#E2DCEA] bg-white px-3 py-2">
                    <Store size={16} className="shrink-0 text-[#824FAB]" aria-hidden="true" />
                    {/* bdi: an English shop name inside the Arabic sheet keeps its place (no "Shop Alpha· Manama") */}
                    <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-[#1A1428]">
                      <bdi>{shop.name || S.thisShop}</bdi>
                      {shop.area && (
                        <span className="ms-1.5 font-normal text-[#6b6480]">
                          · <bdi>{shop.area}</bdi>
                        </span>
                      )}
                    </span>
                    <button type="button" onClick={() => setPickShop(true)} className="h-11 rounded-lg px-2 text-[12.5px] font-semibold text-[#6D4091] hover:bg-[#F3EDF8]">{S.changeShop}</button>
                    <button type="button" onClick={() => setShop(null)} aria-label={S.notAtShop} className="grid h-11 w-11 place-items-center rounded-lg text-[#6b6480] hover:bg-[#F3EDF8]">
                      <X size={15} aria-hidden="true" />
                    </button>
                  </div>
                ) : pickShop ? (
                  <select
                    aria-label={S.pickShopAria}
                    className={FIELD}
                    value=""
                    onChange={(e) => {
                      const c = (customersQ.data || [])[Number(e.target.value)]
                      if (c) setShop({ name: String(c.shop || c.name || ''), area: String(c.area || ''), phone: String(c.phone || '') })
                      setPickShop(false)
                    }}
                  >
                    <option value="">{customersQ.isLoading ? S.loadingShops : S.pickShop}</option>
                    {(customersQ.data || []).map((c, i) => (
                      <option key={`${c.phone}-${i}`} value={i}>
                        {c.shop || c.name}
                        {c.area ? ` · ${c.area}` : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <button type="button" onClick={() => setPickShop(true)} className="flex h-11 w-full items-center gap-2 rounded-xl border border-dashed border-[#D9CCE6] px-3 text-[13.5px] font-medium text-[#6b6480] hover:bg-[#FAF7FC]">
                    <MapPin size={16} aria-hidden="true" /> {S.addShop}
                  </button>
                )}
              </div>

              {error && (
                <p role="alert" className="rounded-xl bg-[#fdecef] px-3 py-2.5 text-[13px] font-medium text-[#9f1239]">{error}</p>
              )}
            </>
          )}

          {(queue.length > 0 || failed.length > 0) && (
            <PendingList S={S} waiting={waiting} failed={failed.map((q) => ({ id: q.client_uuid, error: q.error || '' }))} />
          )}

          {(stage === 'result' || stage === 'queued') && (
            <Link to="/market-intel" onClick={() => { setOpen(false); reset() }} className="flex min-h-11 items-center justify-center gap-1.5 text-[13px] font-semibold text-[#6D4091] hover:underline">
              <CircleDot size={14} aria-hidden="true" /> {S.seeMine}
            </Link>
          )}
        </div>
      </Sheet>
    </>
  )
}

/**
 * How many units are affected: 1 to 9999 in 44 px steps (the shop's Stepper reads "remove" at its
 * minimum, which means nothing here), the number readable in both directions.
 */
function QtyStepper({ value, onChange, labelledBy, less, more }: { value: number; onChange: (n: number) => void; labelledBy: string; less: string; more: string }) {
  const btn = 'grid h-11 w-11 place-items-center rounded-xl text-[#6D4091] transition hover:bg-[#EEE8F4] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6D4091]/60 disabled:opacity-35'
  return (
    <div role="group" aria-labelledby={labelledBy} className="inline-flex h-12 items-center gap-1 rounded-xl border border-[#E2DCEA] bg-white p-0.5">
      <button type="button" disabled={value <= 1} onClick={() => onChange(Math.max(1, value - 1))} aria-label={less} className={btn}>
        <Minus size={17} aria-hidden="true" />
      </button>
      <span dir="ltr" aria-live="polite" className="min-w-[2.75rem] text-center font-display text-[16px] font-bold tabular-nums text-[#1A1428]">
        {value}
      </span>
      <button type="button" disabled={value >= 9999} onClick={() => onChange(Math.min(9999, value + 1))} aria-label={more} className={btn}>
        <Plus size={17} aria-hidden="true" />
      </button>
    </div>
  )
}

/** "Which product?": the chosen product, the typed name ("Not sure / not ours"), or the picker. */
function WhichProduct({
  S,
  product,
  notSure,
  name,
  onName,
  onPick,
  onChange,
  onNotSure,
  shop,
  namePlaceholder,
}: {
  S: CaptureStrings
  product: MiProduct | null
  notSure: boolean
  name: string
  onName: (v: string) => void
  onPick: (p: MiProduct) => void
  onChange: () => void
  onNotSure: () => void
  shop: { phone?: string | null } | null
  namePlaceholder: string
}) {
  return (
    <div>
      <span className={LABEL}>{S.whichProduct}</span>
      {product ? (
        <ProductRow
          p={product}
          action={
            <button type="button" onClick={onChange} className="h-11 shrink-0 rounded-lg px-2.5 text-[12.5px] font-semibold text-[#6D4091] hover:bg-[#F3EDF8]">
              {S.change}
            </button>
          }
        />
      ) : notSure ? (
        <div className="space-y-1.5">
          <input value={name} onChange={(e) => onName(e.target.value)} maxLength={160} className={FIELD} placeholder={namePlaceholder} aria-label={namePlaceholder} autoComplete="off" />
          <button type="button" onClick={onChange} className="h-9 rounded-lg px-1 text-[12.5px] font-semibold text-[#6D4091] hover:underline">
            {S.pickFromList}
          </button>
        </div>
      ) : (
        <ProductPicker
          shop={shop}
          onPick={onPick}
          onNotSure={onNotSure}
          words={{
            search: S.searchProducts,
            atShop: S.atShop,
            recent: S.recent,
            products: S.products,
            results: S.results,
            none: S.noProducts,
            failed: S.productsFailed,
            loading: S.loading,
            notSure: S.notSure,
          }}
        />
      )}
    </div>
  )
}

function ResultCard({ card, S, lang, safety }: { card: MiCard; S: CaptureStrings; lang: CaptureLang; safety: boolean }) {
  const meta = RESULT[card.result] || RESULT.saved_for_review
  const isCase = Boolean(card.case?.id || card.case_id)
  // the API says when his report joined an open case of the same product and issue (case.joined)
  const joined = Boolean(card.case?.joined)
  return (
    <div className="space-y-3" aria-live="polite">
      {card.safety_alerted ? (
        <div className="flex items-center gap-3 rounded-2xl bg-[#9f1239] p-4 text-white">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-white/20">
            <Siren size={22} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <div className="font-display text-[17px] font-bold leading-tight">{S.alerted}</div>
            <div className="mt-0.5 text-[12.5px] opacity-90">{S.alertedBody}</div>
          </div>
        </div>
      ) : safety ? (
        <p className="rounded-xl bg-[#fdecef] px-3 py-2.5 text-[13px] font-semibold text-[#9f1239]">{S.safetyNoted}</p>
      ) : null}
      {isCase ? (
        <div className="flex items-center gap-3 rounded-2xl bg-[#6D4091] p-4 text-white">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-white/20">
            <CheckCircle2 size={22} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <div className="font-display text-[17px] font-bold leading-tight">{joined ? S.joinedTitle : S.reportReceived}</div>
            <div className="mt-0.5 text-[12.5px] opacity-90">{joined ? S.joinedBody(Number(card.case?.shops || 0)) : S.reportReceivedBody}</div>
          </div>
        </div>
      ) : (
        <div className={cn('flex items-center gap-3 rounded-2xl p-4', meta.tone)}>
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-white/20">{meta.icon}</span>
          <div className="min-w-0">
            <div className="font-display text-[17px] font-bold leading-tight">{S.result[card.result] || S.result.saved_for_review}</div>
            {card.item && card.result !== 'saved_for_review' && (
              <div className="mt-0.5 text-[12.5px] opacity-90">
                {lang === 'en' ? card.item.kind_label : ''}
                {card.item.shops > 0 && `${lang === 'en' ? ' · ' : ''}${S.seenIn(card.item.shops)}`}
              </div>
            )}
          </div>
        </div>
      )}
      {card.yq_item && (
        <ProductRow
          p={{ code: card.yq_item.item_code, name: card.yq_item.display_name, brand: card.yq_item.brand, thumb_url: card.yq_item.thumb_url }}
          badge={S.yqCode}
        />
      )}
      {/* the server's own sentence is English: the Arabic sheet says the same in its words (a case already said it) */}
      {(lang === 'en' || !isCase) && (
        <p className="text-[14px] leading-snug text-[#1A1428]">{lang === 'en' ? card.message : S.resultLine[card.result] || S.resultLine.saved_for_review}</p>
      )}
      {card.fallback === 'legacy' && <p className="text-[12px] text-[#6b6480]">{S.legacy}</p>}
    </div>
  )
}

function PendingList({ S, waiting, failed }: { S: CaptureStrings; waiting: number; failed: { id: string; error: string }[] }) {
  const [retrying, setRetrying] = useState(false)
  return (
    <div className="space-y-2 rounded-2xl border border-[#F0E3C8] bg-[#FFFBF3] p-3 text-[12.5px] text-[#7a4d0a]">
      {waiting > 0 && (
        <div className="flex items-center gap-2">
          <WifiOff size={15} className="shrink-0" aria-hidden="true" />
          <span className="flex-1">{S.waiting(waiting)}</span>
          <button
            type="button"
            disabled={retrying}
            onClick={async () => { setRetrying(true); await retryWaiting(); setRetrying(false) }}
            className="inline-flex h-11 items-center gap-1 rounded-lg px-2 font-semibold hover:bg-[#fdf3e3]"
          >
            {retrying ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <RefreshCw size={13} aria-hidden="true" />} {S.retryNow}
          </button>
        </div>
      )}
      {failed.map((q) => (
        <div key={q.id} className="flex items-center gap-2 text-[#9f1239]">
          <span className="flex-1">{S.notSaved} {q.error}</span>
          <button type="button" onClick={() => discardCapture(q.id)} className="h-11 rounded-lg px-2 font-semibold hover:bg-[#fdecef]">{S.discard}</button>
        </div>
      ))}
    </div>
  )
}
