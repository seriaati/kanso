import { useMutation } from '@tanstack/react-query'
import { api, queryClient } from './api'
import type { Label } from './mail'

// Label management (backend: kanso/api/labels.py). A label in the sidebar may stand for one
// label per account (merged by name), so rename, recolor and delete apply to all of its ids.
// Description, AI-managed and bundle are kanso's own settings, not stored in Gmail.

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
    // Lists refetch when next shown: the deleted label's own list would only get a 404 before
    // the view moves away from it.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['labels'] })
      void queryClient.invalidateQueries({ queryKey: ['threads'], refetchType: 'none' })
    },
  })
}
