import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api, CSRF, queryClient } from './api'

export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: () => api<{ ai: boolean; labeling: boolean }>('/config'), staleTime: Infinity })
}

export type KeyProvider = 'anthropic' | 'jev'
/** The last characters of each saved key; null: not set. */
export type ApiKeys = Record<KeyProvider, string | null>

export function useApiKeys() {
  return useQuery({ queryKey: ['ai-keys'], queryFn: () => api<ApiKeys>('/ai/keys') })
}

// A key turns AI features on or off everywhere, so everything is fetched again.
const keysChanged = (keys: ApiKeys) => {
  queryClient.setQueryData(['ai-keys'], keys)
  void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'ai-keys' })
}

/** The provider checks the key first; a rejected one fails with its reason. */
export function useSaveApiKey() {
  return useMutation({
    mutationFn: ({ provider, key }: { provider: KeyProvider; key: string }) =>
      api<ApiKeys>(`/ai/keys/${provider}`, { method: 'PUT', body: { key } }),
    onSuccess: keysChanged,
  })
}

export function useRemoveApiKey() {
  return useMutation({
    mutationFn: (provider: KeyProvider) => api<ApiKeys>(`/ai/keys/${provider}`, { method: 'DELETE' }),
    onSuccess: keysChanged,
  })
}

export type AiUsage = {
  since: string
  features: { feature: string; model: string; requests: number; input_tokens: number; output_tokens: number; cost_usd: number | null }[]
  cost_usd: number
  complete: boolean
  /** Cleared by the next call that works. */
  key_rejected: boolean
}

export function useAiUsage(enabled: boolean) {
  return useQuery({ queryKey: ['ai-usage'], queryFn: () => api<AiUsage>('/ai/usage'), enabled })
}

export type LabelRun = { id: number; total: number; pending: number; labeled: number; failed: number; error: string | null }
/** `estimate_usd`: what labeling the eligible threads costs, roughly; null: unknown (Jev). */
export type LabelInbox = { eligible: number; estimate_usd: number | null; run: LabelRun | null }
/** Threads whose latest message is in this range (ISO times); empty: the whole inbox. */
export type DateRange = { after?: string; before?: string }

const labelInboxKey = (accountId: number, range: DateRange) => ['label-inbox', accountId, range.after ?? null, range.before ?? null]

export function useLabelInbox(accountId: number, enabled: boolean, range: DateRange = {}) {
  const params = new URLSearchParams({ account_id: String(accountId) })
  if (range.after) params.set('after', range.after)
  if (range.before) params.set('before', range.before)
  return useQuery({
    queryKey: labelInboxKey(accountId, range),
    queryFn: () => api<LabelInbox>(`/ai/label-inbox?${params}`),
    enabled,
    refetchInterval: (query) => (query.state.data?.run?.pending ? 1500 : false),
  })
}

export function useStartLabelInbox() {
  return useMutation({
    mutationFn: ({ accountId, range }: { accountId: number; range: DateRange }) =>
      api<LabelInbox>('/ai/label-inbox', { method: 'POST', body: { account_id: accountId, ...range } }),
    onSuccess: (data, { accountId, range }) => queryClient.setQueryData(labelInboxKey(accountId, range), data),
  })
}

/** A "Label existing inbox" run: its status, and start(), which says when the run ends. */
export function useLabelRun(accountId: number, range: DateRange, onFinished: (text: string) => void) {
  const status = useLabelInbox(accountId, true, range)
  const start = useStartLabelInbox()
  const started = useRef<number | null>(null) // the run started here, to report when it ends
  const run = status.data?.run

  useEffect(() => {
    if (!run || run.id !== started.current || run.pending > 0) return
    started.current = null
    const failed = run.failed ? ` ${run.failed} couldn’t be labeled${run.error ? `: ${run.error}` : '.'}` : ''
    onFinished(`Labeled ${run.labeled} of ${run.total} threads.${failed}`)
    void queryClient.invalidateQueries({ queryKey: ['threads'] })
    void queryClient.invalidateQueries({ queryKey: ['labels'] })
  }, [run, onFinished])

  return {
    status: status.data,
    running: run && run.pending > 0 ? run : null,
    starting: start.isPending,
    /** `then` runs once the request is answered, either way. */
    start: (then?: () => void) =>
      start.mutate(
        { accountId, range },
        {
          onSuccess: (data) => {
            started.current = data.run?.id ?? null
            then?.()
          },
          onError: (error) => {
            then?.()
            onFinished(`Couldn’t start labeling: ${error.message}`)
          },
        },
      ),
  }
}

export type Summary = { text: string; model: string; created_at: string }
export type SummaryState = { messages: number; summary: Summary | null }

// Under the thread's key, so a live update to the thread (a new reply) refetches it too.
export const summaryKey = (threadId: number) => ['thread', threadId, 'summary']

export function useSummaryState(threadId: number) {
  return useQuery({ queryKey: summaryKey(threadId), queryFn: () => api<SummaryState>(`/threads/${threadId}/summary`) })
}

type SummaryEvent = { type: 'delta'; text: string } | { type: 'done'; summary: Summary } | { type: 'error'; message: string }

// Resolves with the first value `onEvent` returns.
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

// Aborting only stops listening: the server finishes and stores the summary anyway.
export async function summarize(threadId: number, onText: (text: string) => void, signal: AbortSignal): Promise<Summary> {
  const response = await fetch(`/api/threads/${threadId}/summary`, { method: 'POST', headers: CSRF, signal })
  let text = ''
  return readEvents<SummaryEvent, Summary>(response, (event) => {
    if (event.type === 'delta') onText((text += event.text))
    else if (event.type === 'done') return event.summary
    else throw new Error(event.message)
  })
}

/** One change the assistant proposes, to threads it picked by id or by a search (`query`). */
export type AssistantChange = {
  tool: 'archive' | 'delete' | 'apply_label' | 'remove_label' | 'star' | 'unstar' | 'mark_read' | 'mark_unread'
  label: string | null
  query?: string | null
  threads: { id: number; subject: string }[]
}

/** A proposal: one change, or a batch of them (`parts`) confirmed together. */
export type AssistantAction = Omit<AssistantChange, 'tool'> & {
  id: string
  tool: AssistantChange['tool'] | 'change_batch'
  parts?: AssistantChange[] | null
  status: 'pending' | 'confirmed' | 'declined' | 'skipped' | 'failed' | 'undone'
  entries: number[]
  error: string | null
  /** Made without asking: confirmation is off in Settings. */
  auto: boolean
}

export type ChatItem =
  | { kind: 'user'; text: string; files?: string[] }
  | { kind: 'text' | 'activity'; text: string }
  | { kind: 'action'; action: AssistantAction }

export type Conversation = { id: number; title: string; items: ChatItem[]; pending: boolean }
export type ConversationSummary = { id: number; title: string; updated_at: string; pending: boolean }

export type ChatEvent =
  | { type: 'conversation'; id: number; title: string }
  | { type: 'text' | 'activity'; text: string }
  | { type: 'action'; action: AssistantAction }
  | { type: 'done'; pending: boolean }
  | { type: 'error'; message: string }

export const conversationsKey = (accountId: number) => ['assistant', 'conversations', accountId]
export const conversationKey = (id: number) => ['assistant', 'conversation', id]

export function useConversations(accountId: number) {
  return useQuery({
    queryKey: conversationsKey(accountId),
    queryFn: () => api<ConversationSummary[]>(`/assistant/conversations?account_id=${accountId}`),
  })
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

// A new conversation's id comes first, as a "conversation" event.
/** A file for the assistant to read, as the server takes it (FileIn). */
export type AskFile = { name: string; media_type: string; data: string }

// The server's limits (ai/assistant.py).
export const ASK_FILES = 5

export function readFile(file: File): Promise<AskFile> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const url = String(reader.result) // data:<type>;base64,<data>
      resolve({ name: file.name, media_type: file.type, data: url.slice(url.indexOf(',') + 1) })
    }
    reader.onerror = () => reject(reader.error ?? new Error(`Couldn’t read ${file.name}`))
    reader.readAsDataURL(file)
  })
}

/** About the open thread, if any: the server gives Claude its content. */
export function ask(
  accountId: number,
  conversationId: number | null,
  text: string,
  threadId: number | null,
  files: AskFile[],
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
) {
  const body = { account_id: accountId, conversation_id: conversationId, text, thread_id: threadId, timezone: timezone(), files }
  return chat('/assistant/messages', body, onEvent, signal)
}

// The assistant goes on once all of its turn's proposals are decided.
export function decide(conversationId: number, actionId: string, confirm: boolean, onEvent: (event: ChatEvent) => void, signal: AbortSignal) {
  return chat(`/assistant/conversations/${conversationId}/actions/${actionId}`, { confirm }, onEvent, signal)
}

export function undoAction(conversationId: number, actionId: string) {
  return api<{ thread_ids: number[] }>(`/assistant/conversations/${conversationId}/actions/${actionId}/undo`, { method: 'POST' })
}

export function deleteConversation(id: number) {
  return api(`/assistant/conversations/${id}`, { method: 'DELETE' })
}
