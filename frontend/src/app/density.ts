import { useSyncExternalStore } from 'react'

// Per browser, wide screens only: phones keep their two-line rows either way.

export type Density = 'compact' | 'comfortable'

const KEY = 'kanso.density'
const listeners = new Set<() => void>()

function stored(): Density {
  try {
    return localStorage.getItem(KEY) === 'compact' ? 'compact' : 'comfortable'
  } catch {
    return 'comfortable' // storage blocked
  }
}

let density = stored()

export function setDensity(next: Density) {
  density = next
  try {
    if (next === 'comfortable') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, next)
  } catch {
    // storage blocked: kept until the page reloads
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useDensity(): Density {
  return useSyncExternalStore(subscribe, () => density)
}
