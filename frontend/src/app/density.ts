import { useSyncExternalStore } from 'react'

// How tall the thread list's rows are on a wide screen: compact (the default) or comfortable.
// This browser's own pick, kept in localStorage and set in the account menu. Phones keep their
// two-line rows either way.

export type Density = 'compact' | 'comfortable'

const KEY = 'kanso.density'
const listeners = new Set<() => void>()

function stored(): Density {
  try {
    return localStorage.getItem(KEY) === 'comfortable' ? 'comfortable' : 'compact'
  } catch {
    return 'compact' // storage blocked
  }
}

let density = stored()

export function setDensity(next: Density) {
  density = next
  try {
    if (next === 'compact') localStorage.removeItem(KEY)
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
