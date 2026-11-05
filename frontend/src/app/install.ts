import { useSyncExternalStore } from 'react'
import { inApp } from './native'

// Installing Kanso to the home screen. Android gets the app (.github/workflows/android.yml) from
// its latest release; elsewhere Chrome, Edge and Samsung Internet offer a prompt, started from
// Kanso's own button, and Safari on iOS has none, so it gets the steps instead.

/** The latest release's APK: the same app for every Kanso server. */
export const APP_DOWNLOAD = 'https://github.com/seriaati/kanso/releases/latest/download/kanso.apk'

type InstallPrompt = Event & { prompt: () => Promise<void> }

const standalone = window.matchMedia('(display-mode: standalone)')
const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
const android = /Android/.test(navigator.userAgent)

let deferred: InstallPrompt | null = null
let installed = standalone.matches || (navigator as Navigator & { standalone?: boolean }).standalone === true
const listeners = new Set<() => void>()
const changed = () => listeners.forEach((listener) => listener())

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault() // no mini-infobar: the button offers it
  deferred = event as InstallPrompt
  changed()
})
window.addEventListener('appinstalled', () => {
  deferred = null
  installed = true
  changed()
})
standalone.addEventListener('change', () => {
  installed = standalone.matches
  changed()
})

/** How Kanso can be installed here; null when it is installed or can't be. */
export type Install = 'android' | 'prompt' | 'ios' | null

function state(): Install {
  if (installed || inApp) return null
  if (android) return 'android'
  if (deferred) return 'prompt'
  return ios ? 'ios' : null
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useInstall(): Install {
  return useSyncExternalStore(subscribe, state)
}

/** A prompt can be shown once; the browser offers a new one later if it was dismissed. */
export function install() {
  const prompt = deferred
  deferred = null
  changed()
  void prompt?.prompt()
}
