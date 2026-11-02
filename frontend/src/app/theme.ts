import { useSyncExternalStore } from 'react'

// public/theme.js applies the stored pick before the first paint.

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

export function useTheme(): { theme: Theme; dark: boolean } {
  const picked = useSyncExternalStore(subscribe, () => theme)
  const systemDark = useSyncExternalStore(subscribe, () => system.matches)
  return { theme: picked, dark: picked === 'dark' || (picked === 'system' && systemDark) }
}
