import { useSyncExternalStore } from 'react'
import { registerSW } from 'virtual:pwa-register'

// A new version installs in the background and waits for "Reload" or the next cold start.

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

export function useUpdateReady(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => ready,
  )
}

export function applyUpdate() {
  void updateSW?.(true)
}
