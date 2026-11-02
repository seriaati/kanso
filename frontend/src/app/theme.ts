import { useSyncExternalStore } from 'react'

// Light, dark, or the system's setting (the default). The pick is this browser's own, kept in
// localStorage; public/theme.js applies it before the first paint, and the tokens in
// styles/tokens.css follow data-theme on <html> (or, without one, prefers-color-scheme).

export type Theme = 'system' | 'light' | 'dark'

const KEY = 'kanso.theme'
const system = window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()

function stored(): Theme {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system' // storage blocked
  }
}

let theme = stored()

export function setTheme(next: Theme) {
  theme = next
  try {
    if (next === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, next)
  } catch {
    // storage blocked: kept until the page reloads
  }
  if (next === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = next
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  system.addEventListener('change', listener)
  return () => {
    listeners.delete(listener)
    system.removeEventListener('change', listener)
  }
}

/** The theme picked, and whether the page is dark right now. */
export function useTheme(): { theme: Theme; dark: boolean } {
  const picked = useSyncExternalStore(subscribe, () => theme)
  const systemDark = useSyncExternalStore(subscribe, () => system.matches)
  return { theme: picked, dark: picked === 'dark' || (picked === 'system' && systemDark) }
}
