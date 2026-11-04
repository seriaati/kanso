import { useState } from 'react'
import { Link } from 'react-router'
import type { Account } from '../../app/accounts'
import { userLabels, type Label, type NamedLabel } from '../../app/mail'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import { LabelMark } from '../../components/LabelMark'
import styles from '../settings/Settings.module.css'
import { LabelEditor } from '../sidebar/LabelEditor'

type Props = {
  /** The account in view (undefined: none connected) and its labels. */
  account: Account | undefined
  labels: Label[]
  ai: boolean
  onMenu?: () => void
}

// Labels belong to one account, so they get a page of their own rather than a part of Settings
// (which applies to every account).
export function Labels({ account, labels, ai, onMenu }: Props) {
  // '' for a new label.
  const [editing, setEditing] = useState<string | null>(null)
  const named = userLabels(labels)
  const edited = named.find((l) => l.name === editing)
  const flags = (label: NamedLabel) => [ai && label.aiManaged && 'AI labels new mail', label.isBundle && 'Bundled'].filter(Boolean).join(' · ')
  return (
    <section className={styles.settings} aria-label="Labels">
      <header className={styles.header}>
        {onMenu && (
          <button type="button" className={styles.menu} aria-label="Mailboxes and labels" onClick={onMenu}>
            <Icon name="menu" size={18} />
          </button>
        )}
        <h1 className={styles.title}>Labels</h1>
      </header>
      <div className={styles.scroll}>
        <div className={styles.page}>
          <section className={styles.section}>
            {account ? (
              <>
                <p className={styles.intro}>
                  {account.email}’s labels.{ai && ' Describe a label and turn on AI, and new inbox mail is sorted into it.'}
                </p>
                <ul className={styles.list}>
                  {named.map((label) => (
                    <li key={label.name} className={styles.row}>
                      <LabelMark color={label.color} icon={label.icon} size={14} className={styles.mark} />
                      <div className={styles.text}>
                        <span className={styles.name}>
                          {label.name}
                          {flags(label) && <span className={styles.flags}> · {flags(label)}</span>}
                        </span>
                        {ai && label.description && <span className={styles.muted}>{label.description}</span>}
                      </div>
                      <button
                        type="button"
                        className={styles.iconButton}
                        aria-label={`Edit ${label.name}`}
                        title="Edit"
                        onClick={() => setEditing(label.name)}
                      >
                        <Icon name="compose" size={15} />
                      </button>
                    </li>
                  ))}
                </ul>
                <button type="button" className={styles.add} onClick={() => setEditing('')}>
                  + New label
                </button>
                {(editing === '' || edited) && (
                  <Dialog title={edited ? 'Edit label' : 'New label'} onClose={() => setEditing(null)}>
                    <LabelEditor label={edited} accountId={account.id} ai={ai} onDone={() => setEditing(null)} />
                  </Dialog>
                )}
              </>
            ) : (
              <p className={styles.intro}>
                No Gmail account connected yet. <Link to="/settings">Connect one in Settings</Link>.
              </p>
            )}
          </section>
        </div>
      </div>
    </section>
  )
}
