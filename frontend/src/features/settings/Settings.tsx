import { useState, type ReactNode } from 'react'
import { useDisconnectAccount, type Account } from '../../app/accounts'
import { useAiUsage } from '../../app/ai'
import type { SyncStatus } from '../../app/mail'
import { usePhone } from '../../app/media'
import { usePush } from '../../app/notifications'
import { useChangePassword, useDemoPassword, useLogoutEverywhere, useSettings, useUpdateSettings } from '../../app/settings'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import { Select } from '../../components/Select'
import { Avatar, DisplayOptions } from '../sidebar/AccountSwitcher'
import styles from './Settings.module.css'

type Props = {
  accounts: Account[]
  sync: SyncStatus[]
  ai: boolean
  onMenu?: () => void
}

export function Settings({ accounts, sync, ai, onMenu }: Props) {
  const phone = usePhone()
  const demo = useDemoPassword() !== null // true until known: no flash of Connect on a demo
  return (
    <section className={styles.settings} aria-label="Settings">
      <header className={styles.header}>
        {onMenu && (
          <button type="button" className={styles.menu} aria-label="Mailboxes and labels" onClick={onMenu}>
            <Icon name="menu" size={18} />
          </button>
        )}
        <h1 className={styles.title}>Settings</h1>
      </header>
      <div className={styles.scroll}>
        <div className={styles.page}>
          <Accounts accounts={accounts} sync={sync} demo={demo} />
          {/* On a desktop these are in the account menu. */}
          {phone && (
            <Section title="Display">
              <DisplayOptions density={false} />
            </Section>
          )}
          {!demo && <Notifications />}
          {ai && <Assistant />}
          {ai && <Models />}
          {ai && <Usage />}
          {!demo && <Password />}
          <Sessions />
        </div>
      </div>
    </section>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.section}>
      <h2 className={styles.heading}>{title}</h2>
      {children}
    </section>
  )
}

const dateTime = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

function syncLine(status: SyncStatus | undefined): string {
  if (!status) return ''
  switch (status.state) {
    case 'demo':
      return 'Demo mailbox: never synced'
    case 'error':
      return `Sync problem: ${status.error ?? 'unknown error'}`
    case 'backfill': {
      const total = status.backfill_total ? ` of about ${status.backfill_total.toLocaleString()}` : ''
      return `Copying your mail: ${status.backfill_done.toLocaleString()}${total} messages`
    }
    case 'ready': {
      const when = status.last_synced_at ? `Synced ${dateTime.format(new Date(status.last_synced_at))}` : 'Synced'
      return `${when} · ${status.push ? 'new mail arrives instantly' : 'checked every few minutes'}`
    }
  }
}

function Accounts({ accounts, sync, demo }: { accounts: Account[]; sync: SyncStatus[]; demo: boolean }) {
  const [disconnecting, setDisconnecting] = useState<number | null>(null)
  const target = accounts.find((a) => a.id === disconnecting)
  return (
    <Section title="Accounts">
      <ul className={styles.list}>
        {accounts.map((account) => {
          const status = sync.find((s) => s.account_id === account.id)
          return (
            <li key={account.id} className={styles.row}>
              <Avatar account={account} />
              <div className={styles.text}>
                <span className={styles.name}>{account.display_name}</span>
                <span className={styles.muted}>{account.email}</span>
                <span className={`${styles.muted} ${status?.state === 'error' ? styles.problem : ''}`}>{syncLine(status)}</span>
              </div>
              <button
                type="button"
                className={styles.disconnect}
                aria-label={`Disconnect ${account.email}`}
                onClick={() => setDisconnecting(account.id)}
              >
                Disconnect
              </button>
            </li>
          )
        })}
      </ul>
      {target && (
        <Dialog title="Disconnect account" onClose={() => setDisconnecting(null)}>
          <DisconnectAccount account={target} onDone={() => setDisconnecting(null)} />
        </Dialog>
      )}
      {accounts.length === 0 && <p className={styles.note}>No Gmail account connected yet.</p>}
      {demo ? (
        <p className={styles.note}>This is a demo: its mail is made up, no Gmail account can be connected, and every change resets each day.</p>
      ) : (
        /* A full-page navigation: the backend redirects to Google's consent screen. */
        <a href="/api/accounts/connect" className={styles.add}>
          + Connect a Gmail account
        </a>
      )}
    </Section>
  )
}

function DisconnectAccount({ account, onDone }: { account: Account; onDone: () => void }) {
  const disconnect = useDisconnectAccount()
  return (
    <div className={styles.confirm}>
      <p className={styles.note}>Removes {account.email} and its synced mail from kanso, and revokes kanso’s access. Gmail itself is not changed.</p>
      {disconnect.error && (
        <p role="alert" className={styles.note}>
          {disconnect.error.message}
        </p>
      )}
      <div className={styles.actions}>
        <button type="button" className={styles.cancel} onClick={onDone}>
          Cancel
        </button>
        <button type="button" className={styles.primary} disabled={disconnect.isPending} autoFocus onClick={() => disconnect.mutate(account.id)}>
          Disconnect
        </button>
      </div>
    </div>
  )
}

function Notifications() {
  const { state, set, pending, error } = usePush()
  return (
    <Section title="Notifications">
      {state === 'unsupported' ? (
        <p className={styles.intro}>
          This browser can’t show notifications from kanso. They need kanso opened over HTTPS; on an iPhone or iPad, add kanso to the Home
          Screen (Share → Add to Home Screen) and turn them on in the app.
        </p>
      ) : (
        <label className={styles.toggle}>
          <input type="checkbox" checked={state === 'on'} disabled={!state || state === 'denied' || pending} onChange={(event) => set(event.target.checked)} />
          <span>
            New inbox mail on this device
            <span className={styles.muted}>
              {state === 'denied'
                ? 'Notifications are blocked for kanso in this browser’s site settings. Allow them there, then turn this on.'
                : 'A notification for each new unread message in the inbox, with its sender, subject and first line. Turn it on in each browser or installed app where you want them.'}
            </span>
          </span>
        </label>
      )}
      {error && <p className={styles.note}>Couldn’t change notifications: {error}</p>}
    </Section>
  )
}

function Assistant() {
  const settings = useSettings()
  const update = useUpdateSettings()
  const confirm = settings.data?.assistant_confirm ?? true
  return (
    <Section title="Assistant">
      <label className={styles.toggle}>
        <input
          type="checkbox"
          checked={confirm}
          disabled={!settings.data}
          onChange={(event) => update.mutate({ assistant_confirm: event.target.checked })}
        />
        <span>
          Ask before changing mail
          <span className={styles.muted}>
            The assistant proposes archiving, labeling, starring and marking read, and waits for your Confirm. Turned off, it makes those
            changes at once; each one still shows in the chat with Undo. It never sends or deletes mail.
          </span>
        </span>
      </label>
      {update.error && <p className={styles.note}>Couldn’t save: {update.error.message}</p>}
    </Section>
  )
}

const modelFeatures = [
  { key: 'assistant_model', name: 'Assistant chat' },
  { key: 'summary_model', name: 'Thread summaries' },
  { key: 'labeling_model', name: 'Auto-labeling' },
] as const

// claude-sonnet-5-5 → Sonnet 5.5
function modelName(id: string): string {
  const match = id.match(/^claude-([a-z]+)-(\d+)-(\d+)$/)
  return match ? `${match[1][0].toUpperCase()}${match[1].slice(1)} ${match[2]}.${match[3]}` : id
}

function Models() {
  const settings = useSettings()
  const update = useUpdateSettings()
  const data = settings.data
  return (
    <Section title="AI models">
      <div className={styles.models}>
        {modelFeatures.map((feature) => (
          <div key={feature.key} className={styles.model}>
            <span>{feature.name}</span>
            <Select
              className={styles.picker}
              label={feature.name}
              value={data?.[feature.key] ?? ''}
              options={(data?.models ?? []).map((model) => ({ value: model, label: modelName(model) }))}
              disabled={!data}
              onChange={(value) => update.mutate({ [feature.key]: value })}
            />
          </div>
        ))}
      </div>
      {update.error && <p className={styles.note}>Couldn’t save: {update.error.message}</p>}
    </Section>
  )
}

const tokens = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })
const features: Record<string, string> = { labeling: 'Labeling', summary: 'Summaries', assistant: 'Assistant' }
const dollars = (cost: number) => (cost > 0 && cost < 0.01 ? '< $0.01' : `$${cost.toFixed(2)}`)

function Usage() {
  const usage = useAiUsage(true)
  const data = usage.data
  return (
    <Section title="AI usage">
      {usage.isError && <p className={styles.note}>Couldn’t load the usage: {usage.error.message}</p>}
      {data?.key_rejected && (
        <p className={`${styles.intro} ${styles.problem}`} role="status">
          Anthropic API key was rejected; check ANTHROPIC_API_KEY.
        </p>
      )}
      {data && (
        <>
          <p className={styles.intro}>
            Last 30 days: {data.complete ? '' : 'at least '}
            {dollars(data.cost_usd)}, estimated from Anthropic’s list prices.
          </p>
          {data.features.length > 0 && (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Feature</th>
                  <th scope="col">Model</th>
                  <th scope="col">Requests</th>
                  <th scope="col">Tokens in / out</th>
                  <th scope="col">Cost</th>
                </tr>
              </thead>
              <tbody>
                {data.features.map((f) => (
                  <tr key={`${f.feature}:${f.model}`}>
                    <td>{features[f.feature] ?? f.feature}</td>
                    <td className={styles.muted}>{f.model}</td>
                    <td>{f.requests.toLocaleString()}</td>
                    <td>
                      {tokens.format(f.input_tokens)} / {tokens.format(f.output_tokens)}
                    </td>
                    <td>{f.cost_usd === null ? '?' : dollars(f.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </Section>
  )
}

function Password() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [done, setDone] = useState(false)
  const change = useChangePassword()
  const mismatch = again !== '' && again !== next
  const short = next !== '' && next.length < 8
  return (
    <Section title="Password">
      <form
        className={styles.password}
        onSubmit={(event) => {
          event.preventDefault()
          setDone(false)
          change.mutate(
            { current, new: next },
            {
              onSuccess: () => {
                setDone(true)
                setCurrent('')
                setNext('')
                setAgain('')
              },
            },
          )
        }}
      >
        <label>
          Current password
          <input className={styles.input} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>
          New password
          <input className={styles.input} type="password" autoComplete="new-password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>
          New password again
          <input className={styles.input} type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        </label>
        {short && <p className={styles.note}>Use at least 8 characters.</p>}
        {mismatch && <p className={styles.note}>The new passwords don’t match.</p>}
        {change.error && (
          <p role="alert" className={styles.note}>
            {change.error.message}
          </p>
        )}
        {done && <p className={styles.note}>Password changed. Other browsers and devices are signed out.</p>}
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={!current || next.length < 8 || next !== again || change.isPending}>
            Change password
          </button>
        </div>
      </form>
    </Section>
  )
}

function Sessions() {
  const logout = useLogoutEverywhere()
  return (
    <Section title="Sessions">
      <p className={styles.intro}>Signs out every browser and device where you are logged in to kanso, this one too.</p>
      {logout.error && (
        <p role="alert" className={styles.note}>
          {logout.error.message}
        </p>
      )}
      <div className={styles.actions}>
        <button type="button" className={styles.primary} disabled={logout.isPending} onClick={() => logout.mutate()}>
          Log out everywhere
        </button>
      </div>
    </Section>
  )
}
