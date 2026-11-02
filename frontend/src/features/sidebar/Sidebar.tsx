import { useState } from 'react'
import { Link } from 'react-router'
import type { Account } from '../../app/accounts'
import type { Label, NamedLabel, SyncStatus } from '../../app/mail'
import { href, sameView, type Mailbox, type Route, type View } from '../../app/nav'
import { applyUpdate } from '../../app/update'
import { Dialog } from '../../components/Dialog'
import { Icon, type IconName } from '../../components/Icon'
import { Menu } from '../../components/Menu'
import { AccountSwitcher } from './AccountSwitcher'
import { DeleteLabel, LabelEditor } from './LabelEditor'
import styles from './Sidebar.module.css'

export type Notice = { id: number; text: string; undo?: () => void }

const mailboxes: { mailbox: Mailbox; name: string; icon: IconName; quiet?: boolean }[] = [
  { mailbox: 'inbox', name: 'Inbox', icon: 'inbox' },
  { mailbox: 'starred', name: 'Starred', icon: 'star' },
  { mailbox: 'done', name: 'Done', icon: 'done' },
  { mailbox: 'sent', name: 'Sent', icon: 'sent' },
  { mailbox: 'drafts', name: 'Drafts', icon: 'draft' },
  { mailbox: 'all', name: 'All mail', icon: 'mail' },
  { mailbox: 'spam', name: 'Spam', icon: 'spam', quiet: true },
  { mailbox: 'trash', name: 'Trash', icon: 'delete', quiet: true },
]

type Props = {
  accounts: Account[]
  route: Route
  labels: Label[]
  userLabels: NamedLabel[]
  sync: SyncStatus[]
  notice: Notice | null
  ai: boolean
  update: boolean
  drawer?: { open: boolean }
  onSelectAccount: (id: number | null) => void
  onCompose: () => void
  onLabelChanged: (name: string, change: { renamed?: string; deleted?: boolean }) => void
}

export function Sidebar(props: Props) {
  const { accounts, route, labels, userLabels, sync, notice, ai, update, drawer, onSelectAccount, onCompose, onLabelChanged } = props
  // `editing` is '' for a new label.
  const [menu, setMenu] = useState<{ name: string; anchor: HTMLElement } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const edited = userLabels.find((l) => l.name === editing)
  const deleted = userLabels.find((l) => l.name === deleting)
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

      {mailboxes.map(({ mailbox, name, icon, quiet }) => {
        const view: View = { kind: 'mailbox', mailbox }
        const current = !route.settings && sameView(view, route.view)
        return (
          <Link
            key={mailbox}
            to={to(view)}
            className={`${styles.nav} ${current ? styles.active : ''} ${quiet ? styles.quiet : ''}`}
            aria-current={current ? 'page' : undefined}
          >
            <Icon name={icon} size={15} />
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
                className={`${styles.edit} ${menu?.name === label.name ? styles.menuOpen : ''}`}
                aria-label={`More actions for ${label.name}`}
                aria-haspopup="menu"
                aria-expanded={menu?.name === label.name}
                title="More actions"
                onClick={(event) => {
                  const anchor = event.currentTarget
                  setMenu(menu?.name === label.name ? null : { name: label.name, anchor })
                }}
              >
                <Icon name="more" size={14} />
              </button>
            </div>
          )
        })}
        <button type="button" className={styles.newLabel} onClick={() => setEditing('')}>
          <Icon name="plus" size={15} />
          New label
        </button>
      </div>
      {menu && (
        <Menu
          anchor={menu.anchor}
          label={`${menu.name} actions`}
          onClose={() => setMenu(null)}
          items={[
            { icon: 'compose', label: 'Edit label…', onSelect: () => setEditing(menu.name) },
            { icon: 'delete', label: 'Delete label…', onSelect: () => setDeleting(menu.name) },
          ]}
        />
      )}
      {editing !== null && (editing === '' || edited) && (
        <Dialog title={edited ? 'Edit label' : 'New label'} onClose={() => setEditing(null)}>
          <LabelEditor
            label={edited}
            accountIds={accountIds}
            ai={ai}
            plain
            onDone={(change) => {
              setEditing(null)
              if (change && edited) onLabelChanged(edited.name, change)
            }}
          />
        </Dialog>
      )}
      {deleted && (
        <Dialog title="Delete label" onClose={() => setDeleting(null)}>
          <DeleteLabel
            label={deleted}
            onDone={(done) => {
              setDeleting(null)
              if (done) onLabelChanged(deleted.name, { deleted: true })
            }}
          />
        </Dialog>
      )}

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
