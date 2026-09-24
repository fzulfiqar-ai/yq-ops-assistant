import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

/**
 * One codebase, two builds (16-Sep-2026):
 *   VITE_APP=portal (default)  the internal ops portal — unchanged.
 *   VITE_APP=market            the merchant marketplace — its own title, description, Open Graph
 *                              tags and manifest, and the catalog prefetch pointed at the
 *                              token-less /public/market endpoint (see public/catalog-prefetch.js).
 * scripts/deploy_cf.py and .github/workflows/cf-deploy.yml set VITE_APP per build (Cloudflare Workers
 * static assets host both since 20-Sep-2026; the Vercel projects only redirect there); locally:
 * `VITE_APP=market npm run build` (or .env.local).
 */

const MARKET = {
  title: 'YQ Marketplace · YQ Bahrain',
  description: 'Where Bahrain restocks. Mobile accessories wholesale from YQ Bahrain — trade prices, real warehouse stock, every order confirmed by your representative.',
  ogTitle: 'YQ Marketplace',
  manifest: '/market.webmanifest',
  /** the logo plum (src/market/market.css --m-plum) */
  theme: '#6D4091',
  /** product photos live here; preconnect so the LCP image does not pay DNS+TLS after the catalog lands */
  imageOrigin: 'https://vofwqcqmdwdidueqxtxy.supabase.co',
  fonts: ['/fonts/instrument-sans-v1.woff2', '/fonts/sora-v1.woff2'],
}

/**
 * The first painted pixel. Until 20-Sep-2026 a session's first load showed a bare night gradient
 * with no mark on it (Shell.tsx paints the opening chunk's Suspense fallback) — measured at ~250 ms
 * on a warm load and seconds on a cold one, so the longest-lived frame of the sequence was the one
 * that read as "still loading". This node ships the same night field and the same logo tile, at the
 * place components/Splash.tsx composes them, in the served HTML: the first frame is the brand's own
 * night field (see the measurement note on BOOT_LOGO for what the tile does and does not manage)
 * and the opening then mounts on top of an identical picture. Splash removes this node in a layout
 * effect as it mounts and, seeing it, does not replay the tile's spring.
 *
 * Kept deliberately small: market build only (the portal shares this index.html and is a light app),
 * outside #root (React clears its own container on the first commit), z-index 89 — one below the
 * opening's 90 — and no copy, because UI text lives in src/market/strings.ts. The tile geometry
 * mirrors Splash.tsx: the box above the horizon (top → 60 % of the height), the tile bottom-aligned
 * in it above the space the two lines take (91 / 97 / 112 px at the 3 type steps), 84 px (96 px from
 * lg), radius 22 px, and hidden under 520 px of height exactly as the overlay hides it. The 8 s
 * animation is only a safety net: if the opening chunk never arrives, the frame hides itself instead
 * of sealing the app off (animation-delay survives the reduced-motion kill switch, the duration does
 * not — reduced motion simply gets the same cut, not a fade).
 */
const BOOT_CSS = [
  "#yq-boot{position:fixed;inset:0;z-index:89;background-color:#140F24;background-image:linear-gradient(135deg,#2A1259,#140F24);animation:yq-boot-out 320ms linear 8s both}",
  "#yq-boot span{position:absolute;inset:0 0 40%;display:flex;align-items:flex-end;justify-content:center;padding-bottom:91px}",
  "#yq-boot i{position:relative;display:block;overflow:hidden;width:84px;height:84px;border-radius:22px;box-shadow:0 0 0 1px rgba(255,255,255,.16),0 0 56px 12px rgba(165,88,251,.28),0 22px 48px -18px rgba(165,88,251,.85),0 8px 20px -8px rgba(8,4,20,.6)}",
  "#yq-boot img{display:block;width:100%;height:100%}",
  "@media(min-width:640px){#yq-boot span{padding-bottom:97px}}",
  "@media(min-width:1024px){#yq-boot span{padding-bottom:112px}#yq-boot i{width:96px;height:96px}}",
  "@media(max-height:520px){#yq-boot i{display:none}}",
  "@keyframes yq-boot-out{to{opacity:0;visibility:hidden}}",
].join('')
/**
 * The tile's mark travels IN the HTML, as a data: URI — one request fewer on the critical path, and
 * the bytes are there the moment the node is parsed. ~5 KB of HTML, cheaper than the round trip.
 * If the file is ever missing the URL is used, so a build never fails over the boot frame.
 *
 * MEASURED, 20-Sep-2026, so the next person does not chase it again: on a real load the night field
 * paints immediately but this tile does NOT reach the screen before React mounts — the image raster
 * is starved by the main thread parsing the app, and the node is removed before a frame carrying it
 * is produced. (Block the app's JS and the same markup paints the tile in ~100 ms, which is how we
 * know the geometry and the CSS are right.) Inlining and `decoding="sync"` both help the browser as
 * much as it can be helped from here; a first frame that carries the mark on a cold phone needs the
 * mark to be a CSS paint (an inline SVG or a drawn tile), not an image — we do not have the logo as
 * a vector. What the node does deliver today: the first frame is the brand's night field instead of
 * the cream shell, and the overlay mounts on top of an identical picture.
 *
 * The overlay that takes over (components/Splash.tsx) loads the same file by URL, so the head also
 * preloads it as an image: the mark must not blink out in the frame where the node hands over.
 */
const BOOT_LOGO = (() => {
  try {
    const file = fileURLToPath(new URL('./public/yq-logo-160.webp', import.meta.url))
    return `data:image/webp;base64,${readFileSync(file).toString('base64')}`
  } catch {
    return '/yq-logo-160.webp'
  }
})()
const BOOT_HTML = `<div id="yq-boot" aria-hidden="true"><span><i><img src="${BOOT_LOGO}" alt="" width="96" height="96" decoding="sync" fetchpriority="high" /></i></span></div>`

/**
 * The market build rewrites the shared index.html: its own entry (src/main.market.tsx — one
 * network hop less than main.tsx → MarketApp), its own head (title, OG, manifest, plum theme,
 * viewport-fit for the floating nav, its own self-hosted font preloads in place of the portal's,
 * storage preconnect) and `data-app="market"` on <html> (token scope) and on the prefetch script.
 * `order: 'pre'` so the entry swap happens before Vite resolves the module graph.
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
          // the portal's font preloads (index.html <!-- portal-fonts --> block) → the market's own
          // preloaded woff2, the storage preconnect and the boot-logo preload
          .replace(/\s*<!-- portal-fonts[\s\S]*?<!-- \/portal-fonts -->/, `\n    <link rel="preconnect" href="${MARKET.imageOrigin}" />\n    ${fontLinks}\n    <link rel="preload" as="image" type="image/webp" href="/yq-logo-160.webp" fetchpriority="high" />`)
          // Vite has already substituted %VITE_API_URL% by the time this runs — match the shape, not the placeholder.
          .replace(/(<script src="\/catalog-prefetch\.js" data-api="[^"]*")/, '$1 data-app="market"')
          // the first painted frame (see BOOT_CSS): night field + logo tile before React exists
          .replace('</head>', `<style>${BOOT_CSS}</style>
  </head>`)
          .replace('<div id="root"></div>', `${BOOT_HTML}
    <div id="root"></div>`)
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
    // Nothing beyond the glob: includeAssets used to list the same files again (duplicate manifest
    // entries) and pulled in PNGs the market never renders.
    includeAssets: [],
    workbox: {
      // The precache is the offline shell: the chunks, the stylesheet, index.html, the SVGs and the
      // market's two fonts. NOT the PNGs (R6): the manifest icons, the shortcut icons, the Apple
      // touch icon and the portal's logo are never rendered by the market — the OS fetches the
      // manifest icons at install time on its own — yet they were 281 KB of the 1.1 MB precache
      // every new browser downloaded on the first idle after load.
      globPatterns: ['**/*.{js,css,html,ico,svg,webp,webmanifest,woff2}'],
      // never let the worker answer the version file or any API path from cache; the portal's
      // fonts are copied into every build (shared public/) but the market never loads them
      globIgnores: ['version.json', 'fonts/inter-*.woff2', 'fonts/space-grotesk-*.woff2'],
      navigateFallback: '/index.html',
      navigateFallbackDenylist: [/^\/public\//, /^\/api\//, /\/version\.json$/],
      cleanupOutdatedCaches: true,
      clientsClaim: false,
      skipWaiting: false,
      runtimeCaching: [
        {
          // the same-origin edge copy of the catalog (web/workers/market.js, /api/market[?ref=]):
          // the worker holds the last answer for a day so a merchant offline, or with the whole
          // edge unreachable, still opens the shelf; 4 s at the network first because the edge
          // answers a stale copy in well under that and only a cold-origin MISS takes longer
          urlPattern: /^https?:\/\/[^/]+\/api\/market(\?.*)?$/,
          handler: 'NetworkFirst',
          options: { cacheName: 'yq-market-catalog', networkTimeoutSeconds: 4, expiration: { maxEntries: 8, maxAgeSeconds: 86400 } },
        },
        {
          // the API's own copy — the fallback path (no Worker in front) keeps the same protection
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
  // commit sha when Vercel / GitHub know it, or the BUILD_ID scripts/deploy_cf.py
  // passes; a CLI deploy without Git integration still gets a unique id per deployment
  const buildId =
    env.VERCEL_GIT_COMMIT_SHA?.slice(0, 12) ||
    env.CF_PAGES_COMMIT_SHA?.slice(0, 12) ||
    env.GITHUB_SHA?.slice(0, 12) ||
    env.BUILD_ID?.slice(0, 12) ||
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
