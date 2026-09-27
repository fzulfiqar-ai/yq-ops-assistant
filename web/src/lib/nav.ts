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
  PackagePlus,
  Gauge,
  Trophy,
  Store,
  type LucideIcon,
} from 'lucide-react'
import { isManagement, type Me, type Role } from './auth'

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
// Grouped as plan §33 lists them (Sprint 4, R7b): the Command Centre first, the day's work next, the
// money pages, then the reports and admin. The dormant AI surfaces (Live Feed, AI Agents, Leads,
// Marketing, Coach) are shown to admins only, under "AI tools / Archive"; their routes still exist.
export const NAV: NavItem[] = [
  { section: 'Command Centre', label: 'Command Centre', to: '/command', icon: Gauge }, // admin (management: MANAGEMENT_NAV)
  { section: 'Customer orders', label: 'Customer orders', to: '/shop-orders', icon: ClipboardList, feature: 'Shop Orders' },
  // The storekeeper's one page: confirmed marketplace orders to pick, grouped by salesman.
  { section: 'Customer orders', label: 'Pick list', to: '/picklist', icon: PackageCheck, feature: 'Storekeeper' },
  { section: 'Order for a shop', label: 'Order for a shop', to: '/shop', icon: ShoppingBag, feature: 'Catalog', roles: ['admin', 'member'] },
  // A salesman's "Catalog" IS the shop they sell from — same page the customer sees,
  // in salesman mode. Admins/members keep the internal catalog editor at /catalog.
  { section: 'Catalog', label: 'Catalog', to: '/shop', icon: BookImage, feature: 'Catalog', roles: ['salesman'] },
  { section: 'Catalog', label: 'Catalog', to: '/catalog', icon: BookImage, feature: 'Catalog', roles: ['admin', 'member'] },
  // The "Coming soon" desk: publish the announced range, set the month, link catalog codes on arrival.
  { section: 'Catalog', label: 'Coming soon', to: '/upcoming', icon: PackagePlus, feature: 'Catalog', roles: ['admin', 'member'] },
  { section: 'Catalog', label: 'Product Finds', to: '/finds', icon: Sparkles, feature: 'Product Finds' },
  { section: 'Inventory & stock moves', label: 'Inventory', to: '/inventory', icon: Boxes, feature: 'Inventory' },
  { section: 'Inventory & stock moves', label: 'Stock moves', to: '/stock', icon: ArrowLeftRight, feature: 'Stock Movement' },
  { section: 'Purchasing', label: 'Purchase orders', to: '/orders', icon: ShoppingCart, feature: 'Orders' },
  { section: 'Profitability', label: 'Profitability', to: '/margins', icon: Percent, feature: 'Margins' },
  { section: 'Profitability', label: 'Price tracker', to: '/prices', icon: LineChart, feature: 'Margins' },
  { section: 'Receivables', label: 'Receivables', to: '/receivables', icon: CreditCard, feature: 'Receivables' },
  { section: 'Team performance', label: 'Rep performance', to: '/command/team', icon: Trophy }, // admin (management: MANAGEMENT_NAV)
  { section: 'Team performance', label: 'Reps & statements', to: '/salesmen', icon: UserRoundCheck, feature: 'Shop Admin' },
  { section: 'Offers & campaigns', label: 'Offers & rules', to: '/shop-rules', icon: BadgePercent, feature: 'Shop Admin' },
  { section: 'Reports', label: 'Dashboard', to: '/', icon: LayoutGrid, feature: 'Dashboard' },
  { section: 'Reports', label: 'Sales', to: '/sales', icon: TrendingUp, feature: 'Sales' },
  { section: 'Reports', label: 'Shop analytics', to: '/shop-analytics', icon: BarChart3, feature: 'Shop Admin' },
  { section: 'Admin', label: 'Data upload', to: '/data', icon: Database }, // admin-only
  { section: 'Admin', label: 'Team & access', to: '/team', icon: Users }, // admin-only
  { section: 'AI tools / Archive', label: 'AI Assistant', to: '/assistant', icon: MessageSquare, feature: 'AI Assistant' },
  { section: 'AI tools / Archive', label: 'Field Notes', to: '/field-notes', icon: NotebookPen, feature: 'AI Assistant' },
  { section: 'AI tools / Archive', label: 'AI Agents', to: '/agents', icon: Cpu, feature: 'AI Agents', roles: ['admin'] },
  { section: 'AI tools / Archive', label: 'Live Feed', to: '/feed', icon: Activity, feature: 'Live Feed', roles: ['admin'] },
  { section: 'AI tools / Archive', label: 'Leads', to: '/leads', icon: Target, feature: 'Leads', roles: ['admin'] },
  { section: 'AI tools / Archive', label: 'Marketing', to: '/marketing', icon: Megaphone, feature: 'Marketing', roles: ['admin'] },
  { section: 'AI tools / Archive', label: 'Coach', to: '/coaching', icon: MessageSquareQuote, feature: 'Sales', roles: ['admin'] },
]

/**
 * Management reads the company and changes nothing: exactly these pages, in this order, under
 * these names — nothing else, whatever features the row lists (the API holds the same limit,
 * app/features.py ROLE_FEATURE_LIMITS). The Command Centre pages are the role's own (no feature);
 * the others need the matching read page.
 */
export const MANAGEMENT_NAV: { to: string; label: string; section: string; icon: LucideIcon; feature?: string }[] = [
  { to: '/command', label: 'Command Centre', section: 'Overview', icon: Gauge },
  { to: '/shop-orders', label: 'Customer orders', section: 'Marketplace', icon: ClipboardList, feature: 'Shop Orders' },
  { to: '/command/team', label: 'Team', section: 'Company', icon: Trophy },
  { to: '/command/customers', label: 'Customers', section: 'Company', icon: Store },
  { to: '/inventory', label: 'Products & stock', section: 'Company', icon: Boxes, feature: 'Inventory' },
  { to: '/margins', label: 'Profitability', section: 'Company', icon: Percent, feature: 'Margins' },
  { to: '/receivables', label: 'Receivables', section: 'Company', icon: CreditCard, feature: 'Receivables' },
]

/** Pages the Command Centre's tiles open for management although they are not in its menu. */
const MANAGEMENT_DRILLS = ['/sales', '/prices']

/** May management open this route? Only its own pages and the tiles' drill-downs, plus the profile screen. */
export function managementMayOpen(pathname: string): boolean {
  if (pathname === '/settings' || pathname === '/') return true
  const allowed = [...MANAGEMENT_NAV.map((m) => m.to), ...MANAGEMENT_DRILLS]
  return allowed.some((to) => pathname === to || pathname.startsWith(`${to}/`))
}

export function canAccess(me: Me | null, item: NavItem): boolean {
  if (!me) return false
  // Role restriction is absolute — an admin does NOT get the salesman-only shell entries.
  if (item.roles && !item.roles.includes(me.role)) return false
  if (me.role === 'admin') return true
  if (!item.feature) return false // admin-only item
  if (isManagement(me) && !MANAGEMENT_NAV.some((m) => m.to === item.to)) return false
  return (me.features || []).includes(item.feature)
}

export function navFor(me: Me | null): NavItem[] {
  if (isManagement(me)) {
    const feats = me?.features || []
    return MANAGEMENT_NAV
      .filter((m) => !m.feature || feats.includes(m.feature))
      .map((m) => ({ label: m.label, to: m.to, icon: m.icon, feature: m.feature, section: m.section }))
  }
  return NAV.filter((n) => canAccess(me, n))
}

/** Where to land after login — the first page this user can actually see. */
export function homeFor(me: Me | null): string {
  // A salesman lands on Today (his orders, link and numbers), whatever else he can see.
  if (me?.role === 'salesman') return '/today'
  // Management lands on the Command Centre (Sprint 4, plan §8).
  if (isManagement(me)) return '/command'
  const items = navFor(me)
  return items.length ? items[0].to : '/settings'
}
