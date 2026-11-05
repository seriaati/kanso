import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useEffect, useEffectEvent } from 'react'
import type { IconName } from '../components/Icon'
import { api, queryClient } from './api'
import { letterAvatar } from './format'
import { preloadGlyphs } from './fonts'
import type { View } from './nav'

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
  in_spam: boolean
  has_attachments: boolean
  lang: string | null
  /** Which of `senders` are the user's own, shown as "me". */
  me: string[]
  /** Sent and Drafts only: shown instead of the senders. */
  recipients: string[] | null
  /** The newest sender who isn't the user, and their photo or their domain's logo. */
  sender_name: string | null
  sender_photo: string | null
  /** The one-time code of a recent verification mail, offered for copying. */
  code: string | null
}

export type BundleSender = { name: string; photo: string | null }
/** A label's inbox threads, collapsed into one row of the inbox. */
export type Bundle = { name: string; count: number; newest_at: string; senders: BundleSender[] }

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
  sender_photo: string | null // as in ThreadSummary
  label_ids: number[]
  unread: boolean
  draft: boolean
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
  icon: string | null
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

export type ThreadPage = { threads: ThreadSummary[]; next_cursor: string | null; bundles?: Bundle[] | null }

// accountId is null only while no account is connected.
export function useLabels(accountId: number | null) {
  return useQuery({
    queryKey: ['labels', accountId],
    queryFn: () => api<Label[]>(`/labels?account_id=${accountId}`),
    enabled: accountId !== null,
  })
}

/** Refresh: reads the lists again from the mirror, and asks the worker to sync the account with
 * Gmail now (what that finds arrives as live updates). Settles when the lists have reloaded. */
export function refreshMail(accountId: number | null): Promise<unknown> {
  if (accountId !== null) void api(`/sync/${accountId}`, { method: 'POST' }).catch(() => undefined)
  return Promise.all(['threads', 'labels', 'accounts'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
}

export function useSync() {
  return useQuery({ queryKey: ['sync'], queryFn: () => api<SyncStatus[]>('/sync') })
}

// Also the list's query key.
export function viewParams(view: View, accountId: number | null, labelIds: number[] | undefined) {
  const params = new URLSearchParams({ limit: '50' })
  if (accountId !== null) params.set('account_id', String(accountId))
  if (view.kind === 'mailbox') {
    params.set('mailbox', view.mailbox)
    if (view.mailbox === 'inbox') params.set('grouped', 'true')
  } else if (view.kind === 'search') params.set('q', view.q)
  else for (const id of labelIds ?? []) params.append('label_id', String(id))
  return params
}

// A page shows once the fonts for its rows are in (see preloadGlyphs).
async function threadPage(params: URLSearchParams, cursor: string) {
  const page = await api<ThreadPage>(`/threads?${params}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
  // The avatars draw the initials in bold, without the thread's language.
  const initials = (names: string[]) => ({ text: names.map((name) => letterAvatar(name).letter).join(''), bold: true })
  await preloadGlyphs([
    ...page.threads.flatMap((t) => {
      const people = [...t.senders, ...(t.recipients ?? [])]
      return [
        { text: [...people, t.subject].join(''), lang: t.lang, bold: t.unread },
        { text: t.snippet, lang: t.lang },
        initials(people),
      ]
    }),
    ...(page.bundles ?? []).flatMap((b) => {
      const names = b.senders.map((sender) => sender.name)
      return [{ text: b.name, bold: true }, { text: names.join('') }, initials(names)]
    }),
  ])
  return page
}

export function useThreads(view: View, accountId: number | null, labelIds: number[] | undefined) {
  const params = viewParams(view, accountId, labelIds)
  const enabled = accountId !== null && (view.kind !== 'label' || !!labelIds?.length)
  return useInfiniteQuery({
    queryKey: ['threads', params.toString()],
    queryFn: ({ pageParam }) => threadPage(params, pageParam),
    initialPageParam: '',
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled,
  })
}

export function useBundleThreads(bundle: string | null, accountId: number | null) {
  const params = new URLSearchParams({ mailbox: 'inbox', bundle: bundle ?? '', limit: '50' })
  if (accountId !== null) params.set('account_id', String(accountId))
  return useInfiniteQuery({
    queryKey: ['threads', params.toString()],
    queryFn: ({ pageParam }) => threadPage(params, pageParam),
    initialPageParam: '',
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: bundle !== null && accountId !== null,
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

// Events are batched, and held back while an action is in flight (a refetch could briefly undo its
// optimistic change). After a reconnect everything refetches, since events may have been missed.
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
      refresh('threads', 'labels', 'accounts')
      for (const id of changed) void queryClient.invalidateQueries({ queryKey: ['thread', id] })
      changed.clear()
    }

    const connect = () => {
      source = new EventSource('/api/events')
      source.onopen = () => {
        if (connected) refresh('threads', 'thread', 'labels', 'accounts', 'sync')
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
      // The browser reconnects by itself after a network drop, but gives up on an HTTP error (e.g. 401).
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

// A user label of the account, as the sidebar and pickers show it.
export type NamedLabel = {
  id: number
  name: string
  color: string
  unread: number
  // kanso's own
  icon: LabelIcon | null
  description: string
}

export function namedLabel(label: Label): NamedLabel {
  return {
    id: label.id,
    name: label.name,
    color: labelColor(label.color),
    unread: label.threads_unread,
    icon: isLabelIcon(label.icon) ? label.icon : null,
    description: label.description,
  }
}

export function userLabels(labels: Label[]): NamedLabel[] {
  return labels
    .filter((l) => l.type === 'user')
    .map(namedLabel)
    .sort((a, b) => a.name.localeCompare(b.name))
}

// A Gmail label color outside this palette is drawn in the nearest one, so Gmail's bright
// palette doesn't break the calm one.
export const labelColors = [
  { name: 'Ochre', value: '#A07338' },
  { name: 'Slate', value: '#4F6E8C' },
  { name: 'Stone', value: '#6B6B66' },
  { name: 'Moss', value: '#556B3F' },
  { name: 'Plum', value: '#87607E' },
  { name: 'Rust', value: '#A35A3A' },
  { name: 'Sand', value: '#B8B3A2' },
]
// A label's optional icon, drawn in its color; without one it is a colored dot.
export const labelIcons = [
  'home',
  'briefcase',
  'receipt',
  'cart',
  'plane',
  'heart',
  'book',
  'people',
  'bell',
  'bolt',
  'gift',
  'calendar',
  'star',
  'code',
  'image',
  'link',
  'newspaper',
  'tag',
  'package',
  'shield',
  'bank',
  'chat',
  'school',
  'food',
  'fitness',
  'music',
  'film',
  'game',
  'car',
  'pin',
  'globe',
  'key',
  'megaphone',
  'ticket',
  'paw',
  'leaf',
] as const satisfies readonly IconName[]
export type LabelIcon = (typeof labelIcons)[number]
const isLabelIcon = (name: string | null): name is LabelIcon => (labelIcons as readonly string[]).includes(name ?? '')

const palette = labelColors.map((c) => c.value)
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))

export function labelColor(color: string | null): string {
  if (!color || !/^#[0-9a-f]{6}$/i.test(color)) return '#B8B3A2'
  const target = rgb(color)
  const distance = (hex: string) => rgb(hex).reduce((sum, value, i) => sum + (value - target[i]) ** 2, 0)
  return palette.reduce((best, hex) => (distance(hex) < distance(best) ? hex : best))
}
