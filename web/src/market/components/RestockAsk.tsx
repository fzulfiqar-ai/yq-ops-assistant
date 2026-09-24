import { useCallback, useState, type ReactNode } from 'react'
import { Check, MessageCircle } from 'lucide-react'
import type { RepCard, ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { deviceId, readCustomer, saveDetailsEnabled, writeCustomer } from '../lib/device'
import { cleanPhone, isPhone, productName } from '../lib/format'
import { postRestock } from '../lib/marketApi'
import { S } from '../strings'
import { AnchorButton, Button } from '../ui/Button'
import { Hint, Input, Label } from '../ui/Field'
import { Sheet } from '../ui/Sheet'
import { useToast } from '../ui/Toast'

/**
 * "Tell me when back" — the one action on a sold-out line once the shop takes no backorder.
 * It is a tracked restock request (postRestock → the rep's portal list), and it is never sent
 * without a way to reach the shop: with a phone this device already keeps (checkout details) it
 * posts at once; without one, a small sheet asks for the number first. A request that carried only
 * a device id was a dead end the rep could not act on, under a toast that promised a call.
 *
 * The rep on WhatsApp stays the secondary route wherever the storefront knows him
 * (m.rep.whatsapp_url): a "Tell {rep}" action on the toast, and a second button on the sheet.
 *
 * One hook for the card, the panel and the Quick-order rows, so the three cannot drift.
 */

/** The rep's WhatsApp link with the restock message prefilled, or null when the storefront has no rep. */
// eslint-disable-next-line react-refresh/only-export-components
export function tellRepUrl(rep: RepCard | null | undefined, item: ShopItem): string | null {
  if (!rep?.whatsapp_url) return null
  return `${rep.whatsapp_url.split('?text=')[0]}?text=${encodeURIComponent(S.card.tellBackText(rep.first_name || '', item.item_code, productName(item)))}`
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTellBack(item: ShopItem | null): { asked: boolean; ask: () => void; sheet: ReactNode } {
  const m = useMarket()
  const toast = useToast()
  const [asked, setAsked] = useState(false)
  const [open, setOpen] = useState(false)
  const waUrl = item ? tellRepUrl(m.rep, item) : null
  const first = m.rep?.first_name || ''

  const send = useCallback(
    (phone: string) => {
      if (!item) return
      setAsked(true)
      toast(S.card.tellBackDone, { kind: 'success', action: waUrl ? { label: S.card.tellRep(first || 'YQ'), onClick: () => window.open(waUrl, '_blank', 'noreferrer') } : undefined })
      postRestock({ item_code: item.item_code, phone, device_id: deviceId(), referral_code: m.ref || null }).catch(() => undefined)
    },
    [item, toast, waUrl, first, m.ref],
  )

  const ask = useCallback(() => {
    if (!item || asked) return
    const kept = cleanPhone(readCustomer().phone)
    if (isPhone(kept)) send(kept)
    else setOpen(true)
  }, [item, asked, send])

  const sheet = open && item ? (
    <TellBackSheet
      item={item}
      waUrl={waUrl}
      first={first}
      onClose={() => setOpen(false)}
      onSend={(phone) => {
        // the number the merchant just gave is his checkout number too — unless he asked this device to forget his details
        if (saveDetailsEnabled()) writeCustomer({ ...readCustomer(), phone })
        setOpen(false)
        send(phone)
      }}
    />
  ) : null

  return { asked, ask, sheet }
}

function TellBackSheet({ item, waUrl, first, onClose, onSend }: { item: ShopItem; waUrl: string | null; first: string; onClose: () => void; onSend: (phone: string) => void }) {
  const [phone, setPhone] = useState('')
  const [tried, setTried] = useState(false)
  const ok = isPhone(phone)
  const submit = () => {
    setTried(true)
    if (ok) onSend(cleanPhone(phone))
  }
  return (
    <Sheet
      open
      onClose={onClose}
      variant="dialog"
      title={S.card.tellBack}
      subtitle={`${item.item_code} · ${productName(item)}`}
      footer={
        <div className="flex flex-col gap-2">
          <Button size="lg" full onClick={submit} icon={<Check size={16} aria-hidden="true" />}>
            {S.card.tellBack}
          </Button>
          {waUrl && (
            <AnchorButton href={waUrl} target="_blank" rel="noreferrer" variant="wa" size="lg" full icon={<MessageCircle size={16} aria-hidden="true" />}>
              {S.card.tellRepWa(first || 'YQ')}
            </AnchorButton>
          )}
        </div>
      }
    >
      <div className="px-4 py-4 md:px-5">
        <p className="text-sm leading-snug text-ink-2">{S.card.tellBackWhy}</p>
        <Label htmlFor="yq-tellback-phone" className="mt-3">
          {S.card.tellBackPhone}
        </Label>
        <Input
          id="yq-tellback-phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          autoFocus
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="33001122"
          aria-invalid={tried && !ok}
          aria-describedby="yq-tellback-hint"
        />
        <Hint id="yq-tellback-hint" error={tried && !ok}>
          {tried && !ok ? S.card.tellBackPhoneBad : S.checkout.phoneHint}
        </Hint>
      </div>
    </Sheet>
  )
}

/**
 * The compact form for a row that cannot be picked (a greyed sold-out Quick-order suggestion, the
 * typed sold-out code): the button and its sheet in one, so a list of rows needs no state of its own.
 */
export function TellBackButton({ item, size = 'sm', className }: { item: ShopItem; size?: 'sm' | 'md' | 'lg'; className?: string }) {
  const { asked, ask, sheet } = useTellBack(item)
  return (
    <>
      <Button
        variant="secondary"
        size={size}
        disabled={asked}
        onClick={ask}
        aria-label={`${asked ? S.card.tellBackDone : S.card.tellBack} — ${productName(item)}`}
        icon={asked ? <Check size={14} aria-hidden="true" /> : <MessageCircle size={14} aria-hidden="true" />}
        className={cn('shrink-0 whitespace-nowrap', asked && 'border-ok/30 bg-ok-soft text-ok', className)}
      >
        {asked ? S.card.tellBackDone : S.card.tellBack}
      </Button>
      {sheet}
    </>
  )
}
