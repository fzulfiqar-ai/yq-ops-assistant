import { Activity, BadgeCheck, Clock, House, LayoutGrid, MessageCircle, Package, PackagePlus, ReceiptText, ShieldCheck, Store, Tag, Truck, Zap, type LucideIcon } from 'lucide-react'

/** One icon grammar across the marketplace: lucide line icons, 18px, 1.75 stroke, labelled. */
export const ICON = { size: 18, strokeWidth: 1.75 } as const

/** The five destinations, one glyph each — the phone nav and anything that points at a tab. */
export const NAV_ICONS = {
  home: House,
  browse: LayoutGrid,
  restock: PackagePlus,
  orders: ReceiptText,
  me: Store,
} as const satisfies Record<string, LucideIcon>

/**
 * The `icon` keys a promise row may carry (settings `shop_market_promises`, edited in the portal).
 * The backend defaults use truck · tag · pulse · shield; the rest are there so the office can pick
 * a fitting one without a deploy. Unknown keys fall back to PROMISE_ICON_FALLBACK.
 */
export const PROMISE_ICONS: Record<string, LucideIcon> = {
  truck: Truck,
  tag: Tag,
  pulse: Activity,
  shield: ShieldCheck,
  package: Package,
  clock: Clock,
  check: BadgeCheck,
  chat: MessageCircle,
  zap: Zap,
  store: Store,
}
export const PROMISE_ICON_FALLBACK: LucideIcon = ShieldCheck
