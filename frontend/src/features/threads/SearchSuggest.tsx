import { useEffect, useId, useState, type KeyboardEvent, type RefObject } from 'react'
import { useContacts } from '../../app/compose'
import styles from './SearchSuggest.module.css'

/** `name`: a person or label, not search syntax. */
type Suggestion = { label: string; detail?: string; insert: string; name?: boolean }

const OPERATORS: [string, string][] = [
  ['from:', 'Sender'],
  ['to:', 'Recipient'],
  ['cc:', 'Copied recipient'],
  ['bcc:', 'Blind-copied recipient'],
  ['with:', 'Anyone on the message'],
  ['subject:', 'Words in the subject'],
  ['label:', 'Has a label'],
  ['is:', 'Unread, starred, important, sent, draft…'],
  ['in:', 'Inbox, sent, trash, spam, anywhere'],
  ['has:', 'Has an attachment'],
  ['filename:', 'Attachment name or type'],
  ['list:', 'From a mailing list'],
  ['after:', 'On or after a date'],
  ['before:', 'Before a date'],
  ['newer_than:', 'Within the last 7d, 2w, 3m, 1y'],
  ['older_than:', 'More than 7d, 2w, 3m, 1y ago'],
  ['larger:', 'Bigger than a size, e.g. 5M'],
  ['smaller:', 'Smaller than a size, e.g. 100K'],
]
const SYNTAX: Suggestion[] = [
  { label: '-', detail: 'Exclude what matches', insert: '-' },
  { label: 'OR', detail: 'Match either side', insert: 'OR ' },
  { label: '( )', detail: 'Group terms', insert: '(' },
  { label: '" "', detail: 'Exact phrase', insert: '"' },
]
/** Whether the text uses search syntax, e.g. from:, so it is a search rather than a command. */
export function hasOperator(text: string) {
  return [...text.matchAll(/(?:^|[\s(])-?(\w+):/g)].some(([, name]) => OPERATORS.some(([op]) => op === `${name.toLowerCase()}:`))
}

const PEOPLE = new Set(['from', 'to', 'cc', 'bcc', 'with'])
const VALUES: Record<string, [string, string][]> = {
  is: [
    ['unread', 'Unread'],
    ['read', 'Read'],
    ['starred', 'Starred'],
    ['important', 'Important'],
    ['sent', 'Sent by you'],
    ['draft', 'Drafts'],
  ],
  in: [
    ['inbox', 'In the inbox'],
    ['sent', 'Sent by you'],
    ['draft', 'Drafts'],
    ['trash', 'In the trash'],
    ['spam', 'In spam'],
    ['anywhere', 'Including trash and spam'],
  ],
  has: [['attachment', 'Has an attachment']],
  newer_than: [
    ['1d', 'Last day'],
    ['7d', 'Last week'],
    ['1m', 'Last month'],
    ['1y', 'Last year'],
  ],
  older_than: [
    ['7d', 'More than a week ago'],
    ['1m', 'More than a month ago'],
    ['1y', 'More than a year ago'],
  ],
  larger: [
    ['1M', 'Over 1 MB'],
    ['5M', 'Over 5 MB'],
    ['10M', 'Over 10 MB'],
  ],
  smaller: [
    ['100K', 'Under 100 KB'],
    ['1M', 'Under 1 MB'],
  ],
}

/** Where the word at the caret starts: after a space or parenthesis outside quotes. */
function tokenStart(before: string) {
  let start = 0
  let quoted = false
  for (let i = 0; i < before.length; i++) {
    const c = before[i]
    if (c === '"') quoted = !quoted
    else if (!quoted && (c === ' ' || c === '(' || c === ')')) start = i + 1
  }
  return before[start] === '-' ? start + 1 : start
}

const quote = (value: string) => (/[\s()]/.test(value) ? `"${value}"` : value)

/** Operator and value suggestions for a search box, in Gmail's search syntax. */
export function useSearchSuggest(inputRef: RefObject<HTMLInputElement | null>, labels: string[], className?: string) {
  const [text, setText] = useState<string | null>(null) // null: closed
  const [caret, setCaret] = useState(0)
  const [active, setActive] = useState(-1)
  const id = useId()

  const before = (text ?? '').slice(0, caret)
  const start = tokenStart(before)
  const token = before.slice(start)
  const operator = /^(\w+):"?(.*)$/.exec(token)
  const key = operator?.[1].toLowerCase()
  const value = operator?.[2] ?? ''

  const [contactQuery, setContactQuery] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setContactQuery(key && PEOPLE.has(key) ? value.trim() : ''), 120)
    return () => clearTimeout(timer)
  }, [key, value])
  const contacts = useContacts(contactQuery)

  let suggestions: Suggestion[] = []
  if (text === null) {
    suggestions = []
  } else if (key && PEOPLE.has(key)) {
    suggestions = value.trim()
      ? (contacts.data ?? []).map((c) => ({ label: c.name || c.email, detail: c.name ? c.email : undefined, insert: `${key}:${c.email} `, name: true }))
      : []
  } else if (key === 'label') {
    const typed = value.toLowerCase()
    suggestions = labels
      .filter((name) => name.toLowerCase().includes(typed))
      .slice(0, 8)
      .map((name) => ({ label: name, insert: `label:${quote(name)} `, name: true }))
  } else if (key && key in VALUES) {
    suggestions = VALUES[key]
      .filter(([v]) => v.toLowerCase().startsWith(value.toLowerCase()))
      .map(([v, detail]) => ({ label: `${key}:${v}`, detail, insert: `${key}:${v} ` }))
  } else if (!operator && token) {
    const typed = token.toLowerCase()
    suggestions = OPERATORS.filter(([name]) => name.startsWith(typed)).map(([name, detail]) => ({ label: name, detail, insert: name }))
  } else if (!text.trim()) {
    suggestions = [...OPERATORS.map(([name, detail]) => ({ label: name, detail, insert: name })), ...SYNTAX]
  }
  // A value already typed in full needs no suggestion.
  if (suggestions.length === 1 && suggestions[0].insert.trim() === token.trim()) suggestions = []
  const shown = Math.min(active, suggestions.length - 1)

  const sync = () => {
    const input = inputRef.current
    if (!input) return
    setText(input.value)
    setCaret(input.selectionStart ?? input.value.length)
    setActive(-1)
  }

  const pick = (suggestion: Suggestion) => {
    const input = inputRef.current
    if (!input || text === null) return
    const after = text.slice(caret)
    input.value = text.slice(0, start) + suggestion.insert + after
    const end = start + suggestion.insert.length
    input.setSelectionRange(end, end)
    sync()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!suggestions.length) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      // -1 (nothing highlighted, so Enter searches) is one of the stops.
      const stops = suggestions.length + 1
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive(((((shown + 1 + step) % stops) + stops) % stops) - 1)
    } else if ((event.key === 'Enter' && shown >= 0) || event.key === 'Tab') {
      if (event.key === 'Tab' && event.shiftKey) return
      event.preventDefault()
      pick(suggestions[Math.max(shown, 0)])
    } else if (event.key === 'Escape') {
      event.preventDefault() // close the list, keep the query
      event.stopPropagation()
      setText(null)
    }
  }

  const inputProps = {
    role: 'combobox',
    'aria-expanded': suggestions.length > 0,
    'aria-controls': id,
    'aria-autocomplete': 'list' as const,
    'aria-activedescendant': shown >= 0 ? `${id}-${shown}` : undefined,
    autoComplete: 'off',
    onFocus: sync,
    onInput: sync,
    onClick: sync,
    onKeyUp: (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'Home' || event.key === 'End') sync()
    },
    onBlur: () => setText(null),
    onKeyDown,
  }

  const list =
    suggestions.length > 0 ? (
      <ul className={`${styles.list} ${className ?? ''}`} id={id} role="listbox" aria-label="Search suggestions">
        {suggestions.map((suggestion, i) => (
          <li key={suggestion.insert + i} id={`${id}-${i}`} role="option" aria-selected={i === shown}>
            <button
              type="button"
              tabIndex={-1}
              onMouseDown={(event) => event.preventDefault()} // the input keeps its focus
              onClick={() => pick(suggestion)}
            >
              <span className={suggestion.name ? styles.label : `${styles.label} ${styles.code}`}>{suggestion.label}</span>
              {suggestion.detail && <span className={styles.detail}>{suggestion.detail}</span>}
            </button>
          </li>
        ))}
      </ul>
    ) : null

  return { inputProps, list }
}
