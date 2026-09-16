import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

/**
 * One codebase, two builds (16-Sep-2026):
 *   VITE_APP=portal (default)  the internal ops portal — unchanged.
 *   VITE_APP=market            the merchant marketplace — its own title, description, Open Graph
 *                              tags and manifest, and the catalog prefetch pointed at the
 *                              token-less /public/market endpoint (see public/catalog-prefetch.js).
 * Vercel sets VITE_APP per project; locally: `VITE_APP=market npm run build` (or .env.local).
 */

const MARKET = {
  title: 'YQ Marketplace · YQ Bahrain',
  description: 'Order mobile accessories at trade prices from YQ Bahrain — cables, chargers, TWS and more, delivered by your YQ representative.',
  ogTitle: 'YQ Marketplace',
  manifest: '/market.webmanifest',
  /** the logo plum (src/market/market.css --m-plum) */
  theme: '#6D4091',
  /** product photos live here; preconnect so the LCP image does not pay DNS+TLS after the catalog lands */
  imageOrigin: 'https://vofwqcqmdwdidueqxtxy.supabase.co',
  fonts: ['/fonts/instrument-sans-v1.woff2', '/fonts/sora-v1.woff2'],
}

/**
 * The market build rewrites the shared index.html: its own entry (src/main.market.tsx — one
 * network hop less than main.tsx → MarketApp), its own head (title, OG, manifest, plum theme,
 * viewport-fit for the floating nav, self-hosted font preloads instead of the Google Fonts
 * stylesheet, storage preconnect) and `data-app="market"` on <html> (token scope) and on the
 * prefetch script. `order: 'pre'` so the entry swap happens before Vite resolves the module graph.
 */
function marketHtml(): Plugin {
  return {
    name: 'yq-market-html',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        const fontLinks = MARKET.fonts
          .map((f) => `<link rel="preload" as="font" type="font/woff2" crossorigin href="${f}" />`)
          .join('\n    ')
        return html
          .replace('<html lang="en">', '<html lang="en" dir="ltr" data-app="market">')
          .replace('src="/src/main.tsx"', 'src="/src/main.market.tsx"')
          .replace(/<title>[^<]*<\/title>/, `<title>${MARKET.title}</title>`)
          .replace(/<meta name="description" content="[^"]*" \/>/, `<meta name="description" content="${MARKET.description}" />`)
          .replace(/<meta property="og:title" content="[^"]*" \/>/, `<meta property="og:title" content="${MARKET.ogTitle}" />`)
          .replace(/<meta property="og:description" content="[^"]*" \/>/, `<meta property="og:description" content="${MARKET.description}" />`)
          .replace('<meta name="viewport" content="width=device-width, initial-scale=1.0" />', '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />')
          .replace(
            '<meta name="theme-color" content="#6d28d9" />',
            `<meta name="theme-color" content="${MARKET.theme}" />\n    <meta name="color-scheme" content="light" />\n    <meta name="mobile-web-app-capable" content="yes" />\n    <meta name="robots" content="noindex, nofollow" />\n    <link rel="manifest" href="${MARKET.manifest}" />`,
          )
          // the two Google Fonts preconnects + the blocking stylesheet → self-hosted, preloaded woff2
          .replace(/\s*<link rel="preconnect" href="https:\/\/fonts\.googleapis\.com" \/>/, '')
          .replace(/\s*<link rel="preconnect" href="https:\/\/fonts\.gstatic\.com" crossorigin \/>/, `\n    <link rel="preconnect" href="${MARKET.imageOrigin}" />`)
          .replace(/\s*<!-- A plain stylesheet on purpose:[\s\S]*?<link\s+href="https:\/\/fonts\.googleapis\.com[^"]*"\s+rel="stylesheet"\s*\/>/, `\n    ${fontLinks}`)
          // Vite has already substituted %VITE_API_URL% by the time this runs — match the shape, not the placeholder.
          .replace(/(<script src="\/catalog-prefetch\.js" data-api="[^"]*")/, '$1 data-app="market"')
      },
    },
  }
}

/** `/version.json` — what the running app compares itself against (market/lib/sw.ts). Emitted at
 *  build time so it always names the deployment that is live; VITE_SW_KILL=1 makes every client
 *  unregister its worker and clear its caches on the next check. */
function versionJson(buildId: string, kill: boolean): Plugin {
  return {
    name: 'yq-version-json',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify({ build: buildId, kill, at: new Date().toISOString() }) })
    },
  }
}

function marketPwa(apiUrl: string) {
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const api = esc((apiUrl || 'https://yq-ops-assistant.onrender.com').replace(/\/$/, ''))
  return VitePWA({
    // Registered by market/lib/sw.ts (prompt flow + kill-switch), not by an injected snippet.
    injectRegister: false,
    registerType: 'prompt',
    filename: 'sw.js',
    // public/market.webmanifest is linked by marketHtml(); the plugin must not add a second one.
    manifest: false,
    includeAssets: ['favicon.svg', 'apple-touch-icon.png', 'yq-icon-32.png', 'yq-icon-512.png', 'market.webmanifest', 'fonts/*.woff2'],
    workbox: {
      globPatterns: ['**/*.{js,css,html,ico,png,svg,webp,webmanifest,woff2}'],
      // never let the worker answer the version file or any API path from cache
      globIgnores: ['version.json'],
      navigateFallback: '/index.html',
      navigateFallbackDenylist: [/^\/public\//, /^\/api\//, /\/version\.json$/],
      cleanupOutdatedCaches: true,
      clientsClaim: false,
      skipWaiting: false,
      runtimeCaching: [
        {
          // the catalog only (not quote/order/event): serve stale for a day if the API is asleep
          urlPattern: new RegExp(`^${api}/public/market(\\?.*)?$`),
          handler: 'NetworkFirst',
          options: { cacheName: 'yq-market-catalog', networkTimeoutSeconds: 4, expiration: { maxEntries: 8, maxAgeSeconds: 86400 } },
        },
        {
          // three WebP sizes per photo now, so the cap is doubled
          urlPattern: /^https:\/\/[a-z0-9]+\.supabase\.co\/storage\/v1\/object\/public\//,
          handler: 'CacheFirst',
          options: { cacheName: 'yq-market-images', expiration: { maxEntries: 600, maxAgeSeconds: 30 * 86400 } },
        },
      ],
    },
  })
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }
  // The marketplace Vercel project always builds the market entry, even for a preview branch whose
  // environment has no VITE_APP (Vercel exposes the project's production hostname at build time).
  if (!env.VITE_APP && /^yq-marketplace[.-]/.test(env.VERCEL_PROJECT_PRODUCTION_URL || '')) {
    env.VITE_APP = 'market'
    process.env.VITE_APP = 'market'
  }
  const isMarket = env.VITE_APP === 'market'
  // commit sha when Vercel/GitHub know it; a CLI deploy without Git integration still gets a unique id per deployment
  const buildId =
    env.VERCEL_GIT_COMMIT_SHA?.slice(0, 12) ||
    env.GITHUB_SHA?.slice(0, 12) ||
    env.VERCEL_DEPLOYMENT_ID?.replace(/^dpl_/, '').slice(0, 12) ||
    `local-${Date.now().toString(36)}`
  return {
    define: { __BUILD_ID__: JSON.stringify(buildId) },
    // Inline PostCSS so the market build gets its own Tailwind config (own tokens, market-only
    // content glob); the portal keeps tailwind.config.js and its CSS output is unchanged.
    css: {
      postcss: {
        plugins: [tailwindcss({ config: isMarket ? './tailwind.market.config.js' : './tailwind.config.js' }), autoprefixer()],
      },
    },
    build: isMarket
      ? {
          rollupOptions: {
            output: {
              // stable, auditable chunk names for the budget check (no "Chrome-xxxx" grab-bag)
              manualChunks(id: string) {
                if (/node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react'
                if (/node_modules[\\/]react-router/.test(id)) return 'router'
                if (/node_modules[\\/]minisearch[\\/]/.test(id)) return 'search'
                return undefined
              },
            },
          },
        }
      : undefined,
    plugins: [
      react(),
      ...(isMarket
        ? [marketHtml(), marketPwa(env.VITE_API_URL || ''), versionJson(buildId, env.VITE_SW_KILL === '1')]
        : [
            VitePWA({
              // SELF-DESTRUCT. The API host changed (Railway -> Render) and the API URL is
              // baked into the bundle at build time, so every client still holding a cached
              // pre-migration bundle keeps calling the dead Railway host and hangs forever on
              // "Waking the server...". Render's logs confirmed those browsers never reached
              // the new API at all. A hard refresh does NOT reliably evict a controlling
              // service worker, so this ships a SW that unregisters itself and drops its
              // caches — every stale client self-heals on next load, no user action needed.
              //
              // This trades offline/PWA caching for correctness, which is the right call for
              // an online-only ops portal. Re-enable only once every client is known good.
              // (The marketplace build gets its own, real service worker with a version
              // kill-switch in the PWA step — not this one.)
              selfDestroying: true,
              registerType: 'autoUpdate',
              includeAssets: ['favicon.svg', 'apple-touch-icon.png', 'yq-icon-32.png'],
              manifest: {
                name: 'YQ Bahrain · AI Portal',
                short_name: 'YQ Portal',
                description: 'Run your whole business from one place.',
                theme_color: '#6d28d9',
                background_color: '#140f24',
                display: 'standalone',
                start_url: '/',
                icons: [
                  { src: '/yq-icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
                  { src: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' },
                ],
              },
            }),
          ]),
    ],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: { port: 5173 },
  }
})
