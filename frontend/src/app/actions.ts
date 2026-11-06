import { useMutation, type InfiniteData, type QueryKey } from '@tanstack/react-query'
import { api, queryClient } from './api'
import type { IconName } from '../components/Icon'
import type { Bundle, ThreadDetail, ThreadPage, ThreadSummary } from './mail'

// Optimistic triage actions, rolled back if the request fails.

export type ThreadAction =
  | 'done'
  | 'inbox'
  | 'delete'
  | 'untrash'
  | 'star'
  | 'unstar'
  | 'important'
  | 'unimportant'
  | 'read'
  | 'unread'
  | 'label'
  | 'unlabel'
  | 'spam'
  | 'unspam'

export type ActionInput = {
  action: ThreadAction
  threads: ThreadSummary[]
  label?: { name: string; id: number }
  /** A whole bundle ("Done all"): the server finds its threads; `threads` are only the loaded ones. */
  bundle?: BundleScope
  /** A whole label (its page's "Done all"), likewise. */
  inLabel?: LabelScope
}

export type BundleScope = { name: string; account_id: number; until: string }
export type LabelScope = { id: number; until: string }

export function applied(thread: ThreadSummary, { action, label }: Omit<ActionInput, 'threads'>): ThreadSummary {
  switch (action) {
    case 'done':
    case 'inbox':
      return { ...thread, in_inbox: action === 'inbox' }
    case 'delete':
      return { ...thread, in_trash: true }
    case 'untrash':
      return { ...thread, in_trash: false, in_inbox: true }
    case 'star':
    case 'unstar':
      return { ...thread, starred: action === 'star' }
    case 'important':
    case 'unimportant':
      return { ...thread, important: action === 'important' }
    case 'read':
    case 'unread':
      return { ...thread, unread: action === 'unread' }
    case 'spam':
    case 'unspam':
      return { ...thread, in_spam: action === 'spam', in_inbox: action === 'unspam' }
    case 'label':
    case 'unlabel': {
      if (!label) return thread
      const { id } = label
      const others = thread.label_ids.filter((l) => l !== id)
      return { ...thread, label_ids: action === 'label' ? [...others, id] : others }
    }
  }
}

// Search results can't be judged here, so only Trash and Spam leave them. `labeled`: the thread
// was just given a label, so it leaves the inbox's unlabeled threads for that label's bundle.
export function inList(params: URLSearchParams, thread: ThreadSummary, labeled = false): boolean {
  const mailbox = params.get('mailbox')
  if (mailbox === 'trash') return thread.in_trash
  if (thread.in_trash) return false
  if (mailbox === 'spam') return thread.in_spam
  if (thread.in_spam) return false
  if (mailbox === 'inbox') return thread.in_inbox && !(labeled && params.has('grouped'))
  if (mailbox === 'starred') return thread.starred
  if (mailbox === 'done') return !thread.in_inbox
  const labelIds = params.getAll('label_id').map(Number)
  if (labelIds.length) return thread.label_ids.some((id) => labelIds.includes(id))
  return true
}


export type Quick = 'done' | 'delete' | 'star' | 'important' | 'spam'

const inTrash = (threads: ThreadSummary[]) => threads.length > 0 && threads.every((t) => t.in_trash)

/** In Trash, Done would only repeat Restore (which moves threads to the inbox), so it isn't shown. */
/** What a bundle row, or a label's page, can do to all its threads. */
export type BundleAction = 'done' | 'read' | 'delete'

export const bundleActions: { action: BundleAction; icon: IconName; label: string }[] = [
  { action: 'done', icon: 'done', label: 'Done all' },
  { action: 'delete', icon: 'delete', label: 'Delete all' },
  { action: 'read', icon: 'mailOpen', label: 'Mark all read' },
]
export const redundant = (quick: Quick, threads: ThreadSummary[]) => quick === 'done' && inTrash(threads)

export function quickAction(quick: Quick, threads: ThreadSummary[]): { icon: IconName; title: string; pressed?: boolean } {
  const all = (test: (t: ThreadSummary) => boolean) => threads.length > 0 && threads.every(test)
  if ((quick === 'done' || quick === 'delete') && inTrash(threads)) return { icon: 'restore', title: 'Restore to inbox (d)' }
  switch (quick) {
    case 'done':
      // Pressed: already done, and pressing it moves the threads back to the inbox.
      return all((t) => !t.in_inbox) ? { icon: 'done', title: 'Move to inbox (e)', pressed: true } : { icon: 'done', title: 'Done (e)' }
    case 'delete':
      return { icon: 'delete', title: 'Delete (d)' }
    case 'star':
      return { icon: 'star', title: all((t) => t.starred) ? 'Unstar (s)' : 'Star (s)', pressed: all((t) => t.starred) }
    case 'important': {
      const on = all((t) => t.important)
      return { icon: 'important', title: on ? 'Not important (=)' : 'Important (=)', pressed: on }
    }
    case 'spam':
      return all((t) => t.in_spam) ? { icon: 'inbox', title: 'Not spam (!)' } : { icon: 'spam', title: 'Report spam (!)' }
  }
}

export function quickTarget(quick: Quick, threads: ThreadSummary[]): ThreadAction {
  const all = (test: (t: ThreadSummary) => boolean) => threads.every(test)
  if ((quick === 'done' || quick === 'delete') && inTrash(threads)) return 'untrash'
  switch (quick) {
    case 'done':
      return all((t) => !t.in_inbox) ? 'inbox' : 'done'
    case 'delete':
      return 'delete'
    case 'star':
      return all((t) => t.starred) ? 'unstar' : 'star'
    case 'important':
      return all((t) => t.important) ? 'unimportant' : 'important'
    case 'spam':
      return all((t) => t.in_spam) ? 'unspam' : 'spam'
  }
}

type Snapshot = [QueryKey, unknown][]

function patchCaches(input: ActionInput): Snapshot {
  const ids = new Set(input.threads.map((t) => t.id))
  const snapshot: Snapshot = []
  const left = new Map<number, string>() // threads that left a bundle -> its name
  const lists = queryClient.getQueriesData<InfiniteData<ThreadPage>>({ queryKey: ['threads'] })
  for (const [key, data] of lists) {
    if (!data) continue
    snapshot.push([key, data])
    const params = new URLSearchParams(String(key[1]))
    queryClient.setQueryData<InfiniteData<ThreadPage>>(key, {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        threads: page.threads.flatMap((t) => {
          if (!ids.has(t.id)) return [t]
          const next = applied(t, input)
          if (inList(params, next, input.action === 'label' && !t.bundle)) return [next]
          if (t.bundle) left.set(t.id, t.bundle)
          return []
        }),
      })),
    })
  }
  const fewer = new Map<string, number>()
  for (const name of left.values()) fewer.set(name, (fewer.get(name) ?? 0) + 1)
  // Only an action that takes the threads out of the inbox empties the bundle.
  const emptied = input.action === 'done' || input.action === 'delete' || input.action === 'spam'
  const cleared = emptied && input.bundle?.name
  const recount = (bundle: Bundle) => ({
    ...bundle,
    count: bundle.name === cleared ? 0 : Math.max(bundle.count - (fewer.get(bundle.name) ?? 0), 0),
  })
  for (const [key] of lists) {
    const data = queryClient.getQueryData<InfiniteData<ThreadPage>>(key)
    const [first, ...rest] = data?.pages ?? []
    if (!data || !first?.bundles) continue
    queryClient.setQueryData<InfiniteData<ThreadPage>>(key, { ...data, pages: [{ ...first, bundles: first.bundles.map(recount) }, ...rest] })
  }
  for (const id of ids) {
    const key = ['thread', id]
    const detail = queryClient.getQueryData<ThreadDetail>(key)
    if (!detail) continue
    snapshot.push([key, detail])
    queryClient.setQueryData<ThreadDetail>(key, { ...detail, ...applied(detail, input) })
  }
  return snapshot
}

export const refreshAll = () => {
  for (const key of ['threads', 'thread', 'labels', 'accounts', 'cleanup']) void queryClient.invalidateQueries({ queryKey: [key] })
}

const mutationKey = ['thread-action']

export function useThreadAction(onError: (message: string) => void) {
  return useMutation({
    mutationKey,
    mutationFn: ({ action, threads, label, bundle, inLabel }: ActionInput) =>
      api<{ entries: number[] }>('/threads/actions', {
        method: 'POST',
        body: {
          action,
          label: label?.name,
          ...(bundle ? { bundle } : inLabel ? { in_label: inLabel } : { thread_ids: threads.map((t) => t.id) }),
        },
      }),
    onMutate: async (input) => {
      await queryClient.cancelQueries({ queryKey: ['threads'] })
      await queryClient.cancelQueries({ queryKey: ['thread'] })
      return patchCaches(input)
    },
    onError: (error, _input, snapshot) => {
      for (const [key, data] of snapshot ?? []) queryClient.setQueryData(key, data)
      onError(error.message)
    },
    // Refetch only after the last of a quick run (e e e), so a stale refetch can't bring a thread back.
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey }) === 1) refreshAll()
    },
  })
}

export type Unsubscribed = { method: 'one-click' | 'email' | 'link'; url: string | null }

export const unsubscribe = (threadId: number) => api<Unsubscribed>(`/threads/${threadId}/unsubscribe`, { method: 'POST' })

export function useUndo(onError: (message: string) => void) {
  return useMutation({
    mutationKey,
    mutationFn: (entries: number[]) => api<{ thread_ids: number[] }>('/threads/undo', { method: 'POST', body: { entries } }),
    onError: (error) => onError(`Couldn’t undo: ${error.message}`),
    onSettled: refreshAll,
  })
}
