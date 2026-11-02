import { useEffect, useMemo, useRef, useState } from 'react'
import { useAccent, type Accent } from '../../app/accent'
import type { Attachment } from '../../app/mail'
import { useTheme } from '../../app/theme'
import fullFonts from '../../styles/fonts-full.css?url'
import styles from './Reader.module.css'
import emailCss from './email.css?url'

// Email HTML (already sanitized server-side with nh3) in a sandboxed iframe with no scripts.
// allow-same-origin lets kanso size the frame; without allow-scripts the email can't use it.
// The CSP blocks remote content too, not only the sanitizer, until "Show images".

const SANDBOX = 'allow-same-origin allow-popups allow-popups-to-escape-sandbox'

function decode(value: string) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

// An email that sets its own colors is written for a light page: in the dark theme it gets a
// light sheet (email.css) rather than being recolored. One that doesn't follows the theme.
const STYLED = /\b(?:bgcolor|background|color)\s*[:=]/i

type Theme = 'light' | 'dark' | 'sheet'

function buildDocument(html: string, lang: string | null, attachments: Attachment[], showImages: boolean, theme: Theme, accent: Accent) {
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
  const themeAttr = theme === 'dark' ? ' data-theme="dark"' : theme === 'sheet' ? ' data-theme="light" data-sheet' : ' data-theme="light"'
  return (
    `<!doctype html><html${themeAttr} data-accent="${accent}"${langAttr}><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${csp}">` +
    `<base target="_blank"><link rel="stylesheet" href="${fullFonts}"><link rel="stylesheet" href="${emailCss}">` +
    `</head><body>${body}</body></html>`
  )
}

type Props = { html: string; lang: string | null; attachments: Attachment[]; showImages: boolean }

export function EmailFrame({ html, lang, attachments, showImages }: Props) {
  const { dark } = useTheme()
  const accent = useAccent()
  const theme: Theme = !dark ? 'light' : STYLED.test(html) ? 'sheet' : 'dark'
  const srcDoc = useMemo(
    () => buildDocument(html, lang, attachments, showImages, theme, accent),
    [html, lang, attachments, showImages, theme, accent],
  )
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
      className={`${styles.frame} ${theme === 'sheet' ? styles.sheet : ''}`}
      title="Email content"
      sandbox={SANDBOX}
      srcDoc={srcDoc}
      style={{ height }}
      onLoad={(event) => onLoad(event.currentTarget)}
    />
  )
}
