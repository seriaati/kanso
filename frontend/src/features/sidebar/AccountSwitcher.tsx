import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { logout, type Account } from '../../app/accounts'
import { useAiUsage } from '../../app/ai'
import { setDensity, useDensity, type Density } from '../../app/density'
import { setTheme, useTheme, type Theme } from '../../app/theme'
import { Icon, type IconName } from '../../components/Icon'
import styles from './AccountSwitcher.module.css'

type Props = {
  accounts: Account[]
  selected: Account | null
  /** AI is on: the menu says so if Anthropic rejected the API key. */
  ai: boolean
  onSelect: (id: number | null) => void
}

export function Avatar({ account }: { account: Account | null }) {
  return (
    <span className={`${styles.avatar} ${account ? '' : styles.all}`} style={account ? { background: account.color } : undefined} aria-hidden="true">
      {account ? account.display_name.charAt(0).toUpperCase() : <Icon name="accounts" size={14} strokeWidth={2} />}
    </span>
  )
}

const themes: { theme: Theme; icon: IconName; name: string }[] = [
  { theme: 'system', icon: 'system', name: 'System' },
  { theme: 'light', icon: 'sun', name: 'Light' },
  { theme: 'dark', icon: 'moon', name: 'Dark' },
]

const densities: { density: Density; icon: IconName; name: string }[] = [
  { density: 'compact', icon: 'compact', name: 'Compact' },
  { density: 'comfortable', icon: 'comfortable', name: 'Comfortable' },
]

/** Choose All accounts or one; the menu also leads to Settings (where accounts are connected,
 * renamed and disconnected) and logs out. */
export function AccountSwitcher({ accounts, selected, ai, onSelect }: Props) {
  const [open, setOpen] = useState(false)
  const keyRejected = useAiUsage(open && ai).data?.key_rejected
  const { theme } = useTheme()
  const density = useDensity()
  const ref = useRef<HTMLDivElement>(null)

  const close = () => setOpen(false)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) close()
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const select = (id: number | null) => {
    onSelect(id)
    close()
  }

  return (
    <div className={styles.switcher} ref={ref}>
      <button type="button" className={styles.trigger} aria-expanded={open} onClick={() => (open ? close() : setOpen(true))}>
        <Avatar account={selected} />
        <span className={styles.text}>
          <span className={styles.name}>{selected ? selected.display_name : 'All accounts'}</span>
          <span className={styles.sub}>
            {selected ? selected.email : accounts.map((a) => a.display_name).join(' · ') || 'No account connected'}
          </span>
        </span>
        <Icon name="chevronDown" size={12} strokeWidth={2} />
      </button>

      {open && (
        <div className={styles.popover} role="dialog" aria-label="Accounts">
          <button
            type="button"
            className={styles.row}
            aria-current={selected === null || undefined}
            onClick={() => select(null)}
          >
            <Avatar account={null} />
            <span className={styles.text}>
              <span className={styles.name}>All accounts</span>
            </span>
          </button>

          {accounts.map((account) => (
            <button
              key={account.id}
              type="button"
              className={styles.row}
              aria-current={selected?.id === account.id || undefined}
              onClick={() => select(account.id)}
            >
              <Avatar account={account} />
              <span className={styles.text}>
                <span className={styles.name}>{account.display_name}</span>
                <span className={styles.sub}>{account.email}</span>
              </span>
            </button>
          ))}

          <hr className={styles.divider} />
          {keyRejected && (
            <p className={styles.notice} role="status">
              Anthropic API key was rejected; check ANTHROPIC_API_KEY
            </p>
          )}
          <div className={styles.theme} role="radiogroup" aria-label="Theme">
            <span>Theme</span>
            {themes.map((t) => (
              <button
                key={t.theme}
                type="button"
                role="radio"
                aria-checked={theme === t.theme}
                aria-label={t.name}
                title={t.name}
                onClick={() => setTheme(t.theme)}
              >
                <Icon name={t.icon} size={15} />
              </button>
            ))}
          </div>
          <div className={styles.theme} role="radiogroup" aria-label="Density">
            <span>Density</span>
            {densities.map((d) => (
              <button
                key={d.density}
                type="button"
                role="radio"
                aria-checked={density === d.density}
                aria-label={d.name}
                title={d.name}
                onClick={() => setDensity(d.density)}
              >
                <Icon name={d.icon} size={15} />
              </button>
            ))}
          </div>
          <Link to="/settings" className={styles.action} onClick={close}>
            <Icon name="settings" size={15} />
            Settings
          </Link>
          <button type="button" className={styles.action} onClick={() => void logout()}>
            <Icon name="logout" size={15} />
            Log out
          </button>
        </div>
      )}
    </div>
  )
}
