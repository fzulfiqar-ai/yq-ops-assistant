import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { AlertTriangle, Loader2, ShieldCheck, Tag, Trash2, UserRound } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Sheet } from '@/components/ui/sheet'
import { Stepper } from '@/components/ui/stepper'
import { cn } from '@/lib/utils'
import type { Cart } from '@/lib/cart'
import {
  getMyCustomers,
  postOrder,
  postStaffOrder,
  ShopApiError,
  type CatalogPayload,
  type OrderResponse,
  type Quote,
  type ShopItem,
  type StaffCustomer,
} from '@/lib/shopApi'
import { ProductImage } from './ProductImage'
import { Select } from './Select'
import { bhd, cleanPhone, FIELD, isEmail, isPhone, LABEL, minQtyOf, money, RING, stepOf } from './shared'

const FORM_ID = 'yq-shop-order-form'
const CUSTOMER_KEY = 'yq-shop-customer'

export type ShopMode = 'public' | 'salesman'

interface CustomerDraft {
  name: string
  phone: string
  shop: string
  area: string
  email: string
}

const EMPTY: CustomerDraft = { name: '', phone: '', shop: '', area: '', email: '' }

function readCustomer(): CustomerDraft {
  try {
    const raw = localStorage.getItem(CUSTOMER_KEY)
    if (!raw) return EMPTY
    const p = JSON.parse(raw)
    return {
      name: String(p?.name || ''),
      phone: String(p?.phone || ''),
      shop: String(p?.shop || ''),
      area: String(p?.area || ''),
      email: String(p?.email || ''),
    }
  } catch {
    return EMPTY
  }
}

export interface CartDrawerProps {
  open: boolean
  onClose: () => void
  mode?: ShopMode
  token: string
  data: CatalogPayload
  itemsByCode: Map<string, ShopItem>
  cart: Cart
  quote: Quote | null
  quoting: boolean
  quoteError: string
  coupon: string
  onCouponChange: (code: string) => void
  src: string
  sessionId: string
  onSuccess: (order: OrderResponse, customer: { name: string; shop: string }) => void
}

/**
 * The checkout.
 *
 * Two audiences share every line of it. A shopkeeper on a WhatsApp link fills in
 * who he is and picks a salesman; a salesman standing in that same shop fills in
 * who the SHOP is — and because he has served it before, he mostly just taps a
 * chip. The parts that differ are the header of the form, the salesman block and
 * the verb on the button. Everything else — pricing, warnings, totals — is the
 * server's answer either way.
 */
export function CartDrawer({
  open,
  onClose,
  mode = 'public',
  token,
  data,
  itemsByCode,
  cart,
  quote,
  quoting,
  quoteError,
  coupon,
  onCouponChange,
  src,
  sessionId,
  onSuccess,
}: CartDrawerProps) {
  const staff = mode === 'salesman'
  const [customer, setCustomer] = useState<CustomerDraft>(() => (staff ? EMPTY : readCustomer()))
  const [note, setNote] = useState('')
  const [website, setWebsite] = useState('') // honeypot — a human never fills this
  const [couponDraft, setCouponDraft] = useState(coupon)
  const [salesmanId, setSalesmanId] = useState<number | ''>('')
  const [changingSalesman, setChangingSalesman] = useState(false)
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [recent, setRecent] = useState<StaffCustomer[]>([])

  const salesmen = data.salesmen || []
  const resolvedRef = data.ref || null
  const referralCode = resolvedRef?.referral_code || ''
  const me = data.me || null
  const mySalesmanId = me?.salesman_id ?? null
  const isAdmin = Boolean(me?.is_admin)

  /* Who the order is credited to.
     Public: the link's ?ref, else the dropdown.
     Salesman: himself — unless he is an admin with no salesman record, who must
     say whose order this is. */
  const needsSalesman = staff ? isAdmin && mySalesmanId == null && salesmen.length > 0 : !resolvedRef?.salesman_id && salesmen.length > 0

  // Remember who this is — shopkeepers re-order every few weeks and hate retyping.
  // Never in salesman mode: he serves a different shop every hour, and a
  // pre-filled previous customer is an order sent to the wrong one.
  useEffect(() => {
    if (staff) return
    try {
      localStorage.setItem(CUSTOMER_KEY, JSON.stringify(customer))
    } catch {
      /* private mode — just don't remember */
    }
  }, [customer, staff])

  // His own book of shops, fetched when the drawer opens so it is never stale.
  useEffect(() => {
    if (!staff || !open) return
    let alive = true
    getMyCustomers()
      .then((rows) => {
        if (alive) setRecent(rows)
      })
      .catch(() => {
        /* the quick-pick is a shortcut, not a dependency */
      })
    return () => {
      alive = false
    }
  }, [staff, open])

  const quoteLines = useMemo(() => {
    const m = new Map<string, NonNullable<Quote['lines']>[number]>()
    for (const l of quote?.lines || []) m.set(l.item_code, l)
    return m
  }, [quote])

  const nameOk = customer.name.trim().length > 1
  const phoneOk = isPhone(customer.phone)
  const emailOk = !customer.email.trim() || isEmail(customer.email)
  const salesmanOk = !needsSalesman || salesmanId !== ''
  const canSubmit =
    cart.lines.length > 0 &&
    nameOk &&
    phoneOk &&
    emailOk &&
    salesmanOk &&
    !quoting &&
    !submitting &&
    quote?.can_submit !== false

  const set = (k: keyof CustomerDraft, v: string) => setCustomer((c) => ({ ...c, [k]: v }))
  const blur = (k: string) => setTouched((t) => ({ ...t, [k]: true }))

  const pick = (c: StaffCustomer) => {
    setCustomer({
      name: String(c.name || ''),
      phone: String(c.phone || ''),
      shop: String(c.shop || ''),
      area: String(c.area || ''),
      email: String(c.email || ''),
    })
    setTouched({})
    setSubmitError('')
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched({ name: true, phone: true, email: true, salesman: true })
    if (!canSubmit) return
    setSubmitting(true)
    setSubmitError('')
    const who = {
      name: customer.name.trim(),
      phone: cleanPhone(customer.phone),
      shop: customer.shop.trim(),
      area: customer.area.trim(),
      email: customer.email.trim(),
    }
    try {
      const res = staff
        ? await postStaffOrder({
            lines: cart.lines,
            coupon_code: coupon || '',
            // Only an admin with no salesman record of his own may name one.
            ...(needsSalesman && salesmanId !== '' ? { salesman_id: Number(salesmanId) } : {}),
            customer: who,
            note: note.trim(),
          })
        : await postOrder(token, {
            lines: cart.lines,
            coupon_code: coupon || '',
            referral_code: referralCode,
            salesman_id: salesmanId === '' ? resolvedRef?.salesman_id ?? null : Number(salesmanId),
            customer: who,
            note: note.trim(),
            src,
            session_id: sessionId,
            website,
          })
      setNote('')
      setCouponDraft('')
      if (staff) {
        setCustomer(EMPTY)
        setTouched({})
      }
      onSuccess(res, { name: who.name, shop: who.shop })
    } catch (err: unknown) {
      setSubmitError(
        err instanceof ShopApiError
          ? err.detail || err.message
          : 'We could not send your order. Please check your connection and try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  const progress = quote?.progress || null
  const pct = progress?.threshold_bhd
    ? Math.max(
        0,
        Math.min(
          100,
          ((Number(progress.threshold_bhd) - Number(progress.remaining_bhd || 0)) / Number(progress.threshold_bhd)) * 100,
        ),
      )
    : 0

  const couponMsg = quote?.coupon?.message || ''
  const couponBad = Boolean(quote?.coupon && quote.coupon.valid === false)
  // The server says exactly what is in the way ("Remove X05 UL-1Mtr…", "Minimum
  // order is…"); the fallback only covers an older API that did not.
  const blockedReason =
    quote?.can_submit === false ? quote.block_reason || 'This order cannot be submitted yet.' : ''

  return (
    <Sheet
      open={open}
      onClose={onClose}
      variant="drawer"
      title={staff ? 'New order' : 'Your order'}
      subtitle={`${cart.items} ${cart.items === 1 ? 'product' : 'products'} · ${cart.units} pcs`}
      footer={
        <div>
          {blockedReason && <p className="mb-2 text-[11.5px] font-medium text-[#9f1239]">{blockedReason}</p>}
          {submitError && (
            <p role="alert" className="mb-2 rounded-xl bg-[#fdecef] px-3 py-2 text-[11.5px] font-medium text-[#9f1239]">
              {submitError}
            </p>
          )}
          <div className="mb-2.5 flex items-baseline justify-between">
            <span className="text-[12px] font-medium text-[#6b6480]">Total</span>
            <span className="font-display text-[20px] font-extrabold tracking-[-0.015em] tabular-nums text-[#1a1430]">
              {bhd(quote?.total_bhd)}
            </span>
          </div>
          <button
            type="submit"
            form={FORM_ID}
            disabled={!canSubmit}
            className={cn(
              'flex h-12 w-full items-center justify-center gap-2 rounded-xl text-[14px] font-semibold transition duration-150 ease-out active:scale-[.995]',
              RING,
              canSubmit
                ? 'bg-[#6d28d9] text-white hover:bg-[#5b21b6]'
                : 'cursor-not-allowed bg-[#f0eef6] text-[#a8a2bb]',
            )}
          >
            {submitting ? (
              <>
                <Loader2 size={16} className="animate-spin" aria-hidden="true" /> Sending…
              </>
            ) : staff ? (
              'Place order for this shop'
            ) : (
              'Send order'
            )}
          </button>
          <p className="mt-2.5 flex items-center justify-center gap-1.5 text-[10.5px] text-[#6b6480]">
            <ShieldCheck size={12} aria-hidden="true" />
            {staff ? 'You will confirm this with the shop.' : 'No payment now — your salesman confirms everything first.'}
          </p>
        </div>
      }
    >
      <div className="px-4 py-4 sm:px-5">
        {cart.lines.length === 0 ? (
          <div className="py-14 text-center">
            <p className="font-display text-[15px] font-bold text-[#1a1430]">Nothing in this order yet</p>
            <p className="mt-1 text-[12.5px] text-[#6b6480]">Add a product and it will show up here.</p>
            <button
              type="button"
              onClick={onClose}
              className={cn(
                'mt-4 inline-flex h-11 items-center rounded-xl border border-[#e4e0ee] bg-white px-5 text-[13px] font-semibold text-[#1a1430] transition duration-150 ease-out hover:bg-[#f7f5fb]',
                RING,
              )}
            >
              Back to the catalog
            </button>
          </div>
        ) : (
          <>
            {/* ── lines ── */}
            <ul className="divide-y divide-[#f4f2f9]">
              {cart.lines.map((line) => {
                const item = itemsByCode.get(line.item_code)
                const q = quoteLines.get(line.item_code)
                const step = stepOf(item)
                const min = minQtyOf(item)
                // A dead line: say why, price it at nothing, and make removing it
                // one tap. Only a below-minimum line keeps its stepper — raising the
                // quantity is the fix there, not deleting the product.
                const blocked = Boolean(q?.unavailable)
                const fixableByQty = blocked && Boolean(item) && Number(q?.moq || 1) > line.qty
                return (
                  <li key={line.item_code} className={cn('flex gap-3 py-3.5', blocked && 'opacity-95')}>
                    <div className="h-16 w-16 shrink-0 overflow-hidden rounded-[14px] border border-[#ece9f3]">
                      <ProductImage
                        srcs={[item?.thumb_url, item?.product_image_url]}
                        alt={item?.display_name || line.item_code}
                        width={64}
                        height={64}
                        className="h-full w-full"
                        imgClassName="p-1.5"
                        iconSize={20}
                        showCaption={false}
                      />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="font-display text-[13px] font-bold text-[#1a1430]">{line.item_code}</div>
                          {item?.spec && (
                            <div className="mt-0.5 line-clamp-1 text-[11px] text-[#6b6480]">
                              {item.spec.split('\n')[0]}
                            </div>
                          )}
                        </div>
                        <div className="shrink-0 text-right">
                          {blocked ? (
                            <div className="font-display text-[13px] font-bold text-[#a8a2bb]">—</div>
                          ) : (
                            <>
                              <div className="font-display text-[13px] font-bold tabular-nums text-[#1a1430]">
                                {bhd(q?.line_total_bhd ?? (item?.price_bhd || 0) * line.qty)}
                              </div>
                              <div className="text-[10.5px] tabular-nums text-[#6b6480]">
                                {money(q?.unit_price_bhd ?? item?.price_bhd)} each
                              </div>
                            </>
                          )}
                        </div>
                      </div>
                      {blocked && q?.blocked_reason && (
                        <p className="mt-1.5 flex items-center gap-1.5 text-[11.5px] font-medium text-[#9f1239]">
                          <AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
                          {q.blocked_reason}
                        </p>
                      )}
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        {blocked && !fixableByQty ? (
                          <button
                            type="button"
                            onClick={() => cart.remove(line.item_code)}
                            className={cn(
                              'inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#f3c9d2] bg-[#fdecef] px-3.5 text-[12px] font-semibold text-[#9f1239] transition duration-150 ease-out hover:bg-[#fbdde3]',
                              RING,
                            )}
                          >
                            <Trash2 size={13} aria-hidden="true" /> Remove
                          </button>
                        ) : (
                          <Stepper
                            value={line.qty}
                            step={step}
                            min={min}
                            size="sm"
                            label={line.item_code}
                            onChange={(n) => cart.set(line.item_code, n)}
                            onRemove={() => cart.remove(line.item_code)}
                          />
                        )}
                        {q?.backorder && <Badge tone="amber">Backorder</Badge>}
                        {q?.applied?.length ? <Badge tone="green">{q.applied[0]?.name || 'Discount'}</Badge> : null}
                      </div>
                      {q?.warning && <p className="mt-1.5 text-[11px] text-[#96600d]">{q.warning}</p>}
                    </div>
                  </li>
                )
              })}
            </ul>

            {/* ── progress toward a threshold ── */}
            {progress?.label && (
              <div className="mt-4 rounded-[16px] border border-[#ece9f3] bg-[#faf9fc] p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11.5px] font-medium text-[#1a1430]">{progress.label}</span>
                  {progress.unlocked && <Badge tone="green">Unlocked</Badge>}
                </div>
                <div
                  className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-[#e9e5f3]"
                  role="progressbar"
                  aria-valuenow={Math.round(progress.unlocked ? 100 : pct)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={progress.label}
                >
                  <div
                    className="h-full rounded-full bg-[#6d28d9] transition-[width] duration-500 ease-out"
                    style={{ width: `${progress.unlocked ? 100 : pct}%` }}
                  />
                </div>
              </div>
            )}

            {/* ── coupon ── */}
            <div className="mt-5">
              <label htmlFor="yq-coupon" className={LABEL}>
                Coupon code
              </label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Tag
                    size={14}
                    aria-hidden="true"
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#a8a2bb]"
                  />
                  <input
                    id="yq-coupon"
                    value={couponDraft}
                    onChange={(e) => setCouponDraft(e.target.value.toUpperCase())}
                    placeholder="Have a code?"
                    autoComplete="off"
                    className={cn(FIELD, 'pl-8 uppercase')}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => onCouponChange(couponDraft.trim())}
                  className={cn(
                    'h-11 shrink-0 rounded-xl border border-[#e4e0ee] bg-white px-4 text-[13px] font-semibold text-[#1a1430] transition duration-150 ease-out hover:border-[#d9d2ee] hover:bg-[#f7f5fb]',
                    RING,
                  )}
                >
                  Apply
                </button>
              </div>
              {couponMsg && (
                <p className={cn('mt-1.5 text-[11.5px] font-medium', couponBad ? 'text-[#9f1239]' : 'text-[#137a48]')}>
                  {couponMsg}
                </p>
              )}
            </div>

            {/* ── server warnings ── */}
            {(quote?.warnings || []).length > 0 && (
              <ul className="mt-4 space-y-1.5">
                {(quote?.warnings || []).map((w, i) => (
                  <li
                    key={i}
                    className="flex gap-2 rounded-xl bg-[#fdf3e3] px-3 py-2 text-[11.5px] leading-snug text-[#96600d]"
                  >
                    <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
                    <span>{w}</span>
                  </li>
                ))}
              </ul>
            )}
            {quoteError && (
              <p className="mt-4 rounded-xl bg-[#fdecef] px-3 py-2 text-[11.5px] text-[#9f1239]">{quoteError}</p>
            )}

            {/* ── totals ── */}
            <dl className="mt-4 space-y-2 rounded-[16px] border border-[#ece9f3] bg-white p-4 text-[12.5px]">
              <div className="flex justify-between">
                <dt className="text-[#6b6480]">Subtotal</dt>
                <dd className="tabular-nums text-[#1a1430]">{bhd(quote?.subtotal_bhd)}</dd>
              </div>
              {(quote?.discounts || []).map((d, i) => (
                <div key={d.rule_id ?? i} className="flex justify-between">
                  <dt className="truncate pr-2 text-[#137a48]">{d.name || 'Discount'}</dt>
                  <dd className="shrink-0 tabular-nums text-[#137a48]">−{bhd(d.amount_bhd)}</dd>
                </div>
              ))}
              {!quote?.discounts?.length && Number(quote?.discount_bhd) > 0 && (
                <div className="flex justify-between">
                  <dt className="text-[#137a48]">Discount</dt>
                  <dd className="tabular-nums text-[#137a48]">−{bhd(quote?.discount_bhd)}</dd>
                </div>
              )}
              <div className="flex justify-between">
                <dt className="text-[#6b6480]">Delivery</dt>
                <dd className="tabular-nums text-[#1a1430]">
                  {Number(quote?.delivery_bhd) > 0 ? bhd(quote?.delivery_bhd) : 'Free'}
                </dd>
              </div>
              <div className="flex justify-between border-t border-[#f4f2f9] pt-2.5">
                <dt className="font-semibold text-[#1a1430]">Total</dt>
                <dd className="font-display text-[15px] font-extrabold tabular-nums text-[#1a1430]">
                  {bhd(quote?.total_bhd)}
                </dd>
              </div>
              {quoting && (
                <div className="flex items-center gap-1.5 pt-0.5 text-[10.5px] text-[#6b6480]">
                  <Loader2 size={11} className="animate-spin" aria-hidden="true" /> Updating…
                </div>
              )}
            </dl>

            {/* ── who is ordering ── */}
            <form id={FORM_ID} onSubmit={submit} className="mt-6" noValidate>
              <h3 className="font-display text-[13px] font-bold tracking-[-0.005em] text-[#1a1430]">
                {staff ? 'Customer details' : 'Your details'}
              </h3>

              {/* Salesman mode: he IS the salesman, so this is a fact, not a field. */}
              {staff && mySalesmanId != null && (
                <p className="mt-2 flex items-center gap-1.5 text-[11.5px] text-[#6b6480]">
                  <UserRound size={13} aria-hidden="true" className="shrink-0" />
                  Placing as <b className="font-semibold text-[#1a1430]">{me?.salesman_name || 'you'}</b>
                </p>
              )}

              {/* An admin has no salesman record — he must say whose order this is. */}
              {staff && needsSalesman && (
                <div className="mt-3">
                  <label htmlFor="yq-salesman" className={LABEL}>
                    Salesman <span className="text-[#9f1239]">*</span>
                  </label>
                  <Select
                    id="yq-salesman"
                    value={salesmanId}
                    onChange={(e) => setSalesmanId(e.target.value === '' ? '' : Number(e.target.value))}
                    onBlur={() => blur('salesman')}
                    required
                    aria-invalid={touched.salesman && !salesmanOk}
                  >
                    <option value="">Select…</option>
                    {salesmen.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </Select>
                  {touched.salesman && !salesmanOk && (
                    <p className="mt-1 text-[11px] font-medium text-[#9f1239]">Please choose whose order this is.</p>
                  )}
                </div>
              )}

              {/* Public mode: the customer's salesman, resolved from the link or chosen. */}
              {!staff &&
                (resolvedRef?.salesman_name && !changingSalesman ? (
                  <div className="mt-3 flex items-center justify-between gap-2 rounded-xl bg-[#f3eefc] px-3.5 py-2.5">
                    <span className="text-[12px] text-[#4a4360]">
                      Your salesman: <b className="font-semibold text-[#1a1430]">{resolvedRef.salesman_name}</b>
                    </span>
                    {salesmen.length > 0 && (
                      <button
                        type="button"
                        onClick={() => setChangingSalesman(true)}
                        className={cn(
                          'shrink-0 rounded text-[11.5px] font-semibold text-[#6d28d9] underline underline-offset-2',
                          RING,
                        )}
                      >
                        change
                      </button>
                    )}
                  </div>
                ) : salesmen.length > 0 ? (
                  <div className="mt-3">
                    <label htmlFor="yq-salesman" className={LABEL}>
                      Choose your salesman <span className="text-[#9f1239]">*</span>
                    </label>
                    <Select
                      id="yq-salesman"
                      value={salesmanId}
                      onChange={(e) => setSalesmanId(e.target.value === '' ? '' : Number(e.target.value))}
                      onBlur={() => blur('salesman')}
                      required
                      aria-invalid={touched.salesman && !salesmanOk}
                    >
                      <option value="">Select…</option>
                      {salesmen.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </Select>
                    {touched.salesman && !salesmanOk && (
                      <p className="mt-1 text-[11px] font-medium text-[#9f1239]">Please pick who is serving you.</p>
                    )}
                  </div>
                ) : null)}

              {/* ── recent customers quick-pick (salesman only) ── */}
              {staff && recent.length > 0 && (
                <div className="mt-4">
                  <div className={LABEL}>Recent customers</div>
                  <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1.5 sm:-mx-5 sm:px-5">
                    {recent.map((c, i) => {
                      const on = cleanPhone(customer.phone) !== '' && cleanPhone(c.phone || '') === cleanPhone(customer.phone)
                      return (
                        <button
                          key={`${c.phone || c.name}-${i}`}
                          type="button"
                          onClick={() => pick(c)}
                          aria-pressed={on}
                          className={cn(
                            'w-[9.5rem] shrink-0 rounded-[14px] border px-3 py-2 text-left transition duration-150 ease-out',
                            RING,
                            on
                              ? 'border-[#6d28d9] bg-[#f3eefc]'
                              : 'border-[#e4e0ee] bg-white hover:border-[#d9d2ee] hover:bg-[#f9f8fc]',
                          )}
                        >
                          <span className="block truncate text-[12.5px] font-semibold leading-tight text-[#1a1430]">
                            {c.shop || c.name}
                          </span>
                          <span className="mt-0.5 block truncate text-[11px] leading-tight text-[#6b6480]">
                            {c.area || c.phone || c.name}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              <div className="mt-3 grid gap-3.5 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <label htmlFor="yq-name" className={LABEL}>
                    {staff ? 'Contact name' : 'Your name'} <span className="text-[#9f1239]">*</span>
                  </label>
                  <input
                    id="yq-name"
                    value={customer.name}
                    onChange={(e) => set('name', e.target.value)}
                    onBlur={() => blur('name')}
                    autoComplete={staff ? 'off' : 'name'}
                    required
                    aria-invalid={touched.name && !nameOk}
                    className={FIELD}
                  />
                  {touched.name && !nameOk && (
                    <p className="mt-1 text-[11px] font-medium text-[#9f1239]">
                      {staff ? 'Who is placing this order at the shop?' : 'Please tell us your name.'}
                    </p>
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label htmlFor="yq-phone" className={LABEL}>
                    Phone <span className="text-[#9f1239]">*</span>
                  </label>
                  <input
                    id="yq-phone"
                    type="tel"
                    inputMode="tel"
                    value={customer.phone}
                    onChange={(e) => set('phone', e.target.value)}
                    onBlur={() => blur('phone')}
                    placeholder="33001122"
                    autoComplete={staff ? 'off' : 'tel'}
                    required
                    aria-invalid={touched.phone && !phoneOk}
                    aria-describedby="yq-phone-hint"
                    className={FIELD}
                  />
                  <p
                    id="yq-phone-hint"
                    className={cn(
                      'mt-1 text-[11px]',
                      touched.phone && !phoneOk ? 'font-medium text-[#9f1239]' : 'text-[#6b6480]',
                    )}
                  >
                    {touched.phone && !phoneOk
                      ? 'Enter an 8-digit Bahrain number, or an international number starting with +.'
                      : '8-digit Bahrain number, or international with +'}
                  </p>
                </div>
                <div>
                  <label htmlFor="yq-shop" className={LABEL}>
                    Shop name
                  </label>
                  <input
                    id="yq-shop"
                    value={customer.shop}
                    onChange={(e) => set('shop', e.target.value)}
                    autoComplete={staff ? 'off' : 'organization'}
                    className={FIELD}
                  />
                </div>
                <div>
                  <label htmlFor="yq-area" className={LABEL}>
                    Area
                  </label>
                  <input
                    id="yq-area"
                    value={customer.area}
                    onChange={(e) => set('area', e.target.value)}
                    autoComplete={staff ? 'off' : 'address-level2'}
                    className={FIELD}
                  />
                </div>
                <div className="sm:col-span-2">
                  <label htmlFor="yq-email" className={LABEL}>
                    Email <span className="font-normal text-[#6b6480]">(optional)</span>
                  </label>
                  <input
                    id="yq-email"
                    type="email"
                    value={customer.email}
                    onChange={(e) => set('email', e.target.value)}
                    onBlur={() => blur('email')}
                    autoComplete={staff ? 'off' : 'email'}
                    aria-invalid={touched.email && !emailOk}
                    className={FIELD}
                  />
                  {touched.email && !emailOk && (
                    <p className="mt-1 text-[11px] font-medium text-[#9f1239]">
                      That email address doesn&apos;t look right.
                    </p>
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label htmlFor="yq-note" className={LABEL}>
                    {staff ? 'Note on this order' : 'Note for your salesman'}
                  </label>
                  <textarea
                    id="yq-note"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    placeholder="Delivery day, packing, anything else"
                    className={cn(FIELD, 'h-auto resize-none py-2.5 leading-snug')}
                  />
                </div>
              </div>

              {/* Honeypot: invisible to people, irresistible to bots. Must be sent
                  empty. Public only — a logged-in salesman is already proof of life. */}
              {!staff && (
                <div aria-hidden="true" className="pointer-events-none absolute h-0 w-0 overflow-hidden opacity-0">
                  <label htmlFor="yq-website">Website</label>
                  <input
                    id="yq-website"
                    name="website"
                    type="text"
                    tabIndex={-1}
                    autoComplete="off"
                    value={website}
                    onChange={(e) => setWebsite(e.target.value)}
                  />
                </div>
              )}
            </form>
          </>
        )}
      </div>
    </Sheet>
  )
}
