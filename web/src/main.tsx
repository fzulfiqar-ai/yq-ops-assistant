import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'
import { ThemeProvider } from '@/lib/theme'
import { AuthProvider } from '@/lib/auth'
import { ArcRevealHero } from '@/components/ui/arc-preloader-hero'
import { ErrorBoundary } from '@/components/ErrorBoundary'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, refetchOnWindowFocus: false, retry: 1 } },
})

const INTRO = [{ text: 'YQ Bahrain' }, { text: 'Mobile Accessories' }, { text: 'Intelligence' }]

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <ArcRevealHero storageKey="yq-intro-v2" greetings={INTRO} greetingHold={900} revealDuration={1900} className="!min-h-0">
          <QueryClientProvider client={queryClient}>
            <AuthProvider>
              <App />
            </AuthProvider>
          </QueryClientProvider>
        </ArcRevealHero>
      </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
)
