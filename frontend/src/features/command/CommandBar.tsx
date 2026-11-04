import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Account } from '../../app/accounts'
import type { NamedLabel } from '../../app/mail'
import { href, mailboxNav, type Route, type View } from '../../app/nav'
import { Icon } from '../../components/Icon'
import { LabelMark } from '../../components/LabelMark'
import { hasOperator, useSearchSuggest } from '../threads/SearchSuggest'
import styles from './CommandBar.module.css'

/** `to`: where it goes; null for Search, which turns the bar into the search box. */
type Command = { key: string; name: string; detail: string; icon: ReactNode; to: string | null }

type Props = {
  route: Route
  accounts: Account[]
  labels: NamedLabel[]
  onNavigate: (to: string) => void
  onClose: () => void
}

/** Ctrl/⌘+K: go to a mailbox, label or page, or search mail (as the search box does). */
export function CommandBar({ route, accounts, labels, onNavigate, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const [text, setText] = useState('')
  const [searching, setSearching] = useState(false)
  const [active, setActive] = useState(0)
  const suggest = useSearchSuggest(
    inputRef,
    labels.map((l) => l.name),
    styles.suggestions,
  )

  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])

  const { accountId } = route
  const view = (to: View) => href({ view: to, threadId: null, accountId })
  const searchHref = (q: string) => view({ kind: 'search', q })
  const search = (q: string) => q && onNavigate(searchHref(q))
  // From here on the bar is the search box, its suggestions too.
  const startSearch = () => {
    const input = inputRef.current
    if (!input) return
    input.value = ''
    setText('')
    setSearching(true)
    input.focus()
    suggest.inputProps.onFocus()
  }

  const commands: Command[] = [
    { key: 'search', name: 'Search', detail: 'Search mail', icon: <Icon name="search" size={15} />, to: null },
    ...mailboxNav.map(({ mailbox, name, icon }) => ({
      key: `mailbox:${mailbox}`,
      name,
      detail: 'Mailbox',
      icon: <Icon name={icon} size={15} />,
      to: view({ kind: 'mailbox', mailbox }),
    })),
    ...labels.map((label) => ({
      key: `label:${label.name}`,
      name: label.name,
      detail: 'Label',
      icon: <LabelMark color={label.color} icon={label.icon} size={15} />,
      to: view({ kind: 'label', name: label.name }),
    })),
    ...(accountId === null
      ? []
      : [
          { key: 'cleanup', name: 'Clean up', detail: 'Page', icon: <Icon name="sparkle" size={15} />, to: href({ ...route, threadId: null, page: 'cleanup' }) },
          { key: 'labels', name: 'Labels', detail: 'Page', icon: <Icon name="label" size={15} />, to: href({ ...route, threadId: null, page: 'labels' }) },
        ]),
    { key: 'settings', name: 'Settings', detail: 'Page', icon: <Icon name="settings" size={15} />, to: '/settings' },
    ...(accounts.length > 1
      ? accounts
          .filter((a) => a.id !== accountId)
          .map((a) => ({
            key: `account:${a.id}`,
            name: a.email,
            detail: 'Switch account',
            icon: <Icon name="accounts" size={15} />,
            to: href({ ...route, threadId: null, accountId: a.id, page: route.page === 'settings' ? undefined : route.page }),
          }))
      : []),
  ]

  // Names that start with the text first, then those that contain it; a search for it last.
  const typed = text.trim().toLowerCase()
  const matches = typed
    ? [
        ...commands.filter((c) => c.name.toLowerCase().startsWith(typed)),
        ...commands.filter((c) => !c.name.toLowerCase().startsWith(typed) && c.name.toLowerCase().includes(typed)),
        { key: 'search-text', name: `Search mail for “${text.trim()}”`, detail: '', icon: <Icon name="search" size={15} />, to: searchHref(text.trim()) },
      ]
    : commands
  const shown = searching ? [] : matches
  const current = Math.min(active, shown.length - 1)

  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [current])

  const choose = (command: Command) => (command.to === null ? startSearch() : onNavigate(command.to))

  const onInput = (value: string) => {
    setText(value)
    setActive(0)
    // Search syntax, e.g. from:, turns the bar into the search box.
    if (!searching && hasOperator(value)) {
      setSearching(true)
      suggest.inputProps.onInput()
    }
  }

  return (
    <dialog
      ref={ref}
      className={styles.bar}
      aria-label="Go to or search"
      onCancel={(event) => {
        event.preventDefault() // closed by unmounting
        onClose()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose() // the backdrop
      }}
    >
      <form
        className={styles.field}
        role="search"
        onSubmit={(event) => {
          event.preventDefault()
          if (searching) search(inputRef.current?.value.trim() ?? '')
          else if (shown[current]) choose(shown[current])
        }}
      >
        <Icon name="search" size={15} strokeWidth={2} />
        {searching && <span className={styles.chip}>Search</span>}
        <input
          ref={inputRef}
          className={styles.input}
          type="text"
          placeholder={searching ? 'Search mail' : 'Go to a mailbox, label or page, or search'}
          aria-label={searching ? 'Search mail' : 'Go to or search'}
          aria-controls={searching ? undefined : 'command-list'}
          aria-activedescendant={!searching && current >= 0 ? `command-${current}` : undefined}
          autoComplete="off"
          autoFocus
          {...(searching ? suggest.inputProps : {})}
          onInput={(event) => {
            if (searching) suggest.inputProps.onInput()
            onInput(event.currentTarget.value)
          }}
          onKeyDown={(event) => {
            if (searching) {
              suggest.inputProps.onKeyDown(event)
              if (event.defaultPrevented) return
              if (event.key === 'Backspace' && !event.currentTarget.value) {
                event.preventDefault()
                setSearching(false) // back to the commands
              }
              return
            }
            if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
            event.preventDefault()
            const step = event.key === 'ArrowDown' ? 1 : -1
            setActive((current + step + shown.length) % shown.length)
          }}
        />
      </form>
      {searching && suggest.list}
      {shown.length > 0 && (
        <ul ref={listRef} className={styles.list} id="command-list" role="listbox" aria-label="Commands">
          {shown.map((command, i) => (
            <li key={command.key} id={`command-${i}`} role="option" aria-selected={i === current}>
              <button type="button" tabIndex={-1} onMouseMove={() => setActive(i)} onClick={() => choose(command)}>
                <span className={styles.icon}>{command.icon}</span>
                <span className={styles.name}>{command.name}</span>
                {command.detail && <span className={styles.detail}>{command.detail}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </dialog>
  )
}
