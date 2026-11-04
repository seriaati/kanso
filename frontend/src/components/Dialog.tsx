import { useEffect, useRef, type ReactNode } from 'react'
import styles from './Dialog.module.css'

type Props = { title: string; onClose: () => void; children: ReactNode; /** For a list, not a form. */ wide?: boolean }

// A native modal <dialog>, so focus stays inside.
export function Dialog({ title, onClose, children, wide }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])
  return (
    <dialog
      ref={ref}
      className={`${styles.dialog} ${wide ? styles.wide : ''}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault() // closed by unmounting, so the closing is the caller's
        onClose()
      }}
      onKeyDown={(event) => {
        // Esc closes the dialog only, never the thread behind it (App's shortcuts).
        if (event.key !== 'Escape') return
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose() // the backdrop
      }}
    >
      <h2 className={styles.title}>{title}</h2>
      {children}
    </dialog>
  )
}
