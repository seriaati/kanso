import { useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react'
import type { Account } from '../../app/accounts'
import { ApiError } from '../../app/api'
import {
  ATTACHMENTS_LIMIT,
  attach,
  detach,
  discardDraft,
  refreshMail,
  saveDraft,
  sendDraft,
  useDraft,
  type Draft,
  type DraftFields,
} from '../../app/compose'
import { fileSize } from '../../app/format'
import { Icon, type IconName } from '../../components/Icon'
import { EmailFrame } from '../reader/EmailFrame'
import { cleanHtml, escapeHtml, safeUrl } from './editor'
import styles from './Composer.module.css'
import { RecipientField } from './RecipientField'

const SAVE_AFTER_MS = 1500 // autosave once typing pauses
const RETRY_SAVE_MS = 5000
const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'

export type Focus = 'to' | 'body'

type Props = {
  draftId: number
  accounts: Account[]
  /** Docked under the thread it replies to; otherwise it fills the email pane. */
  inline?: boolean
  title?: string
  focus?: Focus | null
  /** Closed with Esc or ×: the draft stays (or is dropped if nothing was ever written). */
  onClose: () => void
  /** Sent or discarded: what to tell the user, and the thread the sent mail is in. */
  onFinished: (notice: string, threadId?: number | null) => void
}

/** The composer: From, To/Cc/Bcc with autocomplete, Subject, a plain rich-text body,
 * attachments. It autosaves to the Gmail draft; Cmd/Ctrl+Enter sends; Esc closes. */
export function Composer(props: Props) {
  const draft = useDraft(props.draftId)
  if (draft.isError) return <p className={styles.loadError}>Couldn’t open the draft: {draft.error.message}</p>
  if (!draft.data) return null
  return <Form key={props.draftId} initial={draft.data} {...props} />
}

type Status = { kind: 'idle' | 'saving' | 'saved' | 'error'; text?: string }

function Form({ initial: latest, accounts, inline, title, focus, onClose, onFinished }: Props & { initial: Draft }) {
  // The draft as it was when opened; autosaves update the cached copy, not what is on screen.
  const [initial] = useState(latest)
  const id = initial.id
  const [accountId, setAccountId] = useState(initial.account_id)
  const [to, setTo] = useState(initial.to)
  const [cc, setCc] = useState(initial.cc)
  const [bcc, setBcc] = useState(initial.bcc)
  const [subject, setSubject] = useState(initial.subject)
  const [attachments, setAttachments] = useState(initial.attachments)
  const [uploading, setUploading] = useState<string[]>([])
  const [copies, setCopies] = useState(initial.cc.length + initial.bcc.length > 0)
  const [showQuote, setShowQuote] = useState(false)
  const [status, setStatus] = useState<Status>({ kind: initial.saved ? 'saved' : 'idle' })
  const [sending, setSending] = useState(false)
  const [link, setLink] = useState<string | null>(null) // the link field, while open
  const [version, setVersion] = useState(0) // bumped by every edit

  const rootRef = useRef<HTMLElement>(null)
  const editorRef = useRef<HTMLDivElement>(null)
  const toRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const linkRange = useRef<Range | null>(null)
  const savedVersion = useRef(0)
  const inflight = useRef<Promise<unknown>>(Promise.resolve())
  const flushLatest = useRef(() => {})

  const changed = () => setVersion((v) => v + 1)
  const fields = (): DraftFields => ({ account_id: accountId, to, cc, bcc, subject, body_html: editorRef.current?.innerHTML ?? '' })

  /** Save this version (after any save in flight). A failed save is retried shortly. */
  const save = (keepalive = false) => {
    const target = version
    const values = fields()
    const run = inflight.current.then(async () => {
      if (savedVersion.current >= target) return
      setStatus({ kind: 'saving' })
      try {
        await saveDraft(id, values, keepalive)
        savedVersion.current = Math.max(savedVersion.current, target)
        setStatus({ kind: 'saved' })
      } catch (error) {
        setStatus({ kind: 'error', text: `Not saved: ${(error as Error).message}` })
        // Try again unless kanso refused it (that needs a change first, e.g. a missing file).
        const status = error instanceof ApiError ? error.status : 0
        if (status < 400 || status >= 500) setTimeout(() => setVersion((v) => v + 1), RETRY_SAVE_MS)
      }
    })
    inflight.current = run
    return run
  }

  useEffect(() => {
    if (version === 0) return
    const timer = setTimeout(() => void save(), SAVE_AFTER_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- save reads this render's fields
  }, [version])

  // Unsaved edits go out when the composer unmounts or the page is closed or reloaded.
  useEffect(() => {
    flushLatest.current = () => {
      if (savedVersion.current < version) void save(true)
    }
  })
  useEffect(() => {
    const flush = () => flushLatest.current()
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      flush()
    }
  }, [])

  // The stored body, then the focus the composer was opened for.
  useEffect(() => {
    const editor = editorRef.current
    if (!editor) return
    editor.innerHTML = cleanHtml(initial.body_html)
    if (focus === 'to') toRef.current?.focus()
    else if (focus === 'body') {
      editor.focus()
      getSelection()?.collapse(editor, 0)
    }
    if (focus) rootRef.current?.scrollIntoView({ block: 'nearest' })
  }, [initial.body_html, focus])

  const close = () => {
    if (version === 0 && !initial.saved) void discardDraft(id) // nothing was ever written
    else void save()
    onClose()
  }

  const send = async () => {
    if (sending) return
    if (!to.length && !cc.length && !bcc.length) {
      setStatus({ kind: 'error', text: 'Add at least one recipient.' })
      toRef.current?.focus()
      return
    }
    if (uploading.length) {
      setStatus({ kind: 'error', text: 'Wait for the attachments to finish uploading.' })
      return
    }
    setSending(true)
    setStatus({ kind: 'saving', text: 'Sending…' })
    await inflight.current
    try {
      const result = await sendDraft(id, fields())
      savedVersion.current = Infinity // nothing left to save
      refreshMail()
      onFinished(
        result.status === 'sent' ? 'Message sent.' : 'Gmail hasn’t confirmed the send yet; kanso keeps trying (it never sends twice).',
        result.thread_id,
      )
    } catch (error) {
      setSending(false)
      setStatus({ kind: 'error', text: `Not sent: ${(error as Error).message}` })
    }
  }

  const discard = async () => {
    savedVersion.current = Infinity
    await inflight.current
    try {
      await discardDraft(id)
      refreshMail()
      onFinished('Draft discarded.')
    } catch (error) {
      setStatus({ kind: 'error', text: `Couldn’t discard: ${(error as Error).message}` })
    }
  }

  const upload = async (files: File[]) => {
    let used = attachments.reduce((sum, a) => sum + a.size, 0)
    for (const file of files) {
      if (used + file.size > ATTACHMENTS_LIMIT) {
        setStatus({ kind: 'error', text: `${file.name} doesn’t fit: attachments can add up to 25 MB.` })
        continue
      }
      setUploading((list) => [...list, file.name])
      try {
        const draft = await attach(id, file)
        setAttachments(draft.attachments)
        used = draft.attachments.reduce((sum, a) => sum + a.size, 0)
        changed()
      } catch (error) {
        setStatus({ kind: 'error', text: `Couldn’t attach ${file.name}: ${(error as Error).message}` })
      } finally {
        setUploading((list) => list.filter((_, i) => i !== list.indexOf(file.name)))
      }
    }
  }

  const remove = async (attachmentId: string) => {
    try {
      setAttachments((await detach(id, attachmentId)).attachments)
      changed()
    } catch (error) {
      setStatus({ kind: 'error', text: `Couldn’t remove it: ${(error as Error).message}` })
    }
  }

  const format = (command: string) => {
    editorRef.current?.focus()
    document.execCommand(command)
    changed()
  }

  const startLink = () => {
    const selection = getSelection()
    const inEditor = selection?.rangeCount && editorRef.current?.contains(selection.anchorNode)
    linkRange.current = inEditor ? selection.getRangeAt(0).cloneRange() : null
    setLink(inEditor && !selection.isCollapsed ? '' : 'https://')
  }

  const applyLink = (value: string) => {
    setLink(null)
    const editor = editorRef.current
    if (!editor) return
    editor.focus()
    const selection = getSelection()
    if (selection && linkRange.current) {
      selection.removeAllRanges()
      selection.addRange(linkRange.current)
    }
    const url = safeUrl(value)
    if (!url || !selection) return
    if (selection.isCollapsed) document.execCommand('insertHTML', false, `<a href="${escapeHtml(url)}">${escapeHtml(value.trim())}</a>`)
    else document.execCommand('createLink', false, url)
    changed()
  }

  const onPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault()
    const html = event.clipboardData.getData('text/html')
    if (html) document.execCommand('insertHTML', false, cleanHtml(html))
    else document.execCommand('insertText', false, event.clipboardData.getData('text/plain'))
    changed()
  }

  const onEditorKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!(event.metaKey || event.ctrlKey)) return
    const key = event.key.toLowerCase()
    if (key === 'k') {
      event.preventDefault()
      startLink()
    } else if (key === 'u') event.preventDefault() // no underline: plain rich text only
  }

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.defaultPrevented) return
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      void send()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      close()
    }
  }

  const showSubject = !inline || initial.mode !== 'reply'
  const statusText = status.text ?? { idle: '', saving: 'Saving…', saved: 'Draft saved', error: '' }[status.kind]
  const formatButton = (label: string, title: string, command: string, content: IconName | string) => (
    <button
      type="button"
      className={styles.formatTool}
      aria-label={label}
      title={title}
      onMouseDown={(event) => event.preventDefault()} // keep the selection in the editor
      onClick={() => format(command)}
    >
      {content === 'B' ? <b>B</b> : content === 'I' ? <i>I</i> : <Icon name={content as IconName} size={15} />}
    </button>
  )

  return (
    <section
      ref={rootRef}
      className={`${styles.composer} ${inline ? styles.inline : ''}`}
      aria-label={title ?? 'Reply'}
      onKeyDown={onKeyDown}
    >
      {title && (
        <header className={styles.header}>
          <h2 className={styles.title}>{title}</h2>
          <button type="button" className={styles.tool} aria-label="Close" title="Close and keep the draft (Esc)" onClick={close}>
            <Icon name="close" size={15} />
          </button>
        </header>
      )}

      <div className={styles.fields}>
        {accounts.length > 1 && (
          <div className={styles.row}>
            <label className={styles.rowLabel} htmlFor={`from-${id}`}>
              From
            </label>
            <select
              id={`from-${id}`}
              className={styles.select}
              value={accountId}
              onChange={(event) => {
                setAccountId(Number(event.target.value))
                changed()
              }}
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.email}
                </option>
              ))}
            </select>
          </div>
        )}
        <RecipientField
          label="To"
          value={to}
          inputRef={toRef}
          onChange={(value) => {
            setTo(value)
            changed()
          }}
        >
          {!copies && (
            <button type="button" className={styles.copies} onClick={() => setCopies(true)}>
              Cc Bcc
            </button>
          )}
        </RecipientField>
        {copies && (
          <>
            <RecipientField
              label="Cc"
              value={cc}
              onChange={(value) => {
                setCc(value)
                changed()
              }}
            />
            <RecipientField
              label="Bcc"
              value={bcc}
              onChange={(value) => {
                setBcc(value)
                changed()
              }}
            />
          </>
        )}
        {showSubject && (
          <div className={styles.row}>
            <label className={styles.rowLabel} htmlFor={`subject-${id}`}>
              Subject
            </label>
            <input
              id={`subject-${id}`}
              className={styles.subject}
              value={subject}
              onChange={(event) => {
                setSubject(event.target.value)
                changed()
              }}
            />
          </div>
        )}
      </div>

      <div className={styles.formatBar} role="toolbar" aria-label="Formatting">
        {formatButton('Bold', `Bold (${mod}+B)`, 'bold', 'B')}
        {formatButton('Italic', `Italic (${mod}+I)`, 'italic', 'I')}
        <button
          type="button"
          className={styles.formatTool}
          aria-label="Link"
          title={`Link (${mod}+K)`}
          onMouseDown={(event) => event.preventDefault()}
          onClick={startLink}
        >
          <Icon name="link" size={15} />
        </button>
        {formatButton('Bulleted list', 'Bulleted list', 'insertUnorderedList', 'listBullet')}
        {formatButton('Numbered list', 'Numbered list', 'insertOrderedList', 'listNumber')}
        {link !== null && (
          <input
            className={styles.linkInput}
            aria-label="Link address"
            placeholder="Link address"
            value={link}
            autoFocus
            onChange={(event) => setLink(event.target.value)}
            onBlur={() => setLink(null)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                applyLink(link)
              } else if (event.key === 'Escape') {
                event.preventDefault() // close the field, not the composer
                event.stopPropagation()
                setLink(null)
                editorRef.current?.focus()
              }
            }}
          />
        )}
      </div>

      <div
        ref={editorRef}
        className={styles.editor}
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline="true"
        aria-label="Message"
        onInput={changed}
        onPaste={onPaste}
        onKeyDown={onEditorKey}
      />

      {initial.quote_html && (
        <div className={styles.quote}>
          <button
            type="button"
            className={styles.quoteToggle}
            aria-expanded={showQuote}
            title={showQuote ? 'Hide the quoted message' : 'Show the quoted message'}
            onClick={() => setShowQuote(!showQuote)}
          >
            {initial.mode === 'forward' ? 'Forwarded message' : '···'}
          </button>
          {showQuote && <EmailFrame html={initial.quote_html} lang={null} attachments={[]} showImages={false} />}
        </div>
      )}

      {(attachments.length > 0 || uploading.length > 0) && (
        <ul className={styles.attachments} aria-label="Attachments">
          {attachments.map((a) => (
            <li key={a.id} className={styles.attachment}>
              <span className={styles.attachmentName}>{a.filename}</span>
              <span className={styles.muted}>{fileSize(a.size)}</span>
              <button type="button" aria-label={`Remove ${a.filename}`} title="Remove" onClick={() => void remove(a.id)}>
                ×
              </button>
            </li>
          ))}
          {uploading.map((name, i) => (
            <li key={`${name}-${i}`} className={`${styles.attachment} ${styles.muted}`}>
              Uploading {name}…
            </li>
          ))}
        </ul>
      )}

      <footer className={styles.footer}>
        <button type="button" className={styles.send} onClick={() => void send()} disabled={sending} title={`Send (${mod}+Enter)`}>
          <Icon name="send" size={14} strokeWidth={2} />
          Send
        </button>
        <button type="button" className={styles.tool} aria-label="Attach files" title="Attach files (up to 25 MB)" onClick={() => fileRef.current?.click()}>
          <Icon name="attach" size={16} />
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            const files = Array.from(event.target.files ?? [])
            event.target.value = ''
            void upload(files)
          }}
        />
        <span className={`${styles.status} ${status.kind === 'error' ? styles.statusError : ''}`} role="status">
          {statusText}
        </span>
        {inline && (
          <button type="button" className={styles.tool} aria-label="Close" title="Close and keep the draft (Esc)" onClick={close}>
            <Icon name="close" size={15} />
          </button>
        )}
        <button type="button" className={styles.tool} aria-label="Discard draft" title="Discard draft" onClick={() => void discard()}>
          <Icon name="delete" size={16} />
        </button>
      </footer>
    </section>
  )
}
