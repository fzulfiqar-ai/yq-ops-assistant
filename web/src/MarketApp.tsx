import { lazy, StrictMode, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ToastProvider } from '@/components/Toast'
import { MarketProvider } from '@/market/MarketContext'
import { UpdateToast } from '@/market/components/UpdateToast'
import { isSlugShaped, rememberRef } from '@/market/lib/device'
import { setSource } from '@/market/lib/events'
import { watchInstallPrompt } from '@/market/lib/install'
import { startVitals } from '@/market/lib/vitals'
import Home from '@/market/pages/Home'

const SearchPage = lazy(() => import('@/market/pages/SearchPage'))
const CartPage = lazy(() => import('@/market/pages/CartPage'))
const CheckoutPage = lazy(() => import('@/market/pages/CheckoutPage'))
const TrackingPage = lazy(() => import('@/market/pages/TrackingPage'))
const MyOrdersPage = lazy(() => import('@/market/pages/MyOrdersPage'))
const LegacyRedirect = lazy(() => import('@/market/pages/LegacyRedirect'))

/**
 * YQ Marketplace — the merchant front door (built with VITE_APP=market, its own hostname).
 * Same codebase as the portal, none of its weight: no session client, no query cache, no
 * charts. Home is imported statically so the catalog is one download, the rest lazy.
 *
 * Routes: / home · /{slug} salesman storefront · /p/{code} product · /t/{category} ·
 * /search · /cart · /checkout · /orders · /o/{token} tracking · /c/{token} legacy redirect.
 * A slug is matched LAST, after every app route, and validated against the payload.
 */

/** Reads ?ref / ?src on any entry URL (shared product links carry them) before the page renders. */
function EntryParams() {
  const [params] = useSearchParams()
  const location = useLocation()
  useEffect(() => {
    const ref = (params.get('ref') || '').toLowerCase()
    if (ref && isSlugShaped(ref)) rememberRef(ref)
    setSource(params.get('src'))
  }, [params, location.pathname])
  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [location.pathname])
  return null
}

function firstPathSlug(): string | null {
  const seg = window.location.pathname.split('/').filter(Boolean)
  if (seg.length !== 1) return null
  const s = seg[0].toLowerCase()
  const reserved = new Set(['search', 'cart', 'checkout', 'orders', 'p', 't', 'o', 'c', 'join'])
  return !reserved.has(s) && isSlugShaped(s) ? s : null
}

export default function MarketApp() {
  useEffect(() => {
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
                  <Route path="/" element={<Home />} />
                  <Route path="/p/:code" element={<Home />} />
                  <Route path="/t/:category" element={<Home />} />
                  <Route path="/search" element={<SearchPage />} />
                  <Route path="/cart" element={<CartPage />} />
                  <Route path="/checkout" element={<CheckoutPage />} />
                  <Route path="/orders" element={<MyOrdersPage />} />
                  <Route path="/o/:token" element={<TrackingPage />} />
                  <Route path="/c/:token" element={<LegacyRedirect />} />
                  <Route path="/:slug" element={<Home />} />
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
