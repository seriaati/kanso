import { execSync } from 'node:child_process'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// Drops @fontsource's legacy .woff fallbacks so they're never emitted. A PostCSS plugin, so it
// also sees the rules styles/fonts-full.css pulls in with @import.
const woff2Only = {
  postcssPlugin: 'kanso-woff2-only',
  Declaration: {
    src: (decl: { value: string }) => {
      decl.value = decl.value.replace(/,\s*url\([^)]*\.woff\) format\(['"]?woff['"]?\)/g, '')
    },
  },
}

// The service worker caches the app shell only: no mail, and nothing under /api.
const pwa = VitePWA({
  registerType: 'prompt', // a new version waits until the user reloads (app/update.ts)
  injectRegister: false, // registered from main.tsx
  includeManifestIcons: false, // the precache globs below have them already
  manifest: {
    name: 'Kanso',
    short_name: 'Kanso',
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
    // Installed, Kanso can be the browser's mailto: handler (app/compose.ts, linkedDraft).
    protocol_handlers: [{ protocol: 'mailto', url: '/compose?mailto=%s' }],
  },
  workbox: {
    // Only the first paint's Latin fonts; the rest (~1 MB of CSS and subsets) is cached on use.
    globPatterns: ['**/*.{js,css,html,svg,png,webmanifest}', 'assets/{zen-maru-gothic,shippori-mincho-b1}-latin-*.woff2'],
    globIgnores: ['**/fonts-full-*.css'],
    navigateFallback: '/index.html',
    navigateFallbackDenylist: [/^\/api(\/|$)/],
    cleanupOutdatedCaches: true,
    importScripts: ['/push-sw.js'],
    runtimeCaching: [
      {
        // Hashed, so they never change. Bounded, so mail in many scripts can't grow it endlessly.
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

// The commit this build is from, for Settings → About: KANSO_COMMIT in the Docker build (which has
// no .git), else git's, else "dev".
function commit(): string {
  if (process.env.KANSO_COMMIT) return process.env.KANSO_COMMIT
  try {
    return execSync('git rev-parse HEAD', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
  } catch {
    return 'dev'
  }
}

export default defineConfig({
  plugins: [react(), pwa],
  define: { __KANSO_COMMIT__: JSON.stringify(commit()) },
  css: { postcss: { plugins: [woff2Only] } },
  // `bun run dev` talks to the backend running on its default port.
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
