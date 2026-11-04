import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import type { Account } from '../../app/accounts'
import { refreshAll } from '../../app/actions'
import { queryClient } from '../../app/api'
import { inboxAction, suggestLabels, useInboxCount, useSenders, type InboxScope, type Sender, type Suggestion } from '../../app/cleanup'
import type { NamedLabel } from '../../app/mail'
import { modelName, useSettings } from '../../app/settings'
import { DatePicker } from '../../components/DatePicker'
import { Icon } from '../../components/Icon'
import { Select } from '../../components/Select'
import settings from '../settings/Settings.module.css'
import { useLabelRun } from '../../app/ai'
import styles from './Cleanup.module.css'
import { Suggestions } from './Suggestions'

type Notify = (text: string, undo?: () => void) => void

type Props = {
  /** The account in view (undefined: none connected) and its labels. */
  account: Account | undefined
  labels: NamedLabel[]
  /** An Anthropic key is set: label suggestions. */
  ai: boolean
  /** AI labeling is on (Claude or Jev). */
  labeling: boolean
  onMenu?: () => void
  onNotice: Notify
  /** Undoes an action's outbox entries and clears its notice. */
  onUndo: (entries: number[]) => void
  onUnsubscribe: (threadId: number) => void
}

// For an inbox from before kanso, in the order that works best: labels first, then archive what
// is old, so AI labels (and is paid for) only what is left.
export function Cleanup({ account, labels, ai, labeling, onMenu, onNotice, onUndo, onUnsubscribe }: Props) {
  return (
    <section className={settings.settings} aria-label="Clean up">
      <header className={settings.header}>
        {onMenu && (
          <button type="button" className={settings.menu} aria-label="Mailboxes and labels" onClick={onMenu}>
            <Icon name="menu" size={18} />
          </button>
        )}
        <h1 className={settings.title}>Clean up</h1>
      </header>
      <div className={settings.scroll}>
        <div className={settings.page}>
          {account ? (
            <>
              <p className={`${settings.intro} ${styles.lead}`}>
                Get {account.email}’s inbox to zero: set up labels, archive what’s old, then let AI sort the rest. Nothing is deleted;
                archived mail stays in Done and in search.
              </p>
              <SuggestStep account={account} labels={labels} ai={ai} />
              <ArchiveStep account={account} onNotice={onNotice} onUndo={onUndo} />
              <LabelStep account={account} labels={labels} labeling={labeling} onNotice={onNotice} />
              <SendersStep account={account} onNotice={onNotice} onUndo={onUndo} onUnsubscribe={onUnsubscribe} />
            </>
          ) : (
            <p className={settings.intro}>
              No Gmail account connected yet. <Link to="/settings">Connect one in Settings</Link>.
            </p>
          )}
        </div>
      </div>
    </section>
  )
}

function Step({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={settings.section}>
      <h2 className={settings.heading}>{title}</h2>
      {children}
    </section>
  )
}

const needsKey = (what: string) => (
  <p className={settings.note}>
    {what} <Link to="/settings">Add one in Settings</Link>.
  </p>
)

// Dates are the user's local days, yyyy-mm-dd, as DatePicker takes them.
const pad = (n: number) => String(n).padStart(2, '0')
const toDay = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
const today = () => toDay(new Date())
function monthsAgo(months: number): string {
  const date = new Date()
  date.setMonth(date.getMonth() - months)
  return toDay(date)
}
/** The start of a day, or of the day after it. */
function startOf(day: string, next = false): string {
  const date = new Date(`${day}T00:00`)
  if (next) date.setDate(date.getDate() + 1)
  return date.toISOString()
}

const threadCount = (n: number) => `${n.toLocaleString()} ${n === 1 ? 'thread' : 'threads'}`

// What changed: everything shown here, and the inbox.
function refresh() {
  refreshAll()
  void queryClient.invalidateQueries({ queryKey: ['label-inbox'] })
}

/** Runs an action on inbox threads and says so, with an undo. */
function useInboxAction(onNotice: Notify, onUndo: (entries: number[]) => void) {
  const [busy, setBusy] = useState(false)
  const run = (action: 'done' | 'spam', scope: InboxScope, said: string) => {
    setBusy(true)
    inboxAction(action, scope)
      .then(({ entries }) => {
        // No undo for spam, as anywhere in kanso: it would leave the sender blocked.
        const back = action === 'spam' || entries.length === 0 ? undefined : () => onUndo(entries)
        onNotice(said, back)
      })
      .catch((error: Error) => onNotice(`Couldn’t do that: ${error.message}`))
      .finally(() => {
        setBusy(false)
        refresh()
      })
  }
  return { run, busy }
}

function SuggestStep({ account, labels, ai }: { account: Account; labels: NamedLabel[]; ai: boolean }) {
  const settingsData = useSettings().data
  const models = settingsData?.models ?? []
  const fallback = settingsData && models.includes(settingsData.labeling_model) ? settingsData.labeling_model : models[0]
  const [picked, setPicked] = useState<string | null>(null)
  const model = picked ?? fallback
  const [state, setState] = useState<{ busy: boolean; error?: string; suggestions?: Suggestion[] }>({ busy: false })

  const suggest = () => {
    if (!model) return
    setState({ busy: true })
    suggestLabels(account.id, model)
      .then((suggestions) => setState({ busy: false, suggestions }))
      .catch((error: Error) => setState({ busy: false, error: error.message }))
  }

  return (
    <Step title="1. Set up labels">
      {ai ? (
        <>
          <p className={settings.note}>
            Claude reads a sample of your unlabeled inbox mail (senders, subjects and a short snippet) and suggests labels to sort it
            into.
          </p>
          <div className={styles.controls}>
            <Select
              className={settings.picker}
              label="Model"
              value={model ?? ''}
              options={models.map((m) => ({ value: m, label: modelName(m) }))}
              disabled={!model}
              onChange={setPicked}
            />
            <button type="button" className={settings.primary} disabled={!model || state.busy} onClick={suggest}>
              {state.busy ? 'Suggesting…' : 'Suggest labels'}
            </button>
          </div>
          {state.error && (
            <p role="alert" className={settings.note}>
              {state.error}
            </p>
          )}
          {state.suggestions && (
            <Suggestions
              accountId={account.id}
              suggestions={state.suggestions}
              labels={labels}
              onClose={() => setState({ busy: false })}
            />
          )}
        </>
      ) : (
        needsKey('Label suggestions need an Anthropic API key.')
      )}
    </Step>
  )
}

type ActionProps = { account: Account; onNotice: Notify; onUndo: (entries: number[]) => void }

function ArchiveStep({ account, onNotice, onUndo }: ActionProps) {
  const [day, setDay] = useState(monthsAgo(3))
  const before = day ? startOf(day) : null
  const count = useInboxCount(account.id, before).data?.threads
  const { run, busy } = useInboxAction(onNotice, onUndo)
  return (
    <Step title="2. Archive old mail">
      <p className={settings.note}>Mark done every inbox thread whose latest message is older than a day you pick.</p>
      <div className={styles.controls}>
        <span className={styles.field}>
          Older than
          <DatePicker label="Older than" value={day} max={today()} onChange={setDay} />
        </span>
        <button
          type="button"
          className={settings.primary}
          disabled={!before || !count || busy}
          onClick={() => before && count && run('done', { account_id: account.id, before }, `${threadCount(count)} done.`)}
        >
          Archive {count === undefined ? '' : threadCount(count)}
        </button>
      </div>
    </Step>
  )
}

const cost = (usd: number) => (usd < 0.01 ? 'Costs under $0.01.' : `Costs about $${usd.toFixed(2)}.`)

type LabelStepProps = { account: Account; labels: NamedLabel[]; labeling: boolean; onNotice: Notify }

function LabelStep({ account, labels, labeling, onNotice }: LabelStepProps) {
  const sorting = labels.filter((l) => l.aiManaged)
  return (
    <Step title="3. Label the rest">
      {!labeling ? (
        needsKey('AI labeling needs an Anthropic or a TypeSafe API key.')
      ) : sorting.length === 0 ? (
        <p className={settings.note}>
          No label sorts mail with AI yet. Add suggested labels above, or turn on “Label new mail with AI” for a label in{' '}
          <Link to={`/labels?account=${account.id}`}>Labels</Link>.
        </p>
      ) : (
        <LabelRange account={account} sorting={sorting} onNotice={onNotice} />
      )}
    </Step>
  )
}

function LabelRange({ account, sorting, onNotice }: { account: Account; sorting: NamedLabel[]; onNotice: Notify }) {
  const [from, setFrom] = useState(monthsAgo(3))
  const [to, setTo] = useState('')
  const range = { after: from ? startOf(from) : undefined, before: to ? startOf(to, true) : undefined }
  const { status, running, starting, start } = useLabelRun(account.id, range, onNotice)
  const eligible = status?.eligible
  return (
    <>
      <p className={settings.note}>
        AI sorts the unlabeled inbox threads from these days into {sorting.map((l) => l.name).join(', ')}. Mail it isn’t sure about stays
        unlabeled.
      </p>
      <div className={styles.controls}>
        <span className={styles.field}>
          From
          <DatePicker label="From" value={from} max={to || today()} clearable placeholder="The start" onChange={setFrom} />
        </span>
        <span className={styles.field}>
          to
          <DatePicker label="To" value={to} min={from || undefined} max={today()} clearable placeholder="Today" onChange={setTo} />
        </span>
        {running ? (
          <span className={settings.muted} role="status">
            Labeling… {running.total - running.pending} of {running.total}
          </span>
        ) : (
          <button type="button" className={settings.primary} disabled={!eligible || starting} onClick={() => start()}>
            Label {eligible === undefined ? '' : threadCount(eligible)}
          </button>
        )}
      </div>
      {!running && eligible && status?.estimate_usd != null ? <p className={settings.note}>{cost(status.estimate_usd)}</p> : null}
    </>
  )
}

function SendersStep({ account, onNotice, onUndo, onUnsubscribe }: ActionProps & { onUnsubscribe: (threadId: number) => void }) {
  const data = useSenders(account.id).data
  const { run, busy } = useInboxAction(onNotice, onUndo)
  const scope = (sender: Sender): InboxScope => ({ account_id: account.id, sender: sender.email, before: data?.as_of })
  const who = (sender: Sender) => sender.name || sender.email
  return (
    <Step title="Clear by sender">
      <p className={settings.note}>The senders with the most threads in your inbox.</p>
      {data && data.senders.length === 0 && <p className={settings.note}>No sender has more than one thread in your inbox.</p>}
      <ul className={settings.list}>
        {data?.senders.map((sender) => (
          <li key={sender.email} className={`${settings.row} ${styles.sender}`}>
            <div className={settings.text}>
              <span className={settings.name}>
                {who(sender)}
                <span className={settings.flags}> · {threadCount(sender.threads)}</span>
              </span>
              {sender.name && <span className={settings.muted}>{sender.email}</span>}
            </div>
            <div className={styles.senderActions}>
              <button
                type="button"
                className={settings.disconnect}
                disabled={busy}
                title={`Mark done every inbox thread from ${who(sender)}`}
                onClick={() => run('done', scope(sender), `${threadCount(sender.threads)} from ${who(sender)} done.`)}
              >
                Done all
              </button>
              <button type="button" className={settings.disconnect} onClick={() => onUnsubscribe(sender.thread_id)}>
                Unsubscribe
              </button>
              <button
                type="button"
                className={settings.disconnect}
                disabled={busy}
                title="Move their inbox mail to Spam, and their future mail too"
                onClick={() => run('spam', scope(sender), `${who(sender)} blocked; ${threadCount(sender.threads)} moved to Spam.`)}
              >
                Block
              </button>
            </div>
          </li>
        ))}
      </ul>
    </Step>
  )
}
