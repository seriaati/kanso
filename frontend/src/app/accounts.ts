import { useMutation, useQuery } from '@tanstack/react-query'
import { api, queryClient } from './api'

export type Account = { id: number; email: string; display_name: string; color: string }

// Matches the backend's COLORS.
export const accountColors = [
  { name: 'Indigo', value: '#2F4B6E' },
  { name: 'Ochre', value: '#A07338' },
  { name: 'Moss', value: '#556B3F' },
  { name: 'Plum', value: '#87607E' },
  { name: 'Rust', value: '#A35A3A' },
  { name: 'Slate', value: '#4F6E8C' },
  { name: 'Stone', value: '#6B6B66' },
]

const key = ['accounts']

// On the login screen a refetch would reset the query to pending and unmount the form mid-typing.
const whileLoggedIn = (query: { state: { data: unknown } }) => query.state.data !== undefined

// Also the session probe: it fails with Unauthorized when logged out.
export function useAccounts() {
  return useQuery({
    queryKey: key,
    queryFn: () => api<Account[]>('/accounts'),
    refetchOnWindowFocus: whileLoggedIn,
    refetchOnReconnect: whileLoggedIn,
  })
}

export function useUpdateAccount() {
  return useMutation({
    mutationFn: ({ id, ...patch }: Pick<Account, 'id' | 'display_name' | 'color'>) =>
      api<Account>(`/accounts/${id}`, { method: 'PATCH', body: patch }),
    onSuccess: (account) =>
      queryClient.setQueryData<Account[]>(key, (list) => list?.map((a) => (a.id === account.id ? account : a))),
  })
}

export function useDisconnectAccount() {
  return useMutation({
    mutationFn: (id: number) => api(`/accounts/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  })
}

export async function logout() {
  await api('/auth/logout', { method: 'POST' }).catch(() => undefined)
  await queryClient.resetQueries()
}
