import { useState } from 'react'
import { useCreateLabel, useDeleteLabel, useUpdateLabel } from '../../app/labels'
import { labelColors, type NamedLabel } from '../../app/mail'
import styles from './LabelEditor.module.css'

type Props = {
  /** The label to edit, or undefined to create one. */
  label?: NamedLabel
  /** Accounts a new label is created in: the one selected, or all of them. */
  accountIds: number[]
  /** AI is on: the description and AI-managed settings show. */
  ai: boolean
  onDone: (change?: { renamed?: string; deleted?: boolean }) => void
  /** In a dialog (the sidebar's): no card of its own, and no Delete (the label's menu has it). */
  plain?: boolean
}

/** In a dialog from the sidebar, or inline in Settings: name, color, kanso's own settings (description and AI-managed with AI
 * on, bundle always), and Delete for an existing label. Name and color changes go to Gmail. */
export function LabelEditor({ label, accountIds, ai, onDone, plain }: Props) {
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
      create.mutate({ name: trimmed, color, account_ids: accountIds, ...settings }, { onSuccess: () => onDone() })
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
    update.mutate({ ids: label.ids, ...patch }, { onSuccess: () => onDone({ renamed: patch.name }) })
  }

  return (
    <form
      className={`${styles.editor} ${plain ? styles.plain : ''}`}
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
        {label && !plain && (
          <button
            type="button"
            className={styles.delete}
            disabled={busy}
            onClick={() => (confirming ? remove.mutate(label.ids, { onSuccess: () => onDone({ deleted: true }) }) : setConfirming(true))}
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

/** Delete a label (from the sidebar's label menu): asks first. */
export function DeleteLabel({ label, onDone }: { label: NamedLabel; onDone: (deleted: boolean) => void }) {
  const remove = useDeleteLabel()
  return (
    <div className={`${styles.editor} ${styles.plain}`}>
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
          onClick={() => remove.mutate(label.ids, { onSuccess: () => onDone(true) })}
        >
          Delete
        </button>
      </div>
    </div>
  )
}
