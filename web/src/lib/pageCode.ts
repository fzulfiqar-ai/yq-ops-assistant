import { createElement, lazy, useState, type ComponentType, type LazyExoticComponent, type ReactElement } from 'react'

/**
 * Every signed-in portal page's code, in one place (ship 2, plan item 13: the page change without the
 * wait). Each page renders at once when its code is already here — no Suspense gap, no held fallback —
 * and its code can be fetched ahead: on hover or touch of its menu item and at idle after the shell
 * paints (lib/prefetch.ts). App.tsx renders these; nothing else imports a page.
 *
 * React.lazy alone could do neither: it suspended on a page's first render even when its code was already
 * fetched, and a failed download stayed failed until the tab was reloaded.
 *
 * A failed download (a dropped signal, a deploy since the tab opened) reaches the page's error boundary
 * and STAYS failed until that boundary has shown it (lib/pageFrame calls rearmFailedPages); the page's
 * next mount then asks again. Re-arming any earlier loops: React renders a concurrent error once more,
 * synchronously, remounting the page — which picked up the fresh loader, fetched, failed, re-armed… The
 * marketplace's lib/lazyPage re-arms as the failed page renders; measured here (30-Oct-2026) that asked
 * for the Sales chunk ~10 times a second behind an endless skeleton. A warm-up's failure that no page
 * rendered is simply asked again by the next warm-up or mount.
 */
type Module<P> = { default: ComponentType<P> }
export type PortalPage<P> = ((props: P) => ReactElement) & { preload: () => Promise<void> }

/** the pages whose last download failed while a page waited for it, each with its re-arm */
const failedPages = new Set<() => void>()

/** A page's error has been shown (PageErrorBoundary): every failed page asks again on its next mount. */
export function rearmFailedPages(): void {
  for (const arm of [...failedPages]) arm()
}

export function portalPage<P extends object>(load: () => Promise<Module<P>>): PortalPage<P> {
  let loaded: ComponentType<P> | null = null
  let pending: Promise<Module<P>> | null = null
  let failed = false
  /** a render asked for this attempt (React.lazy called its loader): its failure is that render's */
  let rendered = false
  let Lazy: LazyExoticComponent<ComponentType<P>>
  const arm = () => {
    pending = null
    failed = false
    rendered = false
    failedPages.delete(arm)
    Lazy = lazy(() => {
      rendered = true
      return fetchCode()
    })
  }
  function fetchCode(): Promise<Module<P>> {
    if (!pending) {
      pending = load()
        .then((mod) => {
          // a deploy's missing chunk that the reload rule took over resolves to nothing
          if (!mod || !mod.default) throw new Error('The page code did not load')
          loaded = mod.default
          return mod
        })
        .catch((err: unknown) => {
          failed = true
          if (rendered) failedPages.add(arm)
          return { default: failedImport<P>(err) }
        })
    }
    return pending
  }
  arm()
  function Page(props: P): ReactElement {
    // decided once per mount, so a mounted page never swaps its component type
    const [Component] = useState<ComponentType<P> | LazyExoticComponent<ComponentType<P>>>(() => {
      if (loaded) return loaded
      if (failed && !rendered) arm() // a warm-up's failure: this mount asks again
      return Lazy
    })
    return createElement(Component as ComponentType<P>, props)
  }
  const preload = () => {
    if (failed && !rendered) arm()
    return fetchCode().then(() => undefined)
  }
  return Object.assign(Page, { preload })
}

function failedImport<P>(err: unknown): ComponentType<P> {
  return function FailedImport(): never {
    throw err
  }
}


export const Dashboard = portalPage(() => import('@/pages/Dashboard'))
export const Feed = portalPage(() => import('@/pages/Feed'))
export const Assistant = portalPage(() => import('@/pages/Assistant'))
export const FieldNotes = portalPage(() => import('@/pages/FieldNotes'))
export const Orders = portalPage(() => import('@/pages/Orders'))
export const OrderDetail = portalPage(() => import('@/pages/OrderDetail'))
export const Leads = portalPage(() => import('@/pages/Leads'))
export const Coaching = portalPage(() => import('@/pages/Coaching'))
export const Agents = portalPage(() => import('@/pages/Agents'))
export const Catalog = portalPage(() => import('@/pages/Catalog'))
export const Upcoming = portalPage(() => import('@/pages/Upcoming'))
export const Finds = portalPage(() => import('@/pages/Finds'))
export const MarketIntel = portalPage(() => import('@/pages/MarketIntel'))
// Market Intel v2 (r8m): one quality case — timeline, verification, evidence level, supplier actions.
export const MarketCase = portalPage(() => import('@/pages/market-intel/CasePage'))
export const Inventory = portalPage(() => import('@/pages/Inventory'))
export const StockMovement = portalPage(() => import('@/pages/StockMovement'))
export const Sales = portalPage(() => import('@/pages/Sales'))
export const Margins = portalPage(() => import('@/pages/Margins'))
export const PriceTracker = portalPage(() => import('@/pages/PriceTracker'))
export const Receivables = portalPage(() => import('@/pages/Receivables'))
export const DataPage = portalPage(() => import('@/pages/DataPage'))
export const Team = portalPage(() => import('@/pages/Team'))
export const Settings = portalPage(() => import('@/pages/Settings'))
export const PublicCatalog = portalPage(() => import('@/pages/PublicCatalog'))
export const PublicFinds = portalPage(() => import('@/pages/PublicFinds'))
export const Marketing = portalPage(() => import('@/pages/Marketing'))
export const OptOut = portalPage(() => import('@/pages/OptOut'))
export const OrderStatus = portalPage(() => import('@/pages/OrderStatus'))
export const ShopOrders = portalPage(() => import('@/pages/ShopOrders'))
export const Salesmen = portalPage(() => import('@/pages/Salesmen'))
export const ShopRules = portalPage(() => import('@/pages/ShopRules'))
export const ShopAnalytics = portalPage(() => import('@/pages/ShopAnalytics'))
export const MerchantVoice = portalPage(() => import('@/pages/MerchantVoice'))
// R7d: one marketplace merchant — facts, trend against its own usual, INSIGHTS with their evidence.
export const MerchantProfile = portalPage(() => import('@/pages/MerchantProfile'))
// The customer-facing shop, reused inside the portal so a salesman can order for a shop.
export const ShopPage = portalPage(() => import('@/pages/shop/ShopPage'))
export const PickList = portalPage(() => import('@/pages/PickList'))
// The salesman app (SalesmanShell): Today · Catalog · Orders · Customers · Me.
export const SalesToday = portalPage(() => import('@/pages/sales/Today'))
export const SalesCustomers = portalPage(() => import('@/pages/sales/Customers'))
export const SalesAccount = portalPage(() => import('@/pages/sales/Account'))
// The Management Command Centre (R7b): the whole company on one page, plus its team and customer views.
export const CommandCentre = portalPage(() => import('@/pages/CommandCentre'))

/** Anything whose code can be fetched ahead. */
export interface Warmable {
  preload: () => Promise<void>
}

/** Salesman mode of /shop renders the rep's catalog, its own chunk (ShopPage loads it on demand). */
const StaffCatalogCode: Warmable = { preload: () => import('@/pages/shop/StaffCatalog').then(() => undefined, () => undefined) }

/**
 * The code behind each route the menus and tabs link to — the same pages App.tsx renders on that path
 * (web/scripts/portal_shell_test.mjs holds the two together). A path not listed here warms nothing.
 */
const ROUTE_CODE: Readonly<Record<string, readonly Warmable[]>> = {
  '/': [Dashboard],
  '/command': [CommandCentre],
  '/command/team': [CommandCentre],
  '/command/customers': [CommandCentre],
  '/feed': [Feed],
  '/agents': [Agents],
  '/assistant': [Assistant],
  '/field-notes': [FieldNotes],
  '/catalog': [Catalog],
  '/upcoming': [Upcoming],
  '/finds': [Finds],
  '/market-intel': [MarketIntel],
  '/inventory': [Inventory],
  '/stock': [StockMovement],
  '/orders': [Orders],
  '/leads': [Leads],
  '/marketing': [Marketing],
  '/shop': [ShopPage, StaffCatalogCode],
  '/shop-orders': [ShopOrders],
  '/today': [SalesToday],
  '/customers': [SalesCustomers],
  '/account': [SalesAccount],
  '/picklist': [PickList],
  '/salesmen': [Salesmen],
  '/shop-rules': [ShopRules],
  '/shop-analytics': [ShopAnalytics],
  '/merchant-voice': [MerchantVoice],
  '/coaching': [Coaching],
  '/sales': [Sales],
  '/margins': [Margins],
  '/prices': [PriceTracker],
  '/receivables': [Receivables],
  '/data': [DataPage],
  '/team': [Team],
  '/settings': [Settings],
}

/** The code a link to `to` opens (its query and hash ignored), or nothing for a path the portal does not warm. */
export function codeFor(to: string): readonly Warmable[] {
  const path = to.split(/[?#]/, 1)[0] || '/'
  return ROUTE_CODE[path] ?? []
}

/** The paths that have code to warm (the node test checks them against App.tsx's routes). */
export const WARM_PATHS: readonly string[] = Object.keys(ROUTE_CODE)
