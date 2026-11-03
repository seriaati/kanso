import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Icon, type IconName } from './Icon'
import styles from './Menu.module.css'

// checked: one choice among the items (Select), marked with a check.
export type MenuItem = { icon?: IconName; label: string; onSelect: () => void; shortcut?: string; checked?: boolean }

type Props = {
  /** A button (the menu shows below it) or a right-click point. */
  anchor: HTMLElement | { x: number; y: number }
  label: string
  items: MenuItem[]
  onClose: () => void
}

// Fixed to the window, so a scrolling container doesn't clip it.
export function Menu({ anchor, label, items, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{ top: number; left: number; minWidth?: number } | null>(null)
  const button = anchor instanceof HTMLElement ? anchor : null
  useLayoutEffect(() => {
    const menu = ref.current
    if (!menu) return
    const box = button ? button.getBoundingClientRect() : null
    const x = box ? box.left : (anchor as { x: number }).x
    let top = box ? box.bottom + 4 : (anchor as { y: number }).y
    // Near the foot of the window, open upwards.
    if (top + menu.offsetHeight > window.innerHeight - 8) top = (box ? box.top - 4 : top) - menu.offsetHeight
    // At least as wide as its button.
    const width = Math.max(menu.offsetWidth, box?.width ?? 0)
    setPlace({ top: Math.max(8, top), left: Math.max(8, Math.min(x, window.innerWidth - width - 8)), minWidth: box?.width })
  }, [anchor, button])
  // Focus the checked item, else the first, once placed: while hidden it can't take focus.
  const placed = place !== null
  useEffect(() => {
    if (!placed) return
    const menu = ref.current
    ;(menu?.querySelector<HTMLButtonElement>('[aria-checked="true"]') ?? menu?.querySelector('button'))?.focus()
  }, [placed])
  useEffect(() => {
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (ref.current?.contains(target) || button?.contains(target)) return
      onClose()
      // Like system context menus, the dismissing click does nothing else. The swallow is
      // dropped after the press, in case it never becomes a click.
      if (button) return
      const swallow = (click: MouseEvent) => {
        click.preventDefault()
        click.stopPropagation()
      }
      window.addEventListener('click', swallow, { capture: true, once: true })
      window.addEventListener('pointerup', () => setTimeout(() => window.removeEventListener('click', swallow, true)), {
        capture: true,
        once: true,
      })
    }
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault() // not also "back to the list"
      onClose()
      button?.focus()
    }
    const onScroll = (event: Event) => !ref.current?.contains(event.target as Node) && onClose()
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('scroll', onScroll, true)
    window.addEventListener('blur', onClose)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('blur', onClose)
    }
  }, [button, onClose])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const all = [...event.currentTarget.querySelectorAll('button')]
    const at = all.indexOf(document.activeElement as HTMLButtonElement)
    all[(at + (event.key === 'ArrowDown' ? 1 : all.length - 1)) % all.length]?.focus()
  }

  return (
    <div
      ref={ref}
      className={styles.menu}
      role="menu"
      aria-label={label}
      style={place ?? { visibility: 'hidden' }}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role={item.checked === undefined ? 'menuitem' : 'menuitemradio'}
          aria-checked={item.checked}
          className={styles.item}
          onClick={() => {
            onClose()
            item.onSelect()
          }}
        >
          {item.icon && <Icon name={item.icon} size={15} />}
          <span className={styles.label}>{item.label}</span>
          {item.shortcut && <kbd>{item.shortcut}</kbd>}
          {item.checked && <Icon name="check" size={15} />}
        </button>
      ))}
    </div>
  )
}
