import { StrictMode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App.tsx'
import { ThemeProvider } from '@/lib/theme'
import { AuthProvider } from '@/lib/auth'
import { API_BASE } from '@/lib/api'
import { ArcRevealHero } from '@/components/ui/arc-preloader-hero'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ToastProvider } from '@/components/Toast'

// Business data changes ONLY when reports are uploaded, so pages can cache hard:
// revisiting a page within 5 min renders instantly from memory (no spinner, no fetch),
// and cached pages survive navigation for 30 min.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60_000, gcTime: 30 * 60_000, refetchOnWindowFocus: false, retry: 1 },
  },
})

// Warm the API the moment the tab opens — by the time the user has typed their
// password, a cold free-tier instance is already up (fire-and-forget, errors ignored).
if (API_BASE) fetch(`${API_BASE}/health`).catch(() => {})

const INTRO = [{ text: 'YQ Bahrain' }, { text: 'Mobile Accessories' }, { text: 'Intelligence' }]

/** The portal front door: everything except the public shop and order-status links. */
export default function PortalRoot() {
  return (
    <StrictMode>
      <ErrorBoundary>
        <ThemeProvider>
          <ArcRevealHero storageKey="yq-intro-v2" greetings={INTRO} greetingHold={300} revealDuration={600} className="!min-h-0">
            <QueryClientProvider client={queryClient}>
              <ToastProvider>
                <AuthProvider>
                  <App />
                </AuthProvider>
              </ToastProvider>
            </QueryClientProvider>
          </ArcRevealHero>
        </ThemeProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
