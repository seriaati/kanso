import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useEffectEvent, useRef, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router'
import { shortDate } from '../../app/format'
import { quickAction, type Quick } from '../../app/actions'
import { useDensity } from '../../app/density'
import type { Section, ThreadSummary } from '../../app/mail'
import { Avatar } from '../../components/Avatar'
import { Icon } from '../../components/Icon'
import { InstallButton } from '../install/InstallButton'
import { useSwipe } from './swipe'
import styles from './ThreadList.module.css'

const quicks: Quick[] = ['done', 'delete', 'star', 'important']

export type LabelChip = { name: string; color: string }

export type ListRow =
  | { kind: 'thread'; thread: ThreadSummary }
  | { kind: 'section'; section: Section; color: string | null }
  | { kind: 'bundle'; section: Section; color: string | null; open: boolean; first: boolean }

const ROW = 31
const COMFORTABLE_ROW = 40
const SECTION = 34
const BUNDLE = 32
const BUNDLES_GAP = 17 // above the first bundle: a margin, a hairline and padding
// Phone sizes match ThreadList.module.css (max-width: 767px): two-line rows, 44px+ tap targets.
const PHONE_ROW = 64
const PHONE_SECTION = 44
const PHONE_BUNDLE = 48

function rowHeight(row: ListRow, phone: boolean, comfortable: boolean) {
  if (row.kind === 'thread') return phone ? PHONE_ROW : comfortable ? COMFORTABLE_ROW : ROW
  if (row.kind === 'section') return phone ? PHONE_SECTION : SECTION
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
  /** Shown in the unlabeled section's header. */
  labelInbox?: ReactNode
  onDoneAll: (section: Section) => void
  onToggleBundle: (name: string) => void
  threadHref: (id: number) => string
  onOpen: (id: number) => void
  onQuick: (quick: Quick, thread: ThreadSummary) => void
  onSearch: (query: string) => void
  searchRef: RefObject<HTMLInputElement | null>
  phone: boolean
  /** Phones: the account's address, under the title. */
  subtitle?: string
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
  const { labelInbox, onDoneAll, onToggleBundle, threadHref, onOpen, onQuick, onSearch, searchRef, phone, subtitle, footer, onAsk } = props
  const { onRowMenu, menuId, onSelectClick, selectionBar } = props
  const scrollRef = useRef<HTMLDivElement>(null)
  const comfortable = useDensity() === 'comfortable'
  // eslint-disable-next-line react-hooks/incompatible-library -- the build doesn't use React Compiler
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => rowHeight(rows[index], phone, comfortable),
    getItemKey: (index) => {
      const row = rows[index]
      return row.kind === 'thread' ? row.thread.id : `${row.kind}:${row.section.name ?? ''}`
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
          <div className={styles.titles}>
            <h1 className={styles.title}>{title}</h1>
            {subtitle && <span className={styles.subtitle}>{subtitle}</span>}
          </div>
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
            title="Search (/). Try from:, label:, is:unread, has:attachment, before:2024-01-01"
            defaultValue={query}
            onKeyDown={(event) => {
              if (event.key !== 'Escape') return
              event.currentTarget.value = query
              event.currentTarget.blur()
            }}
          />
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
            if (row.kind === 'section')
              return (
                <SectionHeader key={item.key} row={row} top={item.start} onDoneAll={onDoneAll}>
                  {row.section.name === null && labelInbox}
                </SectionHeader>
              )
            if (row.kind === 'bundle')
              return <BundleRow key={item.key} row={row} top={item.start} onDoneAll={onDoneAll} onToggle={onToggleBundle} />
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

const count = (section: Section) => (
  <span className={styles.groupCount} aria-label={`${section.count} threads`}>
    {section.count}
  </span>
)

function doneAllButton(section: Section, onDoneAll: (section: Section) => void, name: string) {
  return (
    <button type="button" className={styles.doneAll} title={`Mark every thread in ${name} done`} onClick={() => onDoneAll(section)}>
      Done all
    </button>
  )
}

type SectionProps = { row: Extract<ListRow, { kind: 'section' }>; top: number; onDoneAll: (section: Section) => void; children: ReactNode }

function SectionHeader({ row, top, onDoneAll, children }: SectionProps) {
  const name = row.section.name ?? 'Other'
  return (
    <div className={styles.section} style={{ transform: `translateY(${top}px)` }} role="heading" aria-level={2}>
      <span
        className={`${styles.groupDot} ${row.color ? '' : styles.hollowDot}`}
        style={row.color ? { background: row.color } : undefined}
        aria-hidden="true"
      />
      <span className={styles.groupName}>{name}</span>
      {count(row.section)}
      <span className={styles.groupActions}>
        {children}
        {doneAllButton(row.section, onDoneAll, name)}
      </span>
    </div>
  )
}

type BundleProps = {
  row: Extract<ListRow, { kind: 'bundle' }>
  top: number
  onDoneAll: (section: Section) => void
  onToggle: (name: string) => void
}

function BundleRow({ row, top, onDoneAll, onToggle }: BundleProps) {
  const name = row.section.name ?? ''
  return (
    <div className={`${styles.bundle} ${row.first ? styles.firstBundle : ''}`} style={{ transform: `translateY(${top}px)` }}>
      <button type="button" className={styles.bundleToggle} aria-expanded={row.open} onClick={() => onToggle(name)}>
        <Icon name="chevronRight" size={12} strokeWidth={2.2} />
        <span className={styles.groupName}>{name}</span>
        {count(row.section)}
        <span className={styles.bundleSenders}>{row.section.senders.join(', ')}</span>
      </button>
      {doneAllButton(row.section, onDoneAll, name)}
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
              <span className={styles.chipDot} style={{ background: chip.color }} aria-hidden="true" />
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
        {quicks.map((quick) => {
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
