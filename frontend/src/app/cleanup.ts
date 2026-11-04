import { useQuery } from '@tanstack/react-query'
import { api } from './api'

// The Clean up page: what is left of an inbox from before kanso, and suggested labels for it.

/** Inbox threads of an account: older than `before` and/or with mail from `sender`. */
export type InboxScope = { account_id: number; before?: string; sender?: string }

export type Sender = { email: string; name: string; threads: number; thread_id: number }
/** `as_of`: pass it as the scope's `before`, so later mail is left alone. */
export type Senders = { as_of: string; senders: Sender[] }

export type Suggestion = { name: string; description: string; color: string; examples: string[] }

export function useInboxCount(accountId: number, before: string | null) {
  return useQuery({
    queryKey: ['cleanup', 'inbox', accountId, before],
    queryFn: () => api<{ threads: number }>(`/cleanup/inbox?account_id=${accountId}&before=${encodeURIComponent(before ?? '')}`),
    enabled: before !== null,
  })
}

export function useSenders(accountId: number) {
  return useQuery({ queryKey: ['cleanup', 'senders', accountId], queryFn: () => api<Senders>(`/cleanup/senders?account_id=${accountId}`) })
}

export const inboxAction = (action: 'done' | 'spam', inbox: InboxScope) =>
  api<{ entries: number[] }>('/threads/actions', { method: 'POST', body: { action, inbox } })

export const suggestLabels = (accountId: number, model: string) =>
  api<Suggestion[]>('/ai/label-suggestions', { method: 'POST', body: { account_id: accountId, model } })
