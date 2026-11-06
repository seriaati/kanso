import {
  useCallback,
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { Menu, type MenuItem } from '../components/Menu'
import { Resizer } from '../components/Resizer'
import { Assistant, type AssistantHandle } from '../features/assistant/Assistant'
import { BottomBar } from '../features/phone/BottomBar'
import { ActionSheet } from '../features/phone/ActionSheet'
import { MenuSheet } from '../features/phone/MenuSheet'
import { Composer } from '../features/compose/Composer'
import { CommandBar } from '../features/command/CommandBar'
import { ShortcutHelp } from '../features/help/ShortcutHelp'
import { LabelPicker, type LabelOption } from '../features/labels/LabelPicker'
import { Login } from '../features/login/Login'
import { Reader, type ThreadDraft } from '../features/reader/Reader'
import { Selection } from '../features/reader/Selection'
import { Cleanup } from '../features/cleanup/Cleanup'
import { Labels } from '../features/labels/Labels'
import { Settings } from '../features/settings/Settings'
import { Sidebar, type Notice } from '../features/sidebar/Sidebar'
import { InboxZero, ThreadList, type LabelChip, type ListRow } from '../features/threads/ThreadList'
import { pickAccount, rememberAccount, useAccounts, type Account } from './accounts'
import { applied, bundleActions, inList, quickAction, quickTarget, redundant, unsubscribe, useThreadAction, useUndo, type BundleAction, type BundleScope, type LabelScope, type Quick, type ThreadAction } from './actions'
import { useConfig } from './ai'
import { ApiError, queryClient, Unauthorized } from './api'
import { createDraft, linkedDraft, openDraft, type ComposeMode, type LinkedDraft } from './compose'
import styles from './App.module.css'
import { useCreateLabel } from './labels'
import {
  namedLabel,
  refreshMail,
  useBundleThreads,
  useLabels,
  useLiveUpdates,
  useSync,
  useThread,
  useThreads,
  userLabels,
  viewParams,
  type NamedLabel,
  type Bundle,
  type ThreadPage,
  type ThreadSummary,
} from './mail'
import { usePhone } from './media'
import { applyAppUpdate, useAppLinks, useAppUpdate, useBackButton, useSystemBars } from './native'
import { href, parse, useRoute, type Mailbox, type Route } from './nav'
import { applyUpdate, useUpdateReady } from './update'

export function App() {
  const accounts = useAccounts()
  const route = useRoute()
  useSystemBars()
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
            ? `Can’t reach Kanso: ${accounts.error.message}`
            : 'Can’t reach Kanso. You may be offline; it tries again when the connection is back.'}
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
// ThreadList.module.css; the sidebar: Sidebar.module.css; the assistant: Assistant.module.css).
const LIST_WIDTH_KEY = 'kanso.listWidth'
const SIDEBAR_WIDTH_KEY = 'kanso.sidebarWidth'
const ASSISTANT_WIDTH_KEY = 'kanso.assistantWidth'

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

const SIDEBAR_COLLAPSED_KEY = 'kanso.sidebarCollapsed'

function storedCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1'
  } catch {
    return false // storage blocked
  }
}

function storeCollapsed(collapsed: boolean) {
  try {
    if (collapsed) localStorage.setItem(SIDEBAR_COLLAPSED_KEY, '1')
    else localStorage.removeItem(SIDEBAR_COLLAPSED_KEY)
  } catch {
    // storage blocked: kept until the page reloads
  }
}

const NOTICE_MS = 5000
const UNDO_MS = 6000

// The actions that say what they did, all but Report spam with an undo.
const told: Partial<Record<ThreadAction, string>> = {
  done: 'done',
  delete: 'moved to Trash',
  untrash: 'restored to the inbox',
  spam: 'reported as spam',
  unspam: 'moved to the inbox',
}

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
  // A thread opened from outside (a notification, a link) gets its list under it, so Back goes
  // there. `starting`: the app opened on it, so the list takes its place in the history.
  const openLink = useEffectEvent((url: string, starting = false) => {
    const target = new URL(url, window.location.origin)
    const linked = parse(target.pathname, target.searchParams)
    if (linked?.threadId == null) {
      if (!starting) void navigate(url)
      return
    }
    const list = href({ ...linked, threadId: null })
    if (starting) void navigate(list, { replace: true })
    else if (list !== location.pathname + location.search) void navigate(list)
    void navigate(url)
  })
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current) return
    opened.current = true
    if (location.key === 'default') openLink(location.pathname + location.search, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, for the page the app opened on
  }, [])
  // A tapped new-mail notification opens its thread here (public/push-sw.js).
  useEffect(() => {
    const container = navigator.serviceWorker
    if (!container) return
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type === 'kanso:open' && typeof event.data.url === 'string') openLink(event.data.url)
    }
    container.addEventListener('message', onMessage)
    return () => container.removeEventListener('message', onMessage)
  }, [])
  // null only while no account is connected.
  const accountId = pickAccount(accounts, urlRoute.accountId)?.id ?? null
  useEffect(() => {
    if (accountId !== null) rememberAccount(accountId)
  }, [accountId])
  const route = { ...urlRoute, accountId }
  const { view, threadId } = route
  const config = useConfig().data
  const ai = config?.ai ?? false
  const labeling = config?.labeling ?? false
  const phone = usePhone()
  const update = useUpdateReady()
  const appUpdate = useAppUpdate()
  // The phone's menu sheet closes whenever the page changes (back too).
  const [menu, setMenu] = useState({ open: false, at: location.key })
  if (menu.at !== location.key) setMenu({ open: false, at: location.key })
  const menuOpen = phone && menu.open
  const openMenu = phone ? () => setMenu({ open: true, at: location.key }) : undefined
  const grouped = view.kind === 'mailbox' && view.mailbox === 'inbox'

  const labels = useLabels(accountId)
  const sync = useSync()
  const named = useMemo(() => userLabels(labels.data ?? []), [labels.data])
  const labelNames = useMemo(() => named.map((l) => l.name), [named])
  const label = view.kind === 'label' ? named.find((l) => l.name === view.name) : undefined
  const labelIds = label && [label.id]
  const list = useThreads(view, accountId, labelIds)
  const listParams = viewParams(view, accountId, labelIds)
  const labelsById = useMemo(
    () =>
      new Map<number, LabelChip>(
        named.map((l) => [l.id, { name: l.name, color: l.color, icon: l.icon }]),
      ),
    [named],
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
  const bundles = useMemo(() => (grouped ? (list.data?.pages[0]?.bundles ?? []).filter((b) => b.count > 0) : []), [grouped, list.data])

  // In the inbox: bundles among the unlabeled threads by time, each where its newest thread is, with
  // its new threads under it (all of them when open). A bundle past the loaded threads waits for them.
  const { rows, threads, openEnd } = useMemo(() => {
    const rows: ListRow[] = []
    let openEnd = -1
    const addBundle = (bundle: Bundle) => {
      const open = bundle.name === openBundle
      rows.push({ kind: 'bundle', bundle, open })
      const under = open ? bundleThreads : listThreads.filter((t) => t.bundle === bundle.name)
      for (const thread of under) rows.push({ kind: 'thread', thread, nested: true })
      if (open) openEnd = rows.length - 1
    }
    const time = (iso: string) => Date.parse(iso)
    const loaded = hasNextPage ? listThreads.at(-1) : undefined
    const placed = bundles.filter((b) => !loaded || time(b.newest_at) >= time(loaded.last_message_at))
    let next = 0
    for (const thread of listThreads) {
      if (thread.bundle) continue
      while (next < placed.length && time(placed[next].newest_at) >= time(thread.last_message_at)) addBundle(placed[next++])
      rows.push({ kind: 'thread', thread })
    }
    for (const bundle of placed.slice(next)) addBundle(bundle)
    return { rows, threads: rows.flatMap((row) => (row.kind === 'thread' ? [row.thread] : [])), openEnd }
  }, [listThreads, bundleThreads, bundles, openBundle, hasNextPage])
  const onRangeEnd = (last: number) => {
    if (hasNextPage && last >= rows.length - 10) loadMore()
    if (bundleList.hasNextPage && !bundleList.isFetching && last >= openEnd - 10) void bundleList.fetchNextPage()
  }

  const [cursor, setCursor] = useState<number | null>(null)
  const cursorId = threadId ?? cursor
  // `seen`: on a phone, its history entry (below) is in place.
  const [composing, setComposing] = useState<{ draftId: number; minimized: boolean; seen?: boolean } | null>(null)
  const [reply, setReply] = useState<ThreadDraft | null>(null)
  const starting = useRef(false)
  const [help, setHelp] = useState(false)
  const [commanding, setCommanding] = useState(false)
  // The assistant, beside the mail (on a phone, a screen of its own). Questions go with the open
  // thread, if any.
  const [asking, setAsking] = useState(false)
  const [picking, setPicking] = useState(false)
  const [listWidth, setListWidth] = useState(() => storedWidth(LIST_WIDTH_KEY))
  const [sidebarWidth, setSidebarWidth] = useState(() => storedWidth(SIDEBAR_WIDTH_KEY))
  const [sidebarCollapsed, setSidebarCollapsed] = useState(storedCollapsed)
  const toggleSidebar = () => {
    setSidebarCollapsed(!sidebarCollapsed)
    storeCollapsed(!sidebarCollapsed)
  }
  const [assistantWidth, setAssistantWidth] = useState(() => storedWidth(ASSISTANT_WIDTH_KEY))
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
  // Labels and Clean up belong to the account in view, so their page stays open; Settings closes.
  const selectAccount = (id: number) => go({ accountId: id, threadId: null, page: route.page === 'settings' ? undefined : route.page })
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
  const run = (action: ThreadAction, on: ThreadSummary[], withLabel?: NamedLabel, scope?: { bundle?: BundleScope; inLabel?: LabelScope; count: number }) => {
    if (on.length === 0 && !scope) return
    const label = withLabel && { name: withLabel.name, id: withLabel.id }
    // Labeled, an unlabeled inbox thread moves into that label's bundle.
    const moves = (t: ThreadSummary) => action === 'label' && !t.bundle
    const leaving = new Set(on.filter((t) => !inList(listParams, applied(t, { action, label }), moves(t))).map((t) => t.id))
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
    const sent = threadAction.mutateAsync({ action, threads: on, label, bundle: scope?.bundle, inLabel: scope?.inLabel })
    sent.catch(() => undefined) // shown by the mutation's onError
    // Marking one thread read says nothing; a whole bundle says how many.
    const what = told[action] ?? (scope && action === 'read' ? 'marked read' : undefined)
    if (what) {
      const count = scope?.count ?? on.length
      const after = action === 'spam' ? ' Sender blocked.' : action === 'unspam' ? ' Sender unblocked.' : ''
      const text = `${count === 1 ? `${what.charAt(0).toUpperCase()}${what.slice(1)}` : `${count} threads ${what}`}.${after}`
      // No undo for Report spam: it would leave the sender blocked. Not spam in Spam reverses both.
      if (action === 'spam') say(text)
      // Undo works even before the server has answered (e z in quick succession).
      else
        say(text, () => {
          setNotice(null)
          void sent.then(({ entries }) => entries.length > 0 && undo(entries))
        })
    }
  }
  const unsubscribeFrom = (id: number) =>
    void unsubscribe(id)
      .then(({ method, url }) => {
        if (method !== 'link' || !url) return say(method === 'email' ? 'Unsubscribed by email.' : 'Unsubscribed.')
        const page = window.open(url, '_blank')
        if (!page) return say('Your browser blocked the unsubscribe page. Allow pop-ups and try again.')
        page.opener = null
        say('Finish unsubscribing on the sender’s page.')
      })
      .catch((error: Error) => say(`Couldn’t unsubscribe: ${error.message}`))
  const quick = (q: Quick, on = targets) => run(quickTarget(q, on), on)
  // Every thread the user has seen (newer arrivals stay), loaded here or not.
  const bundleAction = (action: BundleAction, bundle: Bundle) => {
    if (accountId === null) return
    const loaded = bundle.name === openBundle ? bundleThreads : listThreads.filter((t) => t.bundle === bundle.name)
    const scope = { name: bundle.name, account_id: accountId, until: bundle.newest_at }
    run(action, loaded, undefined, { bundle: scope, count: bundle.count })
  }
  // A label's page: every thread in it up to the newest one shown, loaded here or not.
  const labelAction = (action: BundleAction) => {
    if (!label || threads.length === 0) return
    const total = labels.data?.find((l) => l.id === label.id)?.threads_total ?? threads.length
    const inLabel = { id: label.id, until: threads[0].last_message_at }
    run(action, threads, undefined, { inLabel, count: action === 'read' ? label.unread : total })
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

  // A row's right-click menu; on a phone, its long-press sheet.
  const [rowMenu, setRowMenu] = useState<
    (({ thread: ThreadSummary; bundle?: never } | { bundle: Bundle; thread?: never }) & { x: number; y: number }) | null
  >(null)
  const rowMenuItems = (thread: ThreadSummary): MenuItem[] => {
    const quickItem = (q: Quick): MenuItem => {
      const { icon, title } = quickAction(q, [thread])
      const [, label, shortcut] = /^(.*) \((.+)\)$/.exec(title) ?? [title, title]
      return { icon, label, shortcut, onSelect: () => quick(q, [thread]) }
    }
    return [
      ...(redundant('done', [thread]) ? [] : [quickItem('done')]),
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
      // Phones don't select (no selection bar).
      ...(phone ? [] : [{ icon: 'select', label: selected.has(thread.id) ? 'Deselect' : 'Select', shortcut: 'x', onSelect: () => toggleSelected(thread) } satisfies MenuItem]),
    ]
  }
  const bundleMenuItems = (bundle: Bundle): MenuItem[] =>
    bundleActions.map(({ action, icon, label }) => ({ icon, label, onSelect: () => bundleAction(action, bundle) }))

  const markedRead = useRef<number | null>(null)
  // A thread of drafts only is the composer itself: nothing for the assistant to read.
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
  // `linked`: filled in from a link, which opens it over the inbox.
  const compose = (linked?: LinkedDraft) => {
    if (composing) {
      if (linked) say('Close the open message to start another.')
      return setComposing({ ...composing, minimized: false })
    }
    const under = linked ? href(route) : { pathname: location.pathname, search: location.search }
    startDraft(
      () => createDraft('new', { accountId: accountId ?? undefined }, linked).then((d) => d.id),
      (draftId) => {
        setComposing({ draftId, minimized: false })
        if (phone) void navigate(under, { state: { compose: draftId } })
      },
    )
  }
  // /compose?to=…&subject=… (linkedDraft): Compose, filled in, over the inbox.
  const linked = location.pathname === '/compose' ? location.search : null
  const openLinked = useEffectEvent((search: string) => {
    void navigate(href(route), { replace: true })
    compose(linkedDraft(new URLSearchParams(search)))
  })
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the URL asks for a draft, created once
    if (linked !== null) openLinked(linked)
  }, [linked])
  // The Android app's mailto: links, and its notifications' links to a thread (on this server).
  useAppLinks((url) => {
    if (/^mailto:/i.test(url)) void navigate(`/compose?${new URLSearchParams({ mailto: url })}`)
    // eslint-disable-next-line react-hooks/rules-of-hooks -- useAppLinks calls this from an Effect Event
    else if (url.startsWith(`${window.location.origin}/`)) openLink(url.slice(window.location.origin.length))
  })
  // On a phone the window covers the app, so it has a history entry of its own: Back closes it
  // (as Close does), and closing it leaves the entry.
  const composeEntry = (location.state as { compose?: number } | null)?.compose
  if (composing && !composing.seen && composeEntry === composing.draftId) setComposing({ ...composing, seen: true })
  const composeBacked = !!composing?.seen && composeEntry !== composing.draftId
  const closeCompose = () => {
    setComposing(null)
    if (composing && composeEntry === composing.draftId) navigate(-1)
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
    return { name: l.name, color: l.color, icon: l.icon, state: count === 0 ? 'none' : count === targets.length ? 'all' : 'some' }
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
  const pageTitle = route.page === 'settings' ? 'Settings' : route.page === 'labels' ? 'Labels' : route.page === 'cleanup' ? 'Clean up' : title
  useEffect(() => {
    document.title = `${pageTitle} · Kanso`
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

  const focusSearch = () => {
    searchRef.current?.focus()
    searchRef.current?.select()
  }
  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.defaultPrevented || event.altKey) return
    const target = event.target instanceof Element ? event.target : null
    const typing = !!target?.closest('input, textarea, select, [contenteditable]')
    // Ctrl/⌘ shortcuts; the browser's own where they don't apply (Ctrl+A in a text box).
    if (event.ctrlKey || event.metaKey) {
      if (event.shiftKey) return
      const listed = !route.page && !typing && !help
      switch (event.key.toLowerCase()) {
        case 'k':
          setHelp(false)
          setCommanding((open) => !open)
          break
        case 'f':
          if (!listed || !searchRef.current) return
          focusSearch()
          break
        case 'a':
          if (!listed || phone || threads.length === 0) return
          select(new Set(threads.map((t) => t.id)), null)
          break
        default:
          return
      }
      event.preventDefault()
      return
    }
    if (route.page || typing) return
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
      case 'd':
      case 'Delete':
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
        else if (ai && accountId !== null) setAsking(true)
        else return
        break
      case '/':
        focusSearch()
        break
      case '?':
        setHelp(true)
        break
      case '[':
        if (phone) return
        toggleSidebar()
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
  const showAssistant = ai && accountId !== null && asking
  const paneOpen = phone || threadId !== null
  const screen = route.page ? route.page : phone && showAssistant ? 'assistant' : threadId !== null ? 'thread' : 'list'
  // The Android app's Back: closes what's open over the page first, then follows the history; at
  // its start it goes to the inbox, and from there leaves the app.
  useBackButton((canGoBack) => {
    // Esc for an open dialog or menu, but not for the shortcuts above (there it leaves the thread).
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    const stop = (event: Event) => event.stopPropagation()
    document.addEventListener('keydown', stop)
    ;(document.querySelector('dialog[open]') ?? document.body).dispatchEvent(escape)
    document.removeEventListener('keydown', stop)
    if (escape.defaultPrevented) return true
    const inbox = !route.page && threadId === null && view.kind === 'mailbox' && view.mailbox === 'inbox'
    if (rowMenu) setRowMenu(null)
    else if (menuOpen) setMenu({ open: false, at: location.key })
    else if (phone && showAssistant) setAsking(false)
    else if (canGoBack) navigate(-1)
    else if (!inbox) go({ view: { kind: 'mailbox', mailbox: 'inbox' }, threadId: null, page: undefined }, true)
    else return false
    return true
  })
  const assistant = showAssistant && (
    <Assistant
      key={accountId}
      accountId={accountId}
      ref={assistantRef}
      threadId={draftOnly ? null : threadId}
      threadHref={(id) => href({ ...route, threadId: id })}
      onOpenThread={(id) => {
        if (phone) setAsking(false) // to the thread, from the assistant's screen
        open(id)
      }}
      onNotice={say}
      phone={phone}
      onClose={() => setAsking(false)}
    />
  )
  const rowMenuFor = rowMenu?.thread
    ? { title: rowMenu.thread.subject || '(no subject)', items: rowMenuItems(rowMenu.thread) }
    : rowMenu?.bundle && { title: rowMenu.bundle.name, items: bundleMenuItems(rowMenu.bundle) }

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
              '--assistant-width': assistantWidth ? `${assistantWidth}px` : undefined,
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
          labeling={labeling}
          update={update}
          appUpdate={appUpdate}
          collapsed={sidebarCollapsed}
          onToggle={toggleSidebar}
          onSelectAccount={selectAccount}
          onCompose={() => compose()}
          onLabelChanged={(name, { renamed, deleted }) => {
            if (view.kind !== 'label' || view.name !== name) return
            if (deleted) go({ view: { kind: 'mailbox', mailbox: 'inbox' }, threadId: null }, true)
            else if (renamed) go({ view: { kind: 'label', name: renamed } }, true)
          }}
        />
      )}
      {!phone && !sidebarCollapsed && (
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
          ai={labeling}
          onSelectAccount={selectAccount}
          onClose={() => setMenu({ open: false, at: location.key })}
        />
      )}
      {route.page === 'settings' ? (
        <Settings accounts={accounts} sync={sync.data ?? []} ai={ai} labeling={labeling} onMenu={openMenu} />
      ) : route.page === 'labels' ? (
        <Labels key={accountId} account={accounts.find((a) => a.id === accountId)} labels={labels.data ?? []} ai={labeling} claude={ai} onMenu={openMenu} />
      ) : route.page === 'cleanup' ? (
        <Cleanup
          key={accountId}
          account={accounts.find((a) => a.id === accountId)}
          labels={named}
          ai={ai}
          labeling={labeling}
          onMenu={openMenu}
          onNotice={say}
          onUndo={(entries) => {
            setNotice(null)
            undoAction.mutate(entries, { onSettled: () => void queryClient.invalidateQueries({ queryKey: ['label-inbox'] }) })
          }}
          onUnsubscribe={unsubscribeFrom}
        />
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
            hideChips={grouped}
            onBundleAction={bundleAction}
            onLabelAction={label && threads.length > 0 ? labelAction : undefined}
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
            account={accounts.find((a) => a.id === accountId)}
            onMenu={() => setMenu({ open: true, at: location.key })}
            footer={
              phone && (
                <BottomBar
                  view={view}
                  inboxHref={href({ view: { kind: 'mailbox', mailbox: 'inbox' }, threadId: null, accountId })}
                  onSearch={search}
                  labels={labelNames}
                  onCompose={() => compose()}
                  onAsk={ai && accountId !== null ? () => setAsking(true) : undefined}
                  onMenu={() => setMenu({ open: true, at: location.key })}
                />
              )
            }
            onAsk={ai && !phone && !paneOpen && !showAssistant && accountId !== null ? () => setAsking(true) : undefined}
            onRowMenu={(thread, point) => setRowMenu({ thread, ...point })}
            menuId={rowMenu?.thread?.id}
            onBundleMenu={(bundle, point) => setRowMenu({ bundle, ...point })}
            onRefresh={() => refreshMail(accountId)}
            menuBundle={rowMenu?.bundle?.name}
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
              {phone && showAssistant ? (
                assistant
              ) : (
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
                  onUnsubscribe={() => threadId !== null && unsubscribeFrom(threadId)}
                  onLabel={() => setPicking((open) => !open)}
                  picker={multi ? null : picker}
                  phone={phone}
                  backLabel={title}
                  onBack={back}
                  onAsk={phone && ai && accountId !== null ? () => setAsking(true) : undefined}
                  onAssistant={!phone && ai && accountId !== null ? () => (showAssistant ? setAsking(false) : setAsking(true)) : undefined}
                  assistantOpen={showAssistant}
                />
              )}
            </div>
          )}
          {showAssistant && !phone && (
            <>
              <Resizer
                label="Resize the assistant"
                side="after"
                width={assistantWidth ?? undefined}
                min={280}
                onResize={(width, done) => {
                  setAssistantWidth(width)
                  if (done) storeWidth(ASSISTANT_WIDTH_KEY, width)
                }}
                onReset={() => {
                  setAssistantWidth(null)
                  storeWidth(ASSISTANT_WIDTH_KEY, null)
                }}
              />
              {assistant}
            </>
          )}
        </>
      )}
      {rowMenu &&
        rowMenuFor &&
        (phone ? (
          <ActionSheet title={rowMenuFor.title} items={rowMenuFor.items} onClose={() => setRowMenu(null)} />
        ) : (
          <Menu anchor={rowMenu} label={`Actions for ${rowMenuFor.title}`} items={rowMenuFor.items} onClose={() => setRowMenu(null)} />
        ))}
      {composing && (
        <ComposeWindow key={composing.draftId} minimized={composing.minimized} movable={!phone}>
          <Composer
            key={composing.draftId}
            draftId={composing.draftId}
            title="New message"
            focus="to"
            minimized={composing.minimized}
            closing={composeBacked}
            onToggleMinimize={() => setComposing({ ...composing, minimized: !composing.minimized })}
            onClose={closeCompose}
            onFinished={(text) => {
              closeCompose()
              say(text)
            }}
          />
        </ComposeWindow>
      )}
      {phone && (notice || update || appUpdate) && (
        <Toast notice={notice} update={update} appUpdate={appUpdate} place={screen === 'list' || (screen === 'thread' && !reply) ? 'bar' : screen === 'assistant' ? 'sheet' : 'bottom'} />
      )}
      {help && <ShortcutHelp ai={ai} onClose={() => setHelp(false)} />}
      {commanding && (
        <CommandBar
          route={route}
          accounts={accounts}
          labels={named}
          onNavigate={(to) => {
            setCommanding(false)
            void navigate(to)
          }}
          onClose={() => setCommanding(false)}
        />
      )}
    </div>
  )
}

// The window's sizes in App.module.css (.composeWindow), to keep it on screen.
const windowSize = (minimized: boolean) =>
  minimized ? { width: 320, height: 46 } : { width: Math.min(540, innerWidth - 48), height: Math.min(620, innerHeight - 32) }

/** Bottom right until its top row is dragged; it stays within the app. */
function ComposeWindow({ minimized, movable, children }: { minimized: boolean; movable: boolean; children: ReactNode }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const drag = useRef<{ dx: number; dy: number } | null>(null)
  const inside = (x: number, y: number) => {
    const { width, height } = windowSize(minimized)
    return { x: Math.min(Math.max(x, 0), innerWidth - width), y: Math.min(Math.max(y, 0), innerHeight - height) }
  }
  // Restored near the bottom, it grows back up into view.
  const [shownAs, setShownAs] = useState(minimized)
  if (shownAs !== minimized) {
    setShownAs(minimized)
    if (at) setAt(inside(at.x, at.y))
  }
  const end = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return
    drag.current = null
    event.currentTarget.releasePointerCapture(event.pointerId)
    delete document.documentElement.dataset.dragging
  }
  return (
    <div
      className={`${styles.composeWindow} ${minimized ? styles.minimized : ''}`}
      style={movable && at ? { left: at.x, top: at.y, right: 'auto', bottom: 'auto' } : undefined}
      onPointerDown={(event) => {
        const target = event.target as Element
        if (!movable || event.button !== 0 || !target.closest('[data-drag-handle]') || target.closest('button, input')) return
        event.preventDefault()
        const box = event.currentTarget.getBoundingClientRect()
        drag.current = { dx: event.clientX - box.left, dy: event.clientY - box.top }
        event.currentTarget.setPointerCapture(event.pointerId)
        document.documentElement.dataset.dragging = '' // no text selection meanwhile
      }}
      onPointerMove={(event) => {
        if (drag.current) setAt(inside(event.clientX - drag.current.dx, event.clientY - drag.current.dy))
      }}
      onPointerUp={end}
      onPointerCancel={end}
    >
      {children}
    </div>
  )
}

function Toast({ notice, update, appUpdate, place }: { notice: Notice | null; update: boolean; appUpdate: string | null; place: 'bar' | 'sheet' | 'bottom' }) {
  return (
    <div className={`${styles.toast} ${styles[place] ?? ''}`} role="status">
      {/* One at a time, the app first: installing it restarts the app, which also loads the new Kanso. */}
      {appUpdate && (
        <p>
          <span>A new version of the app is out.</span>
          <button type="button" onClick={() => applyAppUpdate(appUpdate)}>
            Update
          </button>
        </p>
      )}
      {!appUpdate && update && (
        <p>
          <span>A new version of Kanso is ready.</span>
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
