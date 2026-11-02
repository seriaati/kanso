import { useState } from 'react'
import { Link } from 'react-router'
import type { Account } from '../../app/accounts'
import type { Label, NamedLabel, SyncStatus } from '../../app/mail'
import { href, sameView, type Mailbox, type Route, type View } from '../../app/nav'
import { applyUpdate } from '../../app/update'
import { Icon } from '../../components/Icon'
import { AccountSwitcher } from './AccountSwitcher'
import { LabelEditor } from './LabelEditor'
import styles from './Sidebar.module.css'

/** A quiet message at the foot of the sidebar: "Done", with Undo (z), or why an action failed. */
export type Notice = { id: number; text: string; undo?: () => void }

// Trash is quiet (muted), below the design's four. Spam is reachable by URL only (/spam).
const mailboxes: { mailbox: Mailbox; name: string; quiet?: boolean }[] = [
  { mailbox: 'inbox', name: 'Inbox' },
  { mailbox: 'done', name: 'Done' },
  { mailbox: 'sent', name: 'Sent' },
  { mailbox: 'drafts', name: 'Drafts' },
  { mailbox: 'trash', name: 'Trash', quiet: true },
]

type Props = {
  accounts: Account[]
  route: Route
  labels: Label[]
  userLabels: NamedLabel[]
  sync: SyncStatus[]
  notice: Notice | null
  /** AI is on: the label editor shows its AI settings. */
  ai: boolean
  /** A new version of kanso is ready: a quiet "Reload" at the foot. */
  update: boolean
  /** On a phone the sidebar is a drawer over the list: open or not. */
  drawer?: { open: boolean }
  onSelectAccount: (id: number | null) => void
  onCompose: () => void
  onLabelChanged: (name: string, change: { renamed?: string; deleted?: boolean }) => void
}

export function Sidebar(props: Props) {
  const { accounts, route, labels, userLabels, sync, notice, ai, update, drawer, onSelectAccount, onCompose, onLabelChanged } = props
  // The label being edited (by name), or '' for a new one.
  const [editing, setEditing] = useState<string | null>(null)
  const accountIds = route.accountId === null ? accounts.map((a) => a.id) : [route.accountId]
  const selected = accounts.find((a) => a.id === route.accountId) ?? null
  const inboxTotal = labels.filter((l) => l.gmail_id === 'INBOX').reduce((sum, l) => sum + l.threads_total, 0)
  const to = (view: View) => href({ view, threadId: null, accountId: route.accountId })

  return (
    <nav
      className={`${styles.sidebar} ${drawer ? styles.drawer : ''} ${drawer?.open ? styles.open : ''}`}
      aria-label="Mailboxes"
      inert={drawer && !drawer.open}
    >
      <AccountSwitcher accounts={accounts} selected={selected} ai={ai} onSelect={onSelectAccount} />

      <button type="button" className={styles.compose} onClick={onCompose}>
        <Icon name="compose" size={14} strokeWidth={2} />
        Compose
      </button>

      {mailboxes.map(({ mailbox, name, quiet }) => {
        const view: View = { kind: 'mailbox', mailbox }
        const current = !route.settings && sameView(view, route.view)
        return (
          <Link
            key={mailbox}
            to={to(view)}
            className={`${styles.nav} ${current ? styles.active : ''} ${quiet ? styles.quiet : ''}`}
            aria-current={current ? 'page' : undefined}
          >
            <span className={styles.name}>{name}</span>
            {mailbox === 'inbox' && inboxTotal > 0 && <span className={styles.count}>{inboxTotal}</span>}
          </Link>
        )
      })}

      <p className={styles.heading}>Labels</p>
      <div className={styles.labels}>
        {userLabels.map((label) => {
          const view: View = { kind: 'label', name: label.name }
          const current = !route.settings && sameView(view, route.view)
          if (editing === label.name)
            return (
              <LabelEditor
                key={label.name}
                label={label}
                accountIds={accountIds}
                ai={ai}
                onDone={(change) => {
                  setEditing(null)
                  if (change) onLabelChanged(label.name, change)
                }}
              />
            )
          return (
            <div key={label.name} className={styles.labelRow}>
              <Link
                to={to(view)}
                className={`${styles.nav} ${styles.label} ${current ? styles.active : ''}`}
                aria-current={current ? 'page' : undefined}
              >
                <span className={styles.dot} style={{ background: label.color }} aria-hidden="true" />
                <span className={styles.name}>{label.name}</span>
                {label.unread > 0 && (
                  <span className={styles.count} title={`${label.unread} unread`}>
                    {label.unread}
                  </span>
                )}
              </Link>
              <button
                type="button"
                className={styles.edit}
                aria-label={`Edit ${label.name}`}
                title={ai ? 'Rename, recolor, describe for AI or delete' : 'Rename, recolor or delete'}
                onClick={() => setEditing(label.name)}
              >
                <Icon name="more" size={14} />
              </button>
            </div>
          )
        })}
        {editing === '' ? (
          <LabelEditor accountIds={accountIds} ai={ai} onDone={() => setEditing(null)} />
        ) : (
          <button type="button" className={styles.newLabel} onClick={() => setEditing('')}>
            + New label
          </button>
        )}
      </div>

      {notice && (
        <div className={styles.notice} role="status" key={notice.id}>
          <span>{notice.text}</span>
          {notice.undo && (
            <button type="button" onClick={notice.undo} title="Undo (z)">
              Undo
            </button>
          )}
        </div>
      )}
      {update && (
        <div className={styles.notice} role="status">
          <span>A new version of kanso is ready.</span>
          <button type="button" onClick={applyUpdate}>
            Reload
          </button>
        </div>
      )}
      <SyncNote sync={sync.filter((s) => route.accountId === null || s.account_id === route.accountId)} />
    </nav>
  )
}

/** A quiet line while a backfill runs or a sync fails; nothing when all is well. */
function SyncNote({ sync }: { sync: SyncStatus[] }) {
  const notes = sync.flatMap((s) => {
    if (s.state === 'backfill') {
      const total = s.backfill_total ? ` of ${s.backfill_total.toLocaleString()}` : ''
      return [{ id: s.account_id, text: `Syncing ${s.email}: ${s.backfill_done.toLocaleString()}${total}`, title: 'Mail shows up as it syncs' }]
    }
    if (s.state === 'error') return [{ id: s.account_id, text: `Sync problem: ${s.email}`, title: s.error ?? '' }]
    return []
  })
  if (notes.length === 0) return null
  return (
    <div className={styles.sync} role="status">
      {notes.map((note) => (
        <p key={note.id} title={note.title}>
          {note.text}
        </p>
      ))}
    </div>
  )
}
