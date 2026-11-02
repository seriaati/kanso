import { useEffect, useState, type ReactNode, type RefObject } from 'react'
import { parseAddresses, useContacts } from '../../app/compose'
import type { Address } from '../../app/mail'
import styles from './Composer.module.css'

type Props = {
  label: string
  value: Address[]
  onChange: (value: Address[]) => void
  inputRef?: RefObject<HTMLInputElement | null>
  children?: ReactNode
}

export function RecipientField({ label, value, onChange, inputRef, children }: Props) {
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  useEffect(() => {
    const timer = setTimeout(() => setQuery(text.trim()), 120)
    return () => clearTimeout(timer)
  }, [text])
  const contacts = useContacts(query)
  const chosen = new Set(value.map((a) => a.email.toLowerCase()))
  const suggestions = open && text.trim() ? (contacts.data ?? []).filter((c) => !chosen.has(c.email.toLowerCase())) : []
  const id = `recipients-${label.toLowerCase()}`

  const add = (addresses: Address[]) => {
    const seen = new Set(chosen)
    const fresh = addresses.filter((a) => !seen.has(a.email.toLowerCase()) && seen.add(a.email.toLowerCase()))
    if (fresh.length) onChange([...value, ...fresh])
  }
  /** Returns whether anything was left that isn't an address. */
  const commit = (typed = text) => {
    const { addresses, rest } = parseAddresses(typed)
    add(addresses)
    setText(rest)
    setActive(0)
    return rest !== ''
  }
  const pick = (address: Address) => {
    add([address])
    setText('')
    setActive(0)
  }

  return (
    <div className={styles.row}>
      <label className={styles.rowLabel} htmlFor={id}>
        {label}
      </label>
      <div className={styles.recipients}>
        {value.map((address) => (
          <span key={address.email} className={styles.chip} title={address.email}>
            {address.name || address.email}
            <button
              type="button"
              aria-label={`Remove ${address.name || address.email}`}
              onClick={() => onChange(value.filter((a) => a.email !== address.email))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={id}
          ref={inputRef}
          className={styles.recipientInput}
          value={text}
          autoComplete="off"
          role="combobox"
          aria-expanded={suggestions.length > 0}
          aria-controls={`${id}-list`}
          onChange={(event) => {
            const typed = event.target.value
            setOpen(true)
            if (/[,;\n]/.test(typed)) commit(typed)
            else setText(typed)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            setOpen(false)
            commit()
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              if (!suggestions.length) return
              event.preventDefault()
              const step = event.key === 'ArrowDown' ? 1 : -1
              setActive((active + step + suggestions.length) % suggestions.length)
            } else if (event.key === 'Enter' || (event.key === 'Tab' && text.trim())) {
              if (event.metaKey || event.ctrlKey) return // send
              const suggestion = suggestions[Math.min(active, suggestions.length - 1)]
              if (suggestion) {
                event.preventDefault()
                pick(suggestion)
              } else if (text.trim()) {
                event.preventDefault()
                commit()
              }
            } else if (event.key === 'Backspace' && !text && value.length) {
              onChange(value.slice(0, -1))
            } else if (event.key === 'Escape' && suggestions.length) {
              event.preventDefault() // close the suggestions, not the composer
              event.stopPropagation()
              setOpen(false)
            }
          }}
        />
        {suggestions.length > 0 && (
          <ul className={styles.suggestions} id={`${id}-list`} role="listbox">
            {suggestions.map((contact, i) => (
              <li key={contact.email} role="option" aria-selected={i === active}>
                <button
                  type="button"
                  tabIndex={-1}
                  onMouseDown={(event) => event.preventDefault()} // keep the input focused
                  onClick={() => pick(contact)}
                >
                  <span className={styles.suggestionName}>{contact.name || contact.email}</span>
                  {contact.name && <span className={styles.suggestionEmail}>{contact.email}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {children}
    </div>
  )
}
