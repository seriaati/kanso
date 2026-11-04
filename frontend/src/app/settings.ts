import { useMutation, useQuery } from '@tanstack/react-query'
import { api, queryClient } from './api'

export type Settings = {
  assistant_confirm: boolean
  labeling_model: string
  summary_model: string
  assistant_model: string
  models: string[] // the choices
  labeling_models: string[] // auto-labeling's choices, by the API keys set
}

const key = ['settings']

// claude-sonnet-5-5 → Sonnet 5.5
export function modelName(id: string): string {
  if (id === 'jev-latest') return 'Jev'
  const match = id.match(/^claude-([a-z]+)-(\d+)-(\d+)$/)
  return match ? `${match[1][0].toUpperCase()}${match[1].slice(1)} ${match[2]}.${match[3]}` : id
}

export function useSettings() {
  return useQuery({ queryKey: key, queryFn: () => api<Settings>('/settings') })
}

export function useUpdateSettings() {
  return useMutation({
    mutationFn: (patch: Partial<Settings>) => api<Settings>('/settings', { method: 'PATCH', body: patch }),
    onMutate: (patch) => queryClient.setQueryData<Settings>(key, (old) => old && { ...old, ...patch }),
    onSuccess: (settings) => queryClient.setQueryData(key, settings),
    onError: () => queryClient.invalidateQueries({ queryKey: key }),
  })
}

// Public, so the login page can show it. Null on a real instance, undefined until known.
export function useDemoPassword() {
  const query = useQuery({
    queryKey: ['demo'],
    queryFn: () => api<{ password: string | null }>('/auth/demo'),
    staleTime: Infinity,
  })
  return query.data?.password
}

// Every other browser is signed out; this one gets a new session.
export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current: string; new: string }) => api('/auth/password', { method: 'POST', body }),
  })
}

export function useLogoutEverywhere() {
  return useMutation({
    mutationFn: () => api('/auth/logout-everywhere', { method: 'POST' }),
    // This browser's cookie is cleared too, so refetching shows the login screen.
    onSuccess: () => queryClient.resetQueries(),
  })
}
