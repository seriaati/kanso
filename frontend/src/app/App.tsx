import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { Menu, type MenuItem } from '../components/Menu'
import { Resizer } from '../components/Resizer'
import { Assistant, type AssistantHandle } from '../features/assistant/Assistant'
import { BottomBar } from '../features/phone/BottomBar'
import { MenuSheet } from '../features/phone/MenuSheet'
import { Composer } from '../features/compose/Composer'
import { ShortcutHelp } from '../features/help/ShortcutHelp'
import { LabelPicker, type LabelOption } from '../features/labels/LabelPicker'
import { Login } from '../features/login/Login'
import { Reader, type ThreadDraft } from '../features/reader/Reader'
import { Selection } from '../features/reader/Selection'
import { Labels } from '../features/labels/Labels'
import { Settings } from '../features/settings/Settings'
import { Sidebar, type Notice } from '../features/sidebar/Sidebar'
import { LabelInbox } from '../features/threads/LabelInbox'
import { InboxZero, ThreadList, type LabelChip, type ListRow } from '../features/threads/ThreadList'
import { pickAccount, rememberAccount, useAccounts, type Account } from './accounts'
import { applied, inList, quickAction, quickTarget, useThreadAction, useUndo, type Quick, type SectionScope, type ThreadAction } from './actions'
import { useConfig } from './ai'
import { ApiError, Unauthorized } from './api'
import { createDraft, openDraft, type ComposeMode } from './compose'
import styles from './App.module.css'
import { useCreateLabel } from './labels'
import {
  labelColor,
  namedLabel,
  useBundleThreads,
  useLabels,
  useLiveUpdates,
  useSync,
  useThread,
  useThreads,
  userLabels,
  viewParams,
  type NamedLabel,
  type Section,
  type ThreadPage,
  type ThreadSummary,
} from './mail'
import { usePhone } from './media'
import { href, useRoute, type Mailbox, type Route } from './nav'
import { applyUpdate, useUpdateReady } from './update'

export function App() {
  const accounts = useAccounts()
  const route = useRoute()
  // Offline (the shell still starts from the service worker): retry once back online.
  const { isError, refetch } = accounts
  useEffect(() => {
    if (!isError) return
    const retry = () => void refetch()
    window.addEventListener('online', retry)
    return () => window.removeEventListener('online', retry)
  }, [isError, refetch])

  if (accounts.error instanceof Unauthorized) return <Login />
  if (accounts.isError)
    return (
      <div className={styles.notice}>
        <p>
          {accounts.error instanceof ApiError
            ? `Can’t reach kanso: ${accounts.error.message}`
            : 'Can’t reach kanso. You may be offline; it tries again when the connection is back.'}
        </p>
        <button type="button" onClick={() => void refetch()}>
          Try again
        </button>
      </div>
    )
  if (!accounts.data) return null
  if (!route) return <Navigate to="/inbox" replace />
  return <Mail accounts={accounts.data} route={route} />
}

const mailboxTitles: Record<Mailbox, string> = {
  inbox: 'Inbox',
  starred: 'Starred',
  done: 'Done',
  sent: 'Sent',
  drafts: 'Drafts',
  all: 'All mail',
  trash: 'Trash',
  spam: 'Spam',
}

// Dragged widths, per browser. null: the default (the list: an even split with the pane,
// ThreadList.module.css; the sidebar: Sidebar.module.css).
const LIST_WIDTH_KEY = 'kanso.listWidth'
const SIDEBAR_WIDTH_KEY = 'kanso.sidebarWidth'

function storedWidth(key: string): number | null {
  try {
    return Number(localStorage.getItem(key)) || null
  } catch {
    return null // storage blocked
  }
}

function storeWidth(key: string, width: number | null) {
  try {
    if (width === null) localStorage.removeItem(key)
    else localStorage.setItem(key, String(width))
  } catch {
    // storage blocked: kept until the page reloads
  }
}

const NOTICE_MS = 5000
const UNDO_MS = 6000

function Mail({ accounts, route: urlRoute }: { accounts: Account[]; route: Route }) {
  const [notice, setNotice] = useState<Notice | null>(null)
  const say = useCallback((text: string, undo?: () => void) => setNotice({ id: Date.now(), text, undo }), [])
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(null), notice.undo ? UNDO_MS : NOTICE_MS)
    return () => clearTimeout(timer)
  }, [notice])
  useLiveUpdates(
    (action) => say(`Gmail didn’t accept a change (${action}), so it was undone here too.`),
    (message) => say(`A message wasn’t sent: ${message} It is back in Drafts.`),
  )
  const navigate = useNavigate()
  const location = useLocation()
  // A tapped new-mail notification opens its thread here (public/push-sw.js).
  useEffect(() => {
    const container = navigator.serviceWorker
    if (!container) return
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type === 'kanso:open' && typeof event.data.url === 'string') void navigate(event.data.url)
    }
    container.addEventListener('message', onMessage)
    return () => container.removeEventListener('message', onMessage)
  }, [navigate])
  // null only while no account is connected.
  const accountId = pickAccount(accounts, urlRoute.accountId)?.id ?? null
  useEffect(() => {
    if (accountId !== null) rememberAccount(accountId)
  }, [accountId])
  const route = { ...urlRoute, accountId }
  const { view, threadId } = route
  const ai = useConfig().data?.ai ?? false
  const phone = usePhone()
  const update = useUpdateReady()
  // The phone's menu sheet closes whenever the page changes (back too).
  const [menu, setMenu] = useState({ open: false, at: location.key })
  if (menu.at !== location.key) setMenu({ open: false, at: location.key })
  const menuOpen = phone && menu.open
  const openMenu = phone ? () => setMenu({ open: true, at: location.key }) : undefined
  const grouped = view.kind === 'mailbox' && view.mailbox === 'inbox'

  const labels = useLabels(accountId)
  const sync = useSync()
  const named = useMemo(() => userLabels(labels.data ?? []), [labels.data])
  const label = view.kind === 'label' ? named.find((l) => l.name === view.name) : undefined
  const labelIds = label && [label.id]
  const list = useThreads(view, accountId, labelIds)
  const listParams = viewParams(view, accountId, labelIds)
  const labelsById = useMemo(
    () =>
      new Map<number, LabelChip>(
        (labels.data ?? []).filter((l) => l.type === 'user').map((l) => [l.id, { name: l.name, color: labelColor(l.color) }]),
      ),
    [labels.data],
  )
  const listThreads = useMemo(() => unique(list.data?.pages), [list.data])
  const { hasNextPage, isFetching, fetchNextPage } = list
  const loadMore = useCallback(() => {
    if (!isFetching) void fetchNextPage()
  }, [isFetching, fetchNextPage])

  // One bundle at a time; it closes again when the view changes.
  const viewKey = href({ ...route, threadId: null })
  const [bundle, setBundle] = useState<{ viewKey: string; name: string } | null>(null)
  const openBundle = grouped && bundle?.viewKey === viewKey ? bundle.name : null
  const bundleList = useBundleThreads(openBundle, accountId)
  const bundleThreads = useMemo(() => unique(bundleList.data?.pages), [bundleList.data])
  const sections = useMemo(() => (grouped ? (list.data?.pages[0]?.sections ?? []) : []), [grouped, list.data])

  const { rows, threads, mainEnd } = useMemo(() => {
    const colors = new Map(named.map((l) => [l.name, l.color]))
    const color = (name: string | null) => (name === null ? null : (colors.get(name) ?? null))
    const rows: ListRow[] = []
    let current: string | null | undefined
    for (const thread of listThreads) {
      const name = thread.section ?? null
      if (grouped && ai && name !== current) {
        const section = sections.find((s) => !s.bundle && s.name === name) ?? {
          name,
          bundle: false,
          count: listThreads.filter((t) => (t.section ?? null) === name).length,
          newest_at: thread.last_message_at,
          senders: [],
        }
        rows.push({ kind: 'section', section, color: color(name) })
        current = name
      }
      rows.push({ kind: 'thread', thread })
    }
    const mainEnd = rows.length - 1
    const bundles = sections.filter((s) => s.bundle && s.count > 0)
    bundles.forEach((section, i) => {
      const open = section.name === openBundle
      rows.push({ kind: 'bundle', section, color: color(section.name), open, first: i === 0 })
      if (open) for (const thread of bundleThreads) rows.push({ kind: 'thread', thread })
    })
    const opened = bundles.some((s) => s.name === openBundle)
    return { rows, threads: opened ? [...listThreads, ...bundleThreads] : listThreads, mainEnd }
  }, [listThreads, bundleThreads, sections, openBundle, grouped, ai, named])
  const onRangeEnd = (last: number) => {
    if (hasNextPage && last >= mainEnd - 10) loadMore()
    if (bundleList.hasNextPage && !bundleList.isFetching && last >= rows.length - 10) void bundleList.fetchNextPage()
  }

  const [cursor, setCursor] = useState<number | null>(null)
  const cursorId = threadId ?? cursor
  const [composing, setComposing] = useState<{ draftId: number; minimized: boolean } | null>(null)
  const [reply, setReply] = useState<ThreadDraft | null>(null)
  const starting = useRef(false)
  const [help, setHelp] = useState(false)
  // The assistant alone in the email pane, with no thread open (q).
  const [asking, setAsking] = useState(false)
  const [picking, setPicking] = useState(false)
  const [listWidth, setListWidth] = useState(() => storedWidth(LIST_WIDTH_KEY))
  const [sidebarWidth, setSidebarWidth] = useState(() => storedWidth(SIDEBAR_WIDTH_KEY))
  const searchRef = useRef<HTMLInputElement>(null)
  const assistantRef = useRef<AssistantHandle>(null)

  const go = (patch: Partial<Route>, replace = false) => navigate(href({ ...route, ...patch }), { replace })
  const open = (id: number, replace = false) => {
    setCursor(id)
    go({ threadId: id }, replace)
  }
  const closeThread = () => {
    setReply(null)
    if (threadId !== null) setCursor(threadId)
    go({ threadId: null })
  }
  // Labels belong to the account in view, so its page stays open; Settings closes.
  const selectAccount = (id: number) => go({ accountId: id, threadId: null, page: route.page === 'labels' ? 'labels' : undefined })
  const search = (q: string) => go({ view: q ? { kind: 'search', q } : { kind: 'mailbox', mailbox: 'inbox' }, threadId: null })

  // For this view only. `anchor` is where a Shift-click range starts.
  const [selection, setSelection] = useState({ viewKey, ids: new Set<number>(), anchor: null as number | null })
  const selected = selection.viewKey === viewKey ? selection.ids : new Set<number>()
  const select = (ids: Set<number>, anchor = ids.size ? selection.anchor : null) => setSelection({ viewKey, ids, anchor })

  const detail = useThread(threadId)
  const cursorThread = threads.find((t) => t.id === cursorId) ?? (cursorId === threadId ? detail.data : undefined)
  const checked = threads.filter((t) => selected.has(t.id))
  const targets = checked.length ? checked : cursorThread ? [cursorThread] : []
  const multi = !phone && checked.length > 0

  const threadAction = useThreadAction((message) => say(`Couldn’t do that: ${message}`))
  const undoAction = useUndo(say)
  const createLabel = useCreateLabel()

  const undo = (entries: number[]) =>
    undoAction.mutate(entries, { onSuccess: ({ thread_ids }) => thread_ids[0] !== undefined && setCursor(thread_ids[0]) })

  // Threads leaving the view go at once and the cursor moves on, so e e e works through the inbox.
  const run = (action: ThreadAction, on: ThreadSummary[], withLabel?: NamedLabel, scope?: { section: SectionScope; count: number }) => {
    if (on.length === 0 && !scope) return
    const label = withLabel && { name: withLabel.name, id: withLabel.id }
    const leaving = new Set(on.filter((t) => !inList(listParams, applied(t, { action, label }))).map((t) => t.id))
    if (leaving.size) {
      select(new Set())
      if (cursorId !== null && leaving.has(cursorId)) {
        const index = threads.findIndex((t) => t.id === cursorId)
        const next =
          threads.slice(index + 1).find((t) => !leaving.has(t.id)) ??
          threads
            .slice(0, Math.max(index, 0))
            .reverse()
            .find((t) => !leaving.has(t.id))
        if (threadId !== null) {
          if (next) open(next.id, true)
          else {
            setCursor(null)
            go({ threadId: null }, true)
          }
        } else setCursor(next?.id ?? null)
      }
    }
    const sent = threadAction.mutateAsync({ action, threads: on, label, section: scope?.section })
    sent.catch(() => undefined) // shown by the mutation's onError
    if (action === 'done' || action === 'delete') {
      // Undo works even before the server has answered (e z in quick succession).
      const what = action === 'done' ? 'done' : 'moved to Trash'
      const count = scope?.count ?? on.length
      const text = count === 1 ? `${what.charAt(0).toUpperCase()}${what.slice(1)}.` : `${count} threads ${what}.`
      say(text, () => {
        setNotice(null)
        void sent.then(({ entries }) => entries.length > 0 && undo(entries))
      })
    }
    // No undo: it would leave the sender blocked. Not spam in Spam reverses both.
    if (action === 'spam' || action === 'unspam') {
      const count = scope?.count ?? on.length
      const what = action === 'spam' ? 'Reported as spam' : 'Moved to the inbox'
      say(`${count === 1 ? what : `${count} threads: ${what.toLowerCase()}`}. Sender ${action === 'spam' ? 'blocked' : 'unblocked'}.`)
    }
  }
  const quick = (q: Quick, on = targets) => run(quickTarget(q, on), on)
  // Every thread the user has seen (newer arrivals stay), loaded here or not.
  const doneAll = (section: Section) => {
    if (accountId === null) return
    const loaded = section.bundle
      ? section.name === openBundle
        ? bundleThreads
        : []
      : listThreads.filter((t) => (t.section ?? null) === section.name)
    const scope = { name: section.name, bundle: section.bundle, account_id: accountId, until: section.newest_at }
    run('done', loaded, undefined, { section: scope, count: section.count })
  }
  const setUnread = (on: ThreadSummary[], unread: boolean) => {
    if (on.length === 0) return
    // Leave the thread or it is marked read again; until the route catches up, count it as marked.
    if (unread && threadId !== null && on.some((t) => t.id === threadId)) {
      markedRead.current = threadId
      setCursor(threadId)
      go({ threadId: null })
    }
    run(unread ? 'unread' : 'read', on)
  }
  const toggleRead = (on = targets) => setUnread(on, !on.every((t) => t.unread))
  const toggleSelected = (thread: ThreadSummary) => {
    const ids = new Set(selected)
    if (!ids.delete(thread.id)) ids.add(thread.id)
    select(ids, thread.id)
  }
  // A selection started while a thread is open includes that thread.
  const clickSelect = (id: number, range: boolean) => {
    const ids = new Set(selected)
    const starting = ids.size === 0
    if (starting && threadId !== null) ids.add(threadId)
    const from = starting ? cursorId : (selection.anchor ?? cursorId)
    const a = threads.findIndex((t) => t.id === from)
    const b = threads.findIndex((t) => t.id === id)
    if (range && a >= 0 && b >= 0) {
      for (const t of threads.slice(Math.min(a, b), Math.max(a, b) + 1)) ids.add(t.id)
      select(ids, from)
    } else {
      if (starting) ids.add(id)
      else if (!ids.delete(id)) ids.add(id)
      select(ids, id)
    }
  }

  const [rowMenu, setRowMenu] = useState<{ thread: ThreadSummary; x: number; y: number } | null>(null)
  const rowMenuItems = (thread: ThreadSummary): MenuItem[] => {
    const quickItem = (q: Quick): MenuItem => {
      const { icon, title } = quickAction(q, [thread])
      const [, label, shortcut] = /^(.*) \((.+)\)$/.exec(title) ?? [title, title]
      return { icon, label, shortcut, onSelect: () => quick(q, [thread]) }
    }
    return [
      quickItem('done'),
      quickItem('delete'),
      quickItem('star'),
      quickItem('important'),
      quickItem('spam'),
      // eslint-disable-next-line react-hooks/refs -- runs on a click in the menu, not during render
      { icon: 'mail', label: thread.unread ? 'Mark as read' : 'Mark as unread', shortcut: 'u', onSelect: () => toggleRead([thread]) },
      {
        icon: 'label',
        label: 'Label…',
        shortcut: 'l',
        // The picker drops down from the email pane's Label button, so the thread opens.
        onSelect: () => {
          select(new Set())
          if (thread.id !== threadId) open(thread.id)
          setPicking(true)
        },
      },
      { icon: 'select', label: selected.has(thread.id) ? 'Deselect' : 'Select', shortcut: 'x', onSelect: () => toggleSelected(thread) },
    ]
  }

  const markedRead = useRef<number | null>(null)
  // A thread of drafts only is the composer itself: no assistant under it.
  const draftOnly = threadId !== null && !!detail.data?.messages.length && detail.data.messages.every((m) => m.draft)
  const openThread = threadId === null ? undefined : (threads.find((t) => t.id === threadId) ?? detail.data)
  const markRead = useEffectEvent(() => {
    if (threadId === null) {
      markedRead.current = null
      return
    }
    if (!openThread?.unread || markedRead.current === threadId) return
    markedRead.current = threadId
    run('read', [openThread])
  })
  useEffect(() => markRead(), [threadId, openThread?.unread])

  const startDraft = (create: () => Promise<number>, then: (draftId: number) => void) => {
    if (starting.current) return
    starting.current = true
    create()
      .then(then)
      .catch((error: Error) => say(`Couldn’t start the message: ${error.message}`))
      .finally(() => (starting.current = false))
  }
  const compose = () => {
    if (composing) return setComposing({ ...composing, minimized: false })
    startDraft(
      () => createDraft('new', { accountId: accountId ?? undefined }).then((d) => d.id),
      (draftId) => setComposing({ draftId, minimized: false }),
    )
  }
  const startReply = (mode: Exclude<ComposeMode, 'new'>) => {
    const thread = detail.data
    if (threadId === null || !thread || thread.id !== threadId) return
    if (reply?.threadId === threadId) return
    const existing = thread.messages.filter((m) => m.draft).at(-1)
    const source = thread.messages.filter((m) => !m.draft).at(-1)
    if (!existing && !source) return
    startDraft(
      () => (existing ? openDraft(existing.id) : createDraft(mode, { messageId: source?.id })).then((d) => d.id),
      (draftId) => setReply({ threadId, draftId, focus: mode === 'forward' && !existing ? 'to' : 'body' }),
    )
  }

  const pickerOptions: LabelOption[] = named.map((l) => {
    const count = targets.filter((t) => t.label_ids.includes(l.id)).length
    return { name: l.name, color: l.color, state: count === 0 ? 'none' : count === targets.length ? 'all' : 'some' }
  })
  const picker =
    picking && targets.length > 0 && accountId !== null ? (
      <LabelPicker
        up={multi}
        options={pickerOptions}
        onClose={() => setPicking(false)}
        onToggle={(name, add) => run(add ? 'label' : 'unlabel', targets, named.find((l) => l.name === name))}
        onCreate={(name) => {
          const on = targets
          createLabel.mutate(
            { name, color: null, account_id: accountId },
            {
              onSuccess: (created) => run('label', on, namedLabel(created)),
              onError: (error) => say(`Couldn’t create the label: ${error.message}`),
            },
          )
        }}
      />
    ) : null


  const title = view.kind === 'mailbox' ? mailboxTitles[view.mailbox] : view.kind === 'label' ? view.name : 'Search'
  const pageTitle = route.page === 'settings' ? 'Settings' : route.page === 'labels' ? 'Labels' : title
  useEffect(() => {
    document.title = `${pageTitle} · kanso`
  }, [pageTitle])

  const back = () => {
    setReply(null)
    if (threadId !== null) setCursor(threadId)
    // A thread opened from a link has no list page before it.
    if (location.key !== 'default') navigate(-1)
    else go({ threadId: null }, true)
  }

  const syncing = (sync.data ?? []).some((s) => s.state === 'backfill' && s.account_id === accountId)
  let empty: ReactNode = `Nothing in ${title}.`
  if (accounts.length === 0)
    empty = (
      <>
        No Gmail account connected yet. <Link to="/settings">Connect one in Settings</Link>.
      </>
    )
  else if (view.kind === 'search') empty = `No mail matches “${view.q}”.`
  else if (view.kind === 'label' && !label) empty = `There is no label named “${view.name}”.`
  else if (syncing) empty = 'Mail shows up here as it syncs.'
  else if (view.kind === 'mailbox' && view.mailbox === 'inbox') empty = <InboxZero />
  const missingLabel = view.kind === 'label' && labels.isSuccess && !label

  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return
    if (route.page) return
    const target = event.target instanceof Element ? event.target : null
    if (target?.closest('input, textarea, select, [contenteditable]')) return
    if (help) {
      if (event.key === 'Escape' || event.key === '?') {
        setHelp(false)
        event.preventDefault()
      }
      return
    }
    const index = threads.findIndex((t) => t.id === cursorId)
    switch (event.key) {
      case 'j':
      case 'k': {
        const next = index < 0 ? 0 : Math.min(Math.max(index + (event.key === 'j' ? 1 : -1), 0), threads.length - 1)
        const id = threads[next]?.id
        if (id === undefined || id === cursorId) break
        // The open thread follows the cursor without piling up history.
        if (threadId !== null) open(id, true)
        else setCursor(id)
        break
      }
      case 'Enter':
      case 'o':
        if (target?.closest('a, button')) return // the focused link or button handles it
        if (cursorId !== null && cursorId !== threadId) open(cursorId)
        break
      case 'e':
        quick('done')
        break
      case '#':
        quick('delete')
        break
      case 's':
        quick('star')
        break
      case '=':
        quick('important')
        break
      case '!':
        quick('spam')
        break
      case 'u':
        toggleRead()
        break
      case 'l':
        if (targets.length === 0) return
        setPicking(true)
        break
      case 'x':
        if (!cursorThread) return
        toggleSelected(cursorThread)
        break
      case 'c':
        compose()
        break
      case 'r':
        startReply('reply')
        break
      case 'a':
        startReply('reply_all')
        break
      case 'f':
        startReply('forward')
        break
      case 'z':
        if (!notice?.undo) return
        notice.undo()
        break
      case 'q':
        if (assistantRef.current) assistantRef.current.focus()
        else if (ai && !phone && threadId === null) setAsking(true)
        else return
        break
      case '/':
        searchRef.current?.focus()
        searchRef.current?.select()
        break
      case '?':
        setHelp(true)
        break
      case 'Escape':
        if (selected.size) select(new Set())
        else if (threadId !== null) closeThread()
        else if (asking) setAsking(false)
        else if (view.kind === 'search') search('')
        else return
        break
      default:
        return
    }
    event.preventDefault()
  })
  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKey(event)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  // On a phone the assistant is a screen of its own, opened from Ask (or a thread's ⋯ menu).
  const showAssistant = ai && accountId !== null && !draftOnly && (phone ? asking : threadId !== null || asking)
  const paneOpen = phone || threadId !== null || showAssistant
  const assistantOnly = phone || threadId === null
  const screen = route.page ? route.page : phone && showAssistant ? 'assistant' : threadId !== null ? 'thread' : 'list'

  return (
    <div
      className={styles.shell}
      data-screen={phone ? screen : undefined}
      data-pane={phone ? undefined : paneOpen ? 'open' : 'closed'}
      style={
        phone
          ? undefined
          : ({
              '--list-width': listWidth ? `${listWidth}px` : undefined,
              '--sidebar-width': sidebarWidth ? `${sidebarWidth}px` : undefined,
            } as CSSProperties)
      }
    >
      {!phone && (
        <Sidebar
          accounts={accounts}
          route={route}
          labels={labels.data ?? []}
          userLabels={named}
          sync={sync.data ?? []}
          notice={notice}
          ai={ai}
          update={update}
          onSelectAccount={selectAccount}
          onCompose={compose}
          onLabelChanged={(name, { renamed, deleted }) => {
            if (view.kind !== 'label' || view.name !== name) return
            if (deleted) go({ view: { kind: 'mailbox', mailbox: 'inbox' }, threadId: null }, true)
            else if (renamed) go({ view: { kind: 'label', name: renamed } }, true)
          }}
        />
      )}
      {!phone && (
        <Resizer
          label="Resize the sidebar"
          width={sidebarWidth ?? undefined}
          min={160}
          max={360}
          onResize={(width, done) => {
            setSidebarWidth(width)
            if (done) storeWidth(SIDEBAR_WIDTH_KEY, width)
          }}
          onReset={() => {
            setSidebarWidth(null)
            storeWidth(SIDEBAR_WIDTH_KEY, null)
          }}
        />
      )}
      {menuOpen && (
        <MenuSheet
          accounts={accounts}
          route={route}
          labels={labels.data ?? []}
          userLabels={named}
          sync={sync.data ?? []}
          ai={ai}
          onSelectAccount={selectAccount}
          onClose={() => setMenu({ open: false, at: location.key })}
        />
      )}
      {route.page === 'settings' ? (
        <Settings accounts={accounts} sync={sync.data ?? []} ai={ai} onMenu={openMenu} />
      ) : route.page === 'labels' ? (
        <Labels key={accountId} account={accounts.find((a) => a.id === accountId)} labels={labels.data ?? []} ai={ai} onMenu={openMenu} />
      ) : (
        <>
          <ThreadList
            title={title}
            query={view.kind === 'search' ? view.q : ''}
            rows={missingLabel ? [] : rows}
            status={missingLabel || accountId === null ? 'success' : list.status}
            error={list.error}
            empty={empty}
            onRangeEnd={onRangeEnd}
            cursorId={cursorId}
            openId={threadId}
            selected={selected}
            labelsById={labelsById}
            hideLabel={label?.name}
            hideChips={grouped && ai}
            labelInbox={ai && grouped && accountId !== null ? <LabelInbox accountId={accountId} onFinished={say} /> : undefined}
            onDoneAll={doneAll}
            onToggleBundle={(name) => setBundle(openBundle === name ? null : { viewKey, name })}
            threadHref={(id) => href({ ...route, threadId: id })}
            onOpen={(id) => {
              if (selected.size) {
                select(new Set())
                if (id !== threadId) open(id)
              } else if (id === threadId) closeThread()
              else open(id)
            }}
            onQuick={(q, thread) => quick(q, [thread])}
            onSearch={search}
            searchRef={searchRef}
            phone={phone}
            subtitle={accounts.find((a) => a.id === accountId)?.email}
            footer={
              phone && (
                <BottomBar
                  view={view}
                  inboxHref={href({ view: { kind: 'mailbox', mailbox: 'inbox' }, threadId: null, accountId })}
                  onSearch={search}
                  onCompose={compose}
                  onAsk={ai && accountId !== null ? () => setAsking(true) : undefined}
                  onMenu={() => setMenu({ open: true, at: location.key })}
                />
              )
            }
            onAsk={ai && !phone && !paneOpen ? () => setAsking(true) : undefined}
            onRowMenu={phone ? undefined : (thread, point) => setRowMenu({ thread, ...point })}
            menuId={rowMenu?.thread.id}
            onSelectClick={phone ? undefined : clickSelect}
            selectionBar={
              multi && (
                <Selection
                  threads={checked}
                  onQuick={(q) => quick(q)}
                  onRead={(unread) => setUnread(checked, unread)}
                  onLabel={() => setPicking((open) => !open)}
                  picker={picker}
                  onClear={() => select(new Set())}
                />
              )
            }
          />
          {paneOpen && !phone && (
            <Resizer
              label="Resize the thread list"
              width={listWidth ?? undefined}
              onResize={(width, done) => {
                setListWidth(width)
                if (done) storeWidth(LIST_WIDTH_KEY, width)
              }}
              onReset={() => {
                setListWidth(null)
                storeWidth(LIST_WIDTH_KEY, null)
              }}
            />
          )}
          {paneOpen && (
            <div className={styles.pane}>
              {!(showAssistant && assistantOnly) && (
                <Reader
                  threadId={threadId}
                  accounts={accounts}
                  reply={reply}
                  onComposeFinished={(text) => {
                    setReply(null)
                    say(text)
                  }}
                  onCloseReply={() => setReply(null)}
                  onReply={startReply}
                  onCloseThread={closeThread}
                  targets={targets}
                  onQuick={(q) => quick(q)}
                  onToggleRead={() => toggleRead()}
                  onLabel={() => setPicking((open) => !open)}
                  picker={multi ? null : picker}
                  phone={phone}
                  backLabel={title}
                  onBack={back}
                  onAsk={phone && ai && accountId !== null ? () => setAsking(true) : undefined}
                />
              )}
              {showAssistant && (
                <Assistant
                  key={accountId}
                  accountId={accountId}
                  ref={assistantRef}
                  threadId={threadId}
                  threadHref={(id) => href({ ...route, threadId: id })}
                  onOpenThread={(id) => {
                    if (phone) setAsking(false) // to the thread, from the assistant's screen
                    open(id)
                  }}
                  onNotice={say}
                  phone={phone}
                  onClose={assistantOnly ? () => setAsking(false) : undefined}
                />
              )}
            </div>
          )}
        </>
      )}
      {rowMenu && (
        <Menu
          anchor={rowMenu}
          label={`Actions for ${rowMenu.thread.subject || '(no subject)'}`}
          items={rowMenuItems(rowMenu.thread)}
          onClose={() => setRowMenu(null)}
        />
      )}
      {composing && (
        <div className={`${styles.composeWindow} ${composing.minimized ? styles.minimized : ''}`}>
          <Composer
            key={composing.draftId}
            draftId={composing.draftId}
            accounts={accounts}
            title="New message"
            focus="to"
            minimized={composing.minimized}
            onToggleMinimize={() => setComposing({ ...composing, minimized: !composing.minimized })}
            onClose={() => setComposing(null)}
            onFinished={(text) => {
              setComposing(null)
              say(text)
            }}
          />
        </div>
      )}
      {phone && (notice || update) && (
        <Toast notice={notice} update={update} place={screen === 'list' || (screen === 'thread' && !reply) ? 'bar' : screen === 'assistant' ? 'sheet' : 'bottom'} />
      )}
      {help && <ShortcutHelp ai={ai} onClose={() => setHelp(false)} />}
    </div>
  )
}

function Toast({ notice, update, place }: { notice: Notice | null; update: boolean; place: 'bar' | 'sheet' | 'bottom' }) {
  return (
    <div className={`${styles.toast} ${styles[place] ?? ''}`} role="status">
      {update && (
        <p>
          <span>A new version of kanso is ready.</span>
          <button type="button" onClick={applyUpdate}>
            Reload
          </button>
        </p>
      )}
      {notice && (
        <p key={notice.id}>
          <span>{notice.text}</span>
          {notice.undo && (
            <button type="button" onClick={notice.undo}>
              Undo
            </button>
          )}
        </p>
      )}
    </div>
  )
}

// A thread can move between pages as mail arrives, so keep each once.
function unique(pages: ThreadPage[] | undefined): ThreadSummary[] {
  const seen = new Set<number>()
  return (pages ?? []).flatMap((page) => page.threads).filter((t) => !seen.has(t.id) && !!seen.add(t.id))
}
