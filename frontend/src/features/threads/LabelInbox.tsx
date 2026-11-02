import { useEffect, useRef, useState } from 'react'
import { useLabelInbox, useStartLabelInbox } from '../../app/ai'
import { queryClient } from '../../app/api'
import styles from './ThreadList.module.css'

type Props = {
  accountId: number | null
  onFinished: (text: string) => void
}

export function LabelInbox({ accountId, onFinished }: Props) {
  const status = useLabelInbox(accountId, true)
  const start = useStartLabelInbox()
  const [confirming, setConfirming] = useState(false)
  const started = useRef<number | null>(null) // the run started here, to report when it ends
  const run = status.data?.run
  const eligible = status.data?.eligible ?? 0

  useEffect(() => {
    if (!run || run.id !== started.current || run.pending > 0) return
    started.current = null
    const failed = run.failed ? ` ${run.failed} couldn’t be labeled${run.error ? `: ${run.error}` : '.'}` : ''
    onFinished(`Labeled ${run.labeled} of ${run.total} threads.${failed}`)
    void queryClient.invalidateQueries({ queryKey: ['threads'] })
    void queryClient.invalidateQueries({ queryKey: ['labels'] })
  }, [run, onFinished])

  if (run && run.pending > 0)
    return (
      <span className={styles.note} role="status">
        Labeling… {run.total - run.pending} of {run.total}
      </span>
    )
  if (confirming)
    return (
      <>
        <span className={styles.note}>
          Label {eligible} {eligible === 1 ? 'thread' : 'threads'} with AI?
        </span>
        <button
          type="button"
          disabled={start.isPending}
          onClick={() =>
            start.mutate(accountId, {
              onSuccess: (data) => {
                setConfirming(false)
                started.current = data.run?.id ?? null
              },
              onError: (error) => {
                setConfirming(false)
                onFinished(`Couldn’t start labeling: ${error.message}`)
              },
            })
          }
        >
          Label
        </button>
        <button type="button" onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </>
    )
  if (eligible === 0) return null
  return (
    <button type="button" title="Claude sorts the inbox threads without a label into your AI-managed labels" onClick={() => setConfirming(true)}>
      Label with AI
    </button>
  )
}
