import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// @fontsource CSS lists a legacy .woff fallback after each woff2. Every browser kanso supports
// reads woff2, so drop the fallbacks: unreferenced, the .woff files are never emitted. A PostCSS
// plugin, because it also sees the rules styles/fonts-full.css pulls in with @import.
const woff2Only = {
  postcssPlugin: 'kanso-woff2-only',
  Declaration: {
    src: (decl: { value: string }) => {
      decl.value = decl.value.replace(/,\s*url\([^)]*\.woff\) format\(['"]?woff['"]?\)/g, '')
    },
  },
}

// The PWA: installable, and the service worker keeps the app shell only. No mail is stored
// offline and nothing under /api is ever cached: there is no runtime route for it, and
// navigations to /api (e.g. "Connect account") go to the network.
const pwa = VitePWA({
  registerType: 'prompt', // a new version waits until the user reloads (app/update.ts)
  injectRegister: false, // registered from main.tsx
  includeManifestIcons: false, // the precache globs below have them already
  manifest: {
    name: 'kanso',
    short_name: 'kanso',
    description: 'A calm, self-hosted Gmail client for reaching inbox zero.',
    start_url: '/inbox',
    scope: '/',
    display: 'standalone',
    background_color: '#F1EFE7',
    theme_color: '#F1EFE7',
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
      { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  },
  workbox: {
    // The shell: HTML, JS, CSS, icons, and the Latin fonts the first paint uses. Not the
    // deferred font CSS (~1 MB) or the CJK/other font subsets: they are cached as used, below.
    globPatterns: ['**/*.{js,css,html,svg,png,webmanifest}', 'assets/{zen-maru-gothic,shippori-mincho-b1}-latin-*.woff2'],
    globIgnores: ['**/fonts-full-*.css'],
    navigateFallback: '/index.html',
    navigateFallbackDenylist: [/^\/api(\/|$)/],
    cleanupOutdatedCaches: true,
    runtimeCaching: [
      {
        // Fonts and the deferred font CSS as they are used: hashed, so they never change.
        // Bounded, so a mailbox in many scripts can't grow the cache without end.
        urlPattern: ({ url, sameOrigin }) =>
          sameOrigin && url.pathname.startsWith('/assets/') && /(\.woff2|\/fonts-full-[\w-]+\.css)$/.test(url.pathname),
        handler: 'CacheFirst',
        options: {
          cacheName: 'kanso-fonts',
          expiration: { maxEntries: 200, maxAgeSeconds: 60 * 24 * 3600 },
          cacheableResponse: { statuses: [200] },
        },
      },
    ],
  },
})

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), pwa],
  css: { postcss: { plugins: [woff2Only] } },
  // `bun run dev` talks to the backend running on its default port.
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
