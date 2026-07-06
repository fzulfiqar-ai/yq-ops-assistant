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
  Target,
  MessageSquareQuote,
  BookImage,
  ArrowLeftRight,
  LineChart,
  Megaphone,
  type LucideIcon,
} from 'lucide-react'
import type { Me } from './auth'

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  feature?: string // omit = admin-only
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
  { section: 'Sell', label: 'Catalog', to: '/catalog', icon: BookImage, feature: 'Catalog' },
  { section: 'Sell', label: 'Leads', to: '/leads', icon: Target, feature: 'Leads' },
  { section: 'Sell', label: 'Marketing', to: '/marketing', icon: Megaphone, feature: 'Marketing' },
  { section: 'Sell', label: 'Coach', to: '/coaching', icon: MessageSquareQuote, feature: 'Sales' },
  { section: 'Supply', label: 'Inventory', to: '/inventory', icon: Boxes, feature: 'Inventory' },
  { section: 'Supply', label: 'Stock Moves', to: '/stock', icon: ArrowLeftRight, feature: 'Stock Movement' },
  { section: 'Supply', label: 'Orders', to: '/orders', icon: ShoppingCart, feature: 'Orders' },
  { section: 'Money', label: 'Profitability', to: '/margins', icon: Percent, feature: 'Margins' },
  { section: 'Money', label: 'Price Tracker', to: '/prices', icon: LineChart, feature: 'Margins' },
  { section: 'Money', label: 'Receivables', to: '/receivables', icon: CreditCard, feature: 'Receivables' },
  { section: 'Admin', label: 'Data', to: '/data', icon: Database }, // admin-only
  { section: 'Admin', label: 'Team', to: '/team', icon: Users }, // admin-only
]

export function canAccess(me: Me | null, item: NavItem): boolean {
  if (!me) return false
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
  return items.length ? items[0].to : '/settings'
}
