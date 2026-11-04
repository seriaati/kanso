import { useEffect, useEffectEvent, useRef, type AriaRole, type ReactNode } from 'react'
import styles from './Sheet.module.css'

const FAR_ENOUGH = 80 // px pulled down to close on release
const FLICK = 0.5 // px/ms: a quicker pull closes from any distance

type Props = {
  label: string
  role?: AriaRole
  className?: string
  onClose: () => void
  children: ReactNode
}

// A sheet from the bottom, within thumb reach. Pulled down from its top, it follows the finger
// and closes on release; a tap on the scrim closes it too.
export function Sheet({ label, role, className, onClose, children }: Props) {
  const sheetRef = useRef<HTMLDivElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const close = useEffectEvent(onClose)

  // Native listeners: React's touchmove is passive, and the pull has to stop the sheet scrolling.
  useEffect(() => {
    const sheet = sheetRef.current
    const scrim = scrimRef.current
    if (!sheet || !scrim) return
    let pull: { y: number; at: number; offset: number; active: boolean } | null = null
    const move = (offset: number) => {
      sheet.style.translate = offset ? `0 ${offset}px` : ''
      scrim.style.opacity = offset ? String(1 - offset / sheet.offsetHeight) : ''
    }
    const onStart = (event: TouchEvent) => {
      pull = event.touches.length === 1 ? { y: event.touches[0].clientY, at: event.timeStamp, offset: 0, active: false } : null
    }
    const onMove = (event: TouchEvent) => {
      if (!pull) return
      const dy = event.touches[0].clientY - pull.y
      if (!pull.active) {
        if (sheet.scrollTop > 0 || dy <= 0) return void (pull = null) // scrolling the sheet
        pull.active = true
        sheet.dataset.dragging = scrim.dataset.dragging = ''
      }
      event.preventDefault()
      pull.offset = Math.max(0, dy)
      move(pull.offset)
    }
    const onEnd = (event: TouchEvent) => {
      const p = pull
      pull = null
      if (!p?.active) return
      delete sheet.dataset.dragging
      delete scrim.dataset.dragging
      const speed = p.offset / (event.timeStamp - p.at)
      if (p.offset < FAR_ENOUGH && speed < FLICK) return move(0)
      if (p.offset >= sheet.offsetHeight) return close() // already off-screen: nothing to animate
      sheet.addEventListener('transitionend', () => close(), { once: true })
      move(sheet.offsetHeight)
    }
    sheet.addEventListener('touchstart', onStart, { passive: true })
    sheet.addEventListener('touchmove', onMove, { passive: false })
    sheet.addEventListener('touchend', onEnd)
    sheet.addEventListener('touchcancel', onEnd)
    return () => {
      sheet.removeEventListener('touchstart', onStart)
      sheet.removeEventListener('touchmove', onMove)
      sheet.removeEventListener('touchend', onEnd)
      sheet.removeEventListener('touchcancel', onEnd)
    }
  }, [])

  return (
    <>
      <div ref={scrimRef} className={styles.scrim} onClick={onClose} onContextMenu={(event) => event.preventDefault()} aria-hidden="true" />
      <div
        ref={sheetRef}
        className={`${styles.sheet} ${className ?? ''}`}
        role={role}
        aria-label={label}
        onContextMenu={(event) => event.preventDefault()} // a long press that opened it ends here
      >
        <button type="button" className={styles.handle} aria-label="Close" onClick={onClose} />
        {children}
      </div>
    </>
  )
}
