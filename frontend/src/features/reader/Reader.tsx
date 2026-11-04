import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Account } from '../../app/accounts'
import { openDraft, useOpenDraft, type ComposeMode } from '../../app/compose'
import { longDateTime } from '../../app/format'
import { quickAction, redundant, type Quick } from '../../app/actions'
import { useThread, type Address, type Message, type ThreadDetail, type ThreadSummary } from '../../app/mail'
import { Avatar } from '../../components/Avatar'
import { Icon, type IconName } from '../../components/Icon'
import { Composer, type Focus } from '../compose/Composer'
import bar from '../phone/BottomBar.module.css'
import { Avatar as AccountAvatar } from '../sidebar/AccountSwitcher'
import { useConfig } from '../../app/ai'
import { Attachments } from './Attachments'
import { EmailFrame } from './EmailFrame'
import { Summary } from './Summary'
import styles from './Reader.module.css'

// Remote images, to offer "Show images". The frame's CSP blocks them either way.
const REMOTE = /(?:\b(?:src|srcset|background)\s*=\s*["']?\s*|url\(\s*["']?\s*)(?:https?:)?\/\//i

export type ThreadDraft = { threadId: number; draftId: number; focus: Focus | null }

type Props = {
  threadId: number | null
  accounts: Account[]
  reply: ThreadDraft | null
  onComposeFinished: (notice: string, threadId?: number | null) => void
  onCloseReply: () => void
  onReply: (mode: Exclude<ComposeMode, 'new'>) => void
  onCloseThread: () => void
  /** The checked threads, else the open (or cursor) thread. */
  targets: ThreadSummary[]
  onQuick: (quick: Quick) => void
  onToggleRead: () => void
  /** The open thread's sender's list. */
  onUnsubscribe: () => void
  onLabel: () => void
  picker: ReactNode
  phone: boolean
  /** Phones: what Back returns to. */
  backLabel: string
  onBack: () => void
  /** Phones, with AI on: the assistant, about this thread. */
  onAsk?: () => void
  /** Desktop, with AI on: opens the assistant beside the mail (about this thread) or closes it. */
  onAssistant?: () => void
  assistantOpen?: boolean
}

export function Reader(props: Props) {
  const { threadId, accounts, reply, onComposeFinished, onCloseReply, onCloseThread } = props
  const thread = useThread(threadId)
  const detail = thread.data && thread.data.id === threadId ? thread.data : undefined
  const drafts = detail?.messages.filter((m) => m.draft) ?? []
  const draftOnly = !!detail && drafts.length === detail.messages.length
  const opened = useOpenDraft(drafts.at(-1)?.id)

  // Pinned once shown, since every autosave gives the draft a new message (and so a new lookup).
  const [shown, setShown] = useState<ThreadDraft | null>(null)
  const [closedIn, setClosedIn] = useState<{ threadId: number | null; ids: Set<number> }>({ threadId, ids: new Set() })
  const closed = closedIn.threadId === threadId ? closedIn.ids : new Set<number>()
  const setClosed = (change: (ids: Set<number>) => Set<number>) => setClosedIn({ threadId, ids: change(closed) })
  const active = reply?.threadId === threadId ? reply : shown?.threadId === threadId ? shown : null
  if (!active && threadId !== null && opened.data && !closed.has(opened.data.id))
    setShown({ threadId, draftId: opened.data.id, focus: null })

  // A docked draft closed here stays closed in this thread until its "Draft" row reopens it.
  const closeDraft = (draftId: number, inline: boolean) => {
    if (inline) setClosed((set) => new Set(set).add(draftId))
    setShown(null)
    onCloseReply()
  }
  const openThreadDraft = (messageId: number) =>
    void openDraft(messageId).then((draft) => {
      if (threadId === null) return
      setClosed((set) => new Set([...set].filter((id) => id !== draft.id)))
      setShown({ threadId, draftId: draft.id, focus: 'body' })
    })
  const composer = (draft: ThreadDraft, docked: boolean) => (
    <Composer
      key={draft.draftId}
      draftId={draft.draftId}
      docked={docked}
      title={docked ? undefined : 'Draft'}
      focus={draft.focus}
      onClose={
        docked
          ? undefined
          : () => {
              closeDraft(draft.draftId, false)
              onCloseThread()
            }
      }
      onFinished={(notice, sentThread) => {
        closeDraft(draft.draftId, true)
        onComposeFinished(notice, sentThread)
        if (!docked) onCloseThread()
      }}
    />
  )

  const account = accounts.find((a) => a.id === detail?.account_id)
  const me = account?.email ?? ''
  let content
  if (threadId === null) content = <p className={styles.placeholder}>Select a thread to read it. Press ? for keyboard shortcuts.</p>
  // A thread of drafts only is the composer itself; it also stays if the thread goes away.
  else if (active && (draftOnly || !detail)) content = composer(active, false)
  else if (draftOnly && opened.isError) content = <p className={styles.placeholder}>Couldn’t open the draft: {opened.error.message}</p>
  else if (thread.isError)
    content =
      thread.error.message === 'Thread not found' ? (
        <p className={styles.placeholder}>This thread is no longer in your mail.</p>
      ) : (
        <p className={styles.placeholder}>
          Couldn’t load the thread: {thread.error.message}{' '}
          <button type="button" className={styles.retry} onClick={() => void thread.refetch()}>
            Try again
          </button>
        </p>
      )
  else if (detail && !draftOnly)
    content = <ThreadView key={detail.id} thread={detail} me={me} hideDrafts={!!active} onOpenDraft={openThreadDraft} />

  const canReply = !!detail && !draftOnly
  const docked = canReply && active ? active : null
  // Starting a reply shows the end of the thread above it.
  const bodyRef = useRef<HTMLDivElement>(null)
  const started = docked?.focus ? docked.draftId : null
  useEffect(() => {
    const body = bodyRef.current
    if (started === null || !body) return
    const smooth = !matchMedia('(prefers-reduced-motion: reduce)').matches
    body.scrollTo({ top: body.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }, [started])

  const last = detail?.messages.filter((m) => !m.draft).at(-1)
  const all = !!last && replyAllAdds(last, me)
  return (
    <article className={styles.reader} aria-label="Thread">
      {props.phone ? <PhoneHeader {...props} canReply={canReply} /> : <Toolbar {...props} canReply={canReply} />}
      <div className={styles.body} key={threadId ?? 'none'} ref={bodyRef}>
        {content}
      </div>
      {docked && composer(docked, true)}
      {canReply && !docked && !props.phone && (
        <div className={styles.replyBar}>
          <AccountAvatar account={account ?? null} className={styles.replyAvatar} />
          <button
            type="button"
            className={styles.replyPrompt}
            title={all ? 'Reply all (a)' : 'Reply (r)'}
            onClick={() => props.onReply(all ? 'reply_all' : 'reply')}
          >
            {all ? 'Reply all' : 'Reply'}
          </button>
          <ToolButton icon="reply" label="Reply" title="Reply (r)" onClick={() => props.onReply('reply')} />
          <ToolButton icon="forward" label="Forward" title="Forward (f)" onClick={() => props.onReply('forward')} />
        </div>
      )}
      {/* While writing, the keyboard needs the room. */}
      {props.phone && !active && <PhoneActions {...props} canReply={canReply} />}
    </article>
  )
}

/** Whether Reply all writes to more people than Reply (as the server picks them). */
function replyAllAdds(message: Message, me: string) {
  const mine = me.toLowerCase()
  const sender = message.sender.email.toLowerCase()
  if (sender === mine) return message.cc.length > 0
  return [...message.to, ...message.cc].some((a) => ![mine, sender].includes(a.email.toLowerCase()))
}

type ToolButtonProps = { icon: IconName; label: string; title: string; pressed?: boolean; disabled?: boolean; onClick?: () => void }

function ToolButton({ icon, label, title, pressed, disabled, onClick }: ToolButtonProps) {
  return (
    <button
      type="button"
      className={styles.tool}
      aria-label={label}
      aria-pressed={pressed}
      aria-disabled={disabled || undefined}
      title={title}
      onClick={disabled ? undefined : onClick}
    >
      <Icon name={icon} filled={pressed} />
    </button>
  )
}

const name = (title: string) => title.replace(/ \(.*\)$/, '')

type ToolbarProps = Pick<
  Props,
  'targets' | 'onQuick' | 'onToggleRead' | 'onUnsubscribe' | 'onLabel' | 'picker' | 'onReply' | 'onCloseThread' | 'onAssistant' | 'assistantOpen'
> & {
  canReply: boolean
}

function Toolbar(props: ToolbarProps) {
  const { targets, onQuick, onToggleRead, onUnsubscribe, onLabel, picker, canReply, onReply, onCloseThread, onAssistant, assistantOpen } = props
  const none = targets.length === 0
  const quick = (q: Quick) => {
    const { icon, title, pressed } = quickAction(q, targets)
    return <ToolButton icon={icon} label={name(title)} title={title} pressed={pressed} disabled={none} onClick={() => onQuick(q)} />
  }
  return (
    <div className={styles.toolbar} role="toolbar" aria-label="Email actions">
      {!redundant('done', targets) && quick('done')}
      {quick('delete')}
      <span className={styles.separator} aria-hidden="true" />
      <span className={styles.anchor}>
        <ToolButton icon="label" label="Label" title="Label (l)" disabled={none} onClick={onLabel} />
        {picker}
      </span>
      {quick('important')}
      {quick('star')}
      <span className={styles.spacer} />
      <More
        disabled={none}
        unread={!none && targets.every((t) => t.unread)}
        onToggleRead={onToggleRead}
        onReplyAll={canReply ? () => onReply('reply_all') : undefined}
        spam={{ label: name(quickAction('spam', targets).title), toggle: () => onQuick('spam') }}
        onUnsubscribe={canReply ? onUnsubscribe : undefined}
      />
      {onAssistant && (
        <ToolButton
          icon="assistant"
          label="Assistant"
          title={assistantOpen ? 'Close the assistant' : 'Ask the assistant (q)'}
          pressed={assistantOpen}
          onClick={onAssistant}
        />
      )}
      <span className={styles.separator} aria-hidden="true" />
      <ToolButton icon="close" label="Close" title="Close (Esc)" onClick={onCloseThread} />
    </div>
  )
}

type PhoneProps = Props & { canReply: boolean }

// Phones: Back and the rarer actions at the top, the everyday ones at the bottom (PhoneActions).
function PhoneHeader({ targets, onQuick, onToggleRead, onUnsubscribe, onReply, canReply, backLabel, onBack, onAsk }: PhoneProps) {
  const none = targets.length === 0
  const important = quickAction('important', targets)
  return (
    <div className={styles.toolbar}>
      <button type="button" className={styles.back} onClick={onBack}>
        <Icon name="back" size={20} />
        {backLabel}
      </button>
      <span className={styles.spacer} />
      <More
        disabled={none}
        unread={!none && targets.every((t) => t.unread)}
        onToggleRead={onToggleRead}
        onReplyAll={canReply ? () => onReply('reply_all') : undefined}
        onForward={canReply ? () => onReply('forward') : undefined}
        important={{ on: !!important.pressed, toggle: () => onQuick('important') }}
        spam={{ label: name(quickAction('spam', targets).title), toggle: () => onQuick('spam') }}
        onUnsubscribe={canReply ? onUnsubscribe : undefined}
        onAsk={onAsk}
      />
    </div>
  )
}

function PhoneActions({ targets, onQuick, onLabel, picker, onReply, canReply }: PhoneProps) {
  const none = targets.length === 0
  const item = (q: Quick, short: (on: boolean) => string) => {
    const { icon, title, pressed } = quickAction(q, targets)
    // Done and Delete turn into Move to inbox and Restore: their icon says which.
    const on = q === 'star' ? !!pressed : icon === 'inbox' || icon === 'restore'
    return (
      <button
        type="button"
        className={bar.item}
        aria-pressed={q === 'star' ? pressed : undefined}
        aria-disabled={none || undefined}
        title={name(title)}
        onClick={none ? undefined : () => onQuick(q)}
      >
        <Icon name={icon} size={22} filled={pressed} />
        {short(on)}
      </button>
    )
  }
  return (
    <nav className={`${bar.bar} ${styles.actions}`} aria-label="Email actions">
      {!redundant('done', targets) && item('done', (back) => (back ? 'Inbox' : 'Done'))}
      {item('delete', (back) => (back ? 'Restore' : 'Delete'))}
      <span className={styles.slot}>
        <button type="button" className={bar.item} aria-disabled={none || undefined} onClick={none ? undefined : onLabel}>
          <Icon name="label" size={22} />
          Label
        </button>
        {picker}
      </span>
      {item('star', (on) => (on ? 'Starred' : 'Star'))}
      <button
        type="button"
        className={`${bar.item} ${bar.current}`}
        aria-disabled={!canReply || undefined}
        onClick={canReply ? () => onReply('reply') : undefined}
      >
        <Icon name="reply" size={22} />
        Reply
      </button>
    </nav>
  )
}

type MoreProps = {
  disabled: boolean
  unread: boolean
  onToggleRead: () => void
  onReplyAll?: () => void
  onForward?: () => void
  /** Phones: these live here rather than in a toolbar. */
  important?: { on: boolean; toggle: () => void }
  /** Report spam, or Not spam. */
  spam: { label: string; toggle: () => void }
  onUnsubscribe?: () => void
  onAsk?: () => void
}

function More({ disabled, unread, onToggleRead, onReplyAll, onForward, important, spam, onUnsubscribe, onAsk }: MoreProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    return () => document.removeEventListener('pointerdown', onPointer)
  }, [open])
  return (
    <span className={styles.anchor} ref={ref}>
      <ToolButton icon="more" label="More actions" title="More" disabled={disabled} onClick={() => setOpen(!open)} />
      {open && !disabled && (
        <div className={styles.menu} role="menu">
          {onReplyAll && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                onReplyAll()
              }}
            >
              Reply all
              <kbd>a</kbd>
            </button>
          )}
          {onForward && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                onForward()
              }}
            >
              Forward
              <kbd>f</kbd>
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              onToggleRead()
            }}
          >
            {unread ? 'Mark as read' : 'Mark as unread'}
            <kbd>u</kbd>
          </button>
          {important && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                important.toggle()
              }}
            >
              {important.on ? 'Not important' : 'Mark important'}
              <kbd>=</kbd>
            </button>
          )}
          {onUnsubscribe && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                onUnsubscribe()
              }}
            >
              Unsubscribe
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              spam.toggle()
            }}
          >
            {spam.label}
            <kbd>!</kbd>
          </button>
          {onAsk && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                onAsk()
              }}
            >
              Ask about this thread
              <kbd>q</kbd>
            </button>
          )}
        </div>
      )}
    </span>
  )
}

type ThreadViewProps = {
  thread: ThreadDetail
  /** The thread's account: mail from your other accounts is someone else's here. */
  me: string
  /** One is open below the thread. */
  hideDrafts: boolean
  onOpenDraft: (messageId: number) => void
}

function ThreadView({ thread, me, hideDrafts, onOpenDraft }: ThreadViewProps) {
  const messages = thread.messages.filter((m) => !m.draft)
  const drafts = hideDrafts ? [] : thread.messages.filter((m) => m.draft)
  const lastId = messages.at(-1)?.id
  // Unread messages and the latest start expanded; a click flips one from its default.
  const [flipped, setFlipped] = useState<Set<number>>(new Set())
  const flip = (id: number) =>
    setFlipped((set) => {
      const next = new Set(set)
      if (!next.delete(id)) next.add(id)
      return next
    })
  const many = messages.length > 1
  const ai = useConfig().data?.ai ?? false

  return (
    <>
      <h2 className={styles.subject} lang={messages[0]?.lang ?? thread.lang ?? undefined}>
        {thread.subject || '(no subject)'}
      </h2>
      {ai && <Summary threadId={thread.id} />}
      {messages.map((message) => {
        const expanded = (message.unread || message.id === lastId) !== flipped.has(message.id)
        return expanded ? (
          <MessageView
            key={message.id}
            message={message}
            me={me}
            onCollapse={many ? () => flip(message.id) : undefined}
            expanding={flipped.has(message.id)}
          />
        ) : (
          <button key={message.id} type="button" className={styles.collapsed} onClick={() => flip(message.id)}>
            <Avatar className={styles.avatar} name={senderName(message.sender)} src={message.sender_photo} />
            <span className={styles.collapsedName}>{senderName(message.sender)}</span>
            <span className={styles.collapsedSnippet} lang={message.lang ?? undefined}>
              {message.snippet}
            </span>
            <span className={styles.meta}>{longDateTime(message.date)}</span>
          </button>
        )
      })}
      {drafts.map((draft) => (
        <button key={draft.id} type="button" className={styles.collapsed} onClick={() => onOpenDraft(draft.id)} title="Continue writing">
          <span className={styles.draftTag}>Draft</span>
          <span className={styles.collapsedSnippet} lang={draft.lang ?? undefined}>
            {draft.snippet || 'No text yet'}
          </span>
          <span className={styles.meta}>{longDateTime(draft.date)}</span>
        </button>
      ))}
    </>
  )
}

const senderName = (address: Address) => address.name || address.email

/** Everyone else it went to; mail to you alone says nothing. */
function recipients(message: Message, me: string) {
  const names = [...message.to, ...message.cc].filter((a) => a.email.toLowerCase() !== me.toLowerCase()).map(senderName)
  return names.length ? `to ${names.join(', ')}` : ''
}

type MessageViewProps = {
  message: Message
  me: string
  onCollapse?: () => void
  /** Expanded by a click, not from the start: its body grows open. */
  expanding?: boolean
}

function MessageView({ message, me, onCollapse, expanding }: MessageViewProps) {
  const [showImages, setShowImages] = useState(false)
  const html = message.body_html
  // Inline images the HTML shows aren't listed again as attachments.
  const attachments = message.attachments.filter((a) => !(a.inline && a.content_id && html?.includes(`cid:${a.content_id}`)))
  const name = senderName(message.sender)

  return (
    <section className={styles.message}>
      <button type="button" className={styles.sender} onClick={onCollapse} disabled={!onCollapse} title={onCollapse ? 'Collapse' : undefined}>
        <Avatar className={styles.avatar} name={name} src={message.sender_photo} />
        <span className={styles.senderText}>
          <span className={styles.senderName} lang={message.lang ?? undefined} title={message.sender.email}>
            {name}
          </span>
          <span className={styles.meta}>{[recipients(message, me), longDateTime(message.date)].filter(Boolean).join(' · ')}</span>
        </span>
      </button>

      <div className={expanding ? styles.expanding : undefined}>
        <div className={styles.expandingInner}>
          {html !== null && REMOTE.test(html) && !showImages && (
            <p className={styles.imagesBar}>
              Remote images are hidden.
              <button type="button" onClick={() => setShowImages(true)}>
                Show images
              </button>
            </p>
          )}

          <div className={styles.content}>
            {html !== null ? (
              <EmailFrame html={html} lang={message.lang} attachments={message.attachments} showImages={showImages} />
            ) : (
              <TextBody text={message.body_text ?? ''} lang={message.lang} />
            )}
          </div>

          {attachments.length > 0 && <Attachments attachments={attachments} />}
        </div>
      </div>
    </section>
  )
}

const URL_PATTERN = /(https?:\/\/[^\s<>"]+)/

function TextBody({ text, lang }: { text: string; lang: string | null }) {
  return (
    <div className={styles.text} lang={lang ?? undefined}>
      {text.split(/\n\s*\n/).map((paragraph, p) => (
        <p key={p}>
          {paragraph.split(URL_PATTERN).map((part, i) => {
            if (i % 2 === 0) return part
            // Trailing punctuation usually ends the sentence, not the URL.
            const url = part.replace(/[.,;:!?'")\]]+$/, '')
            return (
              <span key={i}>
                <a href={url} target="_blank" rel="noopener noreferrer">
                  {url}
                </a>
                {part.slice(url.length)}
              </span>
            )
          })}
        </p>
      ))}
    </div>
  )
}
