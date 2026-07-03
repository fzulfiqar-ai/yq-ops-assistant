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
  type LucideIcon,
} from 'lucide-react'
import type { Me } from './auth'

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  feature?: string // omit = admin-only
}

// `feature` strings must match app/features.py exactly (served via GET /auth/features).
export const NAV: NavItem[] = [
  { label: 'Dashboard', to: '/', icon: LayoutGrid, feature: 'Dashboard' },
  { label: 'Live Feed', to: '/feed', icon: Activity, feature: 'Live Feed' },
  { label: 'AI Agents', to: '/agents', icon: Cpu, feature: 'AI Agents' },
  { label: 'AI Assistant', to: '/assistant', icon: MessageSquare, feature: 'AI Assistant' },
  { label: 'Field Notes', to: '/field-notes', icon: NotebookPen, feature: 'AI Assistant' },
  { label: 'Leads', to: '/leads', icon: Target, feature: 'Leads' },
  { label: 'Coach', to: '/coaching', icon: MessageSquareQuote, feature: 'Sales' },
  { label: 'Sales', to: '/sales', icon: TrendingUp, feature: 'Sales' },
  { label: 'Catalog', to: '/catalog', icon: BookImage, feature: 'Catalog' },
  { label: 'Inventory', to: '/inventory', icon: Boxes, feature: 'Inventory' },
  { label: 'Stock Moves', to: '/stock', icon: ArrowLeftRight, feature: 'Stock Movement' },
  { label: 'Orders', to: '/orders', icon: ShoppingCart, feature: 'Orders' },
  { label: 'Profitability', to: '/margins', icon: Percent, feature: 'Margins' },
  { label: 'Receivables', to: '/receivables', icon: CreditCard, feature: 'Receivables' },
  { label: 'Data', to: '/data', icon: Database }, // admin-only
  { label: 'Team', to: '/team', icon: Users }, // admin-only
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
