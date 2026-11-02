import type { ReactNode } from 'react'
import { quickAction, type Quick } from '../../app/actions'
import type { ThreadSummary } from '../../app/mail'
import { Icon, type IconName } from '../../components/Icon'
import styles from './Selection.module.css'

type Props = {
  /** The checked threads (Ctrl/⌘-click, Shift-click or x in the list). */
  threads: ThreadSummary[]
  onQuick: (quick: Quick) => void
  onRead: (unread: boolean) => void
  onLabel: () => void
  /** The label picker, while open; it opens upward from the Label button. */
  picker: ReactNode
  onClear: () => void
}

/** A bar floating at the foot of the thread list while threads are checked: how many, and the
 * actions that apply to all of them at once. */
export function Selection({ threads, onQuick, onRead, onLabel, picker, onClear }: Props) {
  const count = threads.length
  const quick = (q: Quick) => {
    const { icon, title, pressed } = quickAction(q, threads)
    return <Action icon={icon} title={title} pressed={pressed} onClick={() => onQuick(q)} />
  }
  return (
    <div className={styles.bar} role="toolbar" aria-label={`Actions for ${count} selected`}>
      <span className={styles.count} role="status">
        {count} selected
      </span>
      <span className={styles.separator} aria-hidden="true" />
      {quick('done')}
      {quick('delete')}
      <Action icon="mailOpen" title="Mark as read" onClick={() => onRead(false)} />
      <Action icon="mail" title="Mark as unread (u)" onClick={() => onRead(true)} />
      {quick('star')}
      {quick('important')}
      <span className={styles.anchor}>
        <Action icon="label" title="Label (l)" onClick={onLabel} />
        {picker}
      </span>
      <span className={styles.separator} aria-hidden="true" />
      <Action icon="close" title="Clear the selection (Esc)" onClick={onClear} />
    </div>
  )
}

type ActionProps = { icon: IconName; title: string; pressed?: boolean; onClick: () => void }

function Action({ icon, title, pressed, onClick }: ActionProps) {
  return (
    <button
      type="button"
      className={styles.action}
      aria-label={title.replace(/ \(.*\)$/, '')}
      aria-pressed={pressed}
      title={title}
      onClick={onClick}
    >
      <Icon name={icon} size={16} filled={pressed} />
    </button>
  )
}
