import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import styles from './DatePicker.module.css'
import { Icon } from './Icon'
import select from './Select.module.css'

type Props = {
  /** What the date is, for screen readers: "From", "Older than". */
  label: string
  /** A local day, yyyy-mm-dd; '' for none. */
  value: string
  onChange: (value: string) => void
  min?: string
  max?: string
  /** Shown while empty, e.g. "Today". */
  placeholder?: string
  /** Offers Clear, back to empty. */
  clearable?: boolean
  className?: string
}

// Days are yyyy-mm-dd strings, which compare in date order.
const pad = (n: number) => String(n).padStart(2, '0')
const toDay = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
function fromDay(day: string): Date {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date)
}
function addDays(day: string, days: number): string {
  const date = fromDay(day)
  date.setDate(date.getDate() + days)
  return toDay(date)
}
/** The same day of another month, or that month's last day. */
function addMonths(day: string, months: number): string {
  const date = fromDay(day)
  const target = new Date(date.getFullYear(), date.getMonth() + months, 1)
  const last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate()
  target.setDate(Math.min(date.getDate(), last))
  return toDay(target)
}
const clamp = (day: string, min?: string, max?: string) => (min && day < min ? min : max && day > max ? max : day)

const shown = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' })
const spoken = new Intl.DateTimeFormat(undefined, { dateStyle: 'full' })
const monthTitle = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' })
const weekdayName = new Intl.DateTimeFormat(undefined, { weekday: 'narrow' })

// The locale's first day of the week (0: Sunday), where the browser knows it.
const weekStart = (() => {
  try {
    type WeekInfo = { firstDay: number }
    const locale = new Intl.Locale(navigator.language) as Intl.Locale & { getWeekInfo?: () => WeekInfo; weekInfo?: WeekInfo }
    return ((locale.getWeekInfo?.() ?? locale.weekInfo)?.firstDay ?? 0) % 7
  } catch {
    return 0
  }
})()
// 2023-01-01 was a Sunday.
const weekdays = Array.from({ length: 7 }, (_, i) => weekdayName.format(new Date(2023, 0, 1 + ((weekStart + i) % 7))))

// In place of <input type="date">, whose calendar can't be styled: a button that opens one.
export function DatePicker({ label, value, onChange, min, max, placeholder = '', clearable, className = '' }: Props) {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)
  const text = value ? shown.format(fromDay(value)) : placeholder
  return (
    <>
      <button
        type="button"
        className={`${select.select} ${className}`}
        aria-label={`${label}: ${text || 'none'}`}
        aria-haspopup="dialog"
        aria-expanded={anchor !== null}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget)}
      >
        <span className={`${select.value} ${value ? '' : styles.placeholder}`}>{text}</span>
        <Icon name="calendar" size={15} />
      </button>
      {anchor && (
        <Calendar
          anchor={anchor}
          label={label}
          value={value}
          min={min}
          max={max}
          clearable={clearable}
          onPick={(day) => {
            setAnchor(null)
            anchor.focus()
            onChange(day)
          }}
          onClose={() => setAnchor(null)}
        />
      )}
    </>
  )
}

type CalendarProps = {
  anchor: HTMLButtonElement
  label: string
  value: string
  min?: string
  max?: string
  clearable?: boolean
  onPick: (day: string) => void
  onClose: () => void
}

// Fixed to the window, like Menu, so a scrolling container doesn't clip it.
function Calendar({ anchor, label, value, min, max, clearable, onPick, onClose }: CalendarProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  const today = toDay(new Date())
  // The day with the keyboard focus; its month is the one shown.
  const [focus, setFocus] = useState(() => clamp(value || today, min, max))
  const keyed = useRef(true) // focus the day: on opening, and after a key moves it

  useLayoutEffect(() => {
    const box = anchor.getBoundingClientRect()
    const own = ref.current
    if (!own) return
    let top = box.bottom + 4
    // Near the foot of the window, open upwards.
    if (top + own.offsetHeight > window.innerHeight - 8) top = box.top - 4 - own.offsetHeight
    setPlace({ top: Math.max(8, top), left: Math.max(8, Math.min(box.left, window.innerWidth - own.offsetWidth - 8)) })
  }, [anchor])

  const placed = place !== null
  useEffect(() => {
    if (!placed || !keyed.current) return
    keyed.current = false
    ref.current?.querySelector<HTMLButtonElement>(`[data-day="${focus}"]`)?.focus()
  }, [placed, focus])

  useEffect(() => {
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (!ref.current?.contains(target) && !anchor.contains(target)) onClose()
    }
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault() // not also "back to the list"
      onClose()
      anchor.focus()
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
  }, [anchor, onClose])

  const first = fromDay(`${focus.slice(0, 7)}-01`)
  const length = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate()
  const blanks = (first.getDay() - weekStart + 7) % 7
  const days = Array.from({ length }, (_, i) => toDay(new Date(first.getFullYear(), first.getMonth(), i + 1)))
  const outside = (day: string) => (min !== undefined && day < min) || (max !== undefined && day > max)
  const canPrev = !min || addDays(days[0], -1) >= min
  const canNext = !max || addDays(days[length - 1], 1) <= max
  const toMonth = (months: number) => setFocus(clamp(addMonths(focus, months), min, max))

  const onGridKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const weekday = (fromDay(focus).getDay() - weekStart + 7) % 7
    const moves: Record<string, () => string> = {
      ArrowLeft: () => addDays(focus, -1),
      ArrowRight: () => addDays(focus, 1),
      ArrowUp: () => addDays(focus, -7),
      ArrowDown: () => addDays(focus, 7),
      PageUp: () => addMonths(focus, -1),
      PageDown: () => addMonths(focus, 1),
      Home: () => addDays(focus, -weekday),
      End: () => addDays(focus, 6 - weekday),
    }
    const move = moves[event.key]
    if (!move) return
    event.preventDefault()
    keyed.current = true
    setFocus(clamp(move(), min, max))
  }

  return (
    <div ref={ref} className={styles.calendar} role="dialog" aria-label={label} style={place ?? { visibility: 'hidden' }}>
      <div className={styles.head}>
        <button type="button" className={styles.nav} aria-label="Previous month" disabled={!canPrev} onClick={() => toMonth(-1)}>
          <Icon name="back" size={15} />
        </button>
        <span className={styles.month} aria-live="polite">
          {monthTitle.format(first)}
        </span>
        <button type="button" className={styles.nav} aria-label="Next month" disabled={!canNext} onClick={() => toMonth(1)}>
          <Icon name="chevronRight" size={15} />
        </button>
      </div>
      <div className={styles.grid} onKeyDown={onGridKey}>
        {weekdays.map((name, i) => (
          <span key={i} className={styles.weekday} aria-hidden="true">
            {name}
          </span>
        ))}
        {Array.from({ length: blanks }, (_, i) => (
          <span key={`blank${i}`} />
        ))}
        {days.map((day, i) => (
          <button
            key={day}
            type="button"
            data-day={day}
            className={styles.day}
            tabIndex={day === focus ? 0 : -1}
            aria-label={spoken.format(fromDay(day))}
            aria-pressed={day === value}
            aria-current={day === today ? 'date' : undefined}
            disabled={outside(day)}
            onClick={() => onPick(day)}
          >
            {i + 1}
          </button>
        ))}
      </div>
      <div className={styles.foot}>
        <button type="button" className={styles.link} disabled={outside(today)} onClick={() => onPick(today)}>
          Today
        </button>
        {clearable && value && (
          <button type="button" className={styles.link} onClick={() => onPick('')}>
            Clear
          </button>
        )}
      </div>
    </div>
  )
}
