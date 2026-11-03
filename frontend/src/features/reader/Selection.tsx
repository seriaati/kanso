import type { ReactNode } from 'react'
import { quickAction, redundant, type Quick } from '../../app/actions'
import type { ThreadSummary } from '../../app/mail'
import { Icon, type IconName } from '../../components/Icon'
import styles from './Selection.module.css'

type Props = {
  threads: ThreadSummary[]
  onQuick: (quick: Quick) => void
  onRead: (unread: boolean) => void
  onLabel: () => void
  picker: ReactNode
  onClear: () => void
}

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
      {!redundant('done', threads) && quick('done')}
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
