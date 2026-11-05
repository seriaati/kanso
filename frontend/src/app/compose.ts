import { useQuery } from '@tanstack/react-query'
import { api, CSRF, queryClient } from './api'
import type { Address } from './mail'

// Every autosave writes the draft to a real Gmail draft.

/** With a content_id it is an image shown in the body (cid:). */
export type DraftAttachment = { id: string; filename: string; mime_type: string; size: number; content_id: string | null }

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

/** `fields`: filled in from a link (linkedDraft), and unsaved until edited, like any new message. */
export async function createDraft(mode: ComposeMode, from: { messageId?: number; accountId?: number }, fields?: LinkedDraft) {
  const draft = await api<Draft>('/drafts', {
    method: 'POST',
    body: { mode, message_id: from.messageId, account_id: from.accountId, tz: timeZone },
  })
  queryClient.setQueryData(['draft', draft.id], { ...draft, ...fields })
  return draft
}

export type LinkedDraft = Pick<Draft, 'to' | 'cc' | 'bcc' | 'subject' | 'body_html'>

/** A message started from a link: /compose?to=…&cc=…&bcc=…&subject=…&body=…, or ?mailto= with a
 * whole mailto: link, as the Android app and the browser's mailto: handler pass it. */
export function linkedDraft(params: URLSearchParams): LinkedDraft {
  const fields = [...params]
  const mailto = params.get('mailto')
  if (mailto && /^mailto:/i.test(mailto)) fields.push(...mailtoFields(mailto.slice('mailto:'.length)))
  const all = (name: string) => fields.filter(([key]) => key === name).map(([, value]) => value)
  const addresses = (name: string) => parseAddresses(all(name).join(',')).addresses
  const body = document.createElement('div')
  body.textContent = all('body')[0] ?? ''
  return {
    to: addresses('to'),
    cc: addresses('cc'),
    bcc: addresses('bcc'),
    subject: all('subject')[0] ?? '',
    body_html: body.innerHTML.replace(/\r?\n/g, '<br>'),
  }
}

// RFC 6068: the addresses, then ?name=value&…, percent-encoded ("+" is a plus, not a space).
function mailtoFields(link: string): [string, string][] {
  const decode = (text: string) => {
    try {
      return decodeURIComponent(text)
    } catch {
      return text // malformed percent-encoding
    }
  }
  const query = link.indexOf('?')
  const fields: [string, string][] = [['to', decode(query < 0 ? link : link.slice(0, query))]]
  if (query < 0) return fields
  for (const pair of link.slice(query + 1).split('&')) {
    const equals = pair.indexOf('=')
    if (equals > 0) fields.push([decode(pair.slice(0, equals)).toLowerCase(), decode(pair.slice(equals + 1))])
  }
  return fields
}

async function openByMessage(messageId: number) {
  const draft = await api<Draft>('/drafts/open', { method: 'POST', body: { message_id: messageId } })
  queryClient.setQueryData(['draft', draft.id], draft)
  return draft
}

// Also opens drafts written in Gmail.
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

// The server's IMAGE_TYPES: what can go in the body.
export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']

export const attachmentUrl = (draftId: number, attachmentId: string) => `/api/drafts/${draftId}/attachments/${attachmentId}`

export async function attach(id: number, file: File, inline = false): Promise<Draft> {
  const query = `filename=${encodeURIComponent(file.name)}${inline ? '&inline=true' : ''}`
  const response = await fetch(`/api/drafts/${id}/attachments?${query}`, {
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

export function refreshMail() {
  for (const key of ['threads', 'thread', 'labels']) void queryClient.invalidateQueries({ queryKey: [key] })
}

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
