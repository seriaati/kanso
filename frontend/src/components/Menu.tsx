import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Icon, type IconName } from './Icon'
import styles from './Menu.module.css'

export type MenuItem = { icon: IconName; label: string; onSelect: () => void }

type Props = {
  /** The button the menu opens from: it shows below it, kept inside the window. */
  anchor: HTMLElement
  label: string
  items: MenuItem[]
  onClose: () => void
}

/** A small "more actions" menu. Fixed to the window, so a scrolling container doesn't clip it;
 * it closes on Esc, a click outside, scrolling or a pick. */
export function Menu({ anchor, label, items, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  useLayoutEffect(() => {
    const box = anchor.getBoundingClientRect()
    const width = ref.current?.offsetWidth ?? 0
    setPlace({ top: box.bottom + 4, left: Math.max(8, Math.min(box.left, window.innerWidth - width - 8)) })
    ref.current?.querySelector('button')?.focus()
  }, [anchor])
  useEffect(() => {
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (!ref.current?.contains(target) && !anchor.contains(target)) onClose()
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault() // not also "back to the list"
      onClose()
      anchor.focus()
    }
    const onScroll = (event: Event) => !ref.current?.contains(event.target as Node) && onClose()
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('scroll', onScroll, true)
    }
  }, [anchor, onClose])
  return (
    <div ref={ref} className={styles.menu} role="menu" aria-label={label} style={place ?? { visibility: 'hidden' }}>
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className={styles.item}
          onClick={() => {
            onClose()
            item.onSelect()
          }}
        >
          <Icon name={item.icon} size={15} />
          {item.label}
        </button>
      ))}
    </div>
  )
}
