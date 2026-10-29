import { lazyPage } from './lazyPage'

/**
 * Every signed-in portal page's code, in one place (ship 2, plan item 13: the page change without the
 * wait). Each page is a lib/lazyPage: once its code is here it renders at once — no Suspense gap, no
 * held fallback — and its code can be fetched ahead: on hover or touch of its menu item and at idle
 * after the shell paints (lib/prefetch.ts). App.tsx renders these; nothing else imports a page.
 *
 * React.lazy alone could not do either: a lazy page suspended on its first render even when its code
 * was already fetched, and a failed download stayed failed until the tab was reloaded.
 */
export const Dashboard = lazyPage(() => import('@/pages/Dashboard'))
export const Feed = lazyPage(() => import('@/pages/Feed'))
export const Assistant = lazyPage(() => import('@/pages/Assistant'))
export const FieldNotes = lazyPage(() => import('@/pages/FieldNotes'))
export const Orders = lazyPage(() => import('@/pages/Orders'))
export const OrderDetail = lazyPage(() => import('@/pages/OrderDetail'))
export const Leads = lazyPage(() => import('@/pages/Leads'))
export const Coaching = lazyPage(() => import('@/pages/Coaching'))
export const Agents = lazyPage(() => import('@/pages/Agents'))
export const Catalog = lazyPage(() => import('@/pages/Catalog'))
export const Upcoming = lazyPage(() => import('@/pages/Upcoming'))
export const Finds = lazyPage(() => import('@/pages/Finds'))
export const MarketIntel = lazyPage(() => import('@/pages/MarketIntel'))
// Market Intel v2 (r8m): one quality case — timeline, verification, evidence level, supplier actions.
export const MarketCase = lazyPage(() => import('@/pages/market-intel/CasePage'))
export const Inventory = lazyPage(() => import('@/pages/Inventory'))
export const StockMovement = lazyPage(() => import('@/pages/StockMovement'))
export const Sales = lazyPage(() => import('@/pages/Sales'))
export const Margins = lazyPage(() => import('@/pages/Margins'))
export const PriceTracker = lazyPage(() => import('@/pages/PriceTracker'))
export const Receivables = lazyPage(() => import('@/pages/Receivables'))
export const DataPage = lazyPage(() => import('@/pages/DataPage'))
export const Team = lazyPage(() => import('@/pages/Team'))
export const Settings = lazyPage(() => import('@/pages/Settings'))
export const PublicCatalog = lazyPage(() => import('@/pages/PublicCatalog'))
export const PublicFinds = lazyPage(() => import('@/pages/PublicFinds'))
export const Marketing = lazyPage(() => import('@/pages/Marketing'))
export const OptOut = lazyPage(() => import('@/pages/OptOut'))
export const OrderStatus = lazyPage(() => import('@/pages/OrderStatus'))
export const ShopOrders = lazyPage(() => import('@/pages/ShopOrders'))
export const Salesmen = lazyPage(() => import('@/pages/Salesmen'))
export const ShopRules = lazyPage(() => import('@/pages/ShopRules'))
export const ShopAnalytics = lazyPage(() => import('@/pages/ShopAnalytics'))
export const MerchantVoice = lazyPage(() => import('@/pages/MerchantVoice'))
// R7d: one marketplace merchant — facts, trend against its own usual, INSIGHTS with their evidence.
export const MerchantProfile = lazyPage(() => import('@/pages/MerchantProfile'))
// The customer-facing shop, reused inside the portal so a salesman can order for a shop.
export const ShopPage = lazyPage(() => import('@/pages/shop/ShopPage'))
export const PickList = lazyPage(() => import('@/pages/PickList'))
// The salesman app (SalesmanShell): Today · Catalog · Orders · Customers · Me.
export const SalesToday = lazyPage(() => import('@/pages/sales/Today'))
export const SalesCustomers = lazyPage(() => import('@/pages/sales/Customers'))
export const SalesAccount = lazyPage(() => import('@/pages/sales/Account'))
// The Management Command Centre (R7b): the whole company on one page, plus its team and customer views.
export const CommandCentre = lazyPage(() => import('@/pages/CommandCentre'))

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
