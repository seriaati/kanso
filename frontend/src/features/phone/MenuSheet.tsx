import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { Link } from 'react-router'
import { logout, type Account } from '../../app/accounts'
import type { Label, NamedLabel, SyncStatus } from '../../app/mail'
import { href, mailboxNav, sameView, type Route, type View } from '../../app/nav'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import { Avatar } from '../sidebar/AccountSwitcher'
import { LabelEditor } from '../sidebar/LabelEditor'
import { SyncNote } from '../sidebar/Sidebar'
import styles from './MenuSheet.module.css'

const FAR_ENOUGH = 80 // px pulled down to close on release
const FLICK = 0.5 // px/ms: a quicker pull closes from any distance

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
// (App closes it when the page changes). Pulled down from its top, it follows the finger and
// closes on release.
export function MenuSheet({ accounts, route, labels, userLabels, sync, ai, onSelectAccount, onClose }: Props) {
  const [creating, setCreating] = useState(false)
  const sheetRef = useRef<HTMLElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const close = useEffectEvent(onClose)

  // Native listeners: React's touchmove is passive, and the pull has to stop the sheet scrolling.
  useEffect(() => {
    const sheet = sheetRef.current
    const scrim = scrimRef.current
    if (!sheet || !scrim) return
    let pull: { y: number; at: number; offset: number; active: boolean } | null = null
    const move = (offset: number) => {
      sheet.style.translate = offset ? `0 ${offset}px` : ''
      scrim.style.opacity = offset ? String(1 - offset / sheet.offsetHeight) : ''
    }
    const onStart = (event: TouchEvent) => {
      pull = event.touches.length === 1 ? { y: event.touches[0].clientY, at: event.timeStamp, offset: 0, active: false } : null
    }
    const onMove = (event: TouchEvent) => {
      if (!pull) return
      const dy = event.touches[0].clientY - pull.y
      if (!pull.active) {
        if (sheet.scrollTop > 0 || dy <= 0) return void (pull = null) // scrolling the sheet
        pull.active = true
        sheet.dataset.dragging = scrim.dataset.dragging = ''
      }
      event.preventDefault()
      pull.offset = Math.max(0, dy)
      move(pull.offset)
    }
    const onEnd = (event: TouchEvent) => {
      const p = pull
      pull = null
      if (!p?.active) return
      delete sheet.dataset.dragging
      delete scrim.dataset.dragging
      const speed = p.offset / (event.timeStamp - p.at)
      if (p.offset < FAR_ENOUGH && speed < FLICK) return move(0)
      if (p.offset >= sheet.offsetHeight) return close() // already off-screen: nothing to animate
      sheet.addEventListener('transitionend', () => close(), { once: true })
      move(sheet.offsetHeight)
    }
    sheet.addEventListener('touchstart', onStart, { passive: true })
    sheet.addEventListener('touchmove', onMove, { passive: false })
    sheet.addEventListener('touchend', onEnd)
    sheet.addEventListener('touchcancel', onEnd)
    return () => {
      sheet.removeEventListener('touchstart', onStart)
      sheet.removeEventListener('touchmove', onMove)
      sheet.removeEventListener('touchend', onEnd)
      sheet.removeEventListener('touchcancel', onEnd)
    }
  }, [])
  const to = (view: View) => href({ view, threadId: null, accountId: route.accountId })
  const inboxTotal = labels.filter((l) => l.gmail_id === 'INBOX').reduce((sum, l) => sum + l.threads_total, 0)

  return (
    <>
      <div ref={scrimRef} className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <nav ref={sheetRef} className={styles.sheet} aria-label="Menu">
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
            <Avatar account={account} className={styles.avatar} />
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
          {route.accountId !== null && (
            <Link
              to={href({ ...route, threadId: null, page: 'cleanup' })}
              className={`${styles.item} ${styles.quiet} ${route.page === 'cleanup' ? styles.active : ''}`}
              aria-current={route.page === 'cleanup' ? 'page' : undefined}
            >
              <Icon name="sparkle" size={17} />
              <span className={styles.name}>Clean up</span>
            </Link>
          )}
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
