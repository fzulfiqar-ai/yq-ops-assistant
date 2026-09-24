import { useState, type FormEvent } from 'react'
import type { UpcomingItem } from '../lib/marketApi'
import { postUpcomingInterest } from '../lib/marketApi'
import { deviceId, readCustomer } from '../lib/device'
import { useMarket } from '../MarketContext'
import { Button } from '../ui/Button'
import { Hint, Input, Label } from '../ui/Field'
import { Sheet } from '../ui/Sheet'
import { useToast } from '../ui/Toast'
import { rememberNotified, upcomingCopy, upcomingName } from './ComingSoonShared'

/**
 * "Notify me when it lands" — the sold-out restock request, for a card that has no stock yet.
 * Phone (prefilled from this phone's saved details) so the rep can reach the shop, and an
 * OPTIONAL quantity labelled "no commitment": it tells the rep how much interest there is, it
 * never becomes an order. One request per device and card; the rep sees it on his Today screen
 * under "Shops interested from your link".
 */
export function ComingSoonNotifySheet({ item, variant, open, onClose, onDone }: { item: UpcomingItem; variant: string | null; open: boolean; onClose: () => void; onDone: () => void }) {
  const t = upcomingCopy()
  const toast = useToast()
  const { rep, ref } = useMarket()
  const [phone, setPhone] = useState(() => readCustomer().phone || '')
  const [qty, setQty] = useState('')
  const [busy, setBusy] = useState(false)
  const name = upcomingName(item)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    const n = Math.floor(Number(qty))
    try {
      const r = await postUpcomingInterest({
        upcoming_id: item.id,
        phone: phone.trim() || null,
        qty_interest: Number.isFinite(n) && n > 0 ? n : null,
        device_id: deviceId(),
        ref: ref || rep?.slug || null,
      })
      if (!r.ok) throw new Error('not saved')
      rememberNotified(item.id)
      toast(t.sent, 'success')
      onDone()
      onClose()
    } catch {
      toast(t.failed, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={t.notifyTitle(item.model_code)}
      subtitle={variant ? `${name} · ${variant}` : name}
      footer={
        <Button type="submit" form="upcoming-notify" full size="lg" loading={busy}>
          {t.send}
        </Button>
      }
    >
      <form id="upcoming-notify" onSubmit={submit} className="space-y-4 px-4 py-4 md:px-5">
        <p className="text-sm leading-snug text-ink-2">{t.notifyLine}</p>
        <div>
          <Label htmlFor="upcoming-phone" hint={t.phoneHint}>
            {t.phone}
          </Label>
          <Input id="upcoming-phone" type="tel" inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={32} placeholder="3xxx xxxx" />
        </div>
        <div>
          <Label htmlFor="upcoming-qty" hint={t.qtyHint}>
            {t.qty}
          </Label>
          <Input id="upcoming-qty" type="number" inputMode="numeric" min={1} max={9999} step={1} value={qty} onChange={(e) => setQty(e.target.value)} aria-describedby="upcoming-qty-hint" />
          <Hint id="upcoming-qty-hint">{t.qtyHint}</Hint>
        </div>
      </form>
    </Sheet>
  )
}
