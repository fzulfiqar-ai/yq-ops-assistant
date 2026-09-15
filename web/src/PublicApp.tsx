import { lazy, StrictMode, Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ToastProvider } from '@/components/Toast'
import ShopPage from '@/pages/shop/ShopPage'

const OrderStatus = lazy(() => import('@/pages/OrderStatus'))

/**
 * The public front door — what a merchant gets from a WhatsApp link.
 *
 * Only the two pages such a link can open: the shop (/c/{token}) and an order's
 * status (/o/{token}). No session, no intro animation, no query cache, no portal
 * routes — see main.tsx for why they live on their own lane. The shop is imported
 * statically so the catalog is one download, not a chain of them.
 */
export default function PublicApp() {
  return (
    <StrictMode>
      <ErrorBoundary>
        <ToastProvider>
          <BrowserRouter>
            <Suspense fallback={null}>
              <Routes>
                <Route path="/c/:token" element={<ShopPage />} />
                <Route path="/o/:orderToken" element={<OrderStatus />} />
              </Routes>
            </Suspense>
          </BrowserRouter>
        </ToastProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
