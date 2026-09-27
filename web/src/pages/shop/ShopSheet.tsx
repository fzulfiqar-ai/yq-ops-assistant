import { ArrowLeftRight, Loader2, MessageCircle, Phone, RotateCcw } from 'lucide-react'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import {
  bhdStr,
  customerOf,
  dayLabel,
  phoneLabel,
  restockText,
  telLink,
  useOrderAgain,
  useShopBasket,
  waLink,
  type BasketItem,
  type BookShop,
  type ShopBasket,
} from '@/pages/sales/lib'
import { CreditChip, StatusBadge } from './ShopPicker'
import { SoldOutDivider } from './SoldOut'
import { RING } from './shared'

const FROM: Record<string, string> = {
  last_order: 'Their last app order',
  due_regulars: 'What they usually buy — due now',
  regulars: 'What they usually buy',
}

function Line({ ln }: { ln: BasketItem }) {
  return (
    <li className="flex items-baseline justify-between gap-3 py-1.5 text-[12.5px]">
      <span className="min-w-0">
        <b className={cn('font-display font-bold', ln.sold_out ? 'text-[#a8a2bb]' : 'text-[#1A1428]')}>{ln.item_code}</b>
        <span className="ml-1.5 truncate text-[#6b6480]">{ln.display_name !== ln.item_code ? ln.display_name : ''}</span>
      </span>
      <span className={cn('shrink-0 tabular-nums', ln.sold_out ? 'text-[#a8a2bb]' : 'font-semibold text-[#1A1428]')}>× {ln.qty}</span>
    </li>
  )
}

/**
 * One shop, before the rep orders or messages it: its status (due / win back), the over-90 credit
 * chip, the suggested repeat (the last app order when that is newer, else the Focus regulars that
 * are due), the sold-out lines apart — and the two fast actions: "Order again" (the cart is filled,
 * sold-out lines left out and named) and "Send restock link" (a WhatsApp message carrying the
 * rep's storefront link with the cart ready, ?order=CODE:QTY — the merchant checks and places it).
 */
export function ShopSheet({
  shop,
  open,
  onClose,
  onChangeShop,
  onOrderAgain,
}: {
  shop: (Partial<BookShop> & Pick<BookShop, 'key' | 'name'>) | null
  open: boolean
  onClose: () => void
  /** shown in the catalog: re-open the picker */
  onChangeShop?: () => void
  /** the catalog preloads its own cart; elsewhere the default goes to the catalog */
  onOrderAgain?: (basket: ShopBasket) => void
}) {
  const orderAgain = useOrderAgain()
  const key = open && shop?.key && !shop.key.startsWith('n:') ? shop.key : null
  const q = useShopBasket(key)
  const b = q.data
  if (!shop) return null
  const phone = b?.shop.phone || shop.phone || null
  const contact = b?.shop.contact_name || shop.contact_name || null
  const available = b?.available || []
  const wa = b?.restock_link ? waLink(phone, restockText(b.restock_link, contact)) || `https://wa.me/?text=${encodeURIComponent(restockText(b.restock_link, contact))}` : null
  const tel = telLink(phone)
  const again = () => {
    if (!b) return
    if (onOrderAgain) onOrderAgain(b)
    else
      orderAgain(
        customerOf({
          key: shop.key,
          name: shop.name,
          focus_name: shop.focus_name ?? b.shop.focus_name,
          contact_name: contact,
          phone,
          area: shop.area ?? b.shop.area,
          email: shop.email ?? b.shop.email,
        }),
      )
    onClose()
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      variant="dialog"
      title={shop.name}
      subtitle={[contact, shop.area || b?.shop.area, phone ? phoneLabel(phone) : 'no phone on file'].filter(Boolean).join(' · ')}
      footer={
        <div className="grid gap-2 sm:grid-cols-2">
          <button
            type="button"
            onClick={again}
            disabled={!available.length}
            className={cn(
              'flex h-12 items-center justify-center gap-2 rounded-xl text-[14px] font-semibold transition duration-150 ease-out',
              RING,
              available.length ? 'bg-[#6D4091] text-white hover:bg-[#5A3478]' : 'cursor-not-allowed bg-[#f0eef6] text-[#a8a2bb]',
            )}
          >
            <RotateCcw size={16} aria-hidden="true" /> Order again{available.length ? ` · ${available.length} ${available.length === 1 ? 'line' : 'lines'}` : ''}
          </button>
          {wa ? (
            <a href={wa} target="_blank" rel="noreferrer" onClick={onClose} className={cn('flex h-12 items-center justify-center gap-2 rounded-xl bg-[#25D366] text-[14px] font-semibold text-white hover:bg-[#1eb356]', RING)}>
              <MessageCircle size={16} aria-hidden="true" /> Send restock link
            </a>
          ) : (
            <span className="flex h-12 items-center justify-center rounded-xl border border-dashed border-[#E2DCEA] px-3 text-center text-[12px] text-[#6b6480]">
              {q.isLoading ? 'Preparing the restock link…' : 'No restock link — nothing in stock to suggest'}
            </span>
          )}
        </div>
      }
    >
      <div className="px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-1.5">
          {shop.status && <StatusBadge shop={{ status: shop.status, due: Boolean(shop.due) }} />}
          <CreditChip credit={b?.shop.credit ?? shop.credit ?? null} />
          {(b?.shop.last_order_date || shop.last_order_date) && (
            <span className="text-[11.5px] text-[#6b6480]">Last order {dayLabel(b?.shop.last_order_date || shop.last_order_date)}</span>
          )}
        </div>
        {shop.why && <p className="mt-1.5 text-[12px] text-[#6b6480]">{shop.why}</p>}

        <div className="mt-3 flex gap-2">
          {onChangeShop && (
            <button type="button" onClick={onChangeShop} className={cn('inline-flex h-10 items-center gap-1.5 rounded-xl border border-[#E2DCEA] bg-white px-3 text-[12.5px] font-semibold text-[#1A1428] hover:bg-[#f7f5fb]', RING)}>
              <ArrowLeftRight size={14} aria-hidden="true" /> Change shop
            </button>
          )}
          {phone && (
            <a href={waLink(phone, `Hello${contact ? ` ${contact.split(' ')[0]}` : ''}, YQ here.`) || '#'} target="_blank" rel="noreferrer" aria-label="WhatsApp" className={cn('grid h-10 w-10 place-items-center rounded-xl border border-[#E2DCEA] bg-white text-[#1d9e50] hover:bg-[#f7f5fb]', RING)}>
              <MessageCircle size={16} aria-hidden="true" />
            </a>
          )}
          {tel && (
            <a href={tel} aria-label="Call" className={cn('grid h-10 w-10 place-items-center rounded-xl border border-[#E2DCEA] bg-white text-[#1A1428] hover:bg-[#f7f5fb]', RING)}>
              <Phone size={16} aria-hidden="true" />
            </a>
          )}
        </div>

        <section className="mt-4 rounded-2xl border border-[#E9E4EF] bg-white p-3.5">
          {q.isLoading ? (
            <p className="flex items-center gap-2 text-[12.5px] text-[#6b6480]">
              <Loader2 size={14} className="animate-spin" aria-hidden="true" /> Reading their usual order…
            </p>
          ) : q.isError ? (
            <p className="text-[12.5px] text-[#9f1239]">Their usual order did not load. Close this and try again.</p>
          ) : !b || !b.suggested.length ? (
            <p className="text-[12.5px] text-[#6b6480]">No usual order yet — once they have bought the same line twice, it shows here.</p>
          ) : (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="font-display text-[13px] font-bold text-[#1A1428]">
                  {FROM[b.suggested_from || 'regulars']}
                  {b.suggested_from === 'last_order' && b.last_order ? ` · ${dayLabel(b.last_order.created_at)}` : ''}
                </h3>
                {available.length > 0 && <span className="text-[12px] font-semibold tabular-nums text-[#6D4091]">≈ {bhdStr(b.value_bhd)}</span>}
              </div>
              <ul className="mt-1.5 divide-y divide-[#F3F0F6]">{available.map((ln) => <Line key={ln.item_code} ln={ln} />)}</ul>
              {b.sold_out.length > 0 && (
                <>
                  <SoldOutDivider count={b.sold_out.length} className="mt-1" />
                  <ul className="divide-y divide-[#F3F0F6]">{b.sold_out.map((ln) => <Line key={ln.item_code} ln={ln} />)}</ul>
                  <p className="mt-1 text-[11px] text-[#6b6480]">Sold-out lines stay out of the cart and out of the link.</p>
                </>
              )}
            </>
          )}
        </section>
        {b?.usual && b.usual.length > 0 && b.suggested_from === 'last_order' && (
          <p className="mt-2 text-[11.5px] text-[#6b6480]">
            Also bought regularly: {b.usual.filter((u) => !b.suggested.some((s) => s.item_code === u.item_code)).slice(0, 6).map((u) => `${u.item_code} ×${u.qty}`).join(', ') || '—'}
          </p>
        )}
      </div>
    </Sheet>
  )
}
