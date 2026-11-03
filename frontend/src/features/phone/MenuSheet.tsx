import { useState } from 'react'
import { Link } from 'react-router'
import { logout, type Account } from '../../app/accounts'
import type { Label, NamedLabel, SyncStatus } from '../../app/mail'
import { href, mailboxNav, sameView, type Route, type View } from '../../app/nav'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import { LabelEditor } from '../sidebar/LabelEditor'
import { SyncNote } from '../sidebar/Sidebar'
import styles from './MenuSheet.module.css'

type Props = {
  accounts: Account[]
  route: Route
  labels: Label[]
  userLabels: NamedLabel[]
  sync: SyncStatus[]
  ai: boolean
  onSelectAccount: (id: number) => void
  onClose: () => void
}

// The phone's sidebar: a sheet from the bottom, within thumb reach. Any link in it closes it
// (App closes it when the page changes).
export function MenuSheet({ accounts, route, labels, userLabels, sync, ai, onSelectAccount, onClose }: Props) {
  const [creating, setCreating] = useState(false)
  const to = (view: View) => href({ view, threadId: null, accountId: route.accountId })
  const inboxTotal = labels.filter((l) => l.gmail_id === 'INBOX').reduce((sum, l) => sum + l.threads_total, 0)

  return (
    <>
      <div className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <nav className={styles.sheet} aria-label="Menu">
        <button type="button" className={styles.handle} aria-label="Close menu" onClick={onClose} />

        <h2 className={styles.heading}>Accounts</h2>
        {accounts.map((account) => (
          <button
            key={account.id}
            type="button"
            className={`${styles.account} ${account.id === route.accountId ? styles.current : ''}`}
            aria-current={account.id === route.accountId || undefined}
            onClick={() => (account.id === route.accountId ? onClose() : onSelectAccount(account.id))}
          >
            <span className={styles.avatar} style={{ background: account.color }} aria-hidden="true">
              {account.display_name.charAt(0).toUpperCase()}
            </span>
            <span className={styles.text}>
              <span className={styles.name}>{account.display_name}</span>
              <span className={styles.sub}>{account.email}</span>
            </span>
            {account.id === route.accountId && <Icon name="check" size={18} />}
          </button>
        ))}
        {accounts.length === 0 && <p className={styles.note}>No Gmail account connected yet.</p>}

        <h2 className={styles.heading}>Mailboxes</h2>
        <div className={styles.grid}>
          {mailboxNav.map(({ mailbox, name, icon, quiet }) => {
            const view: View = { kind: 'mailbox', mailbox }
            const current = !route.page && sameView(view, route.view)
            return (
              <Link
                key={mailbox}
                to={to(view)}
                className={`${styles.item} ${current ? styles.active : ''} ${quiet ? styles.quiet : ''}`}
                aria-current={current ? 'page' : undefined}
              >
                <Icon name={icon} size={17} />
                <span className={styles.name}>{name}</span>
                {mailbox === 'inbox' && inboxTotal > 0 && <span className={styles.count}>{inboxTotal}</span>}
              </Link>
            )
          })}
        </div>

        {route.accountId !== null && (
          <>
            <div className={styles.headingRow}>
              <h2 className={styles.heading}>Labels</h2>
              <Link
                to={href({ ...route, threadId: null, page: 'labels' })}
                className={styles.manage}
                aria-current={route.page === 'labels' ? 'page' : undefined}
              >
                Manage
              </Link>
            </div>
            <div className={styles.grid}>
              {userLabels.map((label) => {
                const view: View = { kind: 'label', name: label.name }
                const current = !route.page && sameView(view, route.view)
                return (
                  <Link
                    key={label.id}
                    to={to(view)}
                    className={`${styles.item} ${current ? styles.active : ''}`}
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
                )
              })}
              <button type="button" className={`${styles.item} ${styles.quiet}`} onClick={() => setCreating(true)}>
                <Icon name="plus" size={15} />
                <span className={styles.name}>New label</span>
              </button>
            </div>
          </>
        )}

        <div className={styles.footer}>
          <Link to={route.accountId === null ? '/settings' : `/settings?account=${route.accountId}`} className={styles.item}>
            <Icon name="settings" size={17} />
            Settings
          </Link>
          <button type="button" className={styles.item} onClick={() => void logout()}>
            <Icon name="logout" size={17} />
            Log out
          </button>
        </div>
        <SyncNote sync={sync.filter((s) => s.account_id === route.accountId)} />
      </nav>
      {creating && route.accountId !== null && (
        <Dialog title="New label" onClose={() => setCreating(false)}>
          <LabelEditor accountId={route.accountId} ai={ai} onDone={() => setCreating(false)} />
        </Dialog>
      )}
    </>
  )
}
