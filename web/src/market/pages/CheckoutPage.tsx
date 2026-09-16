import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, ShieldCheck } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ShopApiError } from '@/lib/shopApi'
import { Select } from '@/pages/shop/Select'
import { bhd, cleanPhone, FIELD, isEmail, isPhone, LABEL, RING } from '@/pages/shop/shared'
import { useMarket } from '../MarketContext'
import { BTN_PRIMARY, RepBanner } from '../components/Bits'
import { Page, TopBar } from '../components/Chrome'
import { clientOrderId, deviceId, EMPTY_CUSTOMER, readCustomer, rememberOrder, rememberQty, resetClientOrderId, saveDetailsEnabled, setSaveDetails, writeCustomer, type CustomerDraft } from '../lib/device'
import { track } from '../lib/events'
import { postMarketOrder } from '../lib/marketApi'
import { S } from '../strings'

const FORM_ID = 'yq-market-checkout'

/** One screen, no account (plan §K). Phone, shop, name, area; prefilled next time; no salesman step. */
export default function CheckoutPage() {
  const navigate = useNavigate()
  const m = useMarket()
  const { cart, quote, quoting, rep, data, note, setNote, coupon, setCoupon, refreshMyOrders } = m
  const [customer, setCustomer] = useState<CustomerDraft>(() => (saveDetailsEnabled() ? readCustomer() : EMPTY_CUSTOMER))
  const [save, setSave] = useState(() => saveDetailsEnabled())
  const [website, setWebsite] = useState('')
  const [pickOpen, setPickOpen] = useState(false)
  const [pick, setPick] = useState<number | ''>('')
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [submitting, setSubmitting] = useState(false)
  const [slow, setSlow] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    document.title = `${S.checkout.title} · ${S.brand}`
    track('checkout_start', { meta: { count: cart.lines.length } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (cart.lines.length === 0 && !submitting) navigate('/cart', { replace: true })
  }, [cart.lines.length, submitting, navigate])

  useEffect(() => {
    if (save) writeCustomer(customer)
  }, [customer, save])

  const nameOk = customer.name.trim().length > 1
  const phoneOk = isPhone(customer.phone)
  const emailOk = !customer.email.trim() || isEmail(customer.email)
  const canSubmit = cart.lines.length > 0 && nameOk && phoneOk && emailOk && !quoting && !submitting && quote?.can_submit !== false
  const set = (k: keyof CustomerDraft, v: string) => setCustomer((c) => ({ ...c, [k]: v }))
  const blur = (k: string) => setTouched((t) => ({ ...t, [k]: true }))
  const areas = data?.settings?.areas || []
  const salesmen = data?.salesmen || []
  const first = rep?.first_name || ''

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched({ name: true, phone: true, email: true })
    if (!canSubmit) return
    setSubmitting(true)
    setError('')
    const slowTimer = window.setTimeout(() => setSlow(true), 3000)
    try {
      const res = await postMarketOrder({
        lines: cart.lines,
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
      for (const l of cart.lines) rememberQty(l.item_code, l.qty)
      resetClientOrderId()
      cart.clear()
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

  return (
    <Page className="pb-44">
      <TopBar back title={S.checkout.title} />
      <main className="mx-auto max-w-xl px-4 pt-2">
        {rep && <RepBanner rep={rep} compact />}
        <form id={FORM_ID} onSubmit={submit} noValidate className="mt-4 grid gap-4">
          <div>
            <label htmlFor="yq-phone" className={LABEL}>{S.checkout.phone} <span className="text-[#9f1239]">*</span></label>
            <input id="yq-phone" type="tel" inputMode="tel" autoComplete="tel" value={customer.phone} onChange={(e) => set('phone', e.target.value)} onBlur={() => blur('phone')} placeholder="33001122" required aria-invalid={touched.phone && !phoneOk} aria-describedby="yq-phone-hint" className={cn(FIELD, 'h-12 text-[16px]')} />
            <p id="yq-phone-hint" className={cn('mt-1 text-[12px]', touched.phone && !phoneOk ? 'font-medium text-[#9f1239]' : 'text-[#6b6480]')}>{touched.phone && !phoneOk ? S.checkout.phoneBad : S.checkout.phoneHint}</p>
          </div>
          <div>
            <label htmlFor="yq-shop" className={LABEL}>{S.checkout.shop}</label>
            <input id="yq-shop" autoComplete="organization" value={customer.shop} onChange={(e) => set('shop', e.target.value)} className={cn(FIELD, 'h-12 text-[16px]')} />
          </div>
          <div>
            <label htmlFor="yq-name" className={LABEL}>{S.checkout.name} <span className="text-[#9f1239]">*</span></label>
            <input id="yq-name" autoComplete="name" value={customer.name} onChange={(e) => set('name', e.target.value)} onBlur={() => blur('name')} required aria-invalid={touched.name && !nameOk} className={cn(FIELD, 'h-12 text-[16px]')} />
            {touched.name && !nameOk && <p className="mt-1 text-[12px] font-medium text-[#9f1239]">{S.checkout.nameBad}</p>}
          </div>
          <div>
            <label htmlFor="yq-area" className={LABEL}>{S.checkout.area}</label>
            <input id="yq-area" list="yq-areas" autoComplete="address-level2" value={customer.area} onChange={(e) => set('area', e.target.value)} placeholder={S.checkout.areaPlaceholder} className={cn(FIELD, 'h-12 text-[16px]')} />
            {areas.length > 0 && <datalist id="yq-areas">{areas.map((a) => <option key={a} value={a} />)}</datalist>}
          </div>
          <div>
            <label htmlFor="yq-email" className={LABEL}>{S.checkout.email}</label>
            <input id="yq-email" type="email" autoComplete="email" value={customer.email} onChange={(e) => set('email', e.target.value)} onBlur={() => blur('email')} aria-invalid={touched.email && !emailOk} className={cn(FIELD, 'h-12 text-[16px]')} />
            {touched.email && !emailOk && <p className="mt-1 text-[12px] font-medium text-[#9f1239]">{S.checkout.emailBad}</p>}
          </div>

          <label className="flex items-center gap-2.5 text-[13px] text-[#1a1430]">
            <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} className="h-5 w-5 rounded border-[#d9d2ee] accent-[#6d28d9]" />
            {S.checkout.save}
          </label>

          {!rep && salesmen.length > 0 && (
            <div>
              {!pickOpen ? (
                <button type="button" onClick={() => setPickOpen(true)} className={cn('text-[12.5px] font-semibold text-[#6d28d9] underline-offset-2 hover:underline', RING)}>{S.checkout.haveRep} {S.checkout.chooseRep}</button>
              ) : (
                <>
                  <label htmlFor="yq-rep" className={LABEL}>{S.checkout.haveRep}</label>
                  <Select id="yq-rep" value={pick} onChange={(e) => setPick(e.target.value === '' ? '' : Number(e.target.value))} className="h-12 text-[16px]">
                    <option value="">{S.checkout.noRep}</option>
                    {salesmen.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
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
      </main>

      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-[#ece9f3] bg-white/95 px-4 pt-3 backdrop-blur-md" style={{ paddingBottom: 'max(0.875rem, env(safe-area-inset-bottom))' }}>
        <div className="mx-auto max-w-xl">
          {error && <p role="alert" className="mb-2 rounded-xl bg-[#fdecef] px-3 py-2 text-[12px] font-medium text-[#9f1239]">{error}</p>}
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-[12px] text-[#6b6480]">{cart.items} {cart.items === 1 ? 'product' : 'products'} · {cart.units} pcs</div>
              <div className="font-display text-[19px] font-extrabold leading-tight tabular-nums text-[#1a1430]">{bhd(quote?.total_bhd)}</div>
            </div>
            <button type="submit" form={FORM_ID} disabled={!canSubmit} className={cn(BTN_PRIMARY, 'shrink-0 px-6')}>
              {submitting ? (<><Loader2 size={16} className="animate-spin" aria-hidden="true" /> {slow ? S.checkout.connecting : S.checkout.sending}</>) : S.checkout.place}
            </button>
          </div>
          <p className="mt-2 flex items-center gap-1.5 text-[11px] text-[#6b6480]"><ShieldCheck size={12} aria-hidden="true" /> {first ? S.cart.placeHint(first) : S.checkout.noPayment}</p>
        </div>
      </div>
    </Page>
  )
}
