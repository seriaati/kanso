import { useQuery } from '@tanstack/react-query'
import { api, CSRF, queryClient } from './api'
import type { Address } from './mail'

// Writing mail (backend: kanso/api/compose.py). A draft is created on the server first; every
// autosave writes it to a real Gmail draft.

export type DraftAttachment = { id: string; filename: string; mime_type: string; size: number }

export type Draft = {
  id: number
  account_id: number
  mode: 'new' | 'reply' | 'forward'
  to: Address[]
  cc: Address[]
  bcc: Address[]
  subject: string
  body_html: string
  quote_html: string | null
  attachments: DraftAttachment[]
  saved: boolean
  sending: boolean
  thread_id: number | null
}

export type DraftFields = Pick<Draft, 'account_id' | 'to' | 'cc' | 'bcc' | 'subject' | 'body_html'>

export type ComposeMode = 'new' | 'reply' | 'reply_all' | 'forward'

export const ATTACHMENTS_LIMIT = 25 * 1024 * 1024 // Gmail's, enforced by the server too

const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone

export async function createDraft(mode: ComposeMode, from: { messageId?: number; accountId?: number }) {
  const draft = await api<Draft>('/drafts', {
    method: 'POST',
    body: { mode, message_id: from.messageId, account_id: from.accountId, tz: timeZone },
  })
  queryClient.setQueryData(['draft', draft.id], draft)
  return draft
}

async function openByMessage(messageId: number) {
  const draft = await api<Draft>('/drafts/open', { method: 'POST', body: { message_id: messageId } })
  queryClient.setQueryData(['draft', draft.id], draft)
  return draft
}

/** The draft behind a draft message of a thread (also one written in Gmail). */
export function useOpenDraft(messageId: number | undefined) {
  return useQuery({
    queryKey: ['draft-of', messageId],
    queryFn: () => openByMessage(messageId ?? 0),
    enabled: messageId !== undefined,
    staleTime: Infinity,
    retry: false,
  })
}

export function openDraft(messageId: number) {
  return queryClient.fetchQuery({ queryKey: ['draft-of', messageId], queryFn: () => openByMessage(messageId), staleTime: Infinity })
}

export function useDraft(id: number) {
  return useQuery({ queryKey: ['draft', id], queryFn: () => api<Draft>(`/drafts/${id}`), staleTime: Infinity })
}

export function saveDraft(id: number, fields: DraftFields, keepalive = false) {
  return api<Draft>(`/drafts/${id}`, { method: 'PUT', body: fields, keepalive }).then((draft) => {
    queryClient.setQueryData(['draft', id], draft)
    return draft
  })
}

export function sendDraft(id: number, fields: DraftFields) {
  return api<{ status: 'sent' | 'pending'; thread_id: number | null }>(`/drafts/${id}/send`, { method: 'POST', body: fields })
}

export function discardDraft(id: number) {
  queryClient.removeQueries({ queryKey: ['draft', id] })
  return api(`/drafts/${id}`, { method: 'DELETE' })
}

/** Upload a file as the request body (no multipart encoding needed). */
export async function attach(id: number, file: File): Promise<Draft> {
  const response = await fetch(`/api/drafts/${id}/attachments?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    headers: { ...CSRF, 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  })
  if (!response.ok) {
    const detail: unknown = (await response.json().catch(() => null))?.detail
    throw new Error(typeof detail === 'string' ? detail : response.statusText)
  }
  return response.json() as Promise<Draft>
}

export function detach(id: number, attachmentId: string) {
  return api<Draft>(`/drafts/${id}/attachments/${attachmentId}`, { method: 'DELETE' })
}

export function useContacts(query: string) {
  return useQuery({
    queryKey: ['contacts', query],
    queryFn: () => api<Address[]>(`/contacts?q=${encodeURIComponent(query)}`),
    enabled: query.length > 0,
    staleTime: 60_000,
    placeholderData: (previous) => previous,
  })
}

/** Mail changed (sent, saved, discarded): refetch what shows it. */
export function refreshMail() {
  for (const key of ['threads', 'thread', 'labels']) void queryClient.invalidateQueries({ queryKey: [key] })
}

/** "Ana <ana@example.com>", "ana@example.com" or a list of them, as typed or pasted. Returns the
 * addresses and whatever couldn't be read as one. */
export function parseAddresses(text: string): { addresses: Address[]; rest: string } {
  const addresses: Address[] = []
  const rest: string[] = []
  for (const part of text.split(/[,;\n]+/)) {
    const value = part.trim()
    if (!value) continue
    const named = /^"?([^"<]*?)"?\s*<([^<>\s]+@[^<>\s]+)>$/.exec(value)
    if (named) addresses.push({ name: named[1].trim(), email: named[2] })
    else if (/^[^\s@<>,;"]+@[^\s@<>,;"]+$/.test(value)) addresses.push({ name: '', email: value })
    else rest.push(value)
  }
  return { addresses, rest: rest.join(', ') }
}
