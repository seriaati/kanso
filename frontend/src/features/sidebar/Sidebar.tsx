import { useState } from 'react'
import { Link } from 'react-router'
import type { Account } from '../../app/accounts'
import type { Label, NamedLabel, SyncStatus } from '../../app/mail'
import { href, mailboxNav, sameView, type Route, type View } from '../../app/nav'
import { applyUpdate } from '../../app/update'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import { LabelMark } from '../../components/LabelMark'
import { Menu } from '../../components/Menu'
import { AccountSwitcher } from './AccountSwitcher'
import { DeleteLabel, LabelEditor } from './LabelEditor'
import styles from './Sidebar.module.css'

export type Notice = { id: number; text: string; undo?: () => void }

type Props = {
  accounts: Account[]
  route: Route
  labels: Label[]
  userLabels: NamedLabel[]
  sync: SyncStatus[]
  notice: Notice | null
  ai: boolean
  labeling: boolean
  update: boolean
  /** An icon rail: names show as tooltips, label editing hides and notices float. */
  collapsed: boolean
  onToggle: () => void
  onSelectAccount: (id: number) => void
  onCompose: () => void
  onLabelChanged: (name: string, change: { renamed?: string; deleted?: boolean }) => void
}

export function Sidebar(props: Props) {
  const { accounts, route, labels, userLabels, sync, notice, ai, labeling, update, collapsed, onToggle, onSelectAccount, onCompose, onLabelChanged } = props
  // `editing` is '' for a new label.
  const [menu, setMenu] = useState<{ name: string; anchor: HTMLElement } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const edited = userLabels.find((l) => l.name === editing)
  const deleted = userLabels.find((l) => l.name === deleting)
  const selected = accounts.find((a) => a.id === route.accountId) ?? null
  const inboxTotal = labels.filter((l) => l.gmail_id === 'INBOX').reduce((sum, l) => sum + l.threads_total, 0)
  const to = (view: View) => href({ view, threadId: null, accountId: route.accountId })

  return (
    <nav
      className={`${styles.sidebar} ${collapsed ? styles.collapsed : ''}`}
      aria-label="Mailboxes"
    >
      <AccountSwitcher accounts={accounts} selected={selected} ai={ai} compact={collapsed} onSelect={onSelectAccount} />

      <button type="button" className={styles.compose} onClick={onCompose} title={collapsed ? 'Compose (c)' : undefined}>
        <Icon name="compose" size={14} strokeWidth={2} />
        <span className={styles.text}>Compose</span>
      </button>

      {mailboxNav.map(({ mailbox, name, icon, quiet }) => {
        const view: View = { kind: 'mailbox', mailbox }
        const current = !route.page && sameView(view, route.view)
        return (
          <Link
            key={mailbox}
            to={to(view)}
            className={`${styles.nav} ${current ? styles.active : ''} ${quiet ? styles.quiet : ''}`}
            aria-current={current ? 'page' : undefined}
            title={collapsed ? name : undefined}
          >
            <Icon name={icon} size={15} />
            <span className={styles.name}>{name}</span>
            {mailbox === 'inbox' && inboxTotal > 0 && <span className={styles.count}>{inboxTotal}</span>}
          </Link>
        )
      })}
      {route.accountId !== null && (
        <Link
          to={href({ ...route, threadId: null, page: 'cleanup' })}
          className={`${styles.nav} ${styles.quiet} ${route.page === 'cleanup' ? styles.active : ''}`}
          aria-current={route.page === 'cleanup' ? 'page' : undefined}
          title={collapsed ? 'Clean up' : undefined}
        >
          <Icon name="sparkle" size={15} />
          <span className={styles.name}>Clean up</span>
        </Link>
      )}

      <div className={styles.headingRow}>
        <p className={styles.heading}>Labels</p>
        {route.accountId !== null && (
          <Link
            to={href({ ...route, threadId: null, page: 'labels' })}
            className={styles.manage}
            aria-current={route.page === 'labels' ? 'page' : undefined}
          >
            Manage
          </Link>
        )}
      </div>
      <div className={styles.labels}>
        {userLabels.map((label) => {
          const view: View = { kind: 'label', name: label.name }
          const current = !route.page && sameView(view, route.view)
          return (
            <div key={label.name} className={styles.labelRow}>
              <Link
                to={to(view)}
                className={`${styles.nav} ${styles.label} ${current ? styles.active : ''}`}
                aria-current={current ? 'page' : undefined}
                title={collapsed ? label.name : undefined}
              >
                <LabelMark color={label.color} icon={label.icon} size={15} />
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
        {route.accountId !== null && (
          <button type="button" className={styles.newLabel} onClick={() => setEditing('')}>
            <Icon name="plus" size={15} />
            New label
          </button>
        )}
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
      {editing !== null && (editing === '' || edited) && route.accountId !== null && (
        <Dialog title={edited ? 'Edit label' : 'New label'} onClose={() => setEditing(null)}>
          <LabelEditor
            label={edited}
            accountId={route.accountId}
            ai={labeling}
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

      <div className={styles.notices}>
        {notice && (
          <div className={styles.notice} role="status" key={notice.id}>
            <span>{notice.text}</span>
            {notice.undo && (
              <button type="button" onClick={notice.undo} title="Undo (z)">
                Undo <kbd>z</kbd>
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
      </div>
      <SyncNote sync={sync.filter((s) => s.account_id === route.accountId)} />
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={!collapsed}
        aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
        title={`${collapsed ? 'Expand' : 'Collapse'} the sidebar ([)`}
        onClick={onToggle}
      >
        <Icon name="sidebar" size={15} />
      </button>
    </nav>
  )
}

export function SyncNote({ sync }: { sync: SyncStatus[] }) {
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
    <div className={styles.sync} role="status" title={notes.map((note) => note.text).join('\n')}>
      {notes.map((note) => (
        <p key={note.id} title={note.title}>
          {note.text}
        </p>
      ))}
    </div>
  )
}
