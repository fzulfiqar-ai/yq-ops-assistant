import { lazy, StrictMode, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { UpdateToast } from '@/market/components/UpdateToast'
import { isSlugShaped, rememberRef } from '@/market/lib/device'
import { setSource } from '@/market/lib/events'
import { watchInstallPrompt } from '@/market/lib/install'
import { startVitals } from '@/market/lib/vitals'
import { MarketProvider } from '@/market/MarketContext'
import Home from '@/market/pages/Home'
import { Shell } from '@/market/shell/Shell'
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

/**
 * YQ Marketplace — the merchant front door (built with VITE_APP=market, its own hostname, its own
 * entry src/main.market.tsx and stylesheet). Home is static so the catalog is one download; every
 * other page is lazy. The Shell (layout route) owns the chrome for phone / tablet / desktop and
 * mounts the product panel and the ⌘K palette over whichever page is showing.
 *
 * Routes: / home · /{slug} salesman storefront · /p/{code} product deep link · /t/{category} ·
 * /shop · /search · /quick · /cart · /checkout · /orders · /me · /o/{token} tracking ·
 * /c/{token} legacy redirect. A slug is matched LAST and validated against the payload.
 *
 * RESERVED first segments live in four places — here, public/catalog-prefetch.js,
 * app/shop.py (_RESERVED_FALLBACK) and the shop_reserved_slugs table. Keep them equal.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const RESERVED = new Set(['search', 'cart', 'checkout', 'orders', 'p', 't', 'o', 'c', 'join', 'shop', 'me', 'quick'])

/** Reads ?ref / ?src on any entry URL (shared product links carry them); scrolls to top on page changes (not overlays). */
function EntryParams() {
  const [params] = useSearchParams()
  const location = useLocation()
  useEffect(() => {
    const ref = (params.get('ref') || '').toLowerCase()
    if (ref && isSlugShaped(ref)) rememberRef(ref)
    setSource(params.get('src'))
  }, [params, location.pathname])
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
    return watchInstallPrompt()
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
