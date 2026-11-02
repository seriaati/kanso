import { useSyncExternalStore } from 'react'
import { registerSW } from 'virtual:pwa-register'

// The service worker (vite.config.ts) keeps the app shell for fast and offline starts. A new
// version of kanso installs in the background and waits: the sidebar offers a quiet "Reload",
// and it also takes over the next time kanso starts with no window open.

let ready = false
const listeners = new Set<() => void>()
const UPDATE_CHECK_MS = 60 * 60 * 1000 // an installed app may stay open for days

const updateSW =
  'serviceWorker' in navigator
    ? registerSW({
        onNeedRefresh() {
          ready = true
          listeners.forEach((listener) => listener())
        },
        onRegisteredSW(_url, registration) {
          if (registration) setInterval(() => void registration.update().catch(() => undefined), UPDATE_CHECK_MS)
        },
      })
    : null

/** Whether a new version is waiting. */
export function useUpdateReady(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => ready,
  )
}

/** Switch to the new version: it takes over and the page reloads. */
export function applyUpdate() {
  void updateSW?.(true)
}
