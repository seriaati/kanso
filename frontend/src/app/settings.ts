import { useMutation, useQuery } from '@tanstack/react-query'
import { api, queryClient } from './api'

// The settings page (backend: kanso/api/prefs.py and POST /api/auth/password).

/** `assistant_confirm`: the assistant asks before it changes mail (the default). */
export type Settings = { assistant_confirm: boolean }

const key = ['settings']

export function useSettings() {
  return useQuery({ queryKey: key, queryFn: () => api<Settings>('/settings') })
}

export function useUpdateSettings() {
  return useMutation({
    mutationFn: (patch: Partial<Settings>) => api<Settings>('/settings', { method: 'PATCH', body: patch }),
    // Shown at once; put back if the server refuses.
    onMutate: (patch) => queryClient.setQueryData<Settings>(key, (old) => old && { ...old, ...patch }),
    onSuccess: (settings) => queryClient.setQueryData(key, settings),
    onError: () => queryClient.invalidateQueries({ queryKey: key }),
  })
}

/** A demo instance's password (docker-compose.demo.yml), null on a real one, undefined until
 * known. Public, so the login page can show it. A demo connects no Gmail and its password can't
 * be changed. */
export function useDemoPassword() {
  const query = useQuery({
    queryKey: ['demo'],
    queryFn: () => api<{ password: string | null }>('/auth/demo'),
    staleTime: Infinity,
  })
  return query.data?.password
}

/** Every other browser is signed out; this one gets a new session. */
export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current: string; new: string }) => api('/auth/password', { method: 'POST', body }),
  })
}

/** Signs out every browser and device, this one too: the login screen follows. */
export function useLogoutEverywhere() {
  return useMutation({
    mutationFn: () => api('/auth/logout-everywhere', { method: 'POST' }),
    // The cookie is cleared too: refetching finds no session and shows the login screen.
    onSuccess: () => queryClient.resetQueries(),
  })
}
