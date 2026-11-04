import { useRef, useState } from 'react'
import { Link } from 'react-router'
import type { View } from '../../app/nav'
import { Icon } from '../../components/Icon'
import { useSearchSuggest } from '../threads/SearchSuggest'
import styles from './BottomBar.module.css'

type Props = {
  view: View
  inboxHref: string
  onSearch: (query: string) => void
  /** User label names, suggested after label:. */
  labels: string[]
  onCompose: () => void
  /** Only with AI on. */
  onAsk?: () => void
  onMenu: () => void
}

// The phone's thread list controls, at the bottom within thumb reach. Search takes the bar's
// place, so its box sits just above the keyboard.
export function BottomBar({ view, inboxHref, onSearch, labels, onCompose, onAsk, onMenu }: Props) {
  const [searching, setSearching] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const suggest = useSearchSuggest(inputRef, labels, styles.suggestions)
  const inbox = view.kind === 'mailbox' && view.mailbox === 'inbox'

  if (searching || view.kind === 'search') {
    const query = view.kind === 'search' ? view.q : ''
    return (
      <form
        role="search"
        className={styles.search}
        onSubmit={(event) => {
          event.preventDefault()
          const input = event.currentTarget.elements.namedItem('q') as HTMLInputElement
          const q = input.value.trim()
          if (!q) return
          onSearch(q)
          setSearching(false)
          input.blur()
        }}
      >
        <label className={styles.box}>
          <Icon name="search" size={16} />
          <input
            key={query}
            ref={inputRef}
            name="q"
            type="search"
            enterKeyHint="search"
            placeholder="Search mail"
            aria-label="Search mail"
            defaultValue={query}
            autoFocus={searching}
            {...suggest.inputProps}
          />
        </label>
        {suggest.list}
        <button
          type="button"
          className={styles.cancel}
          onClick={() => {
            setSearching(false)
            if (view.kind === 'search') onSearch('')
          }}
        >
          Cancel
        </button>
      </form>
    )
  }

  return (
    <nav className={styles.bar} aria-label="Main">
      <Link to={inboxHref} className={`${styles.item} ${inbox ? styles.current : ''}`} aria-current={inbox ? 'page' : undefined}>
        <Icon name="inbox" size={22} />
        Inbox
      </Link>
      <button type="button" className={styles.item} onClick={() => setSearching(true)}>
        <Icon name="search" size={22} />
        Search
      </button>
      <button type="button" className={styles.compose} aria-label="Compose" onClick={onCompose}>
        <Icon name="compose" size={22} />
      </button>
      {onAsk && (
        <button type="button" className={styles.item} onClick={onAsk}>
          <Icon name="assistant" size={22} />
          Ask
        </button>
      )}
      <button type="button" className={styles.item} onClick={onMenu}>
        <Icon name="menu" size={22} />
        Menu
      </button>
    </nav>
  )
}
