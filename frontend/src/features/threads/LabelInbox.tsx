import { useState } from 'react'
import { useLabelRun } from '../../app/ai'
import styles from './ThreadList.module.css'

type Props = {
  accountId: number
  onFinished: (text: string) => void
}

export function LabelInbox({ accountId, onFinished }: Props) {
  const { status, running, starting, start } = useLabelRun(accountId, {}, onFinished)
  const [confirming, setConfirming] = useState(false)
  const eligible = status?.eligible ?? 0

  if (running)
    return (
      <span className={styles.note} role="status">
        Labeling… {running.total - running.pending} of {running.total}
      </span>
    )
  if (confirming)
    return (
      <>
        <span className={styles.note}>
          Label {eligible} {eligible === 1 ? 'thread' : 'threads'} with AI?
        </span>
        <button type="button" disabled={starting} onClick={() => start(() => setConfirming(false))}>
          Label
        </button>
        <button type="button" onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </>
    )
  if (eligible === 0) return null
  return (
    <button type="button" title="AI sorts the inbox threads without a label into your AI-managed labels" onClick={() => setConfirming(true)}>
      Label with AI
    </button>
  )
}
