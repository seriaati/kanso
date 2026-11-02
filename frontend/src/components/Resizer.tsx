import { useRef, type KeyboardEvent, type PointerEvent } from 'react'
import styles from './Resizer.module.css'

const MIN = 320 // the panel before the handle
const MIN_AFTER = 380 // what stays for the one after it
const STEP = 24 // arrow keys

type Props = {
  label: string
  width: number
  /** `done` once a drag or key press ends. */
  onResize: (width: number, done: boolean) => void
  onReset: () => void
}

// Resizes the element before it.
export function Resizer({ label, width, onResize, onReset }: Props) {
  const drag = useRef<{ x: number; width: number; max: number; last: number } | null>(null)

  const measure = (handle: HTMLElement) => {
    const box = (handle.previousElementSibling as HTMLElement).getBoundingClientRect()
    return { width: box.width, max: Math.max(MIN, window.innerWidth - box.left - MIN_AFTER) }
  }
  const clamp = (value: number, max: number) => Math.round(Math.min(Math.max(value, MIN), max))

  const end = (event: PointerEvent<HTMLElement>) => {
    const done = drag.current
    if (!done) return
    drag.current = null
    event.currentTarget.releasePointerCapture(event.pointerId)
    delete document.documentElement.dataset.resizing
    if (done.last !== done.width) onResize(done.last, true)
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={width}
      aria-valuemin={MIN}
      tabIndex={0}
      title="Drag to resize (double-click to reset)"
      className={styles.resizer}
      onPointerDown={(event) => {
        if (event.button !== 0) return
        event.preventDefault()
        const now = measure(event.currentTarget)
        drag.current = { x: event.clientX, ...now, last: now.width }
        event.currentTarget.setPointerCapture(event.pointerId)
        document.documentElement.dataset.resizing = '' // no transitions or text selection meanwhile
      }}
      onPointerMove={(event) => {
        const start = drag.current
        if (!start) return
        start.last = clamp(start.width + event.clientX - start.x, start.max)
        onResize(start.last, false)
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onDoubleClick={onReset}
      onKeyDown={(event: KeyboardEvent<HTMLElement>) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault()
        const now = measure(event.currentTarget)
        onResize(clamp(now.width + (event.key === 'ArrowRight' ? STEP : -STEP), now.max), true)
      }}
    />
  )
}
