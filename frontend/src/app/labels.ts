import { useMutation } from '@tanstack/react-query'
import { api, queryClient } from './api'
import type { Label } from './mail'

// LabelSettings are kanso's own, not stored in Gmail.

export type LabelSettings = { description?: string; ai_managed?: boolean; is_bundle?: boolean }

const refresh = () => {
  void queryClient.invalidateQueries({ queryKey: ['labels'] })
  void queryClient.invalidateQueries({ queryKey: ['threads'] })
}

export function useCreateLabel() {
  return useMutation({
    mutationFn: (body: { name: string; color: string | null; account_id: number } & LabelSettings) =>
      api<Label>('/labels', { method: 'POST', body }),
    onSuccess: refresh,
  })
}

export function useUpdateLabel() {
  return useMutation({
    mutationFn: ({ id, ...patch }: { id: number; name?: string; color?: string } & LabelSettings) =>
      api<Label>(`/labels/${id}`, { method: 'PATCH', body: patch }),
    onSettled: refresh,
  })
}

export function useDeleteLabel() {
  return useMutation({
    mutationFn: (id: number) => api(`/labels/${id}`, { method: 'DELETE' }),
    // Refetch lists only when next shown: the deleted label's list would just get a 404.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['labels'] })
      void queryClient.invalidateQueries({ queryKey: ['threads'], refetchType: 'none' })
    },
  })
}
