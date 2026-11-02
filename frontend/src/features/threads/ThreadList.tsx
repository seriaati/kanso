import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useEffectEvent, useRef, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router'
import { shortDate } from '../../app/format'
import { quickAction, type Quick } from '../../app/actions'
import type { Section, ThreadSummary } from '../../app/mail'
import { Icon } from '../../components/Icon'
import { useSwipe } from './swipe'
import styles from './ThreadList.module.css'

const quicks: Quick[] = ['done', 'delete', 'star', 'important']

export type LabelChip = { name: string; color: string }

/** One line of the list: a thread, or in the grouped inbox a section header or a bundle row. */
export type ListRow =
  | { kind: 'thread'; thread: ThreadSummary }
  | { kind: 'section'; section: Section; color: string | null }
  | { kind: 'bundle'; section: Section; color: string | null; open: boolean; first: boolean }

const ROW = 31
const SECTION = 34
const BUNDLE = 32
const BUNDLES_GAP = 17 // above the first bundle: a margin, a hairline and padding
// On a phone: two-line rows and 44px+ tap targets (ThreadList.module.css, max-width: 767px).
const PHONE_ROW = 64
const PHONE_SECTION = 44
const PHONE_BUNDLE = 48

function rowHeight(row: ListRow, phone: boolean) {
  if (row.kind === 'thread') return phone ? PHONE_ROW : ROW
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
  /** The last row index rendered: the caller loads more threads when it nears the end. */
  onRangeEnd: (lastIndex: number) => void
  cursorId: number | null
  openId: number | null
  selected: Set<number>
  /** User labels by id, shown as chips; `hideLabel` (the label being viewed) is left out. */
  labelsById: Map<number, LabelChip>
  hideLabel?: string
  /** No chips at all: the grouped inbox, where the section header names the label. */
  hideChips?: boolean
  /** Shown in the header of the unlabeled section ("Label existing inbox" with AI on). */
  labelInbox?: ReactNode
  onDoneAll: (section: Section) => void
  onToggleBundle: (name: string) => void
  threadHref: (id: number) => string
  onOpen: (id: number) => void
  onQuick: (quick: Quick, thread: ThreadSummary) => void
  onSearch: (query: string) => void
  searchRef: RefObject<HTMLInputElement | null>
  /** The phone layout: taller rows and swipe actions. */
  phone: boolean
  /** On a phone: opens the sidebar drawer. */
  onMenu?: () => void
  /** With AI on and nothing open: opens the assistant (q). */
  onAsk?: () => void
}

export function ThreadList(props: Props) {
  const { title, query, rows, status, error, empty, cursorId, openId, selected, labelsById, hideLabel, hideChips } = props
  const { labelInbox, onDoneAll, onToggleBundle, threadHref, onOpen, onQuick, onSearch, searchRef, phone, onMenu, onAsk } = props
  const scrollRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line react-hooks/incompatible-library -- the build doesn't use React Compiler
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => rowHeight(rows[index], phone),
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

  // Infinite scroll: the caller fetches the next page once the rows near the end are rendered.
  const rangeEnd = useEffectEvent(props.onRangeEnd)
  useEffect(() => rangeEnd(lastIndex), [lastIndex, rows.length])

  // Row heights change with the layout.
  useEffect(() => virtualizer.measure(), [phone, virtualizer])

  // Keep the keyboard cursor in view.
  const cursorIndex = rows.findIndex((row) => row.kind === 'thread' && row.thread.id === cursorId)
  useEffect(() => {
    if (cursorIndex >= 0) virtualizer.scrollToIndex(cursorIndex)
  }, [cursorIndex, virtualizer])

  return (
    <section className={styles.list} aria-label={title}>
      <header className={styles.header}>
        {onMenu && (
          <button type="button" className={styles.menu} aria-label="Mailboxes and labels" onClick={onMenu}>
            <Icon name="menu" size={18} />
          </button>
        )}
        <h1 className={styles.title}>{title}</h1>
        {selected.size > 0 && (
          <span className={styles.selection} role="status">
            {selected.size} selected
          </span>
        )}
        <form
          role="search"
          className={styles.search}
          onSubmit={(event) => {
            event.preventDefault()
            onSearch(searchRef.current?.value.trim() ?? '')
            searchRef.current?.blur()
          }}
        >
          <Icon name="search" size={13} strokeWidth={2} />
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
        {onAsk && (
          <button type="button" className={styles.ask} aria-label="Ask the assistant" title="Ask the assistant (q)" onClick={onAsk}>
            <Icon name="assistant" size={16} />
          </button>
        )}
      </header>

      <div ref={scrollRef} className={styles.scroll}>
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
                checked={selected.has(thread.id)}
                href={threadHref(thread.id)}
                onOpen={onOpen}
                onQuick={onQuick}
              />
            )
          })}
        </div>
      </div>
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

/** A label section of the grouped inbox: its color, name, thread count and "Done all". */
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

/** A bundle: one collapsed row at the foot of the inbox; a click opens its threads below it. */
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

/** The empty inbox: an ensō that draws itself once, and a quiet line. */
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

/** Who a row names, as Gmail does: "Daniel Reyes, me", or in Sent and Drafts "To: Daniel Reyes". */
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
  checked: boolean
  href: string
  onOpen: (id: number) => void
  onQuick: (quick: Quick, thread: ThreadSummary) => void
}

function ThreadRow({ thread, chips, top, cursor, open, checked, href, onOpen, onQuick }: RowProps) {
  const swipe = useSwipe((direction) => onQuick(direction === 'right' ? 'done' : 'delete', thread))
  const rowClass = [styles.row, thread.unread && styles.unread, (cursor || open) && styles.selected, checked && styles.checked]
    .filter(Boolean)
    .join(' ')
  const lang = thread.lang ?? undefined
  const done = quickAction('done', [thread])
  const remove = quickAction('delete', [thread])
  return (
    <div className={rowClass} style={{ transform: `translateY(${top}px)` }} aria-current={open || undefined} {...swipe.handlers}>
      {swipe.offset !== 0 && (
        // What letting go does: Done to the right, Delete to the left.
        <span className={`${styles.swipe} ${swipe.offset > 0 ? styles.swipeDone : styles.swipeDelete}`} aria-hidden="true">
          <Icon name={swipe.offset > 0 ? done.icon : remove.icon} size={17} />
          {(swipe.offset > 0 ? done : remove).title.replace(/ \(.*\)$/, '')}
        </span>
      )}
      <Link
        to={href}
        className={[styles.main, swipe.dragging && styles.dragging, swipe.offset !== 0 && styles.moving].filter(Boolean).join(' ')}
        style={swipe.offset ? { transform: `translateX(${swipe.offset}px)` } : undefined}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
          event.preventDefault()
          onOpen(thread.id)
        }}
      >
        {checked ? (
          <span className={styles.check} aria-label="Selected">
            <Icon name="check" size={12} strokeWidth={2.4} />
          </span>
        ) : (
          <span className={styles.unreadDot} aria-label={thread.unread ? 'Unread' : undefined} />
        )}
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
