import { lazy, StrictMode, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useSearchParams, useNavigate } from 'react-router-dom'
import { UpdateToast } from '@/market/components/UpdateToast'
import { isSlugShaped, rememberRef } from '@/market/lib/device'
import { setSource } from '@/market/lib/events'
import { watchInstallPrompt } from '@/market/lib/install'
import { startErrorReporting } from '@/market/lib/errors'
import { startVitals } from '@/market/lib/vitals'
import { MarketProvider, useMarket } from '@/market/MarketContext'
import Home from '@/market/pages/Home'
import { Shell } from '@/market/shell/Shell'
import { cartStore } from '@/market/store/cart'
import { locale } from '@/market/strings'
import { ErrorBoundary } from '@/market/ui/ErrorBoundary'
import { ToastProvider } from '@/market/ui/Toast'

const ShopPage = lazy(() => import('@/market/pages/ShopPage'))
const CategoryPage = lazy(() => import('@/market/pages/CategoryPage'))
const SearchPage = lazy(() => import('@/market/pages/SearchPage'))
const CartPage = lazy(() => import('@/market/pages/CartPage'))
const CheckoutPage = lazy(() => import('@/market/pages/CheckoutPage'))
const TrackingPage = lazy(() => import('@/market/pages/TrackingPage'))
const MyOrdersPage = lazy(() => import('@/market/pages/MyOrdersPage'))
const MyYQPage = lazy(() => import('@/market/pages/MyYQPage'))
const QuickOrderPage = lazy(() => import('@/market/pages/QuickOrderPage'))
const LegacyRedirect = lazy(() => import('@/market/pages/LegacyRedirect'))
const AboutPage = lazy(() => import('@/market/pages/AboutPage'))
const BrandPage = lazy(() => import('@/market/pages/BrandPage'))

/**
 * YQ Marketplace — the merchant front door (built with VITE_APP=market, its own hostname, its own
 * entry src/main.market.tsx and stylesheet). Home is static so the catalog is one download; every
 * other page is lazy. The Shell (layout route) owns the chrome for phone / tablet / desktop and
 * mounts the product panel and the ⌘K palette over whichever page is showing.
 *
 * Routes: / home · /{slug} salesman storefront · /p/{code} product deep link · /t/{category} ·
 * /shop · /search · /quick · /cart · /checkout · /orders · /me · /o/{token} tracking ·
 * /brands/{brand} (the "Coming soon" range; /wekome and /coming-soon redirect there) ·
 * /c/{token} legacy redirect. A slug is matched LAST and validated against the payload.
 *
 * RESERVED first segments live in four places — here, public/catalog-prefetch.js,
 * app/shop.py (_RESERVED_FALLBACK) and the shop_reserved_slugs table. Keep them equal.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const RESERVED = new Set(['search', 'cart', 'checkout', 'orders', 'p', 't', 'o', 'c', 'join', 'shop', 'me', 'quick', 'about', 'help', 'ask', 'saved', 'brands', 'wekome', 'coming-soon'])

/**
 * Reads ?ref / ?src on any entry URL (shared product links and rep links carry them); scrolls to
 * top on page changes (not overlays). A valid ?ref is remembered for later visits AND becomes this
 * session's ref at once (MarketApp seeds it on the first render; this covers a later in-app
 * navigation), so the rep's card, the WhatsApp asks and every notify / restock / order POST carry
 * it on the very first visit. A /{slug} storefront keeps its own slug: Home confirms it with the
 * server.
 */
function EntryParams() {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const { setRef } = useMarket()
  useEffect(() => {
    const ref = (params.get('ref') || '').toLowerCase()
    if (ref && isSlugShaped(ref)) {
      rememberRef(ref)
      if (!storefrontSlug(location.pathname)) setRef(ref)
    }
    setSource(params.get('src'))
    // a representative's ready order: ?order=X01:12,UK04:6 → the cart, then the cart page
    const order = params.get('order')
    if (order) {
      const entries = order
        .split(',')
        .map((part) => {
          const [code, qty] = part.split(':')
          return { item_code: decodeURIComponent((code || '').trim()), qty: Math.max(1, Math.floor(Number(qty) || 1)) }
        })
        .filter((e) => e.item_code)
      if (entries.length) {
        cartStore.setMany(entries)
        navigate('/cart', { replace: true })
      }
    }
  }, [params, location.pathname, navigate, setRef])
  useEffect(() => {
    const st = location.state as { panel?: string } | null
    if (st?.panel) return // product panel over the same page: keep the scroll position
    window.scrollTo({ top: 0 })
  }, [location.pathname, location.state])
  return null
}

/** "/sam" → "sam": a rep's storefront path (one segment, not a route, slug-shaped) */
function storefrontSlug(pathname: string): string | null {
  const seg = pathname.split('/').filter(Boolean)
  if (seg.length !== 1) return null
  const s = seg[0].toLowerCase()
  return !RESERVED.has(s) && isSlugShaped(s) ? s : null
}

/**
 * The session's ref on the first render: a /{slug} storefront, else a rep link's ?ref=
 * (/brands/wekome?ref=sam, a shared product link), else the provider falls back to the rep this
 * phone remembers. Seeding ?ref here — not only in EntryParams' storage write — means the first
 * catalog request already carries it, which is also the request public/catalog-prefetch.js sent
 * (it reads ?ref first), so the prefetched payload is used instead of fetched twice.
 */
function initialRef(): string | null {
  const fromPath = storefrontSlug(window.location.pathname)
  if (fromPath) return fromPath
  const ref = (new URLSearchParams(window.location.search).get('ref') || '').toLowerCase()
  return isSlugShaped(ref) ? ref : null
}

export default function MarketApp() {
  useEffect(() => {
    document.documentElement.lang = locale.lang
    document.documentElement.dir = locale.dir
    startVitals()
    const stopErrors = startErrorReporting()
    watchInstallPrompt()
    return stopErrors
  }, [])
  return (
    <StrictMode>
      <ErrorBoundary>
        <ToastProvider>
          <BrowserRouter>
            <MarketProvider initialRef={initialRef()}>
              <EntryParams />
              <UpdateToast />
              <Suspense fallback={null}>
                <Routes>
                  <Route element={<Shell />}>
                    <Route path="/" element={<Home />} />
                    <Route path="/p/:code" element={<Home />} />
                    <Route path="/t/:category" element={<CategoryPage />} />
                    <Route path="/shop" element={<ShopPage />} />
                    <Route path="/search" element={<SearchPage />} />
                    <Route path="/quick" element={<QuickOrderPage />} />
                    <Route path="/about" element={<AboutPage />} />
                    <Route path="/brands/:brand" element={<BrandPage />} />
                    <Route path="/wekome" element={<Navigate to="/brands/wekome" replace />} />
                    <Route path="/coming-soon" element={<Navigate to="/brands/wekome" replace />} />
                    <Route path="/cart" element={<CartPage />} />
                    <Route path="/checkout" element={<CheckoutPage />} />
                    <Route path="/orders" element={<MyOrdersPage />} />
                    <Route path="/me" element={<MyYQPage />} />
                    <Route path="/o/:token" element={<TrackingPage />} />
                    <Route path="/:slug" element={<Home />} />
                  </Route>
                  <Route path="/c/:token" element={<LegacyRedirect />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            </MarketProvider>
          </BrowserRouter>
        </ToastProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
