import { MutationCache, QueryClient } from '@tanstack/react-query'

export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export class Unauthorized extends ApiError {}

type Options = { method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'; body?: unknown; keepalive?: boolean }

// CSRF protection: the API refuses changes without it, and a cross-site page can't add it.
export const CSRF = { 'X-Requested-With': 'kanso' }

export async function api<T = void>(path: string, { method = 'GET', body, keepalive }: Options = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    keepalive,
    headers: body === undefined ? CSRF : { ...CSRF, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const detail: unknown = (await response.json().catch(() => null))?.detail
    const message = typeof detail === 'string' ? detail : response.statusText
    throw response.status === 401 ? new Unauthorized(message, 401) : new ApiError(message, response.status)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: (count, error) => !(error instanceof Unauthorized) && count < 2 },
  },
  // Re-check the session (bringing up login), but not when already logged out: that would unmount the form.
  mutationCache: new MutationCache({
    onError: (error) => {
      if (error instanceof Unauthorized && queryClient.getQueryData(['accounts']))
        void queryClient.invalidateQueries({ queryKey: ['accounts'] })
    },
  }),
})
