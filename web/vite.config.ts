import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
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
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { port: 5173 },
})
