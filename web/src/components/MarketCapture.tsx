import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Camera, CheckCircle2, CircleDot, Copy, ImagePlus, Loader2, MapPin, PackageCheck, RefreshCw, Sparkles, Store, WifiOff, X } from 'lucide-react'
import { Sheet } from '@/components/ui/sheet'
import { isReadOnly, useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import {
  DEMAND_LEVELS,
  discardCapture,
  KINDS,
  MAX_PHOTOS,
  newUuid,
  preparePhoto,
  retryWaiting,
  submitCapture,
  useCaptureQueue,
  type MiCard,
  type MiKind,
  type ReadyPhoto,
} from '@/lib/marketIntel'
import { useCustomers, useSelectedCustomer } from '@/pages/sales/lib'

/**
 * "Spotted" — the rep's one-tap market capture (release R7b, plan §18.1). A floating camera
 * button on Today, the Catalog, Customers and Market Intel: the camera opens at once, the rep adds
 * a one-line note and, if he likes, a kind and the two or three fields that kind needs; Save
 * answers with the office's deterministic result card (Already in YQ / Seen before / New market
 * find / Saved: office will identify). The shop pill he is ordering for comes along by itself.
 *
 * Photos are resized on the phone (1600 px, WebP q0.75) and carry no EXIF. With no signal the
 * capture waits in the page's queue under its client UUID and is retried while the app is open.
 */

const SHOW_ON = ['/today', '/shop', '/customers', '/market-intel']
const CATEGORIES = ['CABLE', 'CHARGER', 'CAR CHARGER', 'POWER BANK', 'EARPHONE', 'BLUETOOTH HEADSET', 'BLUETOOTH SPEAKER', 'CAR ACCESSORIES']
const BRANDS = ['Anker', 'Baseus', 'Borofone', 'Green Lion', 'Hoco', 'Joyroom', 'Ldnio', 'Porodo', 'Powerology', 'Samsung', 'Apple', 'Ugreen', 'Xiaomi', 'WEKOME']

/** Which quick fields each kind asks for (all optional). */
const FIELDS: Record<MiKind, { title: string; brand?: boolean; price?: boolean; category?: boolean; barcode?: boolean; demand?: boolean }> = {
  new_product: { title: 'Name or code on the pack', brand: true, price: true, category: true, barcode: true },
  competitor_price: { title: 'Which product', brand: true, price: true, category: true },
  promotion: { title: "What's on offer", brand: true, price: true },
  shop_asked: { title: 'What they asked for (name or YQ code)', demand: true, category: true },
  complaint: { title: 'Which product (name or YQ code)' },
  other: { title: 'Product (optional)' },
}

const RESULT: Record<MiCard['result'], { title: string; icon: ReactNode; tone: string }> = {
  already_in_yq: { title: 'Already in YQ', icon: <PackageCheck size={22} aria-hidden="true" />, tone: 'bg-[#e8f7ee] text-[#137a48]' },
  seen_before: { title: 'Seen before', icon: <Copy size={22} aria-hidden="true" />, tone: 'bg-[#EEE8F4] text-[#6D4091]' },
  new_find: { title: 'New market find', icon: <Sparkles size={22} aria-hidden="true" />, tone: 'bg-[#6D4091] text-white' },
  saved_for_review: { title: 'Saved: office will identify', icon: <CheckCircle2 size={22} aria-hidden="true" />, tone: 'bg-[#f4f3f8] text-[#4B2C66]' },
}

const CHIP = 'inline-flex h-10 items-center rounded-full px-3.5 text-[13px] font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6D4091]/60'
const FIELD = 'h-11 w-full rounded-xl border border-[#E2DCEA] bg-white px-3 text-[14px] text-[#1A1428] placeholder:text-[#9a93ad] focus:border-[#824FAB] focus:outline-none focus:ring-2 focus:ring-[#824FAB]/25'
const LABEL = 'mb-1 block text-[11.5px] font-semibold uppercase tracking-[0.06em] text-[#6b6480]'

interface Shop {
  name: string
  area: string
  phone: string
}

function titleCase(s: string): string {
  return s.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase())
}

export function MarketCapture() {
  const { me } = useAuth()
  const { pathname } = useLocation()
  const selected = useSelectedCustomer()
  const queue = useCaptureQueue()
  const cameraRef = useRef<HTMLInputElement>(null)
  const galleryRef = useRef<HTMLInputElement>(null)

  const [open, setOpen] = useState(false)
  const [stage, setStage] = useState<'edit' | 'saving' | 'result' | 'queued'>('edit')
  const [photos, setPhotos] = useState<ReadyPhoto[]>([])
  const [preparing, setPreparing] = useState(0)
  const [note, setNote] = useState('')
  const [kind, setKind] = useState<MiKind | null>(null)
  const [title, setTitle] = useState('')
  const [brand, setBrand] = useState('')
  const [price, setPrice] = useState('')
  const [category, setCategory] = useState('')
  const [barcode, setBarcode] = useState('')
  const [demand, setDemand] = useState('')
  const [qty, setQty] = useState('')
  const [shop, setShop] = useState<Shop | null>(null)
  const [pickShop, setPickShop] = useState(false)
  const [card, setCard] = useState<MiCard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uuid, setUuid] = useState(newUuid)

  const customersQ = useCustomers(open && pickShop)
  const allowed = !!me && (me.role === 'admin' || (me.features || []).includes('Market Intel')) && !isReadOnly(me)
  const visible = allowed && SHOW_ON.some((p) => pathname === p || pathname.startsWith(`${p}/`))
  const waiting = queue.filter((q) => q.state !== 'failed').length
  const failed = queue.filter((q) => q.state === 'failed')

  // the photo previews are object URLs: let them go when the component does
  const photosRef = useRef<ReadyPhoto[]>([])
  useEffect(() => {
    photosRef.current = photos
  }, [photos])
  useEffect(() => () => photosRef.current.forEach((p) => URL.revokeObjectURL(p.url)), [])

  if (!visible && !open) return null

  function fromSelected(): Shop | null {
    if (!selected) return null
    return { name: String(selected.shop || selected.name || ''), area: String(selected.area || ''), phone: String(selected.phone || '') }
  }

  function reset(keepPhotos = false) {
    if (!keepPhotos) photos.forEach((p) => URL.revokeObjectURL(p.url))
    setPhotos(keepPhotos ? photos : [])
    setNote('')
    setKind(null)
    setTitle('')
    setBrand('')
    setPrice('')
    setCategory('')
    setBarcode('')
    setDemand('')
    setQty('')
    setCard(null)
    setError(null)
    setPickShop(false)
    setStage('edit')
    setUuid(newUuid())
    setShop(fromSelected())
  }

  function start() {
    if (stage === 'result' || stage === 'queued') reset()
    else if (!open) setShop(fromSelected())
    setOpen(true)
    // the camera opens in the same tap (a user gesture), the sheet waits behind it
    if (photos.length < MAX_PHOTOS) cameraRef.current?.click()
  }

  async function addFiles(list: FileList | null) {
    if (!list?.length) return
    const room = MAX_PHOTOS - photos.length
    const files = Array.from(list).slice(0, Math.max(0, room))
    if (list.length > room) setError(`Up to ${MAX_PHOTOS} photos per sighting.`)
    setPreparing((n) => n + files.length)
    for (const [i, f] of files.entries()) {
      try {
        const ready = await preparePhoto(f, photos.length + i)
        setPhotos((cur) => (cur.length < MAX_PHOTOS ? [...cur, ready] : cur))
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not read that photo.')
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

  const canSave = stage === 'edit' && preparing === 0 && (photos.length > 0 || note.trim() !== '' || title.trim() !== '' || barcode.trim() !== '')

  async function save() {
    if (!canSave) return
    setError(null)
    setStage('saving')
    const f = kind ? FIELDS[kind] : null
    const fields: Record<string, string> = {
      note: note.trim(),
      kind: kind || 'other',
      title: title.trim(),
      brand: f?.brand ? brand.trim() : '',
      price_bhd: f?.price ? price.trim() : '',
      category: f?.category ? category : '',
      barcode: f?.barcode ? barcode.replace(/\D/g, '') : '',
      demand_level: f?.demand ? demand : '',
      demand_qty: f?.demand ? qty.trim() : '',
      shop_name: shop?.name || '',
      shop_phone: shop?.phone || '',
      area: shop?.area || '',
    }
    const draft = { client_uuid: uuid, fields, photos: photos.map((p) => ({ ...p })) }
    try {
      const got = await submitCapture(draft)
      if (got) {
        setCard(got)
        setStage('result')
      } else {
        setStage('queued')
      }
      setPhotos([]) // the queue owns the photos now (it lets their URLs go once sent)
    } catch (e) {
      discardCapture(uuid, true) // the sheet keeps the draft and its photos to fix and save again
      setError(e instanceof Error ? e.message : 'Could not save. Please try again.')
      setStage('edit')
    }
  }

  const f = kind ? FIELDS[kind] : null
  const busy = stage === 'saving'

  const footer =
    stage === 'result' || stage === 'queued' ? (
      <div className="flex gap-2">
        <button type="button" onClick={() => { reset(); cameraRef.current?.click() }} className="flex h-12 flex-1 items-center justify-center gap-2 rounded-xl border border-[#E2DCEA] bg-white text-[14px] font-semibold text-[#4B2C66] hover:bg-[#F7F3FA]">
          <Camera size={17} aria-hidden="true" /> Spot another
        </button>
        <button type="button" onClick={() => { setOpen(false); reset() }} className="flex h-12 flex-1 items-center justify-center rounded-xl bg-[#6D4091] text-[14px] font-semibold text-white hover:bg-[#5A3478]">
          Done
        </button>
      </div>
    ) : (
      <button type="button" onClick={save} disabled={!canSave} className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#6D4091] text-[15px] font-semibold text-white transition hover:bg-[#5A3478] disabled:opacity-45">
        {busy ? <Loader2 size={18} className="animate-spin" aria-hidden="true" /> : <CheckCircle2 size={18} aria-hidden="true" />}
        {busy ? 'Saving…' : preparing ? 'Preparing photo…' : 'Save'}
      </button>
    )

  return (
    <>
      <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => { void addFiles(e.target.files); e.target.value = '' }} />
      <input ref={galleryRef} type="file" accept="image/*" multiple className="hidden" onChange={(e) => { void addFiles(e.target.files); e.target.value = '' }} />

      {visible && (
        <button
          type="button"
          onClick={start}
          aria-label={waiting ? `Spotted: ${waiting} waiting to send` : 'Spotted: capture what you saw in the market'}
          className="fixed right-4 z-30 flex h-[52px] items-center gap-2 rounded-full bg-gradient-to-br from-[#824FAB] to-[#6D4091] pl-4 pr-5 text-[14px] font-bold text-white shadow-[0_12px_28px_-10px_rgba(109,64,145,.75)] ring-1 ring-white/20 transition active:scale-[.97] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[#824FAB]/40 lg:right-8"
          style={{ bottom: `calc(var(--yq-tabbar, 88px) + env(safe-area-inset-bottom, 0px) + ${pathname.startsWith('/shop') ? 84 : 14}px)` }}
        >
          <Camera size={20} strokeWidth={2.2} aria-hidden="true" />
          Spotted
          {waiting > 0 && (
            <span className="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-[#F4B740] px-1 text-[11px] font-bold text-[#1A1428] ring-2 ring-white">{waiting}</span>
          )}
        </button>
      )}

      <Sheet
        open={open}
        onClose={() => { if (!busy) setOpen(false) }}
        title={stage === 'result' ? 'Thanks — got it' : stage === 'queued' ? 'Kept on this phone' : 'What did you spot?'}
        subtitle={stage === 'edit' || stage === 'saving' ? 'Photos of products and shelves, not people. Everything but the photo is optional.' : undefined}
        footer={footer}
      >
        <div className="space-y-5 px-4 py-4 sm:px-5">
          {stage === 'result' && card && <ResultCard card={card} />}

          {stage === 'queued' && (
            <div className="flex gap-3 rounded-2xl bg-[#fdf3e3] p-4 text-[#7a4d0a]">
              <WifiOff size={22} className="mt-0.5 shrink-0" aria-hidden="true" />
              <p className="text-[13.5px] leading-snug">
                <span className="font-bold">No signal right now.</span> Your sighting is saved on this phone and sends by itself when the connection is back, as long as the app stays open.
              </p>
            </div>
          )}

          {(stage === 'edit' || stage === 'saving') && (
            <>
              {/* photos */}
              <div className="flex gap-2 overflow-x-auto pb-1">
                {photos.map((p, i) => (
                  <div key={p.url} className="relative shrink-0">
                    <img src={p.url} alt={`Photo ${i + 1}`} className="h-24 w-24 rounded-xl border border-[#E2DCEA] object-cover" />
                    <button type="button" onClick={() => removePhoto(i)} aria-label={`Remove photo ${i + 1}`} className="absolute -right-1.5 -top-1.5 grid h-7 w-7 place-items-center rounded-full bg-[#1A1428] text-white shadow ring-2 ring-white">
                      <X size={13} aria-hidden="true" />
                    </button>
                  </div>
                ))}
                {preparing > 0 && (
                  <div className="grid h-24 w-24 shrink-0 place-items-center rounded-xl border border-dashed border-[#C9B7DA] text-[#824FAB]">
                    <Loader2 size={20} className="animate-spin" aria-label="Preparing photo" />
                  </div>
                )}
                {photos.length + preparing < MAX_PHOTOS && (
                  <>
                    <button type="button" onClick={() => cameraRef.current?.click()} className="grid h-24 w-24 shrink-0 place-items-center rounded-xl border-2 border-dashed border-[#C9B7DA] bg-[#FAF7FC] text-[#6D4091] hover:bg-[#F3EDF8]">
                      <span className="flex flex-col items-center gap-1 text-[12px] font-semibold">
                        <Camera size={22} aria-hidden="true" /> {photos.length ? 'Another' : 'Camera'}
                      </span>
                    </button>
                    <button type="button" onClick={() => galleryRef.current?.click()} className="grid h-24 w-20 shrink-0 place-items-center rounded-xl border border-[#E2DCEA] bg-white text-[#6b6480] hover:bg-[#F7F3FA]">
                      <span className="flex flex-col items-center gap-1 text-[11.5px] font-semibold">
                        <ImagePlus size={19} aria-hidden="true" /> Gallery
                      </span>
                    </button>
                  </>
                )}
              </div>

              {/* note */}
              <div>
                <label htmlFor="mi-note" className={LABEL}>Note</label>
                <textarea
                  id="mi-note"
                  rows={2}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  maxLength={1000}
                  placeholder="What did you see? e.g. Brand X 20W charger at 3.500 on the counter"
                  className={cn(FIELD, 'h-auto min-h-[64px] resize-none py-2.5 leading-snug')}
                />
              </div>

              {/* kind */}
              <div>
                <span className={LABEL} id="mi-kind">Kind</span>
                <div role="radiogroup" aria-labelledby="mi-kind" className="flex flex-wrap gap-2">
                  {KINDS.map((k) => {
                    const on = kind === k.value
                    return (
                      <button
                        key={k.value}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        onClick={() => setKind(on ? null : k.value)}
                        className={cn(CHIP, on ? 'bg-[#6D4091] text-white shadow-sm' : 'bg-[#F3EDF8] text-[#4B2C66] hover:bg-[#EADFF3]')}
                      >
                        {k.label}
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* quick fields for the chosen kind */}
              {f && (
                <div className="space-y-3 rounded-2xl border border-[#EFE9F5] bg-[#FCFAFE] p-3">
                  <div>
                    <label htmlFor="mi-title" className={LABEL}>{f.title}</label>
                    <input id="mi-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} className={FIELD} placeholder="Optional" autoComplete="off" />
                  </div>
                  {f.brand && (
                    <div>
                      <label htmlFor="mi-brand" className={LABEL}>Brand</label>
                      <input id="mi-brand" list="mi-brands" value={brand} onChange={(e) => setBrand(e.target.value)} maxLength={60} className={FIELD} placeholder="Pick or type" autoComplete="off" />
                      <datalist id="mi-brands">
                        {BRANDS.map((b) => (
                          <option key={b} value={b} />
                        ))}
                      </datalist>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    {f.price && (
                      <div>
                        <label htmlFor="mi-price" className={LABEL}>Price</label>
                        <div className="relative">
                          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[12px] font-semibold text-[#6b6480]">BHD</span>
                          <input id="mi-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ''))} maxLength={10} className={cn(FIELD, 'pl-12 tabular-nums')} placeholder="0.000" />
                        </div>
                      </div>
                    )}
                    {f.category && (
                      <div className={f.price ? '' : 'col-span-2'}>
                        <label htmlFor="mi-cat" className={LABEL}>Category</label>
                        <select id="mi-cat" value={category} onChange={(e) => setCategory(e.target.value)} className={FIELD}>
                          <option value="">Any</option>
                          {CATEGORIES.map((c) => (
                            <option key={c} value={c}>{titleCase(c)}</option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                  {f.barcode && (
                    <div>
                      <label htmlFor="mi-barcode" className={LABEL}>Barcode digits</label>
                      <input id="mi-barcode" inputMode="numeric" value={barcode} onChange={(e) => setBarcode(e.target.value.replace(/[^0-9 ]/g, ''))} maxLength={24} className={cn(FIELD, 'tabular-nums')} placeholder="Optional, from the pack" />
                    </div>
                  )}
                  {f.demand && (
                    <div>
                      <span className={LABEL} id="mi-demand">How much do they want?</span>
                      <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-labelledby="mi-demand">
                        {DEMAND_LEVELS.map((d) => {
                          const on = demand === d.value
                          return (
                            <button key={d.value} type="button" role="radio" aria-checked={on} onClick={() => setDemand(on ? '' : d.value)} className={cn(CHIP, on ? 'bg-[#6D4091] text-white' : 'bg-[#F3EDF8] text-[#4B2C66] hover:bg-[#EADFF3]')}>
                              {d.label}
                            </button>
                          )
                        })}
                        <input aria-label="Quantity" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ''))} maxLength={6} className={cn(FIELD, 'h-10 w-24 tabular-nums')} placeholder="Qty" />
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* the shop */}
              <div>
                <span className={LABEL}>Shop</span>
                {shop && !pickShop ? (
                  <div className="flex items-center gap-2 rounded-xl border border-[#E2DCEA] bg-white px-3 py-2">
                    <Store size={16} className="shrink-0 text-[#824FAB]" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-[#1A1428]">
                      {shop.name || 'This shop'}
                      {shop.area && <span className="ml-1.5 font-normal text-[#6b6480]">· {shop.area}</span>}
                    </span>
                    <button type="button" onClick={() => setPickShop(true)} className="h-9 rounded-lg px-2 text-[12.5px] font-semibold text-[#6D4091] hover:bg-[#F3EDF8]">Change</button>
                    <button type="button" onClick={() => setShop(null)} aria-label="Not at a shop" className="grid h-9 w-9 place-items-center rounded-lg text-[#6b6480] hover:bg-[#F3EDF8]">
                      <X size={15} aria-hidden="true" />
                    </button>
                  </div>
                ) : pickShop ? (
                  <select
                    aria-label="Pick the shop"
                    className={FIELD}
                    value=""
                    onChange={(e) => {
                      const c = (customersQ.data || [])[Number(e.target.value)]
                      if (c) setShop({ name: String(c.shop || c.name || ''), area: String(c.area || ''), phone: String(c.phone || '') })
                      setPickShop(false)
                    }}
                  >
                    <option value="">{customersQ.isLoading ? 'Loading your shops…' : 'Pick one of your shops'}</option>
                    {(customersQ.data || []).map((c, i) => (
                      <option key={`${c.phone}-${i}`} value={i}>
                        {c.shop || c.name}
                        {c.area ? ` · ${c.area}` : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <button type="button" onClick={() => setPickShop(true)} className="flex h-11 w-full items-center gap-2 rounded-xl border border-dashed border-[#D9CCE6] px-3 text-[13.5px] font-medium text-[#6b6480] hover:bg-[#FAF7FC]">
                    <MapPin size={16} aria-hidden="true" /> Add the shop (optional)
                  </button>
                )}
              </div>

              {error && (
                <p role="alert" className="rounded-xl bg-[#fdecef] px-3 py-2.5 text-[13px] font-medium text-[#9f1239]">{error}</p>
              )}
            </>
          )}

          {(queue.length > 0 || failed.length > 0) && (
            <PendingList waiting={waiting} failed={failed.map((q) => ({ id: q.client_uuid, error: q.error || '' }))} />
          )}

          {(stage === 'result' || stage === 'queued') && (
            <Link to="/market-intel" onClick={() => { setOpen(false); reset() }} className="flex items-center justify-center gap-1.5 text-[13px] font-semibold text-[#6D4091] hover:underline">
              <CircleDot size={14} aria-hidden="true" /> See my signals
            </Link>
          )}
        </div>
      </Sheet>
    </>
  )
}

function ResultCard({ card }: { card: MiCard }) {
  const meta = RESULT[card.result] || RESULT.saved_for_review
  return (
    <div className="space-y-3" aria-live="polite">
      <div className={cn('flex items-center gap-3 rounded-2xl p-4', meta.tone)}>
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-white/20">{meta.icon}</span>
        <div className="min-w-0">
          <div className="font-display text-[17px] font-bold leading-tight">{meta.title}</div>
          {card.item && card.result !== 'saved_for_review' && (
            <div className="mt-0.5 text-[12.5px] opacity-90">
              {card.item.kind_label}
              {card.item.shops > 0 && ` · seen in ${card.item.shops} shop${card.item.shops === 1 ? '' : 's'}`}
            </div>
          )}
        </div>
      </div>
      {card.yq_item && (
        <div className="flex items-center gap-3 rounded-2xl border border-[#E2DCEA] bg-white p-3">
          {card.yq_item.thumb_url ? (
            <img src={card.yq_item.thumb_url} alt="" className="h-14 w-14 shrink-0 rounded-xl border border-[#EFE9F5] object-contain" />
          ) : (
            <span className="grid h-14 w-14 shrink-0 place-items-center rounded-xl bg-[#F3EDF8] text-[#6D4091]">
              <PackageCheck size={22} aria-hidden="true" />
            </span>
          )}
          <span className="min-w-0">
            <span className="block truncate text-[14px] font-semibold text-[#1A1428]">{card.yq_item.display_name}</span>
            <span className="block text-[12px] text-[#6b6480]">YQ code {card.yq_item.item_code}</span>
          </span>
        </div>
      )}
      <p className="text-[14px] leading-snug text-[#1A1428]">{card.message}</p>
      {card.fallback === 'legacy' && (
        <p className="text-[12px] text-[#6b6480]">Saved to the Finds board while Market Intel is being set up.</p>
      )}
    </div>
  )
}

function PendingList({ waiting, failed }: { waiting: number; failed: { id: string; error: string }[] }) {
  const [retrying, setRetrying] = useState(false)
  return (
    <div className="space-y-2 rounded-2xl border border-[#F0E3C8] bg-[#FFFBF3] p-3 text-[12.5px] text-[#7a4d0a]">
      {waiting > 0 && (
        <div className="flex items-center gap-2">
          <WifiOff size={15} className="shrink-0" aria-hidden="true" />
          <span className="flex-1">
            {waiting} sighting{waiting === 1 ? '' : 's'} waiting for signal. Keep the app open.
          </span>
          <button
            type="button"
            disabled={retrying}
            onClick={async () => { setRetrying(true); await retryWaiting(); setRetrying(false) }}
            className="inline-flex h-9 items-center gap-1 rounded-lg px-2 font-semibold hover:bg-[#fdf3e3]"
          >
            {retrying ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <RefreshCw size={13} aria-hidden="true" />} Retry now
          </button>
        </div>
      )}
      {failed.map((q) => (
        <div key={q.id} className="flex items-center gap-2 text-[#9f1239]">
          <span className="flex-1">Not saved: {q.error}</span>
          <button type="button" onClick={() => discardCapture(q.id)} className="h-9 rounded-lg px-2 font-semibold hover:bg-[#fdecef]">Discard</button>
        </div>
      ))}
    </div>
  )
}
