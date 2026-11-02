import { useSyncExternalStore } from 'react'

// Per browser. The shades live in tokens.css; public/theme.js applies the stored pick before the first paint.

export const accents = [
  { accent: 'indigo', name: 'Indigo' },
  { accent: 'teal', name: 'Teal' },
  { accent: 'moss', name: 'Moss' },
  { accent: 'ochre', name: 'Ochre' },
  { accent: 'rust', name: 'Rust' },
  { accent: 'plum', name: 'Plum' },
] as const

export type Accent = (typeof accents)[number]['accent']

const KEY = 'kanso.accent'
const listeners = new Set<() => void>()

function stored(): Accent {
  try {
    const value = localStorage.getItem(KEY)
    return accents.find((a) => a.accent === value)?.accent ?? 'indigo'
  } catch {
    return 'indigo' // storage blocked
  }
}

let accent = stored()

export function setAccent(next: Accent) {
  accent = next
  try {
    if (next === 'indigo') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, next)
  } catch {
    // storage blocked: kept until the page reloads
  }
  if (next === 'indigo') delete document.documentElement.dataset.accent
  else document.documentElement.dataset.accent = next
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useAccent(): Accent {
  return useSyncExternalStore(subscribe, () => accent)
}
