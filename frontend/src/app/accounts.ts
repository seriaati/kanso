import { useMutation, useQuery } from '@tanstack/react-query'
import { api, queryClient } from './api'

export type Account = {
  id: number
  email: string
  display_name: string
  color: string
  avatar_url: string | null
  /** Unread threads in the inbox. */
  unread: number
}

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

/** Optimistic: the list takes the new order at once. */
export function useReorderAccounts() {
  return useMutation({
    mutationFn: (ids: number[]) => api<Account[]>('/accounts/order', { method: 'PUT', body: { ids } }),
    onMutate: (ids) =>
      queryClient.setQueryData<Account[]>(key, (list) => list && ids.flatMap((id) => list.filter((a) => a.id === id))),
    onSettled: () => queryClient.invalidateQueries({ queryKey: key }),
  })
}

export function useDisconnectAccount() {
  return useMutation({
    mutationFn: (id: number) => api(`/accounts/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  })
}

const LAST_KEY = 'kanso.account'

// Each account is its own mailbox. Without one in the URL, the last one used in this browser
// opens, else the first. null: no account connected yet.
export function pickAccount(accounts: Account[], wanted: number | null): Account | null {
  let last: number | null = null
  try {
    last = Number(localStorage.getItem(LAST_KEY)) || null
  } catch {
    // storage blocked
  }
  return accounts.find((a) => a.id === wanted) ?? accounts.find((a) => a.id === last) ?? accounts[0] ?? null
}

export function rememberAccount(id: number) {
  try {
    localStorage.setItem(LAST_KEY, String(id))
  } catch {
    // storage blocked
  }
}

export async function logout() {
  await api('/auth/logout', { method: 'POST' }).catch(() => undefined)
  await queryClient.resetQueries()
}
