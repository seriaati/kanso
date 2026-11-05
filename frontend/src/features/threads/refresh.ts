import { useEffect, useEffectEvent, useState, type RefObject } from 'react'

const START = 10 // px, as in swipe.ts: less is a press, more sideways is a row's swipe
const RESISTANCE = 0.5 // the indicator follows the finger at half its speed
const TRIGGER = 64 // px the indicator is pulled to refresh on release
const MAX = 96

/** Refresh, from a button or (on a phone) by pulling the list down at its top. `pull` is how far
 * the indicator is pulled (px), `refreshing` how long the refresh lasts. */
export function useRefresh(scrollRef: RefObject<HTMLElement | null>, onRefresh: () => Promise<unknown>, pullable: boolean) {
  const [pull, setPull] = useState(0)
  const [refreshing, setRefreshing] = useState(false)

  const refresh = () => {
    if (refreshing) return
    setRefreshing(true)
    void onRefresh().finally(() => setRefreshing(false))
  }
  const pulled = useEffectEvent(refresh)

  useEffect(() => {
    const list = scrollRef.current
    if (!pullable || !list) return
    let start: { x: number; y: number } | null = null
    let pulling = false
    let distance = 0
    const reset = () => {
      start = null
      pulling = false
      distance = 0
      setPull(0)
    }
    const down = (event: TouchEvent) => {
      const touch = event.touches[0]
      start = list.scrollTop <= 0 && event.touches.length === 1 ? { x: touch.clientX, y: touch.clientY } : null
    }
    const move = (event: TouchEvent) => {
      if (!start) return
      const dx = event.touches[0].clientX - start.x
      const dy = event.touches[0].clientY - start.y
      if (!pulling) {
        if (Math.abs(dx) < START && Math.abs(dy) < START) return
        if (dy < 0 || Math.abs(dx) > Math.abs(dy) || list.scrollTop > 0) return reset() // a scroll or a swipe
        pulling = true
      }
      distance = Math.min(MAX, Math.max(0, dy - START) * RESISTANCE)
      setPull(distance)
      if (event.cancelable) event.preventDefault() // no overscroll glow under the indicator
    }
    const up = () => {
      if (pulling && distance >= TRIGGER) pulled()
      reset()
    }
    list.addEventListener('touchstart', down, { passive: true })
    list.addEventListener('touchmove', move, { passive: false })
    list.addEventListener('touchend', up)
    list.addEventListener('touchcancel', reset)
    return () => {
      list.removeEventListener('touchstart', down)
      list.removeEventListener('touchmove', move)
      list.removeEventListener('touchend', up)
      list.removeEventListener('touchcancel', reset)
    }
  }, [scrollRef, pullable])

  return { pull, ready: pull >= TRIGGER, refreshing, refresh }
}
