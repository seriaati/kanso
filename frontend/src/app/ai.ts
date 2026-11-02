import { useMutation, useQuery } from '@tanstack/react-query'
import { api, CSRF, queryClient } from './api'

// AI (backend: kanso/api/ai.py). Without an Anthropic API key `ai` is false and the app shows no
// AI controls at all.

export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: () => api<{ ai: boolean }>('/config'), staleTime: Infinity })
}

export type AiUsage = {
  since: string
  features: { feature: string; model: string; requests: number; input_tokens: number; output_tokens: number; cost_usd: number | null }[]
  cost_usd: number
  complete: boolean
  /** Anthropic refused the API key on the latest call (cleared by the next call that works). */
  key_rejected: boolean
}

export function useAiUsage(enabled: boolean) {
  return useQuery({ queryKey: ['ai-usage'], queryFn: () => api<AiUsage>('/ai/usage'), enabled })
}

export type LabelRun = { id: number; total: number; pending: number; labeled: number; failed: number; error: string | null }
export type LabelInbox = { eligible: number; run: LabelRun | null }

/** "Label existing inbox": how many threads a run would classify, and the latest run, polled
 * while it is in progress. */
export function useLabelInbox(accountId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ['label-inbox', accountId],
    queryFn: () => api<LabelInbox>(accountId === null ? '/ai/label-inbox' : `/ai/label-inbox?account_id=${accountId}`),
    enabled,
    refetchInterval: (query) => (query.state.data?.run?.pending ? 1500 : false),
  })
}

export function useStartLabelInbox() {
  return useMutation({
    mutationFn: (accountId: number | null) => api<LabelInbox>('/ai/label-inbox', { method: 'POST', body: { account_id: accountId } }),
    onSuccess: (data, accountId) => queryClient.setQueryData(['label-inbox', accountId], data),
  })
}

// Thread summaries (backend: kanso/ai/summaries.py). The summary query sits under the thread's
// key, so a live update to the thread (a new reply) refetches it too.

export type Summary = { text: string; model: string; created_at: string }
/** `long`: summarized as soon as the thread is open; shorter ones on request. `summary` is null
 * until there is one for the thread's latest message. */
export type SummaryState = { messages: number; long: boolean; lang: string | null; summary: Summary | null }

export const summaryKey = (threadId: number) => ['thread', threadId, 'summary']

export function useSummaryState(threadId: number) {
  return useQuery({ queryKey: summaryKey(threadId), queryFn: () => api<SummaryState>(`/threads/${threadId}/summary`) })
}

type SummaryEvent = { type: 'delta'; text: string } | { type: 'done'; summary: Summary } | { type: 'error'; message: string }

/** Read a Server-Sent Events response, handing each event to `onEvent` until it returns a value
 * (the promise's result). */
async function readEvents<E, T>(response: Response, onEvent: (event: E) => T | undefined): Promise<T> {
  if (!response.ok || !response.body) {
    const detail: unknown = (await response.json().catch(() => null))?.detail
    throw new Error(typeof detail === 'string' ? detail : response.statusText || 'no answer')
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) throw new Error('the connection closed early')
    buffer += value
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const line = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      if (!line.startsWith('data: ')) continue
      const result = onEvent(JSON.parse(line.slice(6)) as E)
      if (result !== undefined) return result
    }
  }
}

/** Summarize a thread. `onText` hears the text so far while Claude writes it (Server-Sent
 * Events); the promise gives the stored summary. A stored one comes back at once, without a
 * call. Aborting only stops listening: the server finishes and stores the summary anyway. */
export async function summarize(threadId: number, onText: (text: string) => void, signal: AbortSignal): Promise<Summary> {
  const response = await fetch(`/api/threads/${threadId}/summary`, { method: 'POST', headers: CSRF, signal })
  let text = ''
  return readEvents<SummaryEvent, Summary>(response, (event) => {
    if (event.type === 'delta') onText((text += event.text))
    else if (event.type === 'done') return event.summary
    else throw new Error(event.message)
  })
}

// The assistant (backend: kanso/ai/assistant.py). Conversations are stored on the server; a
// question or a decision on a proposed change streams the answer as Server-Sent Events.

/** A change the assistant proposed; nothing happens until the user confirms it. */
export type AssistantAction = {
  id: string
  tool: 'archive' | 'apply_label' | 'star' | 'mark_read'
  label: string | null
  threads: { id: number; subject: string }[]
  status: 'pending' | 'confirmed' | 'declined' | 'skipped' | 'failed' | 'undone'
  entries: number[]
  error: string | null
  /** Made at once, without asking: the user turned confirmation off in Settings. */
  auto: boolean
}

export type ChatItem =
  | { kind: 'user' | 'text' | 'activity'; text: string }
  | { kind: 'action'; action: AssistantAction }

export type Conversation = { id: number; title: string; items: ChatItem[]; pending: boolean }
export type ConversationSummary = { id: number; title: string; updated_at: string; pending: boolean }

export type ChatEvent =
  | { type: 'conversation'; id: number; title: string }
  | { type: 'text' | 'activity'; text: string }
  | { type: 'action'; action: AssistantAction }
  | { type: 'done'; pending: boolean }
  | { type: 'error'; message: string }

export const conversationsKey = ['assistant', 'conversations']
export const conversationKey = (id: number) => ['assistant', 'conversation', id]

export function useConversations(enabled: boolean) {
  return useQuery({ queryKey: conversationsKey, queryFn: () => api<ConversationSummary[]>('/assistant/conversations'), enabled })
}

export function useConversation(id: number | null, enabled: boolean) {
  return useQuery({
    queryKey: conversationKey(id ?? 0),
    queryFn: () => api<Conversation>(`/assistant/conversations/${id}`),
    enabled: enabled && id !== null,
    retry: false,
  })
}

const timezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

async function chat(path: string, body: unknown, onEvent: (event: ChatEvent) => void, signal: AbortSignal): Promise<void> {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { ...CSRF, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  return readEvents<ChatEvent, true>(response, (event) => {
    onEvent(event)
    return event.type === 'done' || event.type === 'error' ? true : undefined
  }).then(() => undefined)
}

/** Ask a question: in a conversation, or a new one (its id comes first, as a "conversation"
 * event). `threadId` is the open thread, when "this thread" is on. */
export function ask(conversationId: number | null, text: string, threadId: number | null, onEvent: (event: ChatEvent) => void, signal: AbortSignal) {
  return chat('/assistant/messages', { conversation_id: conversationId, text, thread_id: threadId, timezone: timezone() }, onEvent, signal)
}

/** Confirm or cancel a proposed change; the assistant goes on once its turn's proposals are all
 * decided. */
export function decide(conversationId: number, actionId: string, confirm: boolean, onEvent: (event: ChatEvent) => void, signal: AbortSignal) {
  return chat(`/assistant/conversations/${conversationId}/actions/${actionId}`, { confirm }, onEvent, signal)
}

export function undoAction(conversationId: number, actionId: string) {
  return api<{ thread_ids: number[] }>(`/assistant/conversations/${conversationId}/actions/${actionId}/undo`, { method: 'POST' })
}

export function deleteConversation(id: number) {
  return api(`/assistant/conversations/${id}`, { method: 'DELETE' })
}
