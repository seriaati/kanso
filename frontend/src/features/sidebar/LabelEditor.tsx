import { useState } from 'react'
import { useCreateLabel, useDeleteLabel, useUpdateLabel } from '../../app/labels'
import { labelColors, type NamedLabel } from '../../app/mail'
import styles from './LabelEditor.module.css'

type Props = {
  label?: NamedLabel
  accountId: number
  ai: boolean
  onDone: (change?: { renamed?: string; deleted?: boolean }) => void
}

// Shown in a Dialog. Name and color go to Gmail; the other settings are kanso's own.
export function LabelEditor({ label, accountId, ai, onDone }: Props) {
  const [name, setName] = useState(label?.name ?? '')
  const [color, setColor] = useState(label?.color ?? labelColors[0].value)
  const [description, setDescription] = useState(label?.description ?? '')
  const [aiManaged, setAiManaged] = useState(label?.aiManaged ?? false)
  const [isBundle, setIsBundle] = useState(label?.isBundle ?? false)
  const [confirming, setConfirming] = useState(false)
  const create = useCreateLabel()
  const update = useUpdateLabel()
  const remove = useDeleteLabel()
  const error = create.error ?? update.error ?? remove.error
  const busy = create.isPending || update.isPending || remove.isPending
  const trimmed = name.trim()

  const save = () => {
    const settings = { description: description.trim(), ai_managed: aiManaged, is_bundle: isBundle }
    if (!label) {
      create.mutate({ name: trimmed, color, account_id: accountId, ...settings }, { onSuccess: () => onDone() })
      return
    }
    const patch = {
      name: trimmed !== label.name ? trimmed : undefined,
      color: color !== label.color ? color : undefined,
      description: settings.description !== label.description ? settings.description : undefined,
      ai_managed: aiManaged !== label.aiManaged ? aiManaged : undefined,
      is_bundle: isBundle !== label.isBundle ? isBundle : undefined,
    }
    if (Object.values(patch).every((value) => value === undefined)) return onDone()
    update.mutate({ id: label.id, ...patch }, { onSuccess: () => onDone({ renamed: patch.name }) })
  }

  return (
    <form
      className={styles.editor}
      aria-label={label ? `Edit label ${label.name}` : 'New label'}
      onSubmit={(event) => {
        event.preventDefault()
        if (trimmed) save()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onDone()
      }}
    >
      <input
        className={styles.input}
        aria-label="Label name"
        placeholder="Label name"
        maxLength={225}
        autoFocus
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <div className={styles.swatches} role="radiogroup" aria-label="Color">
        {labelColors.map((c) => (
          <button
            key={c.value}
            type="button"
            role="radio"
            aria-checked={c.value === color}
            aria-label={c.name}
            title={c.name}
            className={styles.swatch}
            style={{ background: c.value }}
            onClick={() => setColor(c.value)}
          />
        ))}
      </div>
      {ai && (
        <textarea
          className={styles.description}
          aria-label="What belongs under this label"
          placeholder="What belongs here, e.g. bills, statements and payment reminders"
          maxLength={500}
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
        />
      )}
      {ai && (
        <label className={styles.toggle} title="Claude puts new inbox mail that fits the description under this label">
          <input type="checkbox" checked={aiManaged} onChange={(event) => setAiManaged(event.target.checked)} />
          Label new mail with AI
        </label>
      )}
      <label className={styles.toggle} title="Its inbox mail shows as one collapsed row at the bottom of the inbox">
        <input type="checkbox" checked={isBundle} onChange={(event) => setIsBundle(event.target.checked)} />
        Bundle in the inbox
      </label>
      {confirming && <p className={styles.hint}>Removes the label in Gmail too. The mail stays.</p>}
      {error && (
        <p role="alert" className={styles.hint}>
          {error.message}
        </p>
      )}
      <div className={styles.actions}>
        {label && (
          <button
            type="button"
            className={styles.delete}
            disabled={busy}
            onClick={() => (confirming ? remove.mutate(label.id, { onSuccess: () => onDone({ deleted: true }) }) : setConfirming(true))}
          >
            {confirming ? 'Confirm delete' : 'Delete'}
          </button>
        )}
        <button type="button" className={styles.cancel} onClick={() => onDone()}>
          Cancel
        </button>
        <button type="submit" className={styles.save} disabled={!trimmed || busy}>
          {label ? 'Save' : 'Create'}
        </button>
      </div>
    </form>
  )
}

export function DeleteLabel({ label, onDone }: { label: NamedLabel; onDone: (deleted: boolean) => void }) {
  const remove = useDeleteLabel()
  return (
    <div className={styles.editor}>
      <p className={styles.hint}>
        “{label.name}” is removed in Gmail too. The mail stays.
      </p>
      {remove.error && (
        <p role="alert" className={styles.hint}>
          {remove.error.message}
        </p>
      )}
      <div className={styles.actions}>
        <button type="button" className={styles.cancel} onClick={() => onDone(false)}>
          Cancel
        </button>
        <button
          type="button"
          className={styles.save}
          disabled={remove.isPending}
          autoFocus
          onClick={() => remove.mutate(label.id, { onSuccess: () => onDone(true) })}
        >
          Delete
        </button>
      </div>
    </div>
  )
}
