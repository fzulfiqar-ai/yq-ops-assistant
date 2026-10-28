/**
 * The salesman shell's five tabs: which one a page lights, and which ones the rep may open — pure,
 * no imports, so web/scripts/rep_field_test.mjs runs it in plain node.
 *
 *  • A page outside the five (Market Intel, Finds, Leads, the profile screen…) used to light Today,
 *    so the bar said "you are on Today" on a page that is not. It now lights no tab.
 *  • A tab whose page the rep may not open (its route's Gate in App.tsx) is not shown: tapping it
 *    only reached "You don't have access to this page".
 */

export interface TabPath {
  to: string
  /** only this exact path (Today), not the pages under it */
  end?: boolean
}

/** The feature each tab's route asks for (App.tsx: <Gate feature=…>); Me (/account) has no gate. */
export const TAB_FEATURE: Readonly<Record<string, string>> = {
  '/today': 'Shop Orders',
  '/shop': 'Catalog',
  '/shop-orders': 'Shop Orders',
  '/customers': 'Shop Orders',
}

/** The tab the page is under, or -1 when it is under none of them. */
export function activeTab(pathname: string, tabs: readonly TabPath[]): number {
  return tabs.findIndex((t) => (t.end ? pathname === t.to : pathname === t.to || pathname.startsWith(`${t.to}/`)))
}

/** May this login open the tab's page? The same rule as the route's Gate: an admin always, else the feature. */
export function tabAllowed(me: { role?: string | null; features?: readonly string[] | null } | null | undefined, to: string): boolean {
  if (!me) return false
  const feature = TAB_FEATURE[to]
  if (!feature) return true
  return me.role === 'admin' || (me.features || []).includes(feature)
}
