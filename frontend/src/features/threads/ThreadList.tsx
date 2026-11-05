import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useEffectEvent, useId, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router'
import { shortDate } from '../../app/format'
import { bundleActions, quickAction, redundant, type BundleAction, type Quick } from '../../app/actions'
import type { Account } from '../../app/accounts'
import { useDensity } from '../../app/density'
import type { Bundle, LabelIcon, ThreadSummary } from '../../app/mail'
import { Avatar } from '../../components/Avatar'
import { Icon } from '../../components/Icon'
import { LabelMark } from '../../components/LabelMark'
import { InstallButton } from '../install/InstallButton'
import { Avatar as AccountAvatar } from '../sidebar/AccountSwitcher'
import { useSearchSuggest } from './SearchSuggest'
import { useSwipe } from './swipe'
import styles from './ThreadList.module.css'

const quicks: Quick[] = ['done', 'delete', 'star', 'important']

export type LabelChip = { name: string; color: string; icon: LabelIcon | null }

export type ListRow =
  | { kind: 'thread'; thread: ThreadSummary }
  | { kind: 'bundle'; bundle: Bundle; open: boolean }
  /** Between the bundles and the unlabeled threads. */
  | { kind: 'divider' }

const ROW = 31
const COMFORTABLE_ROW = 40
const BUNDLE = 32
const DIVIDER = 17 // a margin, a hairline and padding
// Phone sizes match ThreadList.module.css (max-width: 767px): two-line rows, 44px+ tap targets.
const PHONE_ROW = 64
const PHONE_BUNDLE = 48

function rowHeight(row: ListRow, phone: boolean, comfortable: boolean) {
  if (row.kind === 'thread') return phone ? PHONE_ROW : comfortable ? COMFORTABLE_ROW : ROW
  if (row.kind === 'divider') return DIVIDER
  return phone ? PHONE_BUNDLE : BUNDLE
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
  /** Right-click, or on a phone a long press. */
  onRowMenu?: (thread: ThreadSummary, point: { x: number; y: number }) => void
  menuId?: number | null
  onBundleMenu?: (bundle: Bundle, point: { x: number; y: number }) => void
  menuBundle?: string | null
  /** Ctrl/⌘-click (one thread) or Shift-click (a range): select instead of open. */
  onSelectClick?: (id: number, range: boolean) => void
  selectionBar?: ReactNode
}

export function ThreadList(props: Props) {
  const { title, query, rows, status, error, empty, cursorId, openId, selected, labelsById, hideLabel, hideChips } = props
  const { onBundleAction, onToggleBundle, threadHref, onOpen, onQuick, onSearch, searchRef, phone, account, onMenu, footer, onAsk } = props
  const { onRowMenu, menuId, onBundleMenu, menuBundle, onSelectClick, selectionBar } = props
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
      return row.kind === 'thread' ? row.thread.id : row.kind === 'bundle' ? `bundle:${row.bundle.name}` : 'divider'
    },
    overscan: 12,
    paddingStart: 6,
    paddingEnd: 8,
  })
  const items = virtualizer.getVirtualItems()
  const lastIndex = items.at(-1)?.index ?? -1

  // Threads a bundle just revealed fade in under it, in order; rows mounted by scrolling don't.
  const [prevRows, setPrevRows] = useState(rows)
  const [revealed, setRevealed] = useState<ReadonlyMap<number, number>>(new Map()) // id -> order
  if (rows !== prevRows) {
    setPrevRows(rows)
    const before = new Set(prevRows.flatMap((row) => (row.kind === 'thread' ? [row.thread.id] : [])))
    const added = inOpenBundles(rows).filter((id) => !before.has(id))
    if (added.length) setRevealed(new Map(added.map((id, order) => [id, order])))
  }
  useEffect(() => {
    if (!revealed.size) return
    const timer = setTimeout(() => setRevealed(new Map()), 800)
    return () => clearTimeout(timer)
  }, [revealed])

  const rangeEnd = useEffectEvent(props.onRangeEnd)
  useEffect(() => rangeEnd(lastIndex), [lastIndex, rows.length])

  // Row heights change with the layout and the density.
  useEffect(() => virtualizer.measure(), [phone, comfortable, virtualizer])

  // Rows glide (a transform) into the place of one that left, and browsers don't re-hover what
  // slides under a still mouse: mark that row until the mouse moves again.
  const mouse = useRef<{ x: number; y: number } | null>(null)
  const rehover = () => {
    const list = scrollRef.current
    list?.querySelector('[data-hover]')?.removeAttribute('data-hover')
    if (!list || !mouse.current) return
    const row = document.elementFromPoint(mouse.current.x, mouse.current.y)?.closest(`.${styles.row}`)
    if (row && list.contains(row) && !row.matches(':hover')) row.setAttribute('data-hover', '')
  }
  useEffect(() => {
    const frame = requestAnimationFrame(rehover) // no glide under reduced motion
    return () => cancelAnimationFrame(frame)
  }, [rows])

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

      <div
        ref={scrollRef}
        className={`${styles.scroll} ${selectionBar ? styles.underBar : ''}`}
        onPointerMove={(event) => {
          if (event.pointerType !== 'mouse') return
          mouse.current = { x: event.clientX, y: event.clientY }
          scrollRef.current?.querySelector('[data-hover]')?.removeAttribute('data-hover')
        }}
        onPointerLeave={() => {
          mouse.current = null
          rehover()
        }}
        onTransitionEnd={(event) => event.propertyName === 'transform' && rehover()}
      >
        {status === 'error' && <p className={styles.empty}>Couldn’t load threads: {error?.message}</p>}
        {status === 'success' && rows.length === 0 && <p className={styles.empty}>{empty}</p>}
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {items.map((item) => {
            const row = rows[item.index]
            if (row.kind === 'bundle')
              return (
                <BundleRow
                  key={item.key}
                  row={row}
                  top={item.start}
                  menu={row.bundle.name === menuBundle}
                  onAction={onBundleAction}
                  onToggle={onToggleBundle}
                  onMenu={onBundleMenu}
                />
              )
            if (row.kind === 'divider') return <div key={item.key} className={styles.divider} style={{ transform: `translateY(${item.start}px)` }} aria-hidden="true" />
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
                reveal={revealed.get(thread.id)}
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
  menu: boolean
  onAction: (action: BundleAction, bundle: Bundle) => void
  onToggle: (name: string) => void
  onMenu?: (bundle: Bundle, point: { x: number; y: number }) => void
}

const NAMED_SENDERS = 3 // the rest show only their icon

// Like a thread row: the senders and the newest time, the actions on hover (on phones, a swipe).
function BundleRow({ row, top, menu, onAction, onToggle, onMenu }: BundleProps) {
  const { bundle } = row
  const { name } = bundle
  const swipe = useSwipe(
    (direction) => onAction(direction === 'right' ? 'done' : 'delete', bundle),
    onMenu && ((point) => onMenu(bundle, point)),
  )
  const swiped = bundleActions[swipe.offset > 0 ? 0 : 1]
  return (
    <div className={styles.bundle} style={{ transform: `translateY(${top}px)` }}>
      <div
        className={`${styles.row} ${styles.bundleRow} ${menu ? styles.menuOpen : ''}`}
        onContextMenu={
          onMenu &&
          ((event) => {
            event.preventDefault()
            onMenu(bundle, { x: event.clientX, y: event.clientY })
          })
        }
        {...swipe.handlers}
      >
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

// kanso's mark (public/favicon.svg): a brush ensō, the circle drawn in one stroke.
const ENSO = 'M311.8 106.6 L304.8 102.6 L297.5 98.9 L289.9 95.6 L282.1 92.7 L274.1 90.3 L265.9 88.2 L257.6 86.7 L249.1 85.7 L240.5 85.1 L231.9 85.1 L223.3 85.6 L214.7 86.7 L206.4 89.0 L198.2 91.8 L190.2 95.1 L182.4 98.8 L174.9 103.0 L167.6 107.5 L160.6 112.5 L153.9 117.8 L147.5 123.4 L141.5 129.3 L135.7 135.5 L130.3 142.0 L125.2 148.6 L120.4 155.5 L115.9 162.5 L111.8 169.6 L107.9 177.0 L104.4 184.4 L101.1 192.0 L98.1 199.6 L95.5 207.4 L93.1 215.2 L91.0 223.2 L89.2 231.2 L87.7 239.3 L86.6 247.5 L85.7 255.7 L85.2 264.0 L85.1 272.3 L85.4 280.6 L86.0 289.0 L87.0 297.4 L88.5 305.7 L90.4 313.9 L92.7 322.1 L95.5 330.1 L98.8 338.0 L102.4 345.7 L106.6 353.2 L111.1 360.4 L116.1 367.4 L121.5 374.0 L127.3 380.3 L133.5 386.3 L140.0 391.8 L146.8 397.0 L153.9 401.7 L161.2 406.0 L168.7 409.9 L176.4 413.3 L184.3 416.3 L192.2 418.9 L200.3 421.0 L208.4 422.7 L216.6 424.0 L224.7 425.0 L232.9 425.6 L241.0 425.8 L249.1 425.6 L257.2 425.2 L265.1 424.4 L273.1 423.3 L280.9 422.0 L288.7 420.3 L296.4 418.4 L304.0 416.1 L311.6 413.6 L319.0 410.8 L326.3 407.7 L333.5 404.4 L340.6 400.7 L347.5 396.7 L354.2 392.4 L360.8 387.7 L367.1 382.8 L373.2 377.5 L379.1 371.9 L384.6 366.0 L389.8 359.8 L394.7 353.3 L399.2 346.5 L403.3 339.5 L406.9 332.3 L410.1 324.8 L412.9 317.2 L415.2 309.4 L417.0 301.5 L418.3 293.5 L419.1 285.5 L419.5 277.5 L419.3 269.5 L418.7 261.5 L417.7 253.6 L416.2 245.9 L414.3 238.3 L412.1 230.8 L409.4 223.5 L406.5 216.4 L403.2 209.5 L399.6 202.8 L395.7 196.3 L391.6 190.0 L387.3 184.0 L382.7 178.1 L377.9 172.6 L372.9 167.3 L367.6 162.2 L361.8 157.9 L359.6 159.9 L363.5 165.9 L367.6 171.5 L371.5 177.1 L375.4 182.9 L379.0 188.7 L382.5 194.7 L385.7 200.8 L388.7 207.1 L391.5 213.5 L394.1 220.0 L396.3 226.6 L398.3 233.4 L399.9 240.2 L401.2 247.2 L402.1 254.3 L402.6 261.4 L402.7 268.5 L402.5 275.7 L401.8 282.8 L400.7 289.9 L399.1 296.9 L397.1 303.8 L394.7 310.5 L391.9 317.1 L388.7 323.5 L385.2 329.7 L381.2 335.7 L376.9 341.4 L372.4 346.8 L367.5 351.9 L362.4 356.8 L357.1 361.3 L351.5 365.5 L345.8 369.4 L339.9 373.1 L333.9 376.4 L327.8 379.5 L321.7 382.3 L315.4 384.8 L309.1 387.1 L302.7 389.1 L296.3 390.9 L289.8 392.4 L283.3 393.8 L276.8 394.9 L270.2 395.7 L263.6 396.4 L257.0 396.8 L250.3 397.0 L243.6 396.9 L236.9 396.5 L230.2 395.9 L223.6 394.9 L216.9 393.7 L210.3 392.1 L203.7 390.2 L197.3 388.0 L190.9 385.4 L184.7 382.5 L178.6 379.2 L172.8 375.5 L167.1 371.5 L161.7 367.1 L156.6 362.5 L151.8 357.5 L147.3 352.2 L143.1 346.7 L139.3 340.9 L135.9 334.9 L132.9 328.7 L130.2 322.4 L128.0 315.9 L126.1 309.4 L124.6 302.8 L123.4 296.1 L122.6 289.5 L122.2 282.8 L122.1 276.2 L122.3 269.6 L122.7 263.1 L123.5 256.6 L124.5 250.3 L125.7 244.0 L127.2 237.7 L128.8 231.6 L130.7 225.6 L132.8 219.6 L135.1 213.7 L137.5 208.0 L140.2 202.3 L143.0 196.7 L146.1 191.2 L149.4 185.8 L152.9 180.5 L156.6 175.3 L160.5 170.4 L164.7 165.5 L169.1 160.9 L173.8 156.5 L178.7 152.3 L183.8 148.3 L189.1 144.6 L194.7 141.3 L200.4 138.3 L206.4 135.6 L212.4 133.2 L218.7 131.3 L225.0 129.8 L231.2 127.9 L237.6 126.4 L244.1 125.2 L250.6 124.5 L257.2 124.2 L263.8 124.3 L270.4 124.7 L277.0 125.5 L283.5 126.7 L289.9 128.2 L296.3 130.0 L302.5 132.1 Z M320.8 119.4 A13.6 13.6 0 1 0 293.6 119.4 A13.6 13.6 0 1 0 320.8 119.4 Z'
// Its centreline, from the ink drop round to the tail: the brush that paints it in.
const BRUSH = 'M322.9 123.7A151 151 0 0 0 154.9 372.7A151 151 0 0 0 371.0 164.0'

/** The ensō is painted in one stroke and settles: the circle is closed, nothing is left in it. */
export function InboxZero() {
  const mask = useId()
  return (
    <>
      <svg className={styles.enso} viewBox="72 75 360 360" aria-hidden="true">
        <mask id={mask}>
          <path className={styles.brush} d={BRUSH} pathLength={1} />
        </mask>
        <path d={ENSO} mask={`url(#${mask})`} />
      </svg>
      <span className={styles.zeroTitle}>Inbox zero</span>
      <span className={styles.zero}>Nothing left to triage.</span>
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
  /** Just revealed by opening its bundle: its place in the bundle, for the fade-in. */
  reveal?: number
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
  const { thread, chips, top, reveal, cursor, open, menu, checked, href, onOpen, onQuick, onMenu, onSelectClick } = props
  const swipe = useSwipe(
    (direction) => onQuick(direction === 'right' ? 'done' : 'delete', thread),
    onMenu && ((point) => onMenu(thread, point)),
  )
  const rowClass = [styles.row, thread.unread && styles.unread, (cursor || open) && styles.selected, checked && styles.checked, menu && styles.menuOpen, reveal !== undefined && styles.revealed]
    .filter(Boolean)
    .join(' ')
  const lang = thread.lang ?? undefined
  const done = quickAction('done', [thread])
  const remove = quickAction('delete', [thread])
  return (
    <div
      className={rowClass}
      style={{ transform: `translateY(${top}px)`, '--reveal': reveal } as CSSProperties}
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
          {/* What the swipe does: a done thread goes back to the inbox. */}
          <Icon name={swipe.offset > 0 ? (done.pressed ? 'inbox' : done.icon) : remove.icon} size={17} />
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
        {thread.code && <CopyCode code={thread.code} />}
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

/** The ids of the threads shown inside open bundles. */
function inOpenBundles(rows: ListRow[]) {
  let open = false
  return rows.flatMap((row) => {
    if (row.kind !== 'thread') open = row.kind === 'bundle' && row.open
    return open && row.kind === 'thread' ? [row.thread.id] : []
  })
}

/** A verification mail's code: a click copies it, without opening the thread. */
function CopyCode({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(timer)
  }, [copied])
  return (
    <span
      className={styles.code}
      role="button"
      aria-label={copied ? 'Copied' : `Copy code ${code}`}
      title={copied ? 'Copied' : 'Copy code'}
      data-copied={copied || undefined}
      onClick={(event) => {
        if (event.button !== 0) return
        event.preventDefault() // not also open the thread
        event.stopPropagation()
        void navigator.clipboard.writeText(code).then(() => setCopied(true))
      }}
    >
      <span className={styles.codeText}>{code}</span>
      <Icon name={copied ? 'check' : 'copy'} size={12} />
    </span>
  )
}
