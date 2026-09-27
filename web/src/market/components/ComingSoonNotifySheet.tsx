import { useState, type FormEvent } from 'react'
import { Check, MessageCircle } from 'lucide-react'
import type { UpcomingItem } from '../lib/marketApi'
import { postUpcomingInterest } from '../lib/marketApi'
import { currentRef, deviceId, readCustomer } from '../lib/device'
import { cleanPhone, isPhone } from '../lib/format'
import { useMarket } from '../MarketContext'
import { AnchorButton, Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { Hint, Input, Label } from '../ui/Field'
import { Sheet } from '../ui/Sheet'
import { useToast } from '../ui/Toast'
import { askRepUrl, rememberNotified, upcomingCopy, upcomingName, upcomingSpec, variantLabel } from './ComingSoonShared'

/**
 * "Notify me when it lands" — the sold-out restock request, for a card that has no stock yet.
 * The phone is REQUIRED (prefilled from this phone's saved details) and validated the way
 * checkout validates it — the sheet promises "your representative messages you", which needs a
 * number to message. An OPTIONAL quantity labelled "no commitment" tells the rep how much
 * interest there is; it never becomes an order. One request per device and card; the rep sees
 * it on his Today screen under "Shops interested from your link".
 *
 * The card keeps one action, so what it used to carry lives here: the full supplier spec (muted),
 * a variant picker ("Any" by default) when the model comes in two or more, and — under "Notify
 * me" — "Ask your rep" on WhatsApp, prefilled with the model and the picked variant, when the shop
 * came through a rep. The picked variant only names the model in the message and the subtitle;
 * the notify POST is unchanged (card, phone, quantity, device, ref).
 *
 * `done` (this phone already asked): the card's "We will tell you" still opens the sheet when the
 * shop has a rep, so the per-model WhatsApp ask stays one tap away — the form is replaced by the
 * confirmation and nothing can be sent twice.
 */
export function ComingSoonNotifySheet({ item, open, done = false, onClose, onDone }: { item: UpcomingItem; open: boolean; done?: boolean; onClose: () => void; onDone: () => void }) {
  const t = upcomingCopy()
  const toast = useToast()
  const { rep, ref } = useMarket()
  const [phone, setPhone] = useState(() => readCustomer().phone || '')
  const [touched, setTouched] = useState(false)
  const [qty, setQty] = useState('')
  const [busy, setBusy] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const name = upcomingName(item)
  const spec = upcomingSpec(item)
  const variants = item.variants.map(variantLabel)
  const askUrl = askRepUrl(rep, item, picked)
  const phoneOk = isPhone(phone)
  const phoneBad = touched && !phoneOk

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy || done) return
    if (!phoneOk) {
      setTouched(true)
      return
    }
    setBusy(true)
    const n = Math.floor(Number(qty))
    try {
      const r = await postUpcomingInterest({
        upcoming_id: item.id,
        phone: cleanPhone(phone),
        qty_interest: Number.isFinite(n) && n > 0 ? n : null,
        device_id: deviceId(),
        // the session's ref, else the rep link this phone remembers (a first visit's ?ref=)
        ref: ref || rep?.slug || currentRef() || null,
      })
      if (!r.ok) throw new Error('not saved')
      rememberNotified(item.id)
      toast(t.sent, 'success')
      onDone()
      onClose()
    } catch (err) {
      // the server's own phone check (400) — the same rule as here, for a bypassed form
      const status = (err as { status?: number } | null)?.status
      toast(status === 400 ? t.phoneBad : t.failed, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={t.notifyTitle(item.model_code)}
      subtitle={picked ? `${name} · ${picked}` : name}
      footer={
        <div className="flex flex-col gap-2">
          {!done && (
            <Button type="submit" form="upcoming-notify" full size="lg" loading={busy}>
              {t.send}
            </Button>
          )}
          {askUrl && (
            <AnchorButton href={askUrl} target="_blank" rel="noreferrer" variant="wa" size="lg" full icon={<MessageCircle size={16} aria-hidden="true" />}>
              {t.askRange(rep?.first_name || '')}
            </AnchorButton>
          )}
        </div>
      }
    >
      <form id="upcoming-notify" onSubmit={submit} noValidate className="space-y-4 px-4 py-4 md:px-5">
        {spec && <p className="text-xs leading-4 text-ink-3">{spec}</p>}
        {variants.length >= 2 && (
          <div>
            <p id="upcoming-variant" className="mb-2 text-sm font-semibold text-ink">
              {t.variant}
            </p>
            <div role="group" aria-labelledby="upcoming-variant" className="flex flex-wrap gap-x-1.5 gap-y-2.5">
              {[null, ...variants].map((v) => {
                const on = picked === v
                return (
                  // a 24 px chip; `hit` gives it a 44 px tap area on touch without changing the chip
                  <button key={v ?? ''} type="button" aria-pressed={on} onClick={() => setPicked(v)} className="hit relative max-w-full rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                    <Chip tone={on ? 'ink' : 'spec'} size="md">
                      {v ?? t.variantAny}
                    </Chip>
                  </button>
                )
              })}
            </div>
          </div>
        )}
        {done ? (
          <p className="flex items-start gap-2 rounded-md border border-ok/30 bg-ok-soft px-3 py-2.5 text-sm font-semibold leading-snug text-ok">
            <Check size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
            {t.notified}
          </p>
        ) : (
          <>
            <p className="text-sm leading-snug text-ink-2">{t.notifyLine}</p>
            <div>
              <Label htmlFor="upcoming-phone" hint={t.phoneHint}>
                {t.phone}
              </Label>
              <Input
                id="upcoming-phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                onBlur={() => setTouched(true)}
                maxLength={32}
                placeholder="33001122"
                required
                aria-invalid={phoneBad}
                aria-describedby={phoneBad ? 'upcoming-phone-hint' : undefined}
              />
              {phoneBad && (
                <Hint id="upcoming-phone-hint" error>
                  {t.phoneBad}
                </Hint>
              )}
            </div>
            <div>
              {/* the hint once, beside the label (the phone field's pattern) — not again under the input */}
              <Label htmlFor="upcoming-qty" hint={t.qtyHint}>
                {t.qty}
              </Label>
              <Input id="upcoming-qty" type="number" inputMode="numeric" min={1} max={9999} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
            </div>
          </>
        )}
      </form>
    </Sheet>
  )
}
