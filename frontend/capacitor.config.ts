import type { CapacitorConfig } from '@capacitor/cli'

// The Android app (android/, built by scripts/build-android.sh): a WebView on the user's own Kanso
// server, so the app stays same-origin and the session cookie and CSRF checks work as in a browser.
// No server here: the app asks for it at its first start (ServerActivity) and adds it as server.url
// at runtime (MainActivity.load), so one app works with any Kanso.
const config: CapacitorConfig = {
  appId: 'app.kanso',
  appName: 'Kanso',
  // Never shown: the app loads the server. Capacitor needs a page to bundle all the same.
  webDir: 'android/web',
  // See-through until the page paints, so the window's paper color (res/values) shows, not white.
  backgroundColor: '#00000000',
}

export default config
