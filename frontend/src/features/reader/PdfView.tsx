import { getDocument, GlobalWorkerOptions, PasswordResponses, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import styles from './Attachments.module.css'

GlobalWorkerOptions.workerSrc = workerSrc

// pdf.js fetches these by name: the CMaps (CJK text) and the JBIG2 and JPEG 2000 decoders, which
// vite.config.ts copies into the build.
const ASSETS = import.meta.env.DEV ? '/node_modules/pdfjs-dist/' : '/pdfjs/'

const MAX_WIDTH = 900

type Ask = { submit: (password: string) => void; wrong: boolean }

/** A PDF drawn by pdf.js, for where the browser's own viewer can't show it. */
export default function PdfView({ src, zoom }: { src: string; zoom: number }) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [ask, setAsk] = useState<Ask | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    // The CSP doesn't allow WebAssembly, so the decoders run as plain scripts.
    const task = getDocument({ url: src, cMapUrl: `${ASSETS}cmaps/`, wasmUrl: `${ASSETS}wasm/`, useWasm: false })
    let closed = false
    task.onPassword = (submit: Ask['submit'], reason: number) => setAsk({ submit, wrong: reason === PasswordResponses.INCORRECT_PASSWORD })
    task.promise.then(
      (loaded) => {
        setAsk(null)
        setDoc(loaded)
      },
      () => {
        if (!closed) setFailed(true)
      },
    )
    return () => {
      closed = true
      void task.destroy()
    }
  }, [src])

  if (failed) return <p className={styles.note}>The file couldn’t be loaded. Try downloading it.</p>
  if (ask) return <Password ask={ask} />
  if (!doc) return <p className={styles.note}>Loading…</p>
  return <Pages doc={doc} zoom={zoom} />
}

function Password({ ask }: { ask: Ask }) {
  const [password, setPassword] = useState('')
  return (
    <form
      className={styles.password}
      onSubmit={(event) => {
        event.preventDefault()
        ask.submit(password)
      }}
    >
      <label className={styles.field}>
        This PDF is protected by a password.
        <input type="password" autoComplete="off" autoFocus value={password} onChange={(event) => setPassword(event.target.value)} />
      </label>
      <button type="submit" className={styles.unlock} disabled={!password}>
        Open
      </button>
      {ask.wrong && (
        <p role="alert" className={styles.wrong}>
          That password didn’t open it.
        </p>
      )}
    </form>
  )
}

function Pages({ doc, zoom }: { doc: PDFDocumentProxy; zoom: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [fit, setFit] = useState(0)
  useEffect(() => {
    const scroller = ref.current!
    const observer = new ResizeObserver(() => setFit(Math.min(scroller.clientWidth, MAX_WIDTH)))
    observer.observe(scroller)
    return () => observer.disconnect()
  }, [])
  const width = Math.round(fit * zoom)

  // Zooming keeps the middle of the view where it was, as a share of the whole document.
  const middle = useRef({ x: 0.5, y: 0 })
  useLayoutEffect(() => {
    const scroller = ref.current!
    scroller.scrollTo(
      middle.current.x * scroller.scrollWidth - scroller.clientWidth / 2,
      middle.current.y * scroller.scrollHeight - scroller.clientHeight / 2,
    )
  }, [width])

  return (
    <div
      ref={ref}
      className={styles.pages}
      onScroll={({ currentTarget: scroller }) => {
        middle.current = {
          x: (scroller.scrollLeft + scroller.clientWidth / 2) / scroller.scrollWidth,
          y: (scroller.scrollTop + scroller.clientHeight / 2) / scroller.scrollHeight,
        }
      }}
    >
      {fit > 0 && Array.from({ length: doc.numPages }, (_, index) => <Page key={index} doc={doc} number={index + 1} width={width} />)}
    </div>
  )
}

// Drawn only while it is near the view, so a long document doesn't hold every page's bitmap.
function Page({ doc, number, width }: { doc: PDFDocumentProxy; number: number; width: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [near, setNear] = useState(false)
  useEffect(() => {
    const canvas = ref.current!
    const observer = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), { root: canvas.parentElement, rootMargin: '100% 0px' })
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const canvas = ref.current!
    if (!near) {
      canvas.width = 0
      return
    }
    let task: RenderTask | undefined
    let stopped = false
    void doc.getPage(number).then((page) => {
      if (stopped) return
      const size = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: (width * devicePixelRatio) / size.width })
      canvas.width = viewport.width
      canvas.height = viewport.height
      canvas.style.aspectRatio = `${size.width} / ${size.height}`
      task = page.render({ canvas, viewport })
      task.promise.catch(() => {}) // cancelled below
    })
    return () => {
      stopped = true
      task?.cancel()
    }
  }, [doc, number, near, width])

  return <canvas ref={ref} className={styles.page} style={{ width }} />
}
