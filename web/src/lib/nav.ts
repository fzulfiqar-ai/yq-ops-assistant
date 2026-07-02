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
  type LucideIcon,
} from 'lucide-react'
import type { Me } from './auth'

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  feature?: string // omit = admin-only
}

export const NAV: NavItem[] = [
  { label: 'Dashboard', to: '/', icon: LayoutGrid, feature: 'Dashboard' },
  { label: 'Live Feed', to: '/feed', icon: Activity, feature: 'AI Agents' },
  { label: 'AI Agents', to: '/agents', icon: Cpu, feature: 'AI Agents' },
  { label: 'AI Assistant', to: '/assistant', icon: MessageSquare, feature: 'AI Assistant' },
  { label: 'Field Notes', to: '/field-notes', icon: NotebookPen, feature: 'AI Assistant' },
  { label: 'Leads', to: '/leads', icon: Target, feature: 'Sales' },
  { label: 'Coach', to: '/coaching', icon: MessageSquareQuote, feature: 'Sales' },
  { label: 'Sales', to: '/sales', icon: TrendingUp, feature: 'Sales' },
  { label: 'Inventory', to: '/inventory', icon: Boxes, feature: 'Inventory' },
  { label: 'Orders', to: '/orders', icon: ShoppingCart, feature: 'Inventory' },
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
