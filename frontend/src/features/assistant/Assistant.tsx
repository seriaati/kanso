import { useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from 'react'
import {
  ASK_FILES,
  ask,
  conversationKey,
  conversationsKey,
  decide,
  deleteConversation,
  undoAction,
  useConversation,
  useConversations,
  readFile,
  type AssistantAction,
  type ChatEvent,
  type ChatItem,
} from '../../app/ai'
import { refreshAll } from '../../app/actions'
import { ApiError, queryClient } from '../../app/api'
import { Icon } from '../../components/Icon'
import { Markdown } from './markdown'
import styles from './Assistant.module.css'

const CONVERSATION_KEY = 'kanso.assistant.conversation'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
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
  /** Its conversations are the account's: App remounts it (key) when the account changes. */
  accountId: number
  /** The open thread, if any: questions go with its content. */
  threadId: number | null
  threadHref: (id: number) => string
  onOpenThread: (id: number) => void
  onNotice: (text: string, undo?: () => void) => void
  /** A screen of its own; otherwise a panel beside the mail. */
  phone: boolean
  onClose: () => void
  ref?: Ref<AssistantHandle>
}

// Shown until the stored conversation has caught up.
type Live = { base: ChatItem[]; items: ChatItem[]; error: string | null; endedAt: number | null }

export function Assistant({ accountId, threadId, threadHref, onOpenThread, onNotice, phone, onClose, ref }: Props) {
  const storageKey = `${CONVERSATION_KEY}.${accountId}`
  const [conversationId, setConversationId] = useState(() => Number(read(storageKey)) || null)
  const [history, setHistory] = useState(false)
  const [files, setFiles] = useState<File[]>([]) // attached to the next question
  const fileRef = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState('')
  const [live, setLive] = useState<Live | null>(null)
  const busy = live !== null && live.endedAt === null
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])
  const inputRef = useRef<HTMLInputElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)

  const conversation = useConversation(conversationId, true)
  // Deleted elsewhere or pruned: start over.
  const gone = conversation.error instanceof ApiError && conversation.error.status === 404
  const current = gone ? null : conversationId
  const stored = (!gone && conversation.data?.items) || []
  const caughtUp = live?.endedAt != null && conversation.dataUpdatedAt > live.endedAt
  const items = live && !caughtUp ? [...live.base, ...live.items] : stored

  const open = (id: number | null) => {
    abort.current?.abort()
    write(storageKey, id === null ? null : String(id))
    setConversationId(id)
    setLive(null)
    setHistory(false)
    atBottom.current = true
  }
  useEffect(() => inputRef.current?.focus(), [])
  useImperativeHandle(ref, () => ({
    focus: () => {
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
          write(storageKey, String(id))
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
        void queryClient.invalidateQueries({ queryKey: conversationsKey(accountId) })
      })
  }

  const attach = (added: File[]) => {
    const all = [...files, ...added]
    if (all.length > ASK_FILES) onNotice(`Attach up to ${ASK_FILES} files to a question.`)
    setFiles(all.slice(0, ASK_FILES))
  }
  const send = () => {
    const text = draft.trim()
    if ((!text && !files.length) || busy) return
    const attached = files
    setDraft('')
    setFiles([])
    setHistory(false)
    atBottom.current = true
    const names = attached.map((f) => f.name)
    stream(
      async (onEvent, signal) => ask(accountId, current, text, threadId, await Promise.all(attached.map(readFile)), onEvent, signal),
      [{ kind: 'user', text, files: names }],
    )
  }
  const onDecide = (action: AssistantAction, confirm: boolean) => {
    if (busy || current === null) return
    const id = current
    stream((onEvent, signal) => decide(id, action.id, confirm, onEvent, signal), [])
  }

  const last = items.at(-1)
  const size = `${items.length}:${last && last.kind !== 'action' ? last.text.length : ''}:${live?.error ?? ''}`
  // Stays at the bottom unless the reader scrolled up.
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
  }, [])

  const openThread = (id: number) => onOpenThread(id)

  return (
    <aside
      className={`${styles.assistant} ${phone ? styles.sheet : styles.panel}`}
      aria-label="Assistant"
    >
      <div className={styles.header}>
        <span className={styles.title}>Assistant</span>
        <span className={styles.spacer} />
        <button type="button" className={styles.icon} aria-label="New conversation" title="New conversation" onClick={() => {
          open(null)
          requestAnimationFrame(() => inputRef.current?.focus())
        }}>
          <Icon name="plus" size={15} />
        </button>
        <button type="button" className={styles.icon} aria-label="Recent conversations" aria-pressed={history} title="Recent conversations" onClick={() => setHistory(!history)}>
          <Icon name="history" size={15} />
        </button>
        <button type="button" className={styles.icon} aria-label="Close assistant" title="Close the assistant" onClick={onClose}>
          <Icon name="close" size={14} strokeWidth={2} />
        </button>
      </div>
      <div
        className={styles.body}
        ref={bodyRef}
        onScroll={(event) => {
          const body = event.currentTarget
          atBottom.current = body.scrollHeight - body.scrollTop - body.clientHeight < 24
        }}
      >
        {history ? (
          <History accountId={accountId} current={current} onOpen={open} />
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
        {files.length > 0 && (
          <ul className={styles.files} aria-label="Attached files">
            {files.map((file, i) => (
              <li key={`${file.name}-${i}`} className={styles.file}>
                <span className={styles.fileName}>{file.name}</span>
                <button
                  type="button"
                  className={styles.chipRemove}
                  aria-label={`Remove ${file.name}`}
                  title="Remove"
                  onClick={() => setFiles(files.filter((_, j) => j !== i))}
                >
                  <Icon name="close" size={11} strokeWidth={2.2} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className={styles.askRow}>
          <button
            type="button"
            className={styles.icon}
            aria-label="Attach files"
            title="Attach images, PDFs or text files (or paste them)"
            onClick={() => fileRef.current?.click()}
          >
            <Icon name="attach" size={15} />
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,text/*,.md,.csv,.json,.yaml,.yml,.log"
            onChange={(event) => {
              const chosen = Array.from(event.target.files ?? [])
              event.target.value = ''
              attach(chosen)
            }}
          />
          <input
            ref={inputRef}
            type="text"
            value={draft}
            maxLength={4000}
            aria-label="Ask the assistant"
            title="Ask the assistant (q)"
            onChange={(event) => setDraft(event.target.value)}
            onPaste={(event) => {
              const pasted = Array.from(event.clipboardData.files)
              if (!pasted.length) return
              event.preventDefault()
              attach(pasted)
            }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') event.currentTarget.blur()
            }}
          />
          <button type="submit" className={styles.send} aria-label="Send" title="Send (Enter)" disabled={busy || (!draft.trim() && !files.length)}>
            <Icon name="send" size={13} strokeWidth={2.4} />
          </button>
        </div>
      </form>
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
      return (
        <div className={styles.question}>
          {item.files && item.files.length > 0 && (
            <ul className={styles.sentFiles} aria-label="Attached files">
              {item.files.map((name, i) => (
                <li key={i}>
                  <Icon name="attach" size={12} />
                  {name}
                </li>
              ))}
            </ul>
          )}
          {item.text}
        </div>
      )
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
  // Made without asking: confirmation is off in Settings.
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

function History({ accountId, current, onOpen }: { accountId: number; current: number | null; onOpen: (id: number | null) => void }) {
  const list = useConversations(accountId)
  const remove = (id: number) =>
    deleteConversation(id).then(() => {
      if (id === current) onOpen(null)
      void queryClient.invalidateQueries({ queryKey: conversationsKey(accountId) })
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
