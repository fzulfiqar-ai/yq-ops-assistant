import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronDown, Lock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ShopApiError } from '@/lib/shopApi'
import { RepCard } from '../components/RepCard'
import { useMarket, useOrder } from '../MarketContext'
import { clientOrderId, deviceId, EMPTY_CUSTOMER, readCustomer, rememberOrder, rememberQty, resetClientOrderId, saveDetailsEnabled, setSaveDetails, writeCustomer, type CustomerDraft } from '../lib/device'
import { track } from '../lib/events'
import { bhd, cleanPhone, isEmail, isPhone } from '../lib/format'
import { postMarketOrder } from '../lib/marketApi'
import { PageBar, useHideNav, usePageTitle, useShell } from '../shell/ShellContext'
import { cartStore, useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Hint, Input, Label, Select } from '../ui/Field'

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
  const { rep, data } = m
  const { viewport } = useShell()
  const lines = useCartLines()
  const { items, units } = useCartCounts()
  const [customer, setCustomer] = useState<CustomerDraft>(() => (saveDetailsEnabled() ? readCustomer() : EMPTY_CUSTOMER))
  const [save, setSave] = useState(() => saveDetailsEnabled())
  const [website, setWebsite] = useState('')
  const [more, setMore] = useState(() => Boolean(readCustomer().email))
  const [pickOpen, setPickOpen] = useState(false)
  const [pick, setPick] = useState<number | ''>('')
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [submitting, setSubmitting] = useState(false)
  const [slow, setSlow] = useState(false)
  const [error, setError] = useState('')
  usePageTitle(S.checkout.title, true, `${S.checkout.title} · ${S.brand}`)
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
  const canSubmit = lines.length > 0 && nameOk && phoneOk && emailOk && !quoting && !submitting && quote?.can_submit !== false
  const set = (k: keyof CustomerDraft, v: string) => setCustomer((c) => ({ ...c, [k]: v }))
  const blur = (k: string) => setTouched((t) => ({ ...t, [k]: true }))
  const areas = data?.settings?.areas || []
  const salesmen = data?.salesmen || []
  const first = rep?.first_name || ''
  const desktop = viewport === 'desktop' || viewport === 'wide'
  const areaIsOther = customer.area.trim() !== '' && !areas.includes(customer.area.trim())
  const [otherArea, setOtherArea] = useState(areaIsOther)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched({ name: true, phone: true, email: true })
    if (!canSubmit) return
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
        note: note.trim(),
        website,
        device_id: deviceId(),
        client_order_id: clientOrderId(),
      })
      if (res.token) rememberOrder({ token: res.token, order_no: res.order_no, ts: Date.now(), total: res.totals?.total_bhd ?? null })
      for (const l of lines) rememberQty(l.item_code, l.qty)
      resetClientOrderId()
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

  const submitBtn = (
    <Button type="submit" form={FORM_ID} size="lg" className={cn('shrink-0', desktop ? 'w-full' : 'px-6')} disabled={!canSubmit} loading={submitting} icon={<Lock size={15} aria-hidden="true" />}>
      {submitting ? (slow ? S.checkout.connecting : S.checkout.sending) : S.checkout.place}
    </Button>
  )

  return (
    <div className="px-gutter lg:px-0">
      <div className="mx-auto max-w-xl lg:mx-0 lg:grid lg:max-w-none lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start lg:gap-6">
        <div>
          <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.checkout.title}</h1>
          {/* the order, folded */}
          <Link to="/cart" className="mt-1 flex items-center justify-between gap-3 rounded-md border border-line bg-surface px-3.5 py-2.5 text-sm hover:bg-plum-wash lg:mt-4">
            <span className="text-ink-2">{S.cart.summary(items, units)}</span>
            <span className="font-display font-bold tnum text-ink">{bhd(quote?.total_bhd)}</span>
            <span className="font-semibold text-plum">{S.cart.edit}</span>
          </Link>
          {rep && <RepCard rep={rep} compact className="mt-3" />}

          <form id={FORM_ID} onSubmit={submit} noValidate className="mt-4 grid gap-4">
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
            <div>
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

            <label className="flex items-center gap-2.5 text-sm text-ink">
              <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} className="h-5 w-5 rounded border-line accent-[#6D4091]" />
              {S.checkout.save}
            </label>

            {!more ? (
              <button type="button" onClick={() => setMore(true)} className="inline-flex items-center gap-1 self-start text-sm font-semibold text-plum hover:underline">
                {S.checkout.more} <ChevronDown size={14} aria-hidden="true" />
              </button>
            ) : (
              <div>
                <Label htmlFor="yq-email">{S.checkout.email}</Label>
                <Input id="yq-email" type="email" autoComplete="email" value={customer.email} onChange={(e) => set('email', e.target.value)} onBlur={() => blur('email')} aria-invalid={touched.email && !emailOk} />
                {touched.email && !emailOk && <Hint error>{S.checkout.emailBad}</Hint>}
              </div>
            )}

            {!rep && salesmen.length > 0 && (
              <div>
                {!pickOpen ? (
                  <button type="button" onClick={() => setPickOpen(true)} className="text-sm font-semibold text-plum hover:underline">
                    {S.checkout.haveRep} {S.checkout.chooseRep}
                  </button>
                ) : (
                  <>
                    <Label htmlFor="yq-rep">{S.checkout.haveRep}</Label>
                    <Select id="yq-rep" value={pick} onChange={(e) => setPick(e.target.value === '' ? '' : Number(e.target.value))}>
                      <option value="">{S.checkout.noRep}</option>
                      {salesmen.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </Select>
                  </>
                )}
              </div>
            )}

            <div aria-hidden="true" className="pointer-events-none absolute h-0 w-0 overflow-hidden opacity-0">
              <label htmlFor="yq-website">Website</label>
              <input id="yq-website" name="website" type="text" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
            </div>
          </form>
        </div>

        <div className="hidden lg:sticky lg:top-[calc(var(--m-header-h)+16px)] lg:block">
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
            <p className="mt-2 text-xs text-ink-2">{first ? S.checkout.reassure(first) : S.checkout.noPayment}</p>
          </div>
        </div>
      </div>

      {!desktop && (
        <PageBar>
          {error && (
            <p role="alert" className="mb-2 rounded-sm bg-bad-soft px-3 py-2 text-xs font-medium text-bad">
              {error}
            </p>
          )}
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-xs text-ink-2">{S.cart.summary(items, units)}</div>
              <div className="font-display text-xl font-extrabold leading-tight tnum text-ink">{bhd(quote?.total_bhd)}</div>
            </div>
            {submitBtn}
          </div>
          <p className="mt-1.5 text-2xs text-ink-2">{first ? S.checkout.reassure(first) : S.checkout.noPayment}</p>
        </PageBar>
      )}
    </div>
  )
}
