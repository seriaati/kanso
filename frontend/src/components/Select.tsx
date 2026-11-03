import { useState } from 'react'
import { Icon } from './Icon'
import { Menu } from './Menu'
import styles from './Select.module.css'

export type SelectOption = { value: string; label: string }

type Props = {
  /** What is picked, for screen readers and the menu: "From", "Assistant chat". */
  label: string
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  id?: string
  disabled?: boolean
  className?: string
}

// In place of a native <select>, whose open list can't be styled: a button that opens a Menu.
export function Select({ label, value, options, onChange, id, disabled, className = '' }: Props) {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)
  const current = options.find((option) => option.value === value)?.label ?? ''
  return (
    <>
      <button
        type="button"
        id={id}
        className={`${styles.select} ${className}`}
        aria-label={`${label}: ${current}`}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        disabled={disabled}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget)}
      >
        <span className={styles.value}>{current}</span>
        <Icon name="chevronDown" size={14} />
      </button>
      {anchor && (
        <Menu
          anchor={anchor}
          label={label}
          items={options.map((option) => ({
            label: option.label,
            checked: option.value === value,
            onSelect: () => onChange(option.value),
          }))}
          onClose={() => setAnchor(null)}
        />
      )}
    </>
  )
}
