import { useSyncExternalStore } from 'react'

// The CSS modules use the same `@media (max-width: 767px)`.
const PHONE = '(max-width: 767px)'

const phone = window.matchMedia(PHONE)

export function usePhone(): boolean {
  return useSyncExternalStore(
    (listener) => {
      phone.addEventListener('change', listener)
      return () => phone.removeEventListener('change', listener)
    },
    () => phone.matches,
  )
}
