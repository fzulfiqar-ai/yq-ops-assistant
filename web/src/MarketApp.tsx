import { lazy, StrictMode, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useSearchParams, useNavigate } from 'react-router-dom'
import { UpdateToast } from '@/market/components/UpdateToast'
import { isSlugShaped, rememberRef } from '@/market/lib/device'
import { setSource } from '@/market/lib/events'
import { watchInstallPrompt } from '@/market/lib/install'
import { startErrorReporting } from '@/market/lib/errors'
import { startVitals } from '@/market/lib/vitals'
import { MarketProvider } from '@/market/MarketContext'
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

/** Reads ?ref / ?src on any entry URL (shared product links carry them); scrolls to top on page changes (not overlays). */
function EntryParams() {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    const ref = (params.get('ref') || '').toLowerCase()
    if (ref && isSlugShaped(ref)) rememberRef(ref)
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
  }, [params, location.pathname, navigate])
  useEffect(() => {
    const st = location.state as { panel?: string } | null
    if (st?.panel) return // product panel over the same page: keep the scroll position
    window.scrollTo({ top: 0 })
  }, [location.pathname, location.state])
  return null
}

function firstPathSlug(): string | null {
  const seg = window.location.pathname.split('/').filter(Boolean)
  if (seg.length !== 1) return null
  const s = seg[0].toLowerCase()
  return !RESERVED.has(s) && isSlugShaped(s) ? s : null
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
            <MarketProvider initialRef={firstPathSlug()}>
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
