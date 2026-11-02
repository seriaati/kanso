import { useMutation, type InfiniteData, type QueryKey } from '@tanstack/react-query'
import { api, queryClient } from './api'
import type { IconName } from '../components/Icon'
import type { Section, ThreadDetail, ThreadPage, ThreadSummary } from './mail'

// Triage actions (backend: kanso/api/actions.py). The server changes its mirror at once and
// writes back to Gmail in the background; here the change shows before the server answers, and
// is rolled back only if the request fails.

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

export type ActionInput = {
  action: ThreadAction
  threads: ThreadSummary[]
  /** For label/unlabel: the label's name, and its id in each account (account id -> label id). */
  label?: { name: string; ids: Map<number, number> }
  /** "Done all" on a section or bundle of the grouped inbox: the server finds its threads (some
   * may not be loaded here); `threads` are the loaded ones. */
  section?: SectionScope
}

export type SectionScope = { name: string | null; bundle: boolean; account_id: number | null; until: string }

const sectionKey = (name: string | null | undefined, bundle: boolean) => `${bundle ? 'b' : 's'}:${name ?? ''}`

/** A thread as it is after the action. */
export function applied(thread: ThreadSummary, { action, label }: Omit<ActionInput, 'threads'>): ThreadSummary {
  switch (action) {
    case 'done':
    case 'inbox':
      return { ...thread, in_inbox: action === 'inbox' }
    case 'delete':
    case 'untrash':
      return { ...thread, in_trash: action === 'delete' }
    case 'star':
    case 'unstar':
      return { ...thread, starred: action === 'star' }
    case 'important':
    case 'unimportant':
      return { ...thread, important: action === 'important' }
    case 'read':
    case 'unread':
      return { ...thread, unread: action === 'unread' }
    case 'label':
    case 'unlabel': {
      const id = label?.ids.get(thread.account_id)
      if (id === undefined) return thread
      const others = thread.label_ids.filter((l) => l !== id)
      return { ...thread, label_ids: action === 'label' ? [...others, id] : others }
    }
  }
}

/** Whether a thread belongs in the list with these API parameters (see mail.ts viewParams).
 * Search results can't be judged here, so only Trash leaves them. */
export function inList(params: URLSearchParams, thread: ThreadSummary): boolean {
  const mailbox = params.get('mailbox')
  if (mailbox === 'trash') return thread.in_trash
  if (thread.in_trash) return false
  if (mailbox === 'inbox') return thread.in_inbox
  if (mailbox === 'starred') return thread.starred
  if (mailbox === 'done') return !thread.in_inbox
  const labelIds = params.getAll('label_id').map(Number)
  if (labelIds.length) return thread.label_ids.some((id) => labelIds.includes(id))
  return true
}


/** The four triage toggles: on hover rows, in the toolbar and on the keyboard. */
export type Quick = 'done' | 'delete' | 'star' | 'important'

/** A quick action's look for a thread (or several): icon, name with its shortcut, toggle state. */
export function quickAction(quick: Quick, threads: ThreadSummary[]): { icon: IconName; title: string; pressed?: boolean } {
  const all = (test: (t: ThreadSummary) => boolean) => threads.length > 0 && threads.every(test)
  switch (quick) {
    case 'done':
      return all((t) => !t.in_inbox) ? { icon: 'inbox', title: 'Move to inbox (e)' } : { icon: 'done', title: 'Done (e)' }
    case 'delete':
      return all((t) => t.in_trash) ? { icon: 'inbox', title: 'Restore from Trash (#)' } : { icon: 'delete', title: 'Delete (#)' }
    case 'star':
      return { icon: 'star', title: all((t) => t.starred) ? 'Unstar (s)' : 'Star (s)', pressed: all((t) => t.starred) }
    case 'important': {
      const on = all((t) => t.important)
      return { icon: 'important', title: on ? 'Not important (=)' : 'Important (=)', pressed: on }
    }
  }
}

/** Which action a quick toggle means for these threads. */
export function quickTarget(quick: Quick, threads: ThreadSummary[]): ThreadAction {
  const all = (test: (t: ThreadSummary) => boolean) => threads.every(test)
  switch (quick) {
    case 'done':
      return all((t) => !t.in_inbox) ? 'inbox' : 'done'
    case 'delete':
      return all((t) => t.in_trash) ? 'untrash' : 'delete'
    case 'star':
      return all((t) => t.starred) ? 'unstar' : 'star'
    case 'important':
      return all((t) => t.important) ? 'unimportant' : 'important'
  }
}

type Snapshot = [QueryKey, unknown][]

function patchCaches(input: ActionInput): Snapshot {
  const ids = new Set(input.threads.map((t) => t.id))
  const snapshot: Snapshot = []
  const left = new Map<number, string>() // threads that left the inbox -> their section's key
  const lists = queryClient.getQueriesData<InfiniteData<ThreadPage>>({ queryKey: ['threads'] })
  for (const [key, data] of lists) {
    if (!data) continue
    snapshot.push([key, data])
    const params = new URLSearchParams(String(key[1]))
    const bundle = params.get('bundle')
    queryClient.setQueryData<InfiniteData<ThreadPage>>(key, {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        threads: page.threads.flatMap((t) => {
          if (!ids.has(t.id)) return [t]
          const next = applied(t, input)
          if (inList(params, next)) return [next]
          if (params.get('mailbox') === 'inbox') left.set(t.id, bundle === null ? sectionKey(t.section, false) : sectionKey(bundle, true))
          return []
        }),
      })),
    })
  }
  // The grouped inbox's section counts follow (all of a section for "Done all").
  const fewer = new Map<string, number>()
  for (const key of left.values()) fewer.set(key, (fewer.get(key) ?? 0) + 1)
  const cleared = input.section && sectionKey(input.section.name, input.section.bundle)
  const recount = (section: Section) => {
    const key = sectionKey(section.name, section.bundle)
    return { ...section, count: key === cleared ? 0 : Math.max(section.count - (fewer.get(key) ?? 0), 0) }
  }
  for (const [key] of lists) {
    const data = queryClient.getQueryData<InfiniteData<ThreadPage>>(key)
    const [first, ...rest] = data?.pages ?? []
    if (!data || !first?.sections) continue
    queryClient.setQueryData<InfiniteData<ThreadPage>>(key, { ...data, pages: [{ ...first, sections: first.sections.map(recount) }, ...rest] })
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
  for (const key of ['threads', 'thread', 'labels']) void queryClient.invalidateQueries({ queryKey: [key] })
}

const mutationKey = ['thread-action']

export function useThreadAction(onError: (message: string) => void) {
  return useMutation({
    mutationKey,
    mutationFn: ({ action, threads, label, section }: ActionInput) =>
      api<{ entries: number[] }>('/threads/actions', {
        method: 'POST',
        body: section ? { action, section, label: label?.name } : { action, thread_ids: threads.map((t) => t.id), label: label?.name },
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
    // Refetch once the last of a quick run of actions (e e e) is in, so an earlier refetch
    // doesn't briefly bring back a thread a later action removed.
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey }) === 1) refreshAll()
    },
  })
}

export function useUndo(onError: (message: string) => void) {
  return useMutation({
    mutationKey,
    mutationFn: (entries: number[]) => api<{ thread_ids: number[] }>('/threads/undo', { method: 'POST', body: { entries } }),
    onError: (error) => onError(`Couldn’t undo: ${error.message}`),
    onSettled: refreshAll,
  })
}
