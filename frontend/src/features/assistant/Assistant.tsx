import { useEffect, useEffectEvent, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from 'react'
import {
  ask,
  conversationKey,
  conversationsKey,
  decide,
  deleteConversation,
  summarize,
  summaryKey,
  undoAction,
  useConversation,
  useConversations,
  useSummaryState,
  type AssistantAction,
  type ChatEvent,
  type ChatItem,
  type SummaryState,
} from '../../app/ai'
import { refreshAll } from '../../app/actions'
import { ApiError, queryClient } from '../../app/api'
import { Icon } from '../../components/Icon'
import { Markdown } from './markdown'
import styles from './Assistant.module.css'

// The assistant, docked at the foot of the email pane (never over the list or the email): the
// open thread's summary, then the conversation, then the input. It answers about "this thread"
// (the open one; the chip can be removed) or all mail, and proposes changes the user confirms.
// The conversation is stored on the server; the browser remembers which one is open.

const MINIMIZED_KEY = 'kanso.assistant.minimized'
const CONVERSATION_KEY = 'kanso.assistant.conversation'
// A long thread is summarized after it has been open this long, so stepping through threads
// with j/k doesn't start a summary for each one passed.
const AUTO_DELAY_MS = 400

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null // storage blocked
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // storage blocked: remembered until the page reloads
  }
}

export type AssistantHandle = { focus: () => void }

type Props = {
  threadId: number | null
  threadHref: (id: number) => string
  onOpenThread: (id: number) => void
  /** A notice at the foot of the sidebar, with Undo for a confirmed change. */
  onNotice: (text: string, undo?: () => void) => void
  /** The phone layout: a bottom sheet under the thread, closed until opened. */
  phone: boolean
  ref?: Ref<AssistantHandle>
}

/** A question or decision being answered: the conversation as it was (`base`), what streamed in
 * since, and how it ended. Shown until the stored conversation has caught up. */
type Live = { base: ChatItem[]; items: ChatItem[]; error: string | null; endedAt: number | null }

export function Assistant({ threadId, threadHref, onOpenThread, onNotice, phone, ref }: Props) {
  // On a phone the sheet starts closed with every thread opened from the list (not remembered).
  const [minimized, setMinimized] = useState(() => phone || read(MINIMIZED_KEY) === '1')
  const [conversationId, setConversationId] = useState(() => Number(read(CONVERSATION_KEY)) || null)
  const [history, setHistory] = useState(false)
  // "this thread" is on for each newly opened thread; removing the chip asks about all mail.
  const [scope, setScope] = useState<{ threadId: number | null; all: boolean }>({ threadId, all: false })
  const allMail = threadId === null || (scope.threadId === threadId && scope.all)
  const [draft, setDraft] = useState('')
  const [live, setLive] = useState<Live | null>(null)
  const busy = live !== null && live.endedAt === null
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])
  const inputRef = useRef<HTMLInputElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true) // the conversation is scrolled to its end

  const conversation = useConversation(conversationId, true)
  // A conversation that is gone (deleted elsewhere, or pruned) starts over.
  const gone = conversation.error instanceof ApiError && conversation.error.status === 404
  const current = gone ? null : conversationId
  const stored = (!gone && conversation.data?.items) || []
  const caughtUp = live?.endedAt != null && conversation.dataUpdatedAt > live.endedAt
  const items = live && !caughtUp ? [...live.base, ...live.items] : stored

  const open = (id: number | null) => {
    abort.current?.abort()
    write(CONVERSATION_KEY, id === null ? null : String(id))
    setConversationId(id)
    setLive(null)
    setHistory(false)
    atBottom.current = true
  }
  const restore = () => {
    if (!phone) write(MINIMIZED_KEY, '0')
    setMinimized(false)
  }
  useImperativeHandle(ref, () => ({
    focus: () => {
      restore()
      setHistory(false)
      requestAnimationFrame(() => inputRef.current?.focus())
    },
  }))

  const undo = (id: number, action: AssistantAction) =>
    undoAction(id, action.id)
      .then(() => {
        refreshAll()
        void queryClient.invalidateQueries({ queryKey: conversationKey(id) })
      })
      .catch((error: Error) => onNotice(`Couldn’t undo: ${error.message}`))

  /** Stream an answer into `live`, then let the stored conversation take over. */
  const stream = (start: (onEvent: (event: ChatEvent) => void, signal: AbortSignal) => Promise<void>, first: ChatItem[]) => {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    let id = current
    setLive({ base: stored, items: first, error: null, endedAt: null })
    const update = (change: (live: Live) => Live) => setLive((old) => (old ? change(old) : old))
    const onEvent = (event: ChatEvent) => {
      switch (event.type) {
        case 'conversation':
          id = event.id
          write(CONVERSATION_KEY, String(id))
          setConversationId(id)
          break
        case 'text':
          update((l) => {
            const last = l.items.at(-1)
            if (last?.kind === 'text') return { ...l, items: [...l.items.slice(0, -1), { kind: 'text', text: last.text + event.text }] }
            return { ...l, items: [...l.items, { kind: 'text', text: event.text }] }
          })
          break
        case 'activity':
          update((l) => ({ ...l, items: [...l.items, { kind: 'activity', text: event.text }] }))
          break
        case 'action': {
          const { action } = event
          const swap = (list: ChatItem[]) => list.map((i) => (i.kind === 'action' && i.action.id === action.id ? { kind: 'action' as const, action } : i))
          update((l) => {
            const known = [...l.base, ...l.items].some((i) => i.kind === 'action' && i.action.id === action.id)
            return known ? { ...l, base: swap(l.base), items: swap(l.items) } : { ...l, items: [...l.items, { kind: 'action', action }] }
          })
          if (action.status === 'confirmed' && id !== null) {
            const conversation = id
            refreshAll()
            onNotice(describe(action, true), () => void undo(conversation, action))
          }
          break
        }
        case 'error':
          update((l) => ({ ...l, error: event.message }))
          break
        case 'done':
          break
      }
    }
    start(onEvent, controller.signal)
      .catch((error: Error) => {
        if (!controller.signal.aborted) update((l) => ({ ...l, error: `Claude didn’t answer: ${error.message}` }))
      })
      .finally(() => {
        if (controller.signal.aborted) return
        update((l) => ({ ...l, endedAt: Date.now() }))
        if (id !== null) void queryClient.invalidateQueries({ queryKey: conversationKey(id) })
        void queryClient.invalidateQueries({ queryKey: conversationsKey })
      })
  }

  const send = () => {
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    setHistory(false)
    atBottom.current = true
    const about = allMail ? null : threadId
    stream((onEvent, signal) => ask(current, text, about, onEvent, signal), [{ kind: 'user', text }])
  }
  const onDecide = (action: AssistantAction, confirm: boolean) => {
    if (busy || current === null) return
    const id = current
    stream((onEvent, signal) => decide(id, action.id, confirm, onEvent, signal), [])
  }

  // Keep the newest part of the conversation in view as it grows.
  const last = items.at(-1)
  const size = `${items.length}:${last && last.kind !== 'action' ? last.text.length : ''}:${live?.error ?? ''}`
  // It stays at the bottom unless the reader scrolled up, also when the summary above it appears.
  useLayoutEffect(() => {
    const body = bodyRef.current
    if (body && atBottom.current) body.scrollTop = body.scrollHeight
  }, [size, current, history])
  useEffect(() => {
    const body = bodyRef.current
    if (!body) return
    const observer = new ResizeObserver(() => {
      if (atBottom.current) body.scrollTop = body.scrollHeight
    })
    observer.observe(body)
    return () => observer.disconnect()
  }, [minimized])

  const toggle = () => {
    if (!phone) write(MINIMIZED_KEY, minimized ? '0' : '1')
    setMinimized(!minimized)
  }
  const openThread = (id: number) => onOpenThread(id)

  return (
    <aside className={`${styles.assistant} ${minimized ? styles.minimized : ''} ${phone ? styles.sheet : ''}`} aria-label="Assistant">
      {phone && <button type="button" className={styles.handle} aria-hidden="true" tabIndex={-1} onClick={toggle} />}
      <div className={styles.header}>
        <span className={styles.title}>Assistant</span>
        {threadId !== null && !allMail ? (
          <span className={styles.chip}>
            this thread
            <button type="button" className={styles.chipRemove} aria-label="Ask about all mail instead" title="Ask about all mail instead" onClick={() => setScope({ threadId, all: true })}>
              <Icon name="close" size={11} strokeWidth={2.2} />
            </button>
          </span>
        ) : threadId !== null ? (
          <button type="button" className={styles.chipButton} title="Ask about this thread" onClick={() => setScope({ threadId, all: false })}>
            all mail
          </button>
        ) : (
          <span className={styles.chip}>all mail</span>
        )}
        <span className={styles.spacer} />
        {!minimized && (
          <>
            <button type="button" className={styles.icon} aria-label="New conversation" title="New conversation" onClick={() => {
              open(null)
              requestAnimationFrame(() => inputRef.current?.focus())
            }}>
              <Icon name="plus" size={15} />
            </button>
            <button type="button" className={styles.icon} aria-label="Recent conversations" aria-pressed={history} title="Recent conversations" onClick={() => setHistory(!history)}>
              <Icon name="history" size={15} />
            </button>
          </>
        )}
        <button
          type="button"
          className={styles.icon}
          aria-label={minimized ? 'Restore assistant' : 'Minimize assistant'}
          aria-expanded={!minimized}
          title={minimized ? 'Restore (q)' : 'Minimize'}
          onClick={toggle}
        >
          <Icon name={minimized ? 'chevronUp' : 'chevronDown'} size={14} strokeWidth={2} />
        </button>
      </div>
      {!minimized && (
        <>
          {!history && threadId !== null && (
            <div className={styles.summary}>
              <SummaryCard key={threadId} threadId={threadId} />
            </div>
          )}
          <div
            className={styles.body}
            ref={bodyRef}
            onScroll={(event) => {
              const body = event.currentTarget
              atBottom.current = body.scrollHeight - body.scrollTop - body.clientHeight < 24
            }}
          >
            {history ? (
              <History current={current} onOpen={open} />
            ) : (
              <>
                {items.map((item, i) => (
                  <Item
                    key={item.kind === 'action' ? item.action.id : i}
                    item={item}
                    busy={busy}
                    threadHref={threadHref}
                    onOpenThread={openThread}
                    onDecide={onDecide}
                    onUndo={(action) => current !== null && void undo(current, action)}
                  />
                ))}
                {busy && last?.kind !== 'text' && <p className={styles.activity}>Thinking…</p>}
                {live?.error && <p className={styles.error}>{live.error}</p>}
              </>
            )}
          </div>
          <form
            className={styles.ask}
            onSubmit={(event) => {
              event.preventDefault()
              send()
            }}
          >
            <input
              ref={inputRef}
              type="text"
              value={draft}
              maxLength={4000}
              placeholder={threadId === null || allMail ? 'Ask about all mail' : 'Ask about this thread or all mail'}
              aria-label="Ask the assistant"
              title="Ask the assistant (q)"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') event.currentTarget.blur()
              }}
            />
            <button type="submit" className={styles.send} aria-label="Send" title="Send (Enter)" disabled={busy || !draft.trim()}>
              <Icon name="send" size={13} strokeWidth={2.4} />
            </button>
          </form>
        </>
      )}
    </aside>
  )
}

type ItemProps = {
  item: ChatItem
  busy: boolean
  threadHref: (id: number) => string
  onOpenThread: (id: number) => void
  onDecide: (action: AssistantAction, confirm: boolean) => void
  onUndo: (action: AssistantAction) => void
}

function Item({ item, busy, threadHref, onOpenThread, onDecide, onUndo }: ItemProps) {
  switch (item.kind) {
    case 'user':
      return <p className={styles.question}>{item.text}</p>
    case 'activity':
      return <p className={styles.activity}>{item.text}</p>
    case 'text':
      return (
        <div className={styles.answer}>
          <Markdown text={item.text} threadHref={threadHref} onOpenThread={onOpenThread} />
        </div>
      )
    case 'action':
      return <Proposal action={item.action} busy={busy} threadHref={threadHref} onOpenThread={onOpenThread} onDecide={onDecide} onUndo={onUndo} />
  }
}

const count = (n: number) => (n === 1 ? '1 thread' : `${n} threads`)

/** "Archive 2 threads?" while it waits; "Archived 2 threads." once confirmed. */
function describe(action: AssistantAction, done: boolean): string {
  const n = count(action.threads.length)
  switch (action.tool) {
    case 'archive':
      return done ? `Archived ${n}.` : `Archive ${n}?`
    case 'apply_label':
      return done ? `Labeled ${n} “${action.label}”.` : `Add the label “${action.label}” to ${n}?`
    case 'star':
      return done ? `Starred ${n}.` : `Star ${n}?`
    case 'mark_read':
      return done ? `Marked ${n} as read.` : `Mark ${n} as read?`
  }
}

type ProposalProps = Omit<ItemProps, 'item'> & { action: AssistantAction }

function Proposal({ action, busy, threadHref, onOpenThread, onDecide, onUndo }: ProposalProps) {
  let footer
  // Made without asking (confirmation is off in Settings).
  const auto = action.auto && action.status !== 'pending'
  switch (action.status) {
    case 'pending':
      footer = (
        <div className={styles.decide}>
          <button type="button" className={styles.confirm} disabled={busy} onClick={() => onDecide(action, true)}>
            Confirm
          </button>
          <button type="button" className={styles.cancel} disabled={busy} onClick={() => onDecide(action, false)}>
            Cancel
          </button>
        </div>
      )
      break
    case 'confirmed':
      footer = (
        <p className={styles.outcome}>
          {auto ? 'Done without asking.' : describe(action, true)}{' '}
          <button type="button" className={styles.link} onClick={() => onUndo(action)}>
            Undo
          </button>
        </p>
      )
      break
    case 'undone':
      footer = <p className={styles.outcome}>Undone: nothing changed.</p>
      break
    case 'declined':
      footer = <p className={styles.outcome}>Cancelled: nothing changed.</p>
      break
    case 'skipped':
      footer = <p className={styles.outcome}>Not confirmed: nothing changed.</p>
      break
    case 'failed':
      footer = <p className={styles.outcome}>Couldn’t do it: {action.error}</p>
      break
  }
  return (
    <div className={`${styles.proposal} ${action.status === 'pending' ? styles.waiting : ''}`} role="group" aria-label="Proposed change">
      <p className={styles.proposalTitle}>{describe(action, auto)}</p>
      <ul className={styles.targets}>
        {action.threads.map((t) => (
          <li key={t.id}>
            <a
              href={threadHref(t.id)}
              onClick={(event) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
                event.preventDefault()
                onOpenThread(t.id)
              }}
            >
              {t.subject || '(no subject)'}
            </a>
          </li>
        ))}
      </ul>
      {footer}
    </div>
  )
}

function History({ current, onOpen }: { current: number | null; onOpen: (id: number | null) => void }) {
  const list = useConversations(true)
  const remove = (id: number) =>
    deleteConversation(id).then(() => {
      if (id === current) onOpen(null)
      void queryClient.invalidateQueries({ queryKey: conversationsKey })
    })
  if (list.isError) return <p className={styles.error}>Couldn’t load your conversations: {list.error.message}</p>
  if (!list.data) return null
  if (list.data.length === 0) return <p className={styles.activity}>No conversations yet.</p>
  return (
    <ul className={styles.history} aria-label="Recent conversations">
      {list.data.map((c) => (
        <li key={c.id} className={c.id === current ? styles.currentRow : undefined}>
          <button type="button" className={styles.historyOpen} onClick={() => onOpen(c.id)}>
            <span className={styles.historyTitle}>{c.title}</span>
            {c.pending && <span className={styles.waitingTag}>waiting for you</span>}
            <span className={styles.historyTime}>{when(c.updated_at)}</span>
          </button>
          <button type="button" className={styles.icon} aria-label={`Delete “${c.title}”`} title="Delete conversation" onClick={() => void remove(c.id)}>
            <Icon name="delete" size={14} />
          </button>
        </li>
      ))}
    </ul>
  )
}

function when(iso: string): string {
  const date = new Date(iso)
  const today = new Date().toDateString() === date.toDateString()
  return today ? date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** A summary being written: its text so far, or why it failed. */
type Run = { text: string; error: string | null }

/** The open thread's summary (Phase 7). Mounted only while the panel is open, so a minimized
 * panel never starts one. */
function SummaryCard({ threadId }: { threadId: number }) {
  const state = useSummaryState(threadId)
  const [run, setRun] = useState<Run | null>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const start = () => {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setRun({ text: '', error: null })
    summarize(threadId, (text) => setRun({ text, error: null }), controller.signal).then(
      (summary) => {
        queryClient.setQueryData<SummaryState>(summaryKey(threadId), (old) => old && { ...old, summary })
        setRun(null)
      },
      (error: Error) => {
        if (!controller.signal.aborted) setRun({ text: '', error: error.message })
      },
    )
  }
  const startLater = useEffectEvent(start)

  const data = state.data
  // A long thread without a summary of its latest message gets one, once per open (a failure
  // waits for "Try again").
  const auto = !!data?.long && !data.summary && run === null
  useEffect(() => {
    if (!auto) return
    const timer = setTimeout(startLater, AUTO_DELAY_MS)
    return () => clearTimeout(timer)
  }, [auto])

  // Nothing to summarize (a thread of drafts only), or the thread is gone.
  if (state.isError || !data || data.messages === 0) return null
  const lang = data.lang ?? undefined
  if (run?.error)
    return (
      <p className={styles.card}>
        <b>Summary</b> · <span className={styles.muted}>Couldn’t summarize this thread: {run.error}.</span>{' '}
        <button type="button" className={styles.link} onClick={start}>
          Try again
        </button>
      </p>
    )
  if (run)
    return (
      <p className={styles.card} lang={lang} aria-busy="true">
        <b>Summary</b> · {run.text || <span className={styles.muted}>Summarizing…</span>}
      </p>
    )
  if (data.summary)
    return (
      <p className={styles.card} lang={lang}>
        <b>Summary</b> · {data.summary.text}
      </p>
    )
  // Short threads are summarized on request; long ones are on their way.
  if (data.long) return null
  return (
    <button type="button" className={styles.offer} onClick={start} title="Summarize this thread with Claude">
      Summarize this thread
    </button>
  )
}
