import { useEffect, useRef, useState } from 'react'
import { fileSize } from '../../app/format'
import type { Attachment } from '../../app/mail'
import { Icon } from '../../components/Icon'
import styles from './Attachments.module.css'

type Kind = 'image' | 'video' | 'audio' | 'pdf' | 'text'

// The types the server sends inline. An SVG never renders, even as an <img>.
const IMAGES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])
const TEXT = new Set(['text/plain', 'text/csv'])

/** How the viewer shows it, or null when only a download makes sense. */
function kindOf(mime: string): Kind | null {
  if (IMAGES.has(mime)) return 'image'
  if (TEXT.has(mime)) return 'text'
  // Android's Chrome has no built-in PDF viewer to frame one in.
  if (mime === 'application/pdf') return navigator.pdfViewerEnabled ? 'pdf' : null
  const media = mime.split('/')[0]
  if ((media === 'video' || media === 'audio') && document.createElement(media).canPlayType(mime)) return media
  return null
}

const url = (a: Attachment) => `/api/attachments/${a.id}`

function extension(filename: string) {
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(dot + 1, dot + 5).toUpperCase() : ''
}

export function Attachments({ attachments }: { attachments: Attachment[] }) {
  const [open, setOpen] = useState<{ attachment: Attachment; kind: Kind } | null>(null)
  return (
    <>
      <ul className={styles.list} aria-label="Attachments">
        {attachments.map((a) => {
          const kind = kindOf(a.mime_type)
          const body = (
            <>
              <Thumb attachment={a} kind={kind} />
              <span className={styles.label}>
                <b className={styles.name}>{a.filename}</b>
                <span className={styles.size}>{fileSize(a.size)}</span>
              </span>
            </>
          )
          return (
            <li key={a.id} className={styles.card}>
              {kind ? (
                <button type="button" className={styles.open} onClick={() => setOpen({ attachment: a, kind })} title={`Open ${a.filename}`}>
                  {body}
                </button>
              ) : (
                <div className={styles.open} title={a.filename}>
                  {body}
                </div>
              )}
              <a className={styles.download} href={url(a)} download={a.filename} title={`Download ${a.filename}`} aria-label={`Download ${a.filename}`}>
                <Icon name="install" size={15} />
              </a>
            </li>
          )
        })}
      </ul>
      {open && <Viewer attachment={open.attachment} kind={open.kind} onClose={() => setOpen(null)} />}
    </>
  )
}

function Thumb({ attachment, kind }: { attachment: Attachment; kind: Kind | null }) {
  const [failed, setFailed] = useState(false)
  if (!failed && kind === 'image') return <img className={styles.thumb} src={url(attachment)} alt="" loading="lazy" onError={() => setFailed(true)} />
  if (!failed && kind === 'video')
    return (
      <span className={styles.thumbBox}>
        {/* #t picks a frame past the usual black first one. */}
        <video className={styles.thumb} src={`${url(attachment)}#t=0.1`} preload="metadata" muted playsInline onError={() => setFailed(true)} />
        <span className={styles.play}>
          <Icon name="play" size={14} filled />
        </span>
      </span>
    )
  const ext = extension(attachment.filename)
  return (
    <span className={styles.tile}>
      <Icon name={kind === 'video' || kind === 'audio' ? 'play' : kind === 'image' ? 'image' : 'draft'} size={22} strokeWidth={1.5} />
      {ext && <span>{ext}</span>}
    </span>
  )
}

type ViewerProps = { attachment: Attachment; kind: Kind; onClose: () => void }

// A native modal <dialog>, like Dialog, but sized for the file rather than a form.
function Viewer({ attachment, kind, onClose }: ViewerProps) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])
  return (
    <dialog
      ref={ref}
      className={styles.viewer}
      aria-label={attachment.filename}
      onCancel={(event) => {
        event.preventDefault() // closed by unmounting, so the closing is the caller's
        onClose()
      }}
      onKeyDown={(event) => {
        // The thread behind keeps its shortcuts to itself (e would archive it); Space still plays.
        event.stopPropagation()
        if (event.key !== 'Escape') return
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose() // the backdrop
      }}
    >
      <header className={styles.bar}>
        <span className={styles.title}>
          <b className={styles.name}>{attachment.filename}</b>
          <span className={styles.size}>{fileSize(attachment.size)}</span>
        </span>
        <a className={styles.barButton} href={url(attachment)} download={attachment.filename} title="Download">
          <Icon name="install" />
        </a>
        <button type="button" className={styles.barButton} onClick={onClose} title="Close (Esc)">
          <Icon name="close" />
        </button>
      </header>
      <div
        className={styles.stage}
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose() // around the file
        }}
      >
        {kind === 'image' && <img className={styles.media} src={url(attachment)} alt={attachment.filename} />}
        {kind === 'pdf' && <iframe className={styles.frame} src={url(attachment)} title={attachment.filename} />}
        {(kind === 'video' || kind === 'audio' || kind === 'text') && <Fetched attachment={attachment} kind={kind} />}
      </div>
    </dialog>
  )
}

type Loaded = { src: string } | { text: string } | { error: true } | null

// Media plays from a blob: the server has no byte ranges (every request fetches the message
// from Gmail again), and Safari won't play a URL without them. A blob also seeks freely.
function Fetched({ attachment, kind }: { attachment: Attachment; kind: 'video' | 'audio' | 'text' }) {
  const [loaded, setLoaded] = useState<Loaded>(null)
  useEffect(() => {
    const abort = new AbortController()
    let objectUrl: string | undefined
    fetch(url(attachment), { signal: abort.signal })
      .then((response): Promise<string | Blob> => {
        if (!response.ok) throw new Error(response.statusText)
        return kind === 'text' ? response.text() : response.blob()
      })
      .then((body) => {
        if (typeof body === 'string') return setLoaded({ text: body })
        objectUrl = URL.createObjectURL(body)
        setLoaded({ src: objectUrl })
      })
      .catch(() => {
        if (!abort.signal.aborted) setLoaded({ error: true })
      })
    return () => {
      abort.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [attachment, kind])

  if (loaded === null) return <p className={styles.note}>Loading…</p>
  if ('error' in loaded) return <p className={styles.note}>The file couldn’t be loaded. Try downloading it.</p>
  if ('text' in loaded) return <pre className={styles.text}>{loaded.text}</pre>
  return kind === 'video' ? (
    <video className={styles.media} src={loaded.src} controls autoPlay playsInline />
  ) : (
    <audio className={styles.audio} src={loaded.src} controls autoPlay />
  )
}
