import { useState, type ReactNode } from 'react'
import { useDisconnectAccount, type Account } from '../../app/accounts'
import { useAiUsage, useApiKeys, useRemoveApiKey, useSaveApiKey, type KeyProvider } from '../../app/ai'
import type { SyncStatus } from '../../app/mail'
import { usePhone } from '../../app/media'
import { inApp, kanso } from '../../app/native'
import { usePush } from '../../app/notifications'
import { modelName, useChangePassword, useDemoPassword, useLogoutEverywhere, useSettings, useUpdateSettings } from '../../app/settings'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import { Select } from '../../components/Select'
import { Avatar, DisplayOptions } from '../sidebar/AccountSwitcher'
import styles from './Settings.module.css'

type Props = {
  accounts: Account[]
  sync: SyncStatus[]
  ai: boolean
  /** Auto-labeling is on: an Anthropic or a TypeSafe key is set, and it is on here. */
  labeling: boolean
  onMenu?: () => void
}

export function Settings({ accounts, sync, ai, labeling, onMenu }: Props) {
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
          {inApp && <Server />}
          <ApiKeys />
          {ai && <Assistant />}
          <Labeling />
          {(ai || labeling) && <Models ai={ai} labeling={labeling} />}
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
          {inApp
            ? 'This kanso server isn’t set up for notifications in the app: its .env needs FIREBASE_ANDROID_CONFIG (see the setup guide).'
            : 'This browser can’t show notifications from kanso. They need kanso opened over HTTPS; on an iPhone or iPad, add kanso to the Home Screen (Share → Add to Home Screen) and turn them on in the app.'}
        </p>
      ) : (
        <label className={styles.toggle}>
          <input type="checkbox" checked={state === 'on'} disabled={!state || state === 'denied' || pending} onChange={(event) => set(event.target.checked)} />
          <span>
            New inbox mail on this device
            <span className={styles.muted}>
              {state === 'denied'
                ? inApp
                  ? 'Notifications are off for kanso in Android’s app settings. Allow them there, then turn this on.'
                  : 'Notifications are blocked for kanso in this browser’s site settings. Allow them there, then turn this on.'
                : 'A notification for each new unread message in the inbox, with its sender, subject and first line. Turn it on in each browser or installed app where you want them.'}
            </span>
          </span>
        </label>
      )}
      {error && <p className={styles.note}>Couldn’t change notifications: {error}</p>}
    </Section>
  )
}

/** The Android app's kanso server, which it asked for at its first start. */
function Server() {
  const [changing, setChanging] = useState(false)
  return (
    <Section title="Server">
      <div className={styles.row}>
        <div className={styles.text}>
          <span>{location.host}</span>
          <span className={styles.muted}>The kanso this app shows.</span>
        </div>
        <button type="button" className={styles.disconnect} onClick={() => setChanging(true)}>
          Change
        </button>
      </div>
      {changing && (
        <Dialog title="Change server" onClose={() => setChanging(false)}>
          <ChangeServer onDone={() => setChanging(false)} />
        </Dialog>
      )}
    </Section>
  )
}

function ChangeServer({ onDone }: { onDone: () => void }) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const change = () => {
    setPending(true)
    setError(null)
    // On success the app leaves this page for its server screen.
    kanso
      ?.then(({ plugin }) => plugin.changeServer())
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e))
        setPending(false)
      })
  }
  return (
    <div className={styles.confirm}>
      <p className={styles.note}>
        The app forgets {location.host}: it logs out, its notifications on this phone stop, and widgets need their account picked again. Then it asks
        for the new address. Your mail and settings on the server stay as they are.
      </p>
      {error && (
        <p role="alert" className={styles.note}>
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <button type="button" className={styles.cancel} onClick={onDone}>
          Cancel
        </button>
        <button type="button" className={styles.primary} disabled={pending} autoFocus onClick={change}>
          Change server
        </button>
      </div>
    </div>
  )
}

const keyProviders: { id: KeyProvider; name: string; use: string; console: string }[] = [
  { id: 'anthropic', name: 'Anthropic', use: 'Claude: summaries, the assistant and auto-labeling', console: 'https://console.anthropic.com/settings/keys' },
  { id: 'jev', name: 'TypeSafe', use: 'Jev: auto-labeling', console: 'https://console.typesafe.ai' },
]

function ApiKeys() {
  const keys = useApiKeys()
  const [editing, setEditing] = useState<KeyProvider | null>(null)
  const target = keyProviders.find((p) => p.id === editing)
  return (
    <Section title="AI keys">
      <p className={styles.intro}>Your own API keys turn on the AI features. Each is checked with its provider, then stored encrypted.</p>
      <ul className={styles.list}>
        {keyProviders.map((provider) => {
          const hint = keys.data?.[provider.id]
          return (
            <li key={provider.id} className={styles.row}>
              <div className={styles.text}>
                <span className={styles.name}>{provider.name}</span>
                <span className={styles.muted}>{provider.use}</span>
                <span className={styles.muted}>{hint ? `Key ending in ${hint}` : 'No key'}</span>
              </div>
              <button type="button" className={styles.disconnect} disabled={!keys.data} onClick={() => setEditing(provider.id)}>
                {hint ? 'Change' : 'Add key'}
              </button>
            </li>
          )
        })}
      </ul>
      {keys.isError && <p className={styles.note}>Couldn’t load the keys: {keys.error.message}</p>}
      {target && (
        <Dialog title={`${target.name} API key`} onClose={() => setEditing(null)}>
          <KeyEditor provider={target} saved={Boolean(keys.data?.[target.id])} onDone={() => setEditing(null)} />
        </Dialog>
      )}
    </Section>
  )
}

function KeyEditor({ provider, saved, onDone }: { provider: (typeof keyProviders)[number]; saved: boolean; onDone: () => void }) {
  const [key, setKey] = useState('')
  const save = useSaveApiKey()
  const remove = useRemoveApiKey()
  const busy = save.isPending || remove.isPending
  const error = save.error ?? remove.error
  return (
    <form
      className={styles.confirm}
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate({ provider: provider.id, key }, { onSuccess: onDone })
      }}
    >
      <p className={styles.note}>
        Create one in the{' '}
        <a href={provider.console} target="_blank" rel="noreferrer">
          {provider.name} console
        </a>
        .{saved && ' A new key replaces the saved one.'}
      </p>
      <input
        className={styles.input}
        type="password"
        aria-label={`${provider.name} API key`}
        placeholder="Paste the key"
        autoComplete="off"
        spellCheck={false}
        autoFocus
        value={key}
        onChange={(event) => setKey(event.target.value)}
      />
      {error && (
        <p role="alert" className={styles.note}>
          {error.message}
        </p>
      )}
      <div className={styles.actions}>
        {saved && (
          <button type="button" className={styles.cancel} disabled={busy} onClick={() => remove.mutate(provider.id, { onSuccess: onDone })}>
            Remove key
          </button>
        )}
        <button type="submit" className={styles.primary} disabled={!key.trim() || busy}>
          {save.isPending ? 'Checking…' : 'Save'}
        </button>
      </div>
    </form>
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
            changes at once; each one still shows in the chat with Undo. Deleting a label always asks. It never sends or deletes mail.
          </span>
        </span>
      </label>
      {update.error && <p className={styles.note}>Couldn’t save: {update.error.message}</p>}
    </Section>
  )
}

// Shown once there is a key for it, so it can be turned back on.
function Labeling() {
  const settings = useSettings()
  const update = useUpdateSettings()
  const data = settings.data
  if (!data?.labeling_models.length) return null
  return (
    <Section title="AI labeling">
      <label className={styles.toggle}>
        <input type="checkbox" checked={data.ai_labeling} onChange={(event) => update.mutate({ ai_labeling: event.target.checked })} />
        <span>
          Label new inbox mail with AI
          <span className={styles.muted}>
            AI puts each new inbox thread under one of your labels, going by their descriptions, and leaves it unlabeled when none
            clearly fits. Mail you label yourself stays as you left it.
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

function Models({ ai, labeling }: { ai: boolean; labeling: boolean }) {
  const settings = useSettings()
  const update = useUpdateSettings()
  const data = settings.data
  // Chat and summaries need an Anthropic key; auto-labeling can run on Jev.
  const shown = modelFeatures.filter((f) => (f.key === 'labeling_model' ? labeling : ai))
  const choices = (key: (typeof modelFeatures)[number]['key']) => (key === 'labeling_model' ? data?.labeling_models : data?.models) ?? []
  return (
    <Section title="AI models">
      <div className={styles.models}>
        {shown.map((feature) => (
          <div key={feature.key} className={styles.model}>
            <span>{feature.name}</span>
            <Select
              className={styles.picker}
              label={feature.name}
              value={data?.[feature.key] ?? ''}
              options={choices(feature.key).map((model) => ({ value: model, label: modelName(model) }))}
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
const features: Record<string, string> = { labeling: 'Labeling', summary: 'Summaries', assistant: 'Assistant', suggestions: 'Label suggestions', icons: 'Label icons' }
const dollars = (cost: number) => (cost > 0 && cost < 0.01 ? '< $0.01' : `$${cost.toFixed(2)}`)

function Usage() {
  const usage = useAiUsage(true)
  const data = usage.data
  return (
    <Section title="AI usage">
      {usage.isError && <p className={styles.note}>Couldn’t load the usage: {usage.error.message}</p>}
      {data?.key_rejected && (
        <p className={`${styles.intro} ${styles.problem}`} role="status">
          Anthropic API key was rejected; replace it under AI keys.
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
