import { useRef, useState, type MouseEvent, type PointerEvent } from 'react'

/** Right is Done, left is Delete. */
export type SwipeDirection = 'right' | 'left'

const START = 10 // px of sideways movement before a touch counts as a swipe
const FAR_ENOUGH = 0.35 // of the row's width, at most MAX_DISTANCE, to act on release
const MAX_DISTANCE = 120
const RESET_MS = 300

// Touch only (mice keep the hover actions); vertical movement is left to scrolling (pan-y).
export function useSwipe(onSwipe: (direction: SwipeDirection) => void) {
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  const gesture = useRef<{ id: number; x: number; y: number; active: boolean; offset: number } | null>(null)
  const swiped = useRef(false) // swallow the click that ends a swipe

  const end = () => {
    gesture.current = null
    setDragging(false)
  }

  const handlers = {
    onPointerDown(event: PointerEvent<HTMLElement>) {
      if (event.pointerType !== 'touch' || !event.isPrimary) return
      swiped.current = false
      gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, active: false, offset: 0 }
    },
    onPointerMove(event: PointerEvent<HTMLElement>) {
      const g = gesture.current
      if (!g || event.pointerId !== g.id) return
      const dx = event.clientX - g.x
      if (!g.active) {
        if (Math.abs(event.clientY - g.y) > Math.abs(dx)) return end() // scrolling
        if (Math.abs(dx) < START) return
        g.active = true
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
