import { useState } from 'react'
import type { Suggestion } from '../../app/cleanup'
import { useCreateLabel } from '../../app/labels'
import type { NamedLabel } from '../../app/mail'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import settings from '../settings/Settings.module.css'
import { LabelEditor } from '../sidebar/LabelEditor'
import styles from './Cleanup.module.css'

type Props = {
  accountId: number
  suggestions: Suggestion[]
  /** The account's labels: a suggestion already among them shows as added. */
  labels: NamedLabel[]
  onClose: () => void
}

// Each suggestion is added as is, or edited first in the label editor. Added labels sort new
// mail with AI.
export function Suggestions({ accountId, suggestions, labels, onClose }: Props) {
  const [editing, setEditing] = useState<number | null>(null)
  const [added, setAdded] = useState<Set<number>>(new Set())
  const create = useCreateLabel()
  const [adding, setAdding] = useState(false)
  const exists = (name: string) => labels.some((l) => l.name.toLowerCase() === name.toLowerCase())
  const isAdded = (i: number) => added.has(i) || exists(suggestions[i].name)
  const left = suggestions.map((_, i) => i).filter((i) => !isAdded(i))
  const markAdded = (i: number) => setAdded((set) => new Set(set).add(i))

  // Errors show below the list. Returns whether it worked.
  const add = async (i: number) => {
    const s = suggestions[i]
    try {
      await create.mutateAsync({
        name: s.name,
        color: s.color,
        account_id: accountId,
        description: s.description,
        ai_managed: true,
        is_bundle: s.bundle,
      })
      markAdded(i)
      return true
    } catch {
      return false
    }
  }
  const addSome = async (indexes: number[]) => {
    setAdding(true)
    for (const i of indexes) if (!(await add(i))) break
    setAdding(false)
  }

  const edited = editing === null ? undefined : suggestions[editing]
  if (editing !== null && edited)
    return (
      <Dialog title="New label" onClose={() => setEditing(null)}>
        <LabelEditor
          draft={{ name: edited.name, color: edited.color, description: edited.description, aiManaged: true, isBundle: edited.bundle }}
          accountId={accountId}
          ai
          onDone={(change) => {
            if (change?.created) markAdded(editing)
            setEditing(null)
          }}
        />
      </Dialog>
    )

  return (
    <Dialog title="Suggested labels" wide onClose={onClose}>
      {suggestions.length === 0 ? (
        <p className={settings.intro}>Claude had nothing to suggest beyond your labels.</p>
      ) : (
        <ul className={`${settings.list} ${styles.suggestions}`}>
          {suggestions.map((s, i) => (
            <li key={s.name} className={settings.row}>
              <span className={settings.dot} style={{ background: s.color }} aria-hidden="true" />
              <div className={settings.text}>
                <span className={settings.name}>
                  {s.name}
                  {s.bundle && <span className={settings.flags}> · Bundled</span>}
                </span>
                <span className={settings.muted}>{s.description}</span>
                {s.examples.length > 0 && <span className={styles.examples}>e.g. {s.examples.join(' · ')}</span>}
              </div>
              {isAdded(i) ? (
                <span className={styles.done}>Added</span>
              ) : (
                <div className={styles.rowActions}>
                  <button
                    type="button"
                    className={settings.iconButton}
                    aria-label={`Edit ${s.name} before adding`}
                    title="Edit before adding"
                    disabled={adding}
                    onClick={() => setEditing(i)}
                  >
                    <Icon name="compose" size={15} />
                  </button>
                  <button type="button" className={settings.disconnect} disabled={adding} onClick={() => void addSome([i])}>
                    Add
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {create.error && (
        <p role="alert" className={settings.note}>
          {create.error.message}
        </p>
      )}
      <div className={`${settings.actions} ${styles.footer}`}>
        <button type="button" className={settings.cancel} onClick={onClose}>
          Close
        </button>
        {left.length > 0 && (
          <button type="button" className={settings.primary} disabled={adding} onClick={() => void addSome(left)}>
            {left.length === suggestions.length ? 'Add all' : `Add the other ${left.length}`}
          </button>
        )}
      </div>
    </Dialog>
  )
}
