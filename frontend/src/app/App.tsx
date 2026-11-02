import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { Icon } from '../components/Icon'
import { Assistant, type AssistantHandle } from '../features/assistant/Assistant'
import { ShortcutHelp } from '../features/help/ShortcutHelp'
import { LabelPicker, type LabelOption } from '../features/labels/LabelPicker'
import { Login } from '../features/login/Login'
import { Reader, type ThreadDraft } from '../features/reader/Reader'
import { Settings } from '../features/settings/Settings'
import { Sidebar, type Notice } from '../features/sidebar/Sidebar'
import { LabelInbox } from '../features/threads/LabelInbox'
import { ThreadList, type LabelChip, type ListRow } from '../features/threads/ThreadList'
import { useAccounts, type Account } from './accounts'
import { applied, inList, quickTarget, useThreadAction, useUndo, type Quick, type SectionScope, type ThreadAction } from './actions'
import { useConfig } from './ai'
import { ApiError, Unauthorized } from './api'
import { createDraft, openDraft, type ComposeMode } from './compose'
import styles from './App.module.css'
import { useCreateLabel } from './labels'
import {
  labelColor,
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
  // Offline (the app shell still starts from the service worker): try again once back online.
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
  done: 'Done',
  sent: 'Sent',
  drafts: 'Drafts',
  trash: 'Trash',
  spam: 'Spam',
}

const NOTICE_MS = 5000
const UNDO_MS = 6000 // how long Done and Delete can be undone from the notice (z)

/** The split view: sidebar | thread list | email pane, with the assistant docked at the foot of
 * the email pane while a thread is open and AI is on (otherwise the email takes its space). */
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
  // An unknown ?account= falls back to All accounts.
  const route = { ...urlRoute, accountId: accounts.some((a) => a.id === urlRoute.accountId) ? urlRoute.accountId : null }
  const { view, threadId, accountId } = route
  const ai = useConfig().data?.ai ?? false
  const phone = usePhone()
  const update = useUpdateReady()
  // On a phone the sidebar is a drawer; it closes again whenever the page changes (back too).
  const [drawer, setDrawer] = useState({ open: false, at: location.key })
  if (drawer.at !== location.key) setDrawer({ open: false, at: location.key })
  const drawerOpen = phone && drawer.open
  const openDrawer = phone ? () => setDrawer({ open: true, at: location.key }) : undefined
  // The inbox is grouped into label sections (with AI on) and bundles (always).
  const grouped = view.kind === 'mailbox' && view.mailbox === 'inbox'

  const labels = useLabels(accountId)
  const allLabels = useLabels(null, !!route.settings) // the settings page shows every account's
  const sync = useSync()
  const named = useMemo(() => userLabels(labels.data ?? []), [labels.data])
  const label = view.kind === 'label' ? named.find((l) => l.name === view.name) : undefined
  const list = useThreads(view, accountId, label?.ids)
  const listParams = viewParams(view, accountId, label?.ids)
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

  // One bundle at a time opens below its row, and closes again when the view changes.
  const viewKey = href({ ...route, threadId: null })
  const [bundle, setBundle] = useState<{ viewKey: string; name: string } | null>(null)
  const openBundle = grouped && bundle?.viewKey === viewKey ? bundle.name : null
  const bundleList = useBundleThreads(openBundle, accountId)
  const bundleThreads = useMemo(() => unique(bundleList.data?.pages), [bundleList.data])
  const sections = useMemo(() => (grouped ? (list.data?.pages[0]?.sections ?? []) : []), [grouped, list.data])

  // The list's rows (threads, section headers, bundle rows) and the threads in keyboard order.
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
  // The new message being written (c), and the reply or forward docked in the open thread.
  const [composing, setComposing] = useState<number | null>(null)
  const [reply, setReply] = useState<ThreadDraft | null>(null)
  const starting = useRef(false)
  const [help, setHelp] = useState(false)
  const [picking, setPicking] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)
  const assistantRef = useRef<AssistantHandle>(null)
  const myEmails = useMemo(() => new Set(accounts.map((a) => a.email.toLowerCase())), [accounts])

  const go = (patch: Partial<Route>, replace = false) => navigate(href({ ...route, ...patch }), { replace })
  const open = (id: number, replace = false) => {
    setCursor(id)
    setComposing(null)
    go({ threadId: id }, replace)
  }
  const search = (q: string) => go({ view: q ? { kind: 'search', q } : { kind: 'mailbox', mailbox: 'inbox' }, threadId: null })

  // Threads checked with x, for this view only (switching views starts over).
  const [selection, setSelection] = useState({ viewKey, ids: new Set<number>() })
  const selected = selection.viewKey === viewKey ? selection.ids : new Set<number>()
  const select = (ids: Set<number>) => setSelection({ viewKey, ids })

  // What actions apply to: the checked threads, else the open (or cursor) thread.
  const detail = useThread(threadId)
  const cursorThread = threads.find((t) => t.id === cursorId) ?? (cursorId === threadId ? detail.data : undefined)
  const checked = threads.filter((t) => selected.has(t.id))
  const targets = checked.length ? checked : cursorThread ? [cursorThread] : []

  const threadAction = useThreadAction((message) => say(`Couldn’t do that: ${message}`))
  const undoAction = useUndo(say)
  const createLabel = useCreateLabel()

  const undo = (entries: number[]) =>
    undoAction.mutate(entries, { onSuccess: ({ thread_ids }) => thread_ids[0] !== undefined && setCursor(thread_ids[0]) })

  /** Run an action. Threads it moves out of this view leave the list at once, and the cursor
   * (and the open thread) moves on to the next one, so e e e works through the inbox. */
  const run = (action: ThreadAction, on: ThreadSummary[], withLabel?: NamedLabel, scope?: { section: SectionScope; count: number }) => {
    if (on.length === 0 && !scope) return
    const label = withLabel && { name: withLabel.name, ids: new Map(withLabel.accountIds.map((a, i) => [a, withLabel.ids[i]])) }
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
        if (threadId !== null && composing === null) {
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
      // Undo works at once, even before the server has answered (e z in quick succession).
      const what = action === 'done' ? 'done' : 'moved to Trash'
      const count = scope?.count ?? on.length
      const text = count === 1 ? `${what.charAt(0).toUpperCase()}${what.slice(1)}.` : `${count} threads ${what}.`
      say(text, () => {
        setNotice(null)
        void sent.then(({ entries }) => entries.length > 0 && undo(entries))
      })
    }
  }
  const quick = (q: Quick, on = targets) => run(quickTarget(q, on), on)
  /** "Done all" on a section or bundle: every thread in it the user has seen (newer arrivals
   * stay), loaded here or not. */
  const doneAll = (section: Section) => {
    const loaded = section.bundle
      ? section.name === openBundle
        ? bundleThreads
        : []
      : listThreads.filter((t) => (t.section ?? null) === section.name)
    const scope = { name: section.name, bundle: section.bundle, account_id: accountId, until: section.newest_at }
    run('done', loaded, undefined, { section: scope, count: section.count })
  }
  const toggleRead = () => {
    if (targets.length === 0) return
    const markUnread = !targets.every((t) => t.unread)
    // Back to the list: an open thread would be marked read again.
    if (markUnread && threadId !== null && targets.some((t) => t.id === threadId)) {
      setCursor(threadId)
      go({ threadId: null })
    }
    run(markUnread ? 'unread' : 'read', targets)
  }

  // Opening a thread marks it read (once per opening).
  const markedRead = useRef<number | null>(null)
  // A thread of drafts only is the composer itself: no assistant under it.
  const draftOnly = threadId !== null && !!detail.data?.messages.length && detail.data.messages.every((m) => m.draft)
  const openThread = threadId === null ? undefined : (threads.find((t) => t.id === threadId) ?? detail.data)
  const markRead = useEffectEvent(() => {
    if (threadId === null || composing !== null) {
      markedRead.current = null
      return
    }
    if (!openThread?.unread || markedRead.current === threadId) return
    markedRead.current = threadId
    run('read', [openThread])
  })
  useEffect(() => markRead(), [threadId, openThread?.unread, composing])

  /** Open the composer once its draft exists on the server (one at a time). */
  const startDraft = (create: () => Promise<number>, then: (draftId: number) => void) => {
    if (starting.current) return
    starting.current = true
    create()
      .then(then)
      .catch((error: Error) => say(`Couldn’t start the message: ${error.message}`))
      .finally(() => (starting.current = false))
  }
  const compose = () =>
    startDraft(
      () => createDraft('new', { accountId: accountId ?? accounts[0]?.id }).then((d) => d.id),
      (draftId) => {
        setComposing(draftId)
        if (route.settings) go({ settings: false }) // the composer is in the email pane
      },
    )
  /** r, a, f: in the open thread. A thread that has a draft already opens that one. */
  const startReply = (mode: Exclude<ComposeMode, 'new'>) => {
    const thread = detail.data
    if (threadId === null || composing !== null || !thread || thread.id !== threadId) return
    if (reply?.threadId === threadId) return // already writing here
    const existing = thread.messages.filter((m) => m.draft).at(-1)
    const source = thread.messages.filter((m) => !m.draft).at(-1)
    if (!existing && !source) return
    startDraft(
      () => (existing ? openDraft(existing.id) : createDraft(mode, { messageId: source?.id })).then((d) => d.id),
      (draftId) => setReply({ threadId, draftId, focus: mode === 'forward' && !existing ? 'to' : 'body' }),
    )
  }

  const accountIds = accountId === null ? accounts.map((a) => a.id) : [accountId]
  const pickerOptions: LabelOption[] = named
    .filter((l) => targets.every((t) => l.accountIds.includes(t.account_id)))
    .map((l) => {
      const count = targets.filter((t) => l.ids.some((id) => t.label_ids.includes(id))).length
      return { name: l.name, color: l.color, state: count === 0 ? 'none' : count === targets.length ? 'all' : 'some' }
    })
  const picker =
    picking && targets.length > 0 ? (
      <LabelPicker
        options={pickerOptions}
        onClose={() => setPicking(false)}
        onToggle={(name, add) => run(add ? 'label' : 'unlabel', targets, named.find((l) => l.name === name))}
        onCreate={(name) => {
          const on = targets
          createLabel.mutate(
            { name, color: null, account_ids: accountIds },
            {
              onSuccess: (created) =>
                run('label', on, {
                  name,
                  color: '',
                  ids: created.map((l) => l.id),
                  accountIds: created.map((l) => l.account_id),
                  unread: 0,
                  description: '',
                  aiManaged: false,
                  isBundle: false,
                }),
              onError: (error) => say(`Couldn’t create the label: ${error.message}`),
            },
          )
        }}
      />
    ) : null


  const title = view.kind === 'mailbox' ? mailboxTitles[view.mailbox] : view.kind === 'label' ? view.name : 'Search'
  const pageTitle = route.settings ? 'Settings' : title
  useEffect(() => {
    document.title = `${pageTitle} · kanso`
  }, [pageTitle])

  /** On a phone: from the thread (or a new message) back to the list. */
  const back = () => {
    if (composing !== null) return setComposing(null)
    setReply(null)
    if (threadId !== null) setCursor(threadId)
    // The list is usually the page before; a thread opened from a link goes to the list.
    if (location.key !== 'default') navigate(-1)
    else go({ threadId: null }, true)
  }

  const syncing = (sync.data ?? []).some((s) => s.state === 'backfill' && (accountId === null || s.account_id === accountId))
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
  else if (view.kind === 'mailbox' && view.mailbox === 'inbox') empty = 'Inbox zero. Nothing left to triage.'
  const missingLabel = view.kind === 'label' && labels.isSuccess && !label

  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return
    if (route.settings) return // the mail shortcuts don't apply there
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
        // While reading, the open thread follows the cursor (without piling up history).
        if (threadId !== null && composing === null) open(id, true)
        else setCursor(id)
        break
      }
      case 'Enter':
      case 'o':
        if (target?.closest('a, button')) return // the focused link or button handles it
        if (cursorId !== null && (cursorId !== threadId || composing !== null)) open(cursorId)
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
      case 'u':
        toggleRead()
        break
      case 'l':
        if (targets.length === 0) return
        setPicking(true)
        break
      case 'x': {
        if (!cursorThread) return
        const ids = new Set(selected)
        if (!ids.delete(cursorThread.id)) ids.add(cursorThread.id)
        select(ids)
        break
      }
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
        if (!assistantRef.current) return
        assistantRef.current.focus()
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
        else if (composing !== null) setComposing(null)
        else if (threadId !== null) {
          setCursor(threadId)
          go({ threadId: null })
        } else if (view.kind === 'search') search('')
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

  // A phone shows one screen at a time; the others stay mounted underneath.
  const screen = route.settings ? 'settings' : threadId !== null || composing !== null ? 'thread' : 'list'
  const showAssistant = ai && composing === null && !draftOnly && (!phone || threadId !== null)

  return (
    <div className={styles.shell} data-screen={phone ? screen : undefined}>
      <Sidebar
        accounts={accounts}
        route={route}
        labels={labels.data ?? []}
        userLabels={named}
        sync={sync.data ?? []}
        notice={phone ? null : notice}
        ai={ai}
        update={!phone && update}
        drawer={phone ? { open: drawerOpen } : undefined}
        onSelectAccount={(id) => go({ accountId: id, threadId: null, settings: false })}
        onCompose={compose}
        onLabelChanged={(name, { renamed, deleted }) => {
          if (view.kind !== 'label' || view.name !== name) return
          if (deleted) go({ view: { kind: 'mailbox', mailbox: 'inbox' }, threadId: null }, true)
          else if (renamed) go({ view: { kind: 'label', name: renamed } }, true)
        }}
      />
      {drawerOpen && <div className={styles.scrim} onClick={() => setDrawer({ open: false, at: location.key })} aria-hidden="true" />}
      {route.settings ? (
        <Settings accounts={accounts} labels={allLabels.data ?? []} sync={sync.data ?? []} ai={ai} onMenu={openDrawer} />
      ) : (
        <>
          <ThreadList
            title={title}
            query={view.kind === 'search' ? view.q : ''}
            rows={missingLabel ? [] : rows}
            status={missingLabel ? 'success' : list.status}
            error={list.error}
            empty={empty}
            onRangeEnd={onRangeEnd}
            cursorId={cursorId}
            openId={composing !== null ? null : threadId}
            selected={selected}
            labelsById={labelsById}
            hideLabel={label?.name}
            hideChips={grouped && ai}
            labelInbox={ai && grouped ? <LabelInbox accountId={accountId} onFinished={say} /> : undefined}
            onDoneAll={doneAll}
            onToggleBundle={(name) => setBundle(openBundle === name ? null : { viewKey, name })}
            threadHref={(id) => href({ ...route, threadId: id })}
            onOpen={open}
            onQuick={(q, thread) => quick(q, [thread])}
            onSearch={search}
            searchRef={searchRef}
            phone={phone}
            onMenu={openDrawer}
          />
          <div className={styles.pane}>
            <Reader
              threadId={threadId}
              myEmails={myEmails}
              accounts={accounts}
              composing={composing}
              reply={reply}
              onCloseCompose={() => setComposing(null)}
              onComposeFinished={(text) => {
                setComposing(null)
                setReply(null)
                say(text)
              }}
              onCloseReply={() => setReply(null)}
              onReply={startReply}
              onCloseThread={() => {
                setReply(null)
                if (threadId !== null) setCursor(threadId)
                go({ threadId: null })
              }}
              targets={targets}
              onQuick={(q) => quick(q)}
              onToggleRead={toggleRead}
              onLabel={() => setPicking(true)}
              picker={picker}
              phone={phone}
              onBack={back}
            />
            {showAssistant && (
              <Assistant
                ref={assistantRef}
                threadId={threadId}
                threadHref={(id) => href({ ...route, threadId: id })}
                onOpenThread={(id) => open(id)}
                onNotice={say}
                phone={phone}
              />
            )}
          </div>
          {phone && screen === 'list' && (
            <button type="button" className={styles.compose} aria-label="Compose" onClick={compose}>
              <Icon name="compose" size={20} strokeWidth={2} />
            </button>
          )}
        </>
      )}
      {phone && (notice || update) && (
        <Toast notice={notice} update={update} place={screen === 'list' ? 'list' : screen === 'thread' && showAssistant ? 'sheet' : 'bottom'} />
      )}
      {help && <ShortcutHelp ai={ai} onClose={() => setHelp(false)} />}
    </div>
  )
}

/** On a phone, notices (Undo, failures) and the update prompt float at the foot of the screen,
 * clear of the Compose button and the assistant's bar. */
function Toast({ notice, update, place }: { notice: Notice | null; update: boolean; place: 'list' | 'sheet' | 'bottom' }) {
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

/** The threads of a list's pages, each once (a thread can move between pages as mail arrives). */
function unique(pages: ThreadPage[] | undefined): ThreadSummary[] {
  const seen = new Set<number>()
  return (pages ?? []).flatMap((page) => page.threads).filter((t) => !seen.has(t.id) && !!seen.add(t.id))
}
