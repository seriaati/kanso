import { useEffect, useEffectEvent, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { ApiError } from '../../app/api'
import {
  ATTACHMENTS_LIMIT,
  IMAGE_TYPES,
  attach,
  attachmentUrl,
  detach,
  discardDraft,
  refreshMail,
  saveDraft,
  sendDraft,
  useDraft,
  type Draft,
  type DraftAttachment,
  type DraftFields,
} from '../../app/compose'
import { fileSize } from '../../app/format'
import { Icon, type IconName } from '../../components/Icon'
import { EmailFrame } from '../reader/EmailFrame'
import { bodyHtml, cleanHtml, escapeHtml, imageHtml, safeUrl, ZWSP } from './editor'
import { applyMarkdown } from './markdown'
import styles from './Composer.module.css'
import { RecipientField } from './RecipientField'

const SAVE_AFTER_MS = 1500
const RETRY_SAVE_MS = 5000
const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'

export type Focus = 'to' | 'body'

type Props = {
  draftId: number
  /** At the bottom of its thread, with no close: leaving the thread keeps the draft. Otherwise
   * it fills the email pane, or with `onToggleMinimize` is a window. */
  docked?: boolean
  title?: string
  minimized?: boolean
  onToggleMinimize?: () => void
  focus?: Focus | null
  /** Set when it should close as Close does (Back on a phone, App.tsx). */
  closing?: boolean
  onClose?: () => void
  onFinished: (notice: string, threadId?: number | null) => void
}

export function Composer(props: Props) {
  const draft = useDraft(props.draftId)
  if (draft.isError) return <p className={styles.loadError}>Couldn’t open the draft: {draft.error.message}</p>
  if (!draft.data) return null
  return <Form key={props.draftId} initial={draft.data} {...props} />
}

type Status = { kind: 'idle' | 'saving' | 'saved' | 'error'; text?: string }

const isImage = (file: File) => IMAGE_TYPES.includes(file.type)

function Form({ initial: latest, docked, title, minimized, onToggleMinimize, focus, closing, onClose, onFinished }: Props & { initial: Draft }) {
  // Autosaves update the cached draft, not what is on screen.
  const [initial] = useState(latest)
  const id = initial.id
  const [to, setTo] = useState(initial.to)
  const [cc, setCc] = useState(initial.cc)
  const [bcc, setBcc] = useState(initial.bcc)
  const [subject, setSubject] = useState(initial.subject)
  const [attachments, setAttachments] = useState(initial.attachments)
  const [uploading, setUploading] = useState<string[]>([])
  const [copies, setCopies] = useState(initial.cc.length + initial.bcc.length > 0)
  // Docked replies show who it goes to; a click opens the fields.
  const [fieldsOpen, setFieldsOpen] = useState(!docked || initial.mode !== 'reply' || initial.to.length === 0)
  const [showQuote, setShowQuote] = useState(false)
  const [status, setStatus] = useState<Status>({ kind: initial.saved ? 'saved' : 'idle' })
  const [sending, setSending] = useState(false)
  const [link, setLink] = useState<string | null>(null) // the link field, while open
  const [version, setVersion] = useState(0)

  const rootRef = useRef<HTMLElement>(null)
  const editorRef = useRef<HTMLDivElement>(null)
  const toRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const imageRef = useRef<HTMLInputElement>(null)
  const linkRange = useRef<Range | null>(null)
  const imageRange = useRef<Range | null>(null)
  const savedVersion = useRef(0)
  const inflight = useRef<Promise<unknown>>(Promise.resolve())
  const flushLatest = useRef(() => {})

  const changed = () => setVersion((v) => v + 1)
  const fields = (): DraftFields => ({
    account_id: initial.account_id,
    to,
    cc,
    bcc,
    subject,
    body_html: editorRef.current ? bodyHtml(editorRef.current) : '',
  })

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
        // A 4xx needs a change first (e.g. a missing file), so only other failures are retried.
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

  useEffect(() => {
    const editor = editorRef.current
    if (!editor) return
    const images = new Map(initial.attachments.filter((a) => a.content_id).map((a) => [a.content_id, attachmentUrl(id, a.id)]))
    editor.innerHTML = cleanHtml(initial.body_html, (cid) => images.get(cid) ?? null)
    if (focus === 'to') toRef.current?.focus()
    else if (focus === 'body') {
      editor.focus()
      getSelection()?.collapse(editor, 0)
    }
    if (focus && !docked) rootRef.current?.scrollIntoView({ block: 'nearest' })
  }, [id, initial.body_html, initial.attachments, focus, docked])

  const close = () => {
    if (version === 0 && !initial.saved) void discardDraft(id)
    else void save()
    onClose?.()
  }
  const closeNow = useEffectEvent(close)
  useEffect(() => {
    if (closing) closeNow()
  }, [closing])

  const send = async () => {
    if (sending) return
    if (!to.length && !cc.length && !bcc.length) {
      setStatus({ kind: 'error', text: 'Add at least one recipient.' })
      setFieldsOpen(true)
      requestAnimationFrame(() => toRef.current?.focus())
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
        result.status === 'sent' ? 'Message sent.' : 'Gmail hasn’t confirmed the send yet; Kanso keeps trying (it never sends twice).',
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

  // What the attachments add up to, also between two uploads of one paste.
  const used = useRef(0)
  useEffect(() => {
    used.current = attachments.reduce((sum, a) => sum + a.size, 0)
  }, [attachments])
  const uploadOne = async (file: File, inline: boolean): Promise<DraftAttachment | null> => {
    if (used.current + file.size > ATTACHMENTS_LIMIT) {
      setStatus({ kind: 'error', text: `${file.name} doesn’t fit: attachments and images can add up to 25 MB.` })
      return null
    }
    setUploading((list) => [...list, file.name])
    try {
      const draft = await attach(id, file, inline)
      setAttachments(draft.attachments)
      used.current = draft.attachments.reduce((sum, a) => sum + a.size, 0)
      return draft.attachments.at(-1) ?? null // the server adds it last
    } catch (error) {
      setStatus({ kind: 'error', text: `Couldn’t add ${file.name}: ${(error as Error).message}` })
      return null
    } finally {
      setUploading((list) => list.filter((_, i) => i !== list.indexOf(file.name)))
    }
  }

  const upload = async (files: File[]) => {
    for (const file of files) if (await uploadOne(file, false)) changed()
  }

  const caret = () => {
    const selection = getSelection()
    return selection?.rangeCount && editorRef.current?.contains(selection.anchorNode) ? selection.getRangeAt(0).cloneRange() : null
  }

  /** Each goes where the caret was when it was added, one after another. */
  const insertImages = async (files: File[], at: Range | null) => {
    let range = at
    for (const file of files) {
      const added = await uploadOne(file, true)
      const editor = editorRef.current
      if (!added?.content_id || !editor) continue
      editor.focus()
      const selection = getSelection()
      if (!selection) continue
      selection.removeAllRanges()
      if (range && editor.contains(range.startContainer)) selection.addRange(range)
      else {
        selection.selectAllChildren(editor)
        selection.collapseToEnd()
      }
      document.execCommand('insertHTML', false, imageHtml(attachmentUrl(id, added.id), added.content_id, file.name))
      range = selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null
      changed()
    }
  }

  const addFiles = (files: File[], at: Range | null) => {
    const images = files.filter(isImage)
    void insertImages(images, at)
    void upload(files.filter((f) => !isImage(f)))
  }

  const remove = async (attachmentId: string) => {
    try {
      setAttachments((await detach(id, attachmentId)).attachments)
      changed()
    } catch (error) {
      setStatus({ kind: 'error', text: `Couldn’t remove it: ${(error as Error).message}` })
    }
  }

  const format = (command: string, value?: string) => {
    editorRef.current?.focus()
    document.execCommand(command, false, value)
    changed()
  }

  const inside = (tag: string) => {
    const node = getSelection()?.anchorNode
    const element = node instanceof Element ? node : node?.parentElement
    const found = element?.closest(tag)
    return !!found && !!editorRef.current?.contains(found)
  }

  // Selected text becomes inline code; with nothing selected, the line becomes a code block.
  const code = () => {
    const selection = getSelection()
    if (selection && !selection.isCollapsed && !inside('pre')) format('insertHTML', `<code>${escapeHtml(selection.toString())}</code>`)
    else format('formatBlock', inside('pre') ? '<div>' : '<pre>')
  }

  const quote = () => (inside('blockquote') ? format('outdent') : format('formatBlock', '<blockquote>'))

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
    const files = Array.from(event.clipboardData.files)
    if (files.length) return addFiles(files, caret())
    const html = event.clipboardData.getData('text/html')
    if (html) document.execCommand('insertHTML', false, cleanHtml(html))
    else document.execCommand('insertText', false, event.clipboardData.getData('text/plain'))
    changed()
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    const files = Array.from(event.dataTransfer.files)
    if (!files.length) return
    event.preventDefault()
    const at = document.caretRangeFromPoint?.(event.clientX, event.clientY) ?? null
    addFiles(files, at)
  }

  const onInput = (event: FormEvent<HTMLDivElement>) => {
    const input = event.nativeEvent as InputEvent
    if (input.inputType === 'insertText' && input.data) applyMarkdown(event.currentTarget, input.data.slice(-1))
    changed()
  }

  const onEditorKey = (event: KeyboardEvent<HTMLDivElement>) => {
    // Enter on an empty quote line leaves the quote, as in Gmail.
    if (event.key === 'Enter' && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
      const node = getSelection()?.anchorNode
      const quoteLine = (node instanceof Element ? node : node?.parentElement)?.closest('blockquote')
      if (quoteLine && editorRef.current?.contains(quoteLine) && !quoteLine.textContent?.replaceAll(ZWSP, '').trim()) {
        event.preventDefault()
        format('formatBlock', '<div>')
      }
      return
    }
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
      // Docked, it stays (and saves): Esc only steps out of it, and again leaves the thread.
      if (docked) (document.activeElement as HTMLElement | null)?.blur()
      else close()
    }
  }

  const showSubject = !docked || initial.mode !== 'reply'
  const statusText = status.text ?? { idle: '', saving: 'Saving…', saved: 'Draft saved', error: '' }[status.kind]
  // Images the body shows aren't listed; forwarded ones stay listed (the quote shows them).
  const listed = attachments.filter((a) => !a.content_id || initial.quote_html?.includes(`cid:${a.content_id}`))
  const recipients = [...to, ...cc, ...bcc].map((a) => a.name || a.email).join(', ')

  const formatting = (
    <>
      <FormatTool label="Bold" title={`Bold (${mod}+B, or **text**)`} onClick={() => format('bold')}>
        <b>B</b>
      </FormatTool>
      <FormatTool label="Italic" title={`Italic (${mod}+I, or *text*)`} onClick={() => format('italic')}>
        <i>I</i>
      </FormatTool>
      <FormatTool label="Link" title={`Link (${mod}+K, or [text](url))`} onClick={startLink} icon="link" />
      <FormatTool label="Bulleted list" title="Bulleted list (- at the start of a line)" onClick={() => format('insertUnorderedList')} icon="listBullet" />
      <FormatTool label="Numbered list" title="Numbered list (1. at the start of a line)" onClick={() => format('insertOrderedList')} icon="listNumber" />
      <FormatTool label="Code" title="Code (`code`, or ``` for a block)" onClick={code} icon="code" />
      <FormatTool label="Quote" title="Quote (> at the start of a line)" onClick={quote} icon="quote" />
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
    </>
  )
  const files = (
    <>
      <button type="button" className={styles.tool} aria-label="Attach files" title="Attach files (up to 25 MB)" onClick={() => fileRef.current?.click()}>
        <Icon name="attach" size={16} />
      </button>
      <button
        type="button"
        className={styles.tool}
        aria-label="Insert image"
        title="Insert image (or paste one)"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          imageRange.current = caret()
          imageRef.current?.click()
        }}
      >
        <Icon name="image" size={16} />
      </button>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          const chosen = Array.from(event.target.files ?? [])
          event.target.value = ''
          void upload(chosen)
        }}
      />
      <input
        ref={imageRef}
        type="file"
        accept={IMAGE_TYPES.join(',')}
        multiple
        hidden
        onChange={(event) => {
          const chosen = Array.from(event.target.files ?? [])
          event.target.value = ''
          void insertImages(chosen, imageRange.current)
        }}
      />
    </>
  )
  const statusLine = (
    <span className={`${styles.status} ${status.kind === 'error' ? styles.statusError : ''}`} role="status">
      {statusText}
    </span>
  )
  const sendButton = (
    <button type="button" className={styles.send} onClick={() => void send()} disabled={sending} title={`Send (${mod}+Enter)`}>
      <Icon name="send" size={14} strokeWidth={2} />
      Send
    </button>
  )
  const discardButton = (
    <button type="button" className={styles.tool} aria-label="Discard draft" title="Discard draft" onClick={() => void discard()}>
      <Icon name="delete" size={16} />
    </button>
  )

  return (
    <section
      ref={rootRef}
      className={[styles.composer, docked && styles.docked, onToggleMinimize && styles.window, minimized && styles.minimized].filter(Boolean).join(' ')}
      aria-label={title ?? (initial.mode === 'forward' ? 'Forward' : 'Reply')}
      onKeyDown={onKeyDown}
    >
      {!docked && (
        // A window is moved by this row (App.tsx).
        <header className={styles.header} data-drag-handle={onToggleMinimize && !minimized ? '' : undefined}>
          <button type="button" className={styles.tool} aria-label="Close" title="Close and keep the draft (Esc)" onClick={close}>
            <Icon name="close" size={16} />
          </button>
          {onToggleMinimize && (
            <button
              type="button"
              className={`${styles.tool} ${styles.minimize}`}
              aria-label={minimized ? 'Restore' : 'Minimize'}
              aria-expanded={!minimized}
              title={minimized ? 'Restore' : 'Minimize'}
              onClick={onToggleMinimize}
            >
              <Icon name={minimized ? 'chevronUp' : 'minimize'} size={16} />
            </button>
          )}
          {minimized ? (
            <button type="button" className={styles.titleButton} onClick={onToggleMinimize}>
              <span className={styles.title}>{subject.trim() || title}</span>
            </button>
          ) : (
            <span className={styles.spacer} />
          )}
          {!minimized && discardButton}
        </header>
      )}

      {docked && (
        <header className={styles.dockHeader}>
          <Icon name={initial.mode === 'forward' ? 'forward' : 'reply'} size={16} />
          {fieldsOpen ? (
            <span className={styles.dockTitle}>{initial.mode === 'forward' ? 'Forward' : 'Reply'}</span>
          ) : (
            <button type="button" className={styles.dockRecipients} title="Edit the recipients" onClick={() => setFieldsOpen(true)}>
              {recipients}
            </button>
          )}
          <span className={styles.spacer} />
          {discardButton}
        </header>
      )}

      {fieldsOpen && (
        <div className={styles.fields}>
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
              <input
                className={styles.subject}
                aria-label="Subject"
                placeholder="Subject"
                value={subject}
                onChange={(event) => {
                  setSubject(event.target.value)
                  changed()
                }}
              />
            </div>
          )}
        </div>
      )}

      <div className={styles.writing}>
        <div
          ref={editorRef}
          className={styles.editor}
          contentEditable
          suppressContentEditableWarning
          role="textbox"
          aria-multiline="true"
          aria-label="Message"
          onInput={onInput}
          onPaste={onPaste}
          onDrop={onDrop}
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

        {(listed.length > 0 || uploading.length > 0) && (
          <ul className={styles.attachments} aria-label="Attachments">
            {listed.map((a) => (
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
      </div>

      <footer className={styles.footer}>
        <div className={styles.tools} role="toolbar" aria-label="Formatting">
          {formatting}
          <span className={styles.toolSeparator} aria-hidden="true" />
          {files}
        </div>
        {statusLine}
        {sendButton}
      </footer>
    </section>
  )
}

type FormatToolProps = { label: string; title: string; onClick: () => void; icon?: IconName; children?: ReactNode }

function FormatTool({ label, title, onClick, icon, children }: FormatToolProps) {
  return (
    <button
      type="button"
      className={styles.formatTool}
      aria-label={label}
      title={title}
      onMouseDown={(event) => event.preventDefault()} // keep the selection in the editor
      onClick={onClick}
    >
      {icon ? <Icon name={icon} size={15} /> : children}
    </button>
  )
}
