import { createRoot } from 'react-dom/client'
import './market/market.css'
import MarketApp from './MarketApp'

/**
 * The marketplace entry. vite.config.ts (marketHtml) points index.html at THIS file for the
 * market build, so the merchant's first download is the market shell itself — not main.tsx
 * followed by a second round-trip for MarketApp. Ships market.css only (never index.css).
 */
createRoot(document.getElementById('root')!).render(<MarketApp />)
