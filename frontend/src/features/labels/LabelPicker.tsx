import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { Icon } from '../../components/Icon'
import styles from './LabelPicker.module.css'

/** A label as the picker offers it, with whether all, some or none of the threads have it. */
export type LabelOption = { name: string; color: string; state: 'all' | 'some' | 'none' }

type Props = {
  options: LabelOption[]
  /** Add the label (or remove it, when every thread has it already). */
  onToggle: (name: string, add: boolean) => void
  onCreate: (name: string) => void
  onClose: () => void
  /** Open upward (from the selection bar at the foot of the list). */
  up?: boolean
}

/** The `l` popover: type to filter, ↑/↓ and Enter to toggle a label, or create a new one. */
export function LabelPicker({ options, onToggle, onCreate, onClose, up }: Props) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const ref = useRef<HTMLDivElement>(null)

  const q = query.trim()
  const shown = options.filter((o) => o.name.toLowerCase().includes(q.toLowerCase()))
  const canCreate = q !== '' && !options.some((o) => o.name.toLowerCase() === q.toLowerCase())
  const count = shown.length + (canCreate ? 1 : 0)
  const index = Math.min(active, Math.max(count - 1, 0))

  // The picker shares its parent with the Label button that opened it: a press on that button is
  // not "outside" (its click closes the picker instead of reopening it).
  const onOutside = useEffectEvent((event: PointerEvent) => {
    if (!ref.current?.parentElement?.contains(event.target as Node)) onClose()
  })
  useEffect(() => {
    const onPointer = (event: PointerEvent) => onOutside(event)
    document.addEventListener('pointerdown', onPointer)
    return () => document.removeEventListener('pointerdown', onPointer)
  }, [])

  const choose = (i: number) => {
    const option = shown[i]
    if (option) onToggle(option.name, option.state !== 'all')
    else if (canCreate) onCreate(q)
    onClose()
  }

  return (
    <div className={`${styles.picker} ${up ? styles.up : ''}`} ref={ref} role="dialog" aria-label="Label as">
      <input
        className={styles.input}
        autoFocus
        placeholder="Label as…"
        aria-label="Filter labels"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setActive(0)
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            setActive((index + (event.key === 'ArrowDown' ? 1 : count - 1)) % Math.max(count, 1))
          } else if (event.key === 'Enter') choose(index)
          else if (event.key === 'Escape') onClose()
          else return
          event.preventDefault()
        }}
      />
      <ul className={styles.list} role="listbox" aria-label="Labels">
        {shown.map((option, i) => (
          <li key={option.name} role="option" aria-selected={i === index} aria-checked={option.state === 'all'}>
            <button type="button" className={styles.option} onMouseEnter={() => setActive(i)} onClick={() => choose(i)}>
              <span className={styles.box} aria-hidden="true">
                {option.state === 'all' ? <Icon name="check" size={11} strokeWidth={2.4} /> : option.state === 'some' ? '–' : null}
              </span>
              <span className={styles.dot} style={{ background: option.color }} aria-hidden="true" />
              <span className={styles.name}>{option.name}</span>
            </button>
          </li>
        ))}
        {canCreate && (
          <li role="option" aria-selected={index === shown.length}>
            <button type="button" className={styles.option} onMouseEnter={() => setActive(shown.length)} onClick={() => choose(shown.length)}>
              <span className={styles.create}>Create “{q}”</span>
            </button>
          </li>
        )}
        {count === 0 && <li className={styles.empty}>Type a name to create a label</li>}
      </ul>
    </div>
  )
}
