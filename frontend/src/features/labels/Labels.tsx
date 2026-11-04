import { useState } from 'react'
import { Link } from 'react-router'
import type { Account } from '../../app/accounts'
import { usePickIcons } from '../../app/labels'
import { userLabels, type Label } from '../../app/mail'
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
  /** An Anthropic key is set: Claude can pick icons. */
  claude: boolean
  onMenu?: () => void
}

// Labels belong to one account, so they get a page of their own rather than a part of Settings
// (which applies to every account).
export function Labels({ account, labels, ai, claude, onMenu }: Props) {
  // '' for a new label.
  const [editing, setEditing] = useState<string | null>(null)
  const named = userLabels(labels)
  const edited = named.find((l) => l.name === editing)
  const pickIcons = usePickIcons()
  const iconless = named.filter((l) => !l.icon).length
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
                  {account.email}’s labels.{ai && ' AI sorts new inbox mail into them by their descriptions.'}
                </p>
                <ul className={styles.list}>
                  {named.map((label) => (
                    <li key={label.name} className={styles.row}>
                      <LabelMark color={label.color} icon={label.icon} size={14} className={styles.mark} />
                      <div className={styles.text}>
                        <span className={styles.name}>
                          {label.name}
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
                <div className={styles.labelActions}>
                  <button type="button" className={styles.add} onClick={() => setEditing('')}>
                    + New label
                  </button>
                  {claude && iconless > 0 && (
                    <button
                      type="button"
                      className={styles.add}
                      title="Claude picks an icon for each label without one, when one clearly fits"
                      disabled={pickIcons.isPending}
                      onClick={() => pickIcons.mutate(account.id)}
                    >
                      <Icon name="sparkle" size={14} />
                      {pickIcons.isPending ? 'Picking icons…' : 'Pick icons with AI'}
                    </button>
                  )}
                </div>
                {pickIcons.isError && <p className={styles.muted}>Couldn’t pick icons: {pickIcons.error.message}</p>}
                {pickIcons.data && <p className={styles.muted}>{picked(pickIcons.data)}</p>}
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

function picked(results: { icon: string | null }[]) {
  const given = results.filter((r) => r.icon).length
  const unsure = results.length - given
  if (!given) return 'No icon clearly fit; the labels keep their dot.'
  const gave = `Gave ${given} label${given === 1 ? '' : 's'} an icon.`
  return unsure ? `${gave} ${unsure} had no clear fit and keep${unsure === 1 ? 's' : ''} a dot.` : gave
}
