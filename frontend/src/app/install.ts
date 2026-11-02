import { useSyncExternalStore } from 'react'

// Installing kanso to the home screen. Chrome, Edge and Samsung Internet offer a prompt, started
// from kanso's own button; Safari on iOS has none, so it gets the steps instead.

type InstallPrompt = Event & { prompt: () => Promise<void> }

const standalone = window.matchMedia('(display-mode: standalone)')
const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)

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

/** How kanso can be installed here; null when it is installed or can't be. */
export type Install = 'prompt' | 'ios' | null

function state(): Install {
  if (installed) return null
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
