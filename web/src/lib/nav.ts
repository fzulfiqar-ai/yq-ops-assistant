import {
  LayoutGrid,
  Cpu,
  MessageSquare,
  Boxes,
  TrendingUp,
  Percent,
  CreditCard,
  Users,
  Database,
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
  { label: 'AI Agents', to: '/agents', icon: Cpu, feature: 'AI Agents' },
  { label: 'AI Assistant', to: '/assistant', icon: MessageSquare, feature: 'AI Assistant' },
  { label: 'Inventory', to: '/inventory', icon: Boxes, feature: 'Inventory' },
  { label: 'Sales', to: '/sales', icon: TrendingUp, feature: 'Sales' },
  { label: 'Margins', to: '/margins', icon: Percent, feature: 'Margins' },
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
