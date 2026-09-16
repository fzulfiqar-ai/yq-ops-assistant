import {
  LayoutGrid,
  Cpu,
  Activity,
  MessageSquare,
  Boxes,
  TrendingUp,
  Percent,
  CreditCard,
  Users,
  Database,
  NotebookPen,
  ShoppingCart,
  ShoppingBag,
  Target,
  MessageSquareQuote,
  BookImage,
  Sparkles,
  ArrowLeftRight,
  LineChart,
  Megaphone,
  ClipboardList,
  UserRoundCheck,
  BadgePercent,
  BarChart3,
  PackageCheck,
  type LucideIcon,
} from 'lucide-react'
import type { Me, Role } from './auth'

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  feature?: string // omit = admin-only
  /** When set, only these roles ever see the item — checked BEFORE the admin bypass. */
  roles?: Role[]
  section: string
}

// `feature` strings must match app/features.py exactly (served via GET /auth/features).
export const NAV: NavItem[] = [
  { section: 'Overview', label: 'Dashboard', to: '/', icon: LayoutGrid, feature: 'Dashboard' },
  { section: 'Overview', label: 'Live Feed', to: '/feed', icon: Activity, feature: 'Live Feed' },
  { section: 'AI Team', label: 'AI Agents', to: '/agents', icon: Cpu, feature: 'AI Agents' },
  { section: 'AI Team', label: 'AI Assistant', to: '/assistant', icon: MessageSquare, feature: 'AI Assistant' },
  { section: 'AI Team', label: 'Field Notes', to: '/field-notes', icon: NotebookPen, feature: 'AI Assistant' },
  { section: 'Sell', label: 'Sales', to: '/sales', icon: TrendingUp, feature: 'Sales' },
  // A salesman's "Catalog" IS the shop they sell from — same page the customer sees,
  // in salesman mode. Admins/members keep the internal catalog editor at /catalog.
  { section: 'Sell', label: 'Catalog', to: '/shop', icon: BookImage, feature: 'Catalog', roles: ['salesman'] },
  { section: 'Sell', label: 'Catalog', to: '/catalog', icon: BookImage, feature: 'Catalog', roles: ['admin', 'member'] },
  { section: 'Sell', label: 'Order for a shop', to: '/shop', icon: ShoppingBag, feature: 'Catalog', roles: ['admin', 'member'] },
  { section: 'Sell', label: 'Product Finds', to: '/finds', icon: Sparkles, feature: 'Product Finds' },
  { section: 'Sell', label: 'Leads', to: '/leads', icon: Target, feature: 'Leads' },
  { section: 'Sell', label: 'Marketing', to: '/marketing', icon: Megaphone, feature: 'Marketing' },
  { section: 'Sell', label: 'Shop Orders', to: '/shop-orders', icon: ClipboardList, feature: 'Shop Orders' },
  { section: 'Sell', label: 'Coach', to: '/coaching', icon: MessageSquareQuote, feature: 'Sales' },
  // The storekeeper's one page: confirmed marketplace orders to pick, grouped by salesman.
  { section: 'Supply', label: 'Pick list', to: '/picklist', icon: PackageCheck, feature: 'Storekeeper' },
  { section: 'Supply', label: 'Inventory', to: '/inventory', icon: Boxes, feature: 'Inventory' },
  { section: 'Supply', label: 'Stock Moves', to: '/stock', icon: ArrowLeftRight, feature: 'Stock Movement' },
  { section: 'Supply', label: 'Orders', to: '/orders', icon: ShoppingCart, feature: 'Orders' },
  { section: 'Money', label: 'Profitability', to: '/margins', icon: Percent, feature: 'Margins' },
  { section: 'Money', label: 'Price Tracker', to: '/prices', icon: LineChart, feature: 'Margins' },
  { section: 'Money', label: 'Receivables', to: '/receivables', icon: CreditCard, feature: 'Receivables' },
  { section: 'Admin', label: 'Salesmen', to: '/salesmen', icon: UserRoundCheck, feature: 'Shop Admin' },
  { section: 'Admin', label: 'Offers & Rules', to: '/shop-rules', icon: BadgePercent, feature: 'Shop Admin' },
  { section: 'Admin', label: 'Shop Analytics', to: '/shop-analytics', icon: BarChart3, feature: 'Shop Admin' },
  { section: 'Admin', label: 'Data', to: '/data', icon: Database }, // admin-only
  { section: 'Admin', label: 'Team', to: '/team', icon: Users }, // admin-only
]

export function canAccess(me: Me | null, item: NavItem): boolean {
  if (!me) return false
  // Role restriction is absolute — an admin does NOT get the salesman-only shell entries.
  if (item.roles && !item.roles.includes(me.role)) return false
  if (me.role === 'admin') return true
  if (!item.feature) return false // admin-only item
  return (me.features || []).includes(item.feature)
}

export function navFor(me: Me | null): NavItem[] {
  return NAV.filter((n) => canAccess(me, n))
}

/** Where to land after login — the first page this user can actually see. */
export function homeFor(me: Me | null): string {
  const items = navFor(me)
  // A salesman's home is always the catalog they sell from, whatever else they can see.
  if (me?.role === 'salesman') {
    const shop = items.find((n) => n.to === '/shop')
    if (shop) return shop.to
  }
  return items.length ? items[0].to : '/settings'
}
