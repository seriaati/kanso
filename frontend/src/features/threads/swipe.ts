import { useEffect, useRef, useState, type MouseEvent, type PointerEvent } from 'react'

/** Right is Done, left is Delete. */
export type SwipeDirection = 'right' | 'left'

const START = 10 // px of movement before a touch counts as a swipe (or a scroll), not a press
const FAR_ENOUGH = 0.35 // of the row's width, at most MAX_DISTANCE, to act on release
const MAX_DISTANCE = 120
const RESET_MS = 300
const LONG_PRESS_MS = 500

// Touch only (mice keep the hover actions); vertical movement is left to scrolling (pan-y).
// A touch held still for LONG_PRESS_MS calls onLongPress instead.
export function useSwipe(onSwipe: (direction: SwipeDirection) => void, onLongPress?: (point: { x: number; y: number }) => void) {
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  const gesture = useRef<{ id: number; x: number; y: number; active: boolean; offset: number } | null>(null)
  const swiped = useRef(false) // swallow the click that ends a swipe
  const pressTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(pressTimer.current), [])

  const end = () => {
    clearTimeout(pressTimer.current)
    gesture.current = null
    setDragging(false)
  }

  const handlers = {
    onPointerDown(event: PointerEvent<HTMLElement>) {
      if (event.pointerType !== 'touch' || !event.isPrimary) return
      swiped.current = false
      gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, active: false, offset: 0 }
      const point = { x: event.clientX, y: event.clientY }
      if (onLongPress)
        pressTimer.current = setTimeout(() => {
          end()
          swallowNextClick()
          onLongPress(point)
        }, LONG_PRESS_MS)
    },
    onPointerMove(event: PointerEvent<HTMLElement>) {
      const g = gesture.current
      if (!g || event.pointerId !== g.id) return
      const dx = event.clientX - g.x
      if (!g.active) {
        const dy = event.clientY - g.y
        if (Math.abs(dx) < START && Math.abs(dy) < START) return // still a press
        if (Math.abs(dy) > Math.abs(dx)) return end() // scrolling
        g.active = true
        clearTimeout(pressTimer.current)
        swiped.current = true
        setDragging(true)
        event.currentTarget.setPointerCapture(event.pointerId)
      }
      g.offset = dx
      setOffset(dx)
    },
    onPointerUp(event: PointerEvent<HTMLElement>) {
      const g = gesture.current
      if (!g || event.pointerId !== g.id) return
      end()
      if (!g.active) return
      const width = event.currentTarget.clientWidth
      if (Math.abs(g.offset) < Math.min(width * FAR_ENOUGH, MAX_DISTANCE)) return setOffset(0)
      const direction: SwipeDirection = g.offset > 0 ? 'right' : 'left'
      setOffset(direction === 'right' ? width : -width)
      onSwipe(direction)
      // A row that stays in the list (e.g. Done in a label view) slides back.
      setTimeout(() => setOffset(0), RESET_MS)
    },
    onPointerCancel() {
      end()
      setOffset(0)
    },
    onClickCapture(event: MouseEvent<HTMLElement>) {
      if (!swiped.current) return
      swiped.current = false
      event.preventDefault()
      event.stopPropagation()
    },
  }
  return { offset, dragging, handlers }
}

// The finger lifting after a long press can click whatever opened under it (the sheet's items), so
// the click is swallowed wherever it lands. Dropped after the lift, in case no click comes.
function swallowNextClick() {
  const swallow = (click: globalThis.MouseEvent) => {
    click.preventDefault()
    click.stopPropagation()
  }
  const drop = () => {
    window.removeEventListener('pointerup', drop, true)
    window.removeEventListener('pointercancel', drop, true)
    setTimeout(() => window.removeEventListener('click', swallow, true))
  }
  window.addEventListener('click', swallow, { capture: true, once: true })
  window.addEventListener('pointerup', drop, true)
  window.addEventListener('pointercancel', drop, true)
}
