import { useEffect, useMemo, useRef, useState } from 'react'
import type { Attachment } from '../../app/mail'
import fullFonts from '../../styles/fonts-full.css?url'
import styles from './Reader.module.css'
import emailCss from './email.css?url'

// An email's HTML (already sanitized by the server with nh3) in a sandboxed iframe:
// - No scripts: the sandbox has no allow-scripts, and the CSP below allows none either.
// - Same origin (allow-same-origin) so kanso can measure the content and size the frame to fit,
//   and the frame shares the app's cached fonts. Without allow-scripts the email can't use that.
// - Links open in a new tab (allow-popups, <base target>; the sanitizer adds rel=noopener).
// - Remote content is blocked by the CSP, not only by the sanitizer: images and CSS url()s may
//   come from the attachment proxy (cid: images) or data: URLs only, until "Show images" adds
//   http(s) for this one email. Fonts and stylesheets only from kanso itself.

const SANDBOX = 'allow-same-origin allow-popups allow-popups-to-escape-sandbox'

function decode(value: string) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function buildDocument(html: string, lang: string | null, attachments: Attachment[], showImages: boolean) {
  const origin = location.origin
  const byCid = new Map(
    attachments.flatMap((a) => (a.content_id ? [[a.content_id.toLowerCase(), `${origin}/api/attachments/${a.id}`] as const] : [])),
  )
  const body = html.replace(/cid:([^"'\s)>]+)/gi, (match, cid: string) => byCid.get(decode(cid).toLowerCase()) ?? match)
  const csp = [
    "default-src 'none'",
    `img-src ${origin}/api/attachments/ data:${showImages ? ' https: http:' : ''}`,
    `style-src ${origin} 'unsafe-inline'`,
    `font-src ${origin} data:`,
  ].join('; ')
  const langAttr = lang && /^[A-Za-z0-9-]+$/.test(lang) ? ` lang="${lang}"` : ''
  return (
    `<!doctype html><html${langAttr}><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${csp}">` +
    `<base target="_blank"><link rel="stylesheet" href="${fullFonts}"><link rel="stylesheet" href="${emailCss}">` +
    `</head><body>${body}</body></html>`
  )
}

type Props = { html: string; lang: string | null; attachments: Attachment[]; showImages: boolean }

export function EmailFrame({ html, lang, attachments, showImages }: Props) {
  const srcDoc = useMemo(() => buildDocument(html, lang, attachments, showImages), [html, lang, attachments, showImages])
  const [height, setHeight] = useState(0)
  const observer = useRef<ResizeObserver>(null)
  useEffect(() => () => observer.current?.disconnect(), [])

  const onLoad = (frame: HTMLIFrameElement) => {
    const doc = frame.contentDocument
    const win = frame.contentWindow
    if (!doc?.body || !win) return
    // The body's height, plus a horizontal scrollbar's when a wide email has one.
    const measure = () => setHeight(doc.body.scrollHeight + win.innerHeight - doc.documentElement.clientHeight)
    observer.current?.disconnect()
    observer.current = new ResizeObserver(measure)
    observer.current.observe(doc.body)
    measure()
    // Keyboard shortcuts keep working after a click into the email.
    doc.addEventListener('keydown', (event) => window.dispatchEvent(new KeyboardEvent('keydown', event)))
  }

  return (
    <iframe
      className={styles.frame}
      title="Email content"
      sandbox={SANDBOX}
      srcDoc={srcDoc}
      style={{ height }}
      onLoad={(event) => onLoad(event.currentTarget)}
    />
  )
}
