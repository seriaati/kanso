import { useMutation } from '@tanstack/react-query'
import { api, queryClient } from './api'
import type { Label } from './mail'

// A sidebar label may merge one label per account (by name), so changes apply to all its ids.
// LabelSettings are kanso's own, not stored in Gmail.

export type LabelSettings = { description?: string; ai_managed?: boolean; is_bundle?: boolean }

const refresh = () => {
  void queryClient.invalidateQueries({ queryKey: ['labels'] })
  void queryClient.invalidateQueries({ queryKey: ['threads'] })
}

export function useCreateLabel() {
  return useMutation({
    mutationFn: (body: { name: string; color: string | null; account_ids: number[] } & LabelSettings) =>
      api<Label[]>('/labels', { method: 'POST', body }),
    onSuccess: refresh,
  })
}

export function useUpdateLabel() {
  return useMutation({
    mutationFn: ({ ids, ...patch }: { ids: number[]; name?: string; color?: string } & LabelSettings) =>
      Promise.all(ids.map((id) => api<Label>(`/labels/${id}`, { method: 'PATCH', body: patch }))),
    onSettled: refresh,
  })
}

export function useDeleteLabel() {
  return useMutation({
    mutationFn: (ids: number[]) => Promise.all(ids.map((id) => api(`/labels/${id}`, { method: 'DELETE' }))),
    // Refetch lists only when next shown: the deleted label's list would just get a 404.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['labels'] })
      void queryClient.invalidateQueries({ queryKey: ['threads'], refetchType: 'none' })
    },
  })
}
