import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { summarize, summaryKey, useSummaryState, type SummaryState } from '../../app/ai'
import { queryClient } from '../../app/api'
import { Icon } from '../../components/Icon'
import styles from './Reader.module.css'

// Delay before summarizing, so stepping through threads with j/k doesn't start one each.
const AUTO_DELAY_MS = 400

type Run = { text: string; error: string | null }

/** One English sentence under the subject, made when the thread opens. Only with AI on. */
export function Summary({ threadId }: { threadId: number }) {
  const state = useSummaryState(threadId)
  const [run, setRun] = useState<Run | null>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const start = () => {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setRun({ text: '', error: null })
    summarize(threadId, (text) => setRun({ text, error: null }), controller.signal).then(
      (summary) => {
        queryClient.setQueryData<SummaryState>(summaryKey(threadId), (old) => old && { ...old, summary })
        setRun(null)
      },
      (error: Error) => {
        if (!controller.signal.aborted) setRun({ text: '', error: error.message })
      },
    )
  }
  const startLater = useEffectEvent(start)

  const data = state.data
  // Once per open, and again when a new message arrives; a failure waits for "Try again".
  const auto = !!data && data.messages > 0 && !data.summary && run === null
  useEffect(() => {
    if (!auto) return
    const timer = setTimeout(startLater, AUTO_DELAY_MS)
    return () => clearTimeout(timer)
  }, [auto])

  // A thread of drafts only, or one that is gone.
  if (state.isError || !data || data.messages === 0) return null
  let body
  if (run?.error)
    body = (
      <span className={styles.summaryMuted}>
        Couldn’t summarize this thread: {run.error}.{' '}
        <button type="button" className={styles.summaryRetry} onClick={start}>
          Try again
        </button>
      </span>
    )
  else if (data.summary && !run) body = data.summary.text
  else body = run?.text || <span className={styles.summaryMuted}>Summarizing…</span>
  return (
    <p className={styles.summary} lang="en" aria-busy={run !== null && !run.error} title="Summary by Claude">
      <span className={styles.summaryIcon}>
        <Icon name="sparkle" size={13} filled />
      </span>
      <span>{body}</span>
    </p>
  )
}
