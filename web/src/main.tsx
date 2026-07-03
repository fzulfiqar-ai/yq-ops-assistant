import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'
import { ThemeProvider } from '@/lib/theme'
import { AuthProvider } from '@/lib/auth'
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
// password, a cold Railway instance is already up (fire-and-forget, errors ignored).
import('./lib/api').then(({ API_BASE }) => {
  if (API_BASE) fetch(`${API_BASE}/health`).catch(() => {})
})

const INTRO = [{ text: 'YQ Bahrain' }, { text: 'Mobile Accessories' }, { text: 'Intelligence' }]

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <ArcRevealHero storageKey="yq-intro-v2" greetings={INTRO} greetingHold={900} revealDuration={1900} className="!min-h-0">
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
  </StrictMode>,
)
