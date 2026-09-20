import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronDown, Lock, MessageCircle, ReceiptText } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ShopApiError } from '@/lib/shopApi'
import { RepCard } from '../components/RepCard'
import { useMarket, useOrder } from '../MarketContext'
import { clientOrderId, deviceId, EMPTY_CUSTOMER, readCustomer, rememberOrder, rememberQty, resetClientOrderId, saveDetailsEnabled, setSaveDetails, writeCustomer, type CustomerDraft } from '../lib/device'
import { track } from '../lib/events'
import { bhd, cleanPhone, isEmail, isPhone, money, productName } from '../lib/format'
import { postMarketOrder, recognizePhone } from '../lib/marketApi'
import { clearSmallAck } from '../lib/smallOrder'
import { PageBar, useHideNav, usePageTitle, useShell } from '../shell/ShellContext'
import { useReducedMotion } from '../shell/useViewport'
import { cartStore, useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Hint, Input, Label, Select } from '../ui/Field'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'

const FORM_ID = 'yq-market-checkout'

/**
 * One screen, no account. Phone · Shop · Name · Area (chips from settings) · Note, prefilled next
 * time; Email under "More"; "Already have a representative?" only when unattributed. The nav
 * hides here — one job. Submitting is idempotent (client_order_id) and never clears the form on
 * an error.
 */
export default function CheckoutPage() {
  const navigate = useNavigate()
  const m = useMarket()
  const { quote, quoting, coupon, setCoupon, note, setNote, refreshMyOrders } = useOrder()
  const { rep, data, itemsByCode, recognized } = m
  const { viewport } = useShell()
  const reduced = useReducedMotion()
  const lines = useCartLines()
  const { items, units } = useCartCounts()
  const [customer, setCustomer] = useState<CustomerDraft>(() => (saveDetailsEnabled() ? readCustomer() : EMPTY_CUSTOMER))
  const [save, setSave] = useState(() => saveDetailsEnabled())
  const [website, setWebsite] = useState('')
  const [more, setMore] = useState(() => Boolean(readCustomer().email))
  const [pickOpen, setPickOpen] = useState(false)
  const [pick, setPick] = useState<number | ''>('')
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  /** set by the first send attempt — the one-line reason under the button appears only after it */
  const [tried, setTried] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [slow, setSlow] = useState(false)
  const [error, setError] = useState('')
  // Only `request` mode turns this checkout into a small order request. In `allow` mode an order
  // under the minimum goes through like any other — the portal and /about promise that — so it
  // keeps the normal wholesale wording; `block` mode never reaches checkout (can_submit is false).
  const minimum = quote?.minimum || null
  const small = Boolean(minimum && !minimum.met && minimum.mode === 'request')
  const smallFee = small ? Number(minimum?.fee_bhd) || 0 : 0
  const pageTitle = small ? S.small.title : S.checkout.title
  usePageTitle(pageTitle, true, `${pageTitle} · ${S.brand}`)
  useHideNav(true)

  useEffect(() => {
    track('checkout_start', { meta: { count: lines.length } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    if (lines.length === 0 && !submitting) navigate('/cart', { replace: true })
  }, [lines.length, submitting, navigate])
  useEffect(() => {
    if (save) writeCustomer(customer)
  }, [customer, save])

  const nameOk = customer.name.trim().length > 1
  const phoneOk = isPhone(customer.phone)
  const emailOk = !customer.email.trim() || isEmail(customer.email)
  const formOk = nameOk && phoneOk && emailOk
  const canSubmit = lines.length > 0 && formOk && !quoting && !submitting && quote?.can_submit !== false
  // Why the send did not go through, once it has been attempted. The button stays solid plum and
  // takes the tap: a `disabled:opacity-50` plum slab is 2.5:1 against its own white label and reads
  // as a broken control, and nothing on the screen says what is missing.
  const invalid = tried && !formOk
  const reason = invalid ? (!phoneOk && !nameOk ? S.checkout.missing : !phoneOk ? S.checkout.missingPhone : !nameOk ? S.checkout.missingName : S.checkout.emailBad) : tried && quoting ? S.cart.updating : ''
  const set = (k: keyof CustomerDraft, v: string) => setCustomer((c) => ({ ...c, [k]: v }))
  const blur = (k: string) => setTouched((t) => ({ ...t, [k]: true }))
  const areas = data?.settings?.areas || []
  const salesmen = data?.salesmen || []
  const first = rep?.first_name || ''
  const desktop = viewport === 'desktop' || viewport === 'wide'
  const areaIsOther = customer.area.trim() !== '' && !areas.includes(customer.area.trim())
  const [otherArea, setOtherArea] = useState(areaIsOther)
  const [deliveryPref, setDeliveryPref] = useState<string | null>(null)
  const [known, setKnown] = useState<{ shop?: string | null; area?: string | null; first_name?: string | null } | null>(null)
  const [askedPhone, setAskedPhone] = useState('')
  // a complete number we have not asked about yet → one recognise call; prefill only what is empty
  const digits = cleanPhone(customer.phone)
  if (digits.length >= 8 && digits !== askedPhone && !recognized) {
    setAskedPhone(digits)
    recognizePhone(digits, deviceId())
      .then((r) => {
        if (!r.known) return
        setKnown(r.known)
        setCustomer((c) => ({ ...c, shop: c.shop || r.known?.shop || '', area: c.area || r.known?.area || '', name: c.name || r.known?.first_name || '' }))
      })
      .catch(() => undefined)
  }

  /** Take the merchant to the field that is holding the send up, instead of greying the button out. */
  const focusMissing = () => {
    const id = !phoneOk ? 'yq-phone' : !nameOk ? 'yq-name' : !emailOk ? 'yq-email' : ''
    if (!id) return
    if (id === 'yq-email') setMore(true)
    window.requestAnimationFrame(() => {
      const el = document.getElementById(id) as HTMLInputElement | null
      el?.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' })
      el?.focus({ preventScroll: true })
    })
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched({ name: true, phone: true, email: true })
    setTried(true)
    if (!canSubmit) {
      focusMissing()
      return
    }
    setSubmitting(true)
    setError('')
    const slowTimer = window.setTimeout(() => setSlow(true), 3000)
    try {
      const res = await postMarketOrder({
        lines,
        coupon_code: coupon || '',
        session_ref: rep?.slug || m.ref || undefined,
        salesman_id: !rep && pick !== '' ? Number(pick) : null,
        customer: { name: customer.name.trim(), phone: cleanPhone(customer.phone), shop: customer.shop.trim(), area: customer.area.trim(), email: customer.email.trim() },
        note: [deliveryPref ? `${S.checkout.deliveryLabel}: ${deliveryPref}` : null, note.trim()].filter(Boolean).join(' · '),
        website,
        device_id: deviceId(),
        client_order_id: clientOrderId(),
      })
      if (res.token) rememberOrder({ token: res.token, order_no: res.order_no, ts: Date.now(), total: res.totals?.total_bhd ?? null })
      for (const l of lines) rememberQty(l.item_code, l.qty)
      resetClientOrderId()
      clearSmallAck()
      cartStore.clear()
      setCoupon('')
      setNote('')
      setSaveDetails(save)
      refreshMyOrders()
      navigate(`/o/${res.token}`, { replace: true, state: { placed: res } })
    } catch (err: unknown) {
      setError(err instanceof ShopApiError ? err.detail || err.message : S.checkout.failed)
      setSubmitting(false)
    } finally {
      window.clearTimeout(slowTimer)
      setSlow(false)
    }
  }

  // The send stays live until the shop itself is blocked (can_submit false, with the reason shown
  // on the Restock page): an incomplete form is answered by moving to the field, not by a washed-out
  // slab. Should it ever be disabled, it goes grey — never a 50 %-opacity plum with white on it.
  const submitBtn = (
    <Button
      type="submit"
      form={FORM_ID}
      size="lg"
      full
      className={cn('shrink-0', !submitting && 'disabled:bg-line-2 disabled:text-ink-3 disabled:opacity-100 disabled:shadow-none')}
      disabled={quote?.can_submit === false || lines.length === 0}
      loading={submitting}
      icon={<Lock size={15} aria-hidden="true" />}
    >
      {submitting ? (slow ? S.checkout.connecting : S.checkout.sending) : small ? S.small.send : S.checkout.place}
    </Button>
  )
  /** the line under the send: why it did not go (after a tap) or the standing reassurance */
  const sendHint = (className: string) => (
    <p className={cn(className, invalid ? 'font-medium text-bad' : 'text-ink-2')}>{reason || (small ? S.small.sendHint : first ? S.checkout.reassure(first) : S.checkout.noPayment)}</p>
  )

  return (
    <div className="px-gutter lg:px-0">
      <div className="mx-auto max-w-xl lg:mx-auto lg:grid lg:max-w-6xl lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start lg:gap-8">
        <div>
          <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{pageTitle}</h1>
          {/* the order, folded */}
          <Link to="/cart" className="mt-1 flex items-center justify-between gap-3 rounded-md border border-line bg-surface px-3.5 py-2.5 text-sm hover:bg-plum-wash lg:mt-4">
            <span className="text-ink-2">{S.cart.summary(items, units)}</span>
            <span className="font-display font-bold tnum text-ink">{bhd(quote?.total_bhd)}</span>
            <span className="font-semibold text-plum">{S.cart.edit}</span>
          </Link>
          {small && minimum && (
            <section aria-label={S.small.title} className="mt-3 rounded-lg border border-plum/15 bg-plum-wash p-4">
              <div className="flex items-start gap-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface text-plum shadow-1 ring-1 ring-inset ring-plum/10" aria-hidden="true">
                  <MessageCircle size={17} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold leading-snug text-plum-ink">{S.small.body(bhd(minimum.value_bhd))}</p>
                  {smallFee > 0 && (
                    <p className="mt-1.5 flex items-start gap-1.5 text-xs leading-snug text-ink-2">
                      <ReceiptText size={13} className="mt-px shrink-0 text-deal-ink" aria-hidden="true" />
                      {S.small.fee(bhd(smallFee))}
                    </p>
                  )}
                  <Link to="/cart" className="-ms-1 mt-1.5 inline-flex h-10 items-center rounded-sm px-1 text-sm font-semibold text-plum hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                    {S.small.keep}
                  </Link>
                </div>
              </div>
            </section>
          )}
          {known?.shop && (
            <p className="mt-3 rounded-md bg-plum-wash px-3.5 py-2.5 text-sm text-plum-ink">
              <b className="font-semibold">{S.checkout.welcomeBack(known.shop)}</b> · {S.checkout.welcomeBackHint}
            </p>
          )}
          {rep && <RepCard rep={rep} compact className="mt-3 lg:hidden" />}

          <form id={FORM_ID} onSubmit={submit} noValidate className="mt-4 grid gap-4 lg:grid-cols-2 lg:gap-x-5">
            <div>
              <Label htmlFor="yq-phone">
                {S.checkout.phone} <span className="text-bad">*</span>
              </Label>
              <Input id="yq-phone" type="tel" inputMode="tel" autoComplete="tel" value={customer.phone} onChange={(e) => set('phone', e.target.value)} onBlur={() => blur('phone')} placeholder="33001122" required aria-invalid={touched.phone && !phoneOk} aria-describedby="yq-phone-hint" />
              <Hint id="yq-phone-hint" error={touched.phone && !phoneOk}>
                {touched.phone && !phoneOk ? S.checkout.phoneBad : S.checkout.phoneHint}
              </Hint>
            </div>
            <div>
              <Label htmlFor="yq-shop">{S.checkout.shop}</Label>
              <Input id="yq-shop" autoComplete="organization" value={customer.shop} onChange={(e) => set('shop', e.target.value)} />
            </div>
            <div>
              <Label htmlFor="yq-name">
                {S.checkout.name} <span className="text-bad">*</span>
              </Label>
              <Input id="yq-name" autoComplete="name" value={customer.name} onChange={(e) => set('name', e.target.value)} onBlur={() => blur('name')} required aria-invalid={touched.name && !nameOk} />
              {touched.name && !nameOk && <Hint error>{S.checkout.nameBad}</Hint>}
            </div>
            <div className="lg:col-span-2">
              <Label htmlFor="yq-area">{S.checkout.area}</Label>
              {areas.length > 0 ? (
                <>
                  <div className="flex flex-wrap gap-1.5" role="group" aria-label={S.checkout.area}>
                    {areas.map((a) => {
                      const on = !otherArea && customer.area.trim() === a
                      return (
                        <button
                          key={a}
                          type="button"
                          aria-pressed={on}
                          onClick={() => {
                            setOtherArea(false)
                            set('area', a)
                          }}
                          className={cn('h-10 rounded-full border px-3.5 text-sm font-medium transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', on ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink-2 hover:bg-plum-wash')}
                        >
                          {a}
                        </button>
                      )
                    })}
                    <button
                      type="button"
                      aria-pressed={otherArea}
                      onClick={() => {
                        setOtherArea(true)
                        if (areas.includes(customer.area.trim())) set('area', '')
                      }}
                      className={cn('h-10 rounded-full border px-3.5 text-sm font-medium transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', otherArea ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink-2 hover:bg-plum-wash')}
                    >
                      {S.checkout.other}
                    </button>
                  </div>
                  {otherArea && <Input id="yq-area" autoComplete="address-level2" value={customer.area} onChange={(e) => set('area', e.target.value)} placeholder={S.checkout.areaPlaceholder} className="mt-2" autoFocus />}
                </>
              ) : (
                <Input id="yq-area" autoComplete="address-level2" value={customer.area} onChange={(e) => set('area', e.target.value)} placeholder={S.checkout.areaPlaceholder} />
              )}
            </div>

            <div className="lg:col-span-2">
              <Label>{S.checkout.deliveryLabel}</Label>
              <div className="flex flex-wrap gap-1.5" role="group" aria-label={S.checkout.deliveryLabel}>
                {S.checkout.deliveryOptions.map((o) => {
                  const on = deliveryPref === o
                  return (
                    <button key={o} type="button" aria-pressed={on} onClick={() => setDeliveryPref(on ? null : o)} className={cn('h-10 rounded-full border px-3.5 text-sm font-medium transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', on ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink-2 hover:bg-plum-wash')}>
                      {o}
                    </button>
                  )
                })}
              </div>
              <Hint>{S.checkout.deliveryHint}</Hint>
            </div>

            {/* the three small controls that close the form: one bordered block of 48 px rows, the
                same card language as the rest of the page — loose 18 px links read as debug UI */}
            <div className="divide-y divide-line-2 rounded-md border border-line bg-surface lg:col-span-2">
              <label className="flex h-12 w-full cursor-pointer items-center gap-2.5 px-3.5 text-sm text-ink">
                <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} className="h-5 w-5 shrink-0 rounded border-line accent-[#6D4091]" />
                <span className="min-w-0 flex-1 truncate">{S.checkout.save}</span>
              </label>

              {!more ? (
                <button type="button" onClick={() => setMore(true)} aria-expanded={false} className="flex h-12 w-full items-center justify-between gap-3 px-3.5 text-sm font-semibold text-plum transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70">
                  {S.checkout.more} <ChevronDown size={16} aria-hidden="true" />
                </button>
              ) : (
                <div className="px-3.5 py-3">
                  <Label htmlFor="yq-email">{S.checkout.email}</Label>
                  <Input id="yq-email" type="email" autoComplete="email" value={customer.email} onChange={(e) => set('email', e.target.value)} onBlur={() => blur('email')} aria-invalid={touched.email && !emailOk} />
                  {touched.email && !emailOk && <Hint error>{S.checkout.emailBad}</Hint>}
                </div>
              )}

              {!rep &&
                salesmen.length > 0 &&
                (!pickOpen ? (
                  <button type="button" onClick={() => setPickOpen(true)} aria-expanded={false} className="flex h-12 w-full items-center justify-between gap-3 px-3.5 text-start text-sm transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70">
                    <span className="min-w-0 truncate text-ink-2">{S.checkout.haveRep}</span>
                    <span className="shrink-0 font-semibold text-plum">{S.checkout.chooseRep}</span>
                  </button>
                ) : (
                  <div className="px-3.5 py-3">
                    <Label htmlFor="yq-rep">{S.checkout.haveRep}</Label>
                    <Select id="yq-rep" value={pick} onChange={(e) => setPick(e.target.value === '' ? '' : Number(e.target.value))}>
                      <option value="">{S.checkout.noRep}</option>
                      {salesmen.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </Select>
                  </div>
                ))}
            </div>

            <div aria-hidden="true" className="pointer-events-none absolute h-0 w-0 overflow-hidden opacity-0">
              <label htmlFor="yq-website">Website</label>
              <input id="yq-website" name="website" type="text" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
            </div>
          </form>
        </div>

        {/* --m-sticky-h is the MEASURED sticky header (brand row + any category strip); the plain
            header height is the fallback for a page the shell has not measured */}
        <div className="hidden lg:sticky lg:top-[calc(var(--m-sticky-h,var(--m-header-h))+16px)] lg:block">
          <div className="rounded-lg border border-line bg-surface p-4">
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-ink-2">{S.cart.summary(items, units)}</span>
              <span className="font-display text-xl font-extrabold tnum text-ink">{bhd(quote?.total_bhd)}</span>
            </div>
            {error && (
              <p role="alert" className="mt-3 rounded-sm bg-bad-soft px-3 py-2 text-xs font-medium text-bad">
                {error}
              </p>
            )}
            <div className="mt-4">{submitBtn}</div>
            {sendHint('mt-2 text-xs')}
          </div>

          {/* the lines, so the merchant sees what they are confirming */}
          <div className="mt-4 rounded-lg border border-line bg-surface">
            <div className="flex items-center justify-between px-4 pt-3">
              <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.cart.title}</h2>
              <Link to="/cart" className="text-xs font-semibold text-plum hover:underline">
                {S.cart.edit}
              </Link>
            </div>
            <ul className="divide-y divide-line-2 px-4">
              {lines.slice(0, 5).map((l) => {
                const it = itemsByCode.get(l.item_code)
                const q = quote?.lines?.find((x) => x.item_code === l.item_code)
                return (
                  <li key={l.item_code} className="flex items-center gap-3 py-2.5">
                    <span className="h-10 w-10 shrink-0 overflow-hidden rounded-sm border border-line-2 bg-white">
                      <ProductImage item={it} alt="" sizes={SIZES_THUMB} size={40} imgClassName="p-0.5" iconSize={14} showCaption={false} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-ink">{it ? productName(it) : l.item_code}</span>
                      <span className="block text-xs tnum text-ink-2">
                        {l.qty} × {money(q?.unit_price_bhd ?? it?.price_bhd)}
                      </span>
                    </span>
                    <span className="text-sm font-semibold tnum text-ink">{bhd(q?.line_total_bhd ?? (Number(it?.price_bhd) || 0) * l.qty)}</span>
                  </li>
                )
              })}
            </ul>
            {lines.length > 5 && (
              <Link to="/cart" className="block border-t border-line-2 px-4 py-2.5 text-center text-xs font-semibold text-plum hover:underline">
                {S.checkout.moreLines(lines.length - 5)}
              </Link>
            )}
          </div>

          {/* how it works */}
          <div className="mt-4 rounded-lg border border-line bg-surface p-4">
            <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.checkout.how}</h2>
            <ol className="mt-2 space-y-2">
              {S.placed.next.map((step, i) => (
                <li key={step} className="flex items-start gap-2.5 text-sm text-ink-2">
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-plum-wash text-2xs font-bold text-plum">{i + 1}</span>
                  {step}
                </li>
              ))}
            </ol>
          </div>
          {rep && <RepCard rep={rep} compact className="mt-4" />}
        </div>
      </div>

      {!desktop && (
        <PageBar>
          {error && (
            <p role="alert" className="mb-2 rounded-sm bg-bad-soft px-3 py-2 text-xs font-medium text-bad">
              {error}
            </p>
          )}
          {/* stacked, not squeezed: "Place wholesale order" + the lock take ~330 px of a 390 px bar,
              and the money column was the one that gave way (BHD / 21.000 on two lines at 390) */}
          <div className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate text-xs text-ink-2">{S.cart.summary(items, units)}</span>
            <span className="shrink-0 whitespace-nowrap font-display text-xl font-extrabold leading-tight tnum text-ink">{bhd(quote?.total_bhd)}</span>
          </div>
          <div className="mt-2">{submitBtn}</div>
          {sendHint('mt-1.5 text-2xs')}
        </PageBar>
      )}
    </div>
  )
}
