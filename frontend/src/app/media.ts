import { useSyncExternalStore } from 'react'

/** Phone width: one screen at a time (list, thread, settings), the sidebar as a drawer and the
 * assistant as a bottom sheet. The CSS modules use the same `@media (max-width: 767px)`; wider
 * screens get the split view unchanged. */
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
