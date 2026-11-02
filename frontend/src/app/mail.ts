import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useEffect, useEffectEvent } from 'react'
import { api, queryClient } from './api'
import type { View } from './nav'

// Server state for reading mail. Shapes follow the backend's read API (kanso/api/*.py).

export type ThreadSummary = {
  id: number
  account_id: number
  subject: string
  snippet: string
  senders: string[]
  last_message_at: string
  message_count: number
  label_ids: number[]
  unread: boolean
  starred: boolean
  important: boolean
  in_inbox: boolean
  in_trash: boolean
  has_attachments: boolean
  lang: string | null
  /** In the grouped inbox: the label section the thread is placed in (null: unlabeled). */
  section?: string | null
  /** The names in `senders` that are the user's own addresses: shown as "me". */
  me: string[]
  /** Sent and Drafts: who the user wrote to, shown instead of the senders. */
  recipients: string[] | null
}

/** A section of the grouped inbox, or a bundle (its threads load when it is opened). */
export type Section = { name: string | null; bundle: boolean; count: number; newest_at: string; senders: string[] }

export type Address = { name: string; email: string }

export type Attachment = {
  id: number
  filename: string
  mime_type: string
  size: number
  content_id: string | null
  inline: boolean
}

export type Message = {
  id: number
  date: string
  subject: string
  snippet: string
  sender: Address
  to: Address[]
  cc: Address[]
  bcc: Address[]
  reply_to: Address[]
  label_ids: number[]
  unread: boolean
  draft: boolean // being written: the reader opens it in the composer
  lang: string | null
  body_html: string | null // sanitized server-side; cid: URLs point at attachments
  body_text: string | null // only when there is no HTML
  attachments: Attachment[]
}

export type ThreadDetail = ThreadSummary & { messages: Message[] }

export type Label = {
  id: number
  account_id: number
  gmail_id: string
  name: string
  type: 'system' | 'user'
  color: string | null
  description: string
  ai_managed: boolean
  is_bundle: boolean
  threads_total: number
  threads_unread: number
  messages_total: number
}

export type SyncStatus = {
  account_id: number
  email: string
  state: 'backfill' | 'ready' | 'error' | 'demo'
  error: string | null
  backfill_done: number
  backfill_total: number | null
  last_synced_at: string | null
  push: boolean
}

export type ThreadPage = { threads: ThreadSummary[]; next_cursor: string | null; sections?: Section[] | null }

export function useLabels(accountId: number | null, enabled = true) {
  return useQuery({
    queryKey: ['labels', accountId],
    queryFn: () => api<Label[]>(accountId === null ? '/labels' : `/labels?account_id=${accountId}`),
    enabled,
  })
}

export function useSync() {
  return useQuery({ queryKey: ['sync'], queryFn: () => api<SyncStatus[]>('/sync') })
}

/** The thread list API's parameters for a view; also the list's query key. */
export function viewParams(view: View, accountId: number | null, labelIds: number[] | undefined) {
  const params = new URLSearchParams({ limit: '50' })
  if (accountId !== null) params.set('account_id', String(accountId))
  if (view.kind === 'mailbox') {
    params.set('mailbox', view.mailbox)
    // The inbox is grouped: by label section with AI on, and bundles apart either way.
    if (view.mailbox === 'inbox') params.set('grouped', 'true')
  } else if (view.kind === 'search') params.set('q', view.q)
  else for (const id of labelIds ?? []) params.append('label_id', String(id))
  return params
}

/** The threads of a view, newest first, a page at a time. A label view needs the ids of the
 * labels with that name (one per account); with none it stays disabled. */
export function useThreads(view: View, accountId: number | null, labelIds: number[] | undefined) {
  const params = viewParams(view, accountId, labelIds)
  const enabled = view.kind !== 'label' || !!labelIds?.length
  return useInfiniteQuery({
    queryKey: ['threads', params.toString()],
    queryFn: ({ pageParam }) => api<ThreadPage>(`/threads?${params}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`),
    initialPageParam: '',
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled,
  })
}

/** The threads of one bundle of the inbox, loaded when it is opened. */
export function useBundleThreads(bundle: string | null, accountId: number | null) {
  const params = new URLSearchParams({ mailbox: 'inbox', bundle: bundle ?? '', limit: '50' })
  if (accountId !== null) params.set('account_id', String(accountId))
  return useInfiniteQuery({
    queryKey: ['threads', params.toString()],
    queryFn: ({ pageParam }) => api<ThreadPage>(`/threads?${params}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ''}`),
    initialPageParam: '',
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: bundle !== null,
  })
}

export function useThread(id: number | null) {
  return useQuery({
    queryKey: ['thread', id],
    queryFn: () => api<ThreadDetail>(`/threads/${id}`),
    enabled: id !== null,
  })
}

type Event =
  | { type: 'threads_changed'; account_id: number; thread_ids: number[] }
  | { type: 'sync'; account_id: number }
  | { type: 'outbox_failed'; account_id: number; action: string }
  | { type: 'send_failed'; message: string }

/** Live updates from the server-sent event stream: changed threads refetch whatever shows them.
 * A backfill sends many events, so they are batched, and held back while an action is in flight
 * (a refetch then could briefly undo its optimistic change). After a dropped connection
 * everything is refetched, since events may have been missed meanwhile. `onWriteFailed` hears
 * about a change Gmail never accepted (the worker gave up on it); `onSendFailed` about a send
 * the worker finished after a crash, which Gmail then refused. */
export function useLiveUpdates(onWriteFailed: (action: string) => void, onSendFailed: (message: string) => void) {
  const onFailed = useEffectEvent(onWriteFailed)
  const onNotSent = useEffectEvent(onSendFailed)

  useEffect(() => {
    let source: EventSource
    let connected = false
    let retry: ReturnType<typeof setTimeout> | undefined
    let flush: ReturnType<typeof setTimeout> | undefined
    const changed = new Set<number>()

    const refresh = (...keys: string[]) => {
      for (const key of keys) void queryClient.invalidateQueries({ queryKey: [key] })
    }
    const applyChanges = () => {
      if (queryClient.isMutating()) {
        flush = setTimeout(applyChanges, 300)
        return
      }
      flush = undefined
      refresh('threads', 'labels')
      for (const id of changed) void queryClient.invalidateQueries({ queryKey: ['thread', id] })
      changed.clear()
    }

    const connect = () => {
      source = new EventSource('/api/events')
      source.onopen = () => {
        if (connected) refresh('threads', 'thread', 'labels', 'sync')
        connected = true
      }
      source.onmessage = (message: MessageEvent<string>) => {
        const event = JSON.parse(message.data) as Event
        if (event.type === 'threads_changed') {
          for (const id of event.thread_ids) changed.add(id)
          flush ??= setTimeout(applyChanges, 300)
        } else if (event.type === 'sync') refresh('sync')
        else if (event.type === 'outbox_failed') onFailed(event.action)
        else if (event.type === 'send_failed') onNotSent(event.message)
      }
      // The browser reconnects by itself after a network drop. It gives up on an HTTP error
      // (e.g. 401 when the session expired): check the session, then try again a bit later.
      source.onerror = () => {
        if (source.readyState !== EventSource.CLOSED) return
        refresh('accounts')
        retry = setTimeout(connect, 5000)
      }
    }

    connect()
    return () => {
      source.close()
      clearTimeout(retry)
      clearTimeout(flush)
    }
  }, [])
}

/** A user label as the sidebar shows it: the unified view merges same-named labels across
 * accounts. System labels (INBOX, UNREAD…) are the mailboxes, or not shown at all. */
export type NamedLabel = {
  name: string
  color: string
  ids: number[]
  accountIds: number[]
  unread: number
  // kanso's own settings, the same for every account (the editor changes them together)
  description: string
  aiManaged: boolean
  isBundle: boolean
}

export function userLabels(labels: Label[]): NamedLabel[] {
  const byName = new Map<string, NamedLabel>()
  for (const label of labels) {
    if (label.type !== 'user') continue
    const named = byName.get(label.name)
    if (named) {
      named.ids.push(label.id)
      named.accountIds.push(label.account_id)
      named.unread += label.threads_unread
    } else {
      byName.set(label.name, {
        name: label.name,
        color: labelColor(label.color),
        ids: [label.id],
        accountIds: [label.account_id],
        unread: label.threads_unread,
        description: label.description,
        aiManaged: label.ai_managed,
        isBundle: label.is_bundle,
      })
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name))
}

// The design's label colors, which a label can be given (the backend writes the nearest Gmail
// palette color, which maps back here). A Gmail label color outside them is drawn in the nearest
// one, so Gmail's bright palette doesn't break the calm one; an uncolored label is neutral.
export const labelColors = [
  { name: 'Ochre', value: '#A07338' },
  { name: 'Slate', value: '#4F6E8C' },
  { name: 'Stone', value: '#6B6B66' },
  { name: 'Moss', value: '#556B3F' },
  { name: 'Plum', value: '#87607E' },
  { name: 'Rust', value: '#A35A3A' },
  { name: 'Sand', value: '#B8B3A2' },
]
const palette = labelColors.map((c) => c.value)
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))

export function labelColor(color: string | null): string {
  if (!color || !/^#[0-9a-f]{6}$/i.test(color)) return '#B8B3A2'
  const target = rgb(color)
  const distance = (hex: string) => rgb(hex).reduce((sum, value, i) => sum + (value - target[i]) ** 2, 0)
  return palette.reduce((best, hex) => (distance(hex) < distance(best) ? hex : best))
}
