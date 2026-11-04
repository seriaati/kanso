import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useEffectEvent, useRef, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router'
import { shortDate } from '../../app/format'
import { quickAction, redundant, type Quick } from '../../app/actions'
import type { Account } from '../../app/accounts'
import { useDensity } from '../../app/density'
import type { Bundle, LabelIcon, ThreadSummary } from '../../app/mail'
import { Avatar } from '../../components/Avatar'
import { Icon, type IconName } from '../../components/Icon'
import { LabelMark } from '../../components/LabelMark'
import { InstallButton } from '../install/InstallButton'
import { Avatar as AccountAvatar } from '../sidebar/AccountSwitcher'
import { useSearchSuggest } from './SearchSuggest'
import { useSwipe } from './swipe'
import styles from './ThreadList.module.css'

const quicks: Quick[] = ['done', 'delete', 'star', 'important']

export type LabelChip = { name: string; color: string; icon: LabelIcon | null }

/** What a bundle row can do to all its threads. */
export type BundleAction = 'done' | 'read' | 'delete'

export type ListRow =
  | { kind: 'thread'; thread: ThreadSummary }
  | { kind: 'bundle'; bundle: Bundle; open: boolean; first: boolean }

const ROW = 31
const COMFORTABLE_ROW = 40
const BUNDLE = 32
const BUNDLES_GAP = 17 // above the first bundle: a margin, a hairline and padding
// Phone sizes match ThreadList.module.css (max-width: 767px): two-line rows, 44px+ tap targets.
const PHONE_ROW = 64
const PHONE_BUNDLE = 48

function rowHeight(row: ListRow, phone: boolean, comfortable: boolean) {
  if (row.kind === 'thread') return phone ? PHONE_ROW : comfortable ? COMFORTABLE_ROW : ROW
  return (phone ? PHONE_BUNDLE : BUNDLE) + (row.first ? BUNDLES_GAP : 0)
}

type Props = {
  title: string
  query: string
  rows: ListRow[]
  status: 'pending' | 'error' | 'success'
  error: Error | null
  empty: ReactNode
  onRangeEnd: (lastIndex: number) => void
  cursorId: number | null
  openId: number | null
  selected: Set<number>
  /** `hideLabel` (the label being viewed) is left out. */
  labelsById: Map<number, LabelChip>
  hideLabel?: string
  hideChips?: boolean
  onBundleAction: (action: BundleAction, bundle: Bundle) => void
  onToggleBundle: (name: string) => void
  threadHref: (id: number) => string
  onOpen: (id: number) => void
  onQuick: (quick: Quick, thread: ThreadSummary) => void
  onSearch: (query: string) => void
  searchRef: RefObject<HTMLInputElement | null>
  phone: boolean
  /** Phones: the account in view, its avatar beside the title and its address under it. */
  account?: Account
  /** Phones: tapping the avatar opens the menu, which starts with the accounts. */
  onMenu?: () => void
  /** Phones: the bottom bar. */
  footer?: ReactNode
  onAsk?: () => void
  onRowMenu?: (thread: ThreadSummary, point: { x: number; y: number }) => void
  menuId?: number | null
  /** Ctrl/⌘-click (one thread) or Shift-click (a range): select instead of open. */
  onSelectClick?: (id: number, range: boolean) => void
  selectionBar?: ReactNode
}

export function ThreadList(props: Props) {
  const { title, query, rows, status, error, empty, cursorId, openId, selected, labelsById, hideLabel, hideChips } = props
  const { onBundleAction, onToggleBundle, threadHref, onOpen, onQuick, onSearch, searchRef, phone, account, onMenu, footer, onAsk } = props
  const { onRowMenu, menuId, onSelectClick, selectionBar } = props
  const scrollRef = useRef<HTMLDivElement>(null)
  const comfortable = useDensity() === 'comfortable'
  const labelNames = [...labelsById.values()].map((label) => label.name)
  const suggest = useSearchSuggest(searchRef, labelNames, styles.suggestions)
  // eslint-disable-next-line react-hooks/incompatible-library -- the build doesn't use React Compiler
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => rowHeight(rows[index], phone, comfortable),
    getItemKey: (index) => {
      const row = rows[index]
      return row.kind === 'thread' ? row.thread.id : `bundle:${row.bundle.name}`
    },
    overscan: 12,
    paddingStart: 6,
    paddingEnd: 8,
  })
  const items = virtualizer.getVirtualItems()
  const lastIndex = items.at(-1)?.index ?? -1

  const rangeEnd = useEffectEvent(props.onRangeEnd)
  useEffect(() => rangeEnd(lastIndex), [lastIndex, rows.length])

  // Row heights change with the layout and the density.
  useEffect(() => virtualizer.measure(), [phone, comfortable, virtualizer])

  const cursorIndex = rows.findIndex((row) => row.kind === 'thread' && row.thread.id === cursorId)
  useEffect(() => {
    if (cursorIndex >= 0) virtualizer.scrollToIndex(cursorIndex)
  }, [cursorIndex, virtualizer])

  return (
    <section className={`${styles.list} ${comfortable ? styles.comfortable : ''}`} aria-label={title}>
      <header className={styles.header}>
        {phone ? (
          <>
            {account && (
              <button type="button" className={styles.account} aria-label="Switch account" onClick={onMenu}>
                <AccountAvatar account={account} className={styles.accountAvatar} />
              </button>
            )}
            <div className={styles.titles}>
              <h1 className={styles.title}>{title}</h1>
              {account && <span className={styles.subtitle}>{account.email}</span>}
            </div>
          </>
        ) : (
          <h1 className={styles.title}>{title}</h1>
        )}
        {phone && <InstallButton />}
        {selected.size > 0 && !selectionBar && (
          <span className={styles.selection} role="status">
            {selected.size} selected
          </span>
        )}
        {/* On a phone, search is in the bottom bar. */}
        {!phone && (
        <form
          role="search"
          className={styles.search}
          onSubmit={(event) => {
            event.preventDefault()
            onSearch(searchRef.current?.value.trim() ?? '')
            searchRef.current?.blur()
          }}
        >
          {/* The box folds to this icon while the email pane is open. */}
          <button
            type="button"
            className={styles.searchIcon}
            aria-label="Search"
            title="Search (/)"
            tabIndex={-1}
            onMouseDown={(event) => event.preventDefault()} // the input keeps its focus
            onClick={() => searchRef.current?.focus()}
          >
            <Icon name="search" size={13} strokeWidth={2} />
          </button>
          <input
            key={query}
            ref={searchRef}
            type="text"
            placeholder="Search"
            aria-label="Search mail"
            title="Search (/)"
            defaultValue={query}
            {...suggest.inputProps}
            onKeyDown={(event) => {
              // Esc leaves the box at once, its suggestions too (they close on blur).
              if (event.key !== 'Escape') return suggest.inputProps.onKeyDown(event)
              event.currentTarget.value = query
              event.currentTarget.blur()
            }}
          />
          {suggest.list}
        </form>
        )}
        {onAsk && (
          <button type="button" className={styles.ask} aria-label="Ask the assistant" title="Ask the assistant (q)" onClick={onAsk}>
            <Icon name="assistant" size={16} />
          </button>
        )}
      </header>

      <div ref={scrollRef} className={`${styles.scroll} ${selectionBar ? styles.underBar : ''}`}>
        {status === 'error' && <p className={styles.empty}>Couldn’t load threads: {error?.message}</p>}
        {status === 'success' && rows.length === 0 && <p className={styles.empty}>{empty}</p>}
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {items.map((item) => {
            const row = rows[item.index]
            if (row.kind === 'bundle')
              return <BundleRow key={item.key} row={row} top={item.start} onAction={onBundleAction} onToggle={onToggleBundle} />
            const thread = row.thread
            const chips = hideChips
              ? []
              : thread.label_ids.flatMap((id) => {
                  const chip = labelsById.get(id)
                  return chip && chip.name !== hideLabel ? [chip] : []
                })
            return (
              <ThreadRow
                key={thread.id}
                thread={thread}
                chips={chips}
                top={item.start}
                cursor={thread.id === cursorId}
                open={thread.id === openId}
                menu={thread.id === menuId}
                checked={selected.has(thread.id)}
                href={threadHref(thread.id)}
                onOpen={onOpen}
                onQuick={onQuick}
                onMenu={onRowMenu}
                onSelectClick={onSelectClick}
              />
            )
          })}
        </div>
      </div>
      {selectionBar && <div className={styles.selectionBar}>{selectionBar}</div>}
      {footer}
    </section>
  )
}

const count = (bundle: Bundle) => (
  <span className={styles.groupCount} aria-label={`${bundle.count} threads`}>
    {bundle.count}
  </span>
)

type BundleProps = {
  row: Extract<ListRow, { kind: 'bundle' }>
  top: number
  onAction: (action: BundleAction, bundle: Bundle) => void
  onToggle: (name: string) => void
}

const bundleActions: { action: BundleAction; icon: IconName; label: string }[] = [
  { action: 'done', icon: 'done', label: 'Done all' },
  { action: 'delete', icon: 'delete', label: 'Delete all' },
  { action: 'read', icon: 'mailOpen', label: 'Mark all read' },
]
const NAMED_SENDERS = 3 // the rest show only their icon

// Like a thread row: the senders and the newest time, the actions on hover (on phones, a swipe).
function BundleRow({ row, top, onAction, onToggle }: BundleProps) {
  const { bundle } = row
  const { name } = bundle
  const swipe = useSwipe((direction) => onAction(direction === 'right' ? 'done' : 'delete', bundle))
  const swiped = bundleActions[swipe.offset > 0 ? 0 : 1]
  return (
    <div className={`${styles.bundle} ${row.first ? styles.firstBundle : ''}`} style={{ transform: `translateY(${top}px)` }}>
      <div className={`${styles.row} ${styles.bundleRow}`} {...swipe.handlers}>
        {swipe.offset !== 0 && (
          <span className={`${styles.swipe} ${swipe.offset > 0 ? styles.swipeDone : styles.swipeDelete}`} aria-hidden="true">
            <Icon name={swiped.icon} size={17} />
            {swiped.label}
          </span>
        )}
        <button
          type="button"
          className={[styles.main, styles.bundleToggle, swipe.dragging && styles.dragging, swipe.offset !== 0 && styles.moving].filter(Boolean).join(' ')}
          style={swipe.offset ? { transform: `translateX(${swipe.offset}px)` } : undefined}
          aria-expanded={row.open}
          onClick={() => onToggle(name)}
        >
          <span className={styles.bundleChevron}>
            <Icon name="chevronRight" size={12} strokeWidth={2.2} />
          </span>
          <span className={styles.from}>
            <span className={styles.groupName}>{name}</span> {count(bundle)}
          </span>
          <span className={`${styles.summary} ${styles.bundleSenders}`}>
            {bundle.senders.map((sender, i) => (
              <span key={sender.name} className={`${styles.bundleSender} ${i < NAMED_SENDERS ? styles.named : ''}`} title={sender.name}>
                <Avatar className={styles.senderAvatar} name={sender.name} src={sender.photo} />
                {i < NAMED_SENDERS && sender.name}
              </span>
            ))}
          </span>
          <span className={styles.time}>{shortDate(bundle.newest_at)}</span>
        </button>
        <span className={styles.actions}>
          {bundleActions.map(({ action, icon, label }) => (
            <button
              key={action}
              type="button"
              className={styles.action}
              aria-label={label}
              title={`${label} in ${name}`}
              onClick={() => onAction(action, bundle)}
            >
              <Icon name={icon} size={15} />
            </button>
          ))}
        </span>
      </div>
    </div>
  )
}

export function InboxZero() {
  return (
    <>
      <svg className={styles.enso} viewBox="0 0 64 64" aria-hidden="true">
        <path d="M38.2 8.8A24 24 0 1 0 54.6 23.8" pathLength={1} />
      </svg>
      <span className={styles.zero}>Inbox zero. Nothing left to triage.</span>
    </>
  )
}

/** As Gmail does: "Daniel Reyes, me", or in Sent and Drafts "To: Daniel Reyes". */
function people(thread: ThreadSummary) {
  if (thread.recipients) return thread.recipients.length ? `To: ${thread.recipients.join(', ')}` : '(no recipients)'
  return [...new Set(thread.senders.map((name) => (thread.me.includes(name) ? 'me' : name)))].join(', ')
}

type RowProps = {
  thread: ThreadSummary
  chips: LabelChip[]
  top: number
  cursor: boolean
  open: boolean
  menu: boolean
  checked: boolean
  href: string
  onOpen: (id: number) => void
  onQuick: (quick: Quick, thread: ThreadSummary) => void
  onMenu?: (thread: ThreadSummary, point: { x: number; y: number }) => void
  onSelectClick?: (id: number, range: boolean) => void
}

function ThreadRow(props: RowProps) {
  const { thread, chips, top, cursor, open, menu, checked, href, onOpen, onQuick, onMenu, onSelectClick } = props
  const swipe = useSwipe((direction) => onQuick(direction === 'right' ? 'done' : 'delete', thread))
  const rowClass = [styles.row, thread.unread && styles.unread, (cursor || open) && styles.selected, checked && styles.checked, menu && styles.menuOpen]
    .filter(Boolean)
    .join(' ')
  const lang = thread.lang ?? undefined
  const done = quickAction('done', [thread])
  const remove = quickAction('delete', [thread])
  return (
    <div
      className={rowClass}
      style={{ transform: `translateY(${top}px)` }}
      aria-current={open || undefined}
      onContextMenu={
        onMenu &&
        ((event) => {
          event.preventDefault()
          onMenu(thread, { x: event.clientX, y: event.clientY })
        })
      }
      {...swipe.handlers}
    >
      {swipe.offset !== 0 && (
        <span className={`${styles.swipe} ${swipe.offset > 0 ? styles.swipeDone : styles.swipeDelete}`} aria-hidden="true">
          <Icon name={swipe.offset > 0 ? done.icon : remove.icon} size={17} />
          {(swipe.offset > 0 ? done : remove).title.replace(/ \(.*\)$/, '')}
        </span>
      )}
      <Link
        to={href}
        className={[styles.main, swipe.dragging && styles.dragging, swipe.offset !== 0 && styles.moving].filter(Boolean).join(' ')}
        style={swipe.offset ? { transform: `translateX(${swipe.offset}px)` } : undefined}
        // Shift-click would also select the text between the rows.
        onMouseDown={(event) => onSelectClick && event.shiftKey && event.preventDefault()}
        onClick={(event) => {
          if (event.button !== 0) return
          const modified = event.metaKey || event.ctrlKey || event.shiftKey
          if (modified && !onSelectClick) return // the browser's own (a new tab or window)
          event.preventDefault()
          if (modified) onSelectClick?.(thread.id, event.shiftKey)
          else onOpen(thread.id)
        }}
      >
        {checked ? (
          <span
            className={styles.check}
            aria-label="Selected"
            title={onSelectClick ? 'Deselect' : undefined}
            onClick={(event) => {
              if (!onSelectClick || event.button !== 0) return
              event.preventDefault() // not also open the thread
              event.stopPropagation()
              onSelectClick(thread.id, false)
            }}
          >
            <Icon name="check" size={12} strokeWidth={2.4} />
          </span>
        ) : (
          <span className={styles.unreadDot} aria-label={thread.unread ? 'Unread' : undefined} />
        )}
        <Avatar
          className={styles.avatar}
          // Threads only the user wrote in: whoever they wrote to.
          name={thread.sender_name ?? thread.recipients?.[0] ?? thread.senders[0] ?? ''}
          src={thread.sender_photo}
        />
        <span className={styles.from} lang={lang}>
          {people(thread)}
          {thread.message_count > 1 && <span className={styles.count}> {thread.message_count}</span>}
        </span>
        <span className={styles.summary} lang={lang}>
          {chips.map((chip) => (
            <span key={chip.name} className={styles.chip} lang="">
              <LabelMark color={chip.color} icon={chip.icon} size={11} dot={6} />
              {chip.name}
            </span>
          ))}
          <span className={styles.subject}>{thread.subject || '(no subject)'}</span>
          {thread.snippet && <span className={styles.muted}> — {thread.snippet}</span>}
        </span>
        {thread.important && (
          <span className={styles.important} aria-label="Important">
            <Icon name="important" size={11} filled />
          </span>
        )}
        {thread.starred && (
          <span className={styles.star} aria-label="Starred">
            ★
          </span>
        )}
        <span className={styles.time}>{shortDate(thread.last_message_at)}</span>
      </Link>
      <span className={styles.actions}>
        {quicks.filter((quick) => !redundant(quick, [thread])).map((quick) => {
          const { icon, title, pressed } = quickAction(quick, [thread])
          return (
            <button
              key={quick}
              type="button"
              className={styles.action}
              aria-label={title.replace(/ \(.*\)$/, '')}
              aria-pressed={pressed}
              title={title}
              onClick={() => onQuick(quick, thread)}
            >
              <Icon name={icon} size={15} filled={pressed} />
            </button>
          )
        })}
      </span>
    </div>
  )
}
