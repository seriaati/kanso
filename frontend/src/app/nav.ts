import { useLocation } from 'react-router'
import type { IconName } from '../components/Icon'

// Labels go by name in the URL. `accountId` is the URL's; App picks one when it's missing.

export const mailboxes = ['inbox', 'starred', 'done', 'sent', 'drafts', 'all', 'trash', 'spam'] as const
export type Mailbox = (typeof mailboxes)[number]

// The sidebar's (and the phone menu's) mailboxes, in order.
export const mailboxNav: { mailbox: Mailbox; name: string; icon: IconName; quiet?: boolean }[] = [
  { mailbox: 'inbox', name: 'Inbox', icon: 'inbox' },
  { mailbox: 'starred', name: 'Starred', icon: 'star' },
  { mailbox: 'done', name: 'Done', icon: 'done' },
  { mailbox: 'sent', name: 'Sent', icon: 'sent' },
  { mailbox: 'drafts', name: 'Drafts', icon: 'draft' },
  { mailbox: 'all', name: 'All mail', icon: 'mail' },
  { mailbox: 'spam', name: 'Spam', icon: 'spam', quiet: true },
  { mailbox: 'trash', name: 'Trash', icon: 'delete', quiet: true },
]

export type View = { kind: 'mailbox'; mailbox: Mailbox } | { kind: 'label'; name: string } | { kind: 'search'; q: string }

// A page replaces the thread list: Settings (all accounts) or Labels (the account in view).
export type Page = 'settings' | 'labels'

export type Route = { view: View; threadId: number | null; accountId: number | null; page?: Page }

const isMailbox = (value: string | undefined): value is Mailbox => mailboxes.includes(value as Mailbox)

export function parse(pathname: string, params: URLSearchParams): Route | null {
  let segments: string[]
  try {
    segments = pathname.split('/').filter(Boolean).map(decodeURIComponent)
  } catch {
    return null // malformed percent-encoding
  }
  const [first, second, third] = segments
  const accountId = Number(params.get('account')) || null
  const thread = (segment: string | undefined) => (segment && /^\d+$/.test(segment) ? Number(segment) : null)
  if ((first === 'settings' || first === 'labels') && !second) return { view: { kind: 'mailbox', mailbox: 'inbox' }, threadId: null, accountId, page: first }
  if (first === 'label' && second) return { view: { kind: 'label', name: second }, threadId: thread(third), accountId }
  if (first === 'search') return { view: { kind: 'search', q: params.get('q') ?? '' }, threadId: thread(second), accountId }
  if (isMailbox(first)) return { view: { kind: 'mailbox', mailbox: first }, threadId: thread(second), accountId }
  return null
}

export function useRoute(): Route | null {
  const { pathname, search } = useLocation()
  return parse(pathname, new URLSearchParams(search))
}

export function href({ view, threadId, accountId, page }: Route): string {
  if (page) return accountId === null ? `/${page}` : `/${page}?account=${accountId}`
  let path =
    view.kind === 'mailbox' ? `/${view.mailbox}` : view.kind === 'label' ? `/label/${encodeURIComponent(view.name)}` : '/search'
  if (threadId !== null) path += `/${threadId}`
  const params = new URLSearchParams()
  if (accountId !== null) params.set('account', String(accountId))
  if (view.kind === 'search') params.set('q', view.q)
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

export function sameView(a: View, b: View): boolean {
  if (a.kind === 'mailbox' && b.kind === 'mailbox') return a.mailbox === b.mailbox
  if (a.kind === 'label' && b.kind === 'label') return a.name === b.name
  return a.kind === 'search' && b.kind === 'search' && a.q === b.q
}
