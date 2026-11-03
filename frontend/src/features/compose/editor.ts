// The server sanitizes the body again (nh3) before it goes anywhere.

const KEEP = new Set(['B', 'STRONG', 'I', 'EM', 'A', 'UL', 'OL', 'LI', 'BR', 'P', 'DIV', 'BLOCKQUOTE', 'CODE', 'PRE', 'H1', 'H2', 'H3'])
const DROP = new Set(['SCRIPT', 'STYLE', 'TITLE', 'HEAD', 'META', 'LINK', 'TEMPLATE', 'IFRAME', 'OBJECT', 'SVG', 'MATH', 'IMG'])
// Blocks of pasted HTML that would otherwise run into the next line.
const BLOCKS = new Set(['H4', 'H5', 'H6', 'TR', 'TABLE', 'SECTION', 'ARTICLE', 'HEADER', 'FOOTER'])

// Left after a Markdown shortcut, so typing goes on outside the new <code> or <b>; never saved.
export const ZWSP = '​'

export function safeUrl(href: string): string | null {
  const value = href.trim()
  if (/^(https?:|mailto:)/i.test(value)) return value
  if (/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(value)) return `mailto:${value}`
  if (/^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(value)) return `https://${value}`
  return null
}

/** The URL that shows the draft's inline image with this Content-ID, if it has one. */
export type ImageUrl = (contentId: string) => string | null

function clean(source: Node, target: Node, doc: Document, image?: ImageUrl) {
  for (const node of Array.from(source.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      target.appendChild(doc.createTextNode(node.textContent ?? ''))
      continue
    }
    if (!(node instanceof Element)) continue
    if (node.tagName === 'IMG' && image) {
      const cid = /^cid:(.+)$/i.exec(node.getAttribute('src') ?? '')?.[1]
      const url = cid && image(cid)
      if (cid && url) target.appendChild(imageElement(doc, url, cid, node.getAttribute('alt') ?? ''))
      continue
    }
    if (DROP.has(node.tagName)) continue
    if (KEEP.has(node.tagName)) {
      const copy = doc.createElement(node.tagName.toLowerCase())
      if (node.tagName === 'A') {
        const href = safeUrl(node.getAttribute('href') ?? '')
        if (href) copy.setAttribute('href', href)
      }
      clean(node, copy, doc, image)
      target.appendChild(copy)
    } else if (BLOCKS.has(node.tagName)) {
      const block = doc.createElement('div')
      clean(node, block, doc, image)
      target.appendChild(block)
    } else {
      clean(node, target, doc, image) // unwrap: keep the text, drop the tag
    }
  }
}

function imageElement(doc: Document, url: string, cid: string, alt: string) {
  const img = doc.createElement('img')
  img.src = url
  img.alt = alt
  img.dataset.cid = cid
  return img
}

export function imageHtml(url: string, cid: string, alt: string): string {
  return imageElement(document, url, cid, alt).outerHTML
}

// DOMParser documents are inert: no scripts run and no images load while this reads them.
// Pasted HTML has no `image`: its images are dropped.
export function cleanHtml(html: string, image?: ImageUrl): string {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const out = document.implementation.createHTMLDocument('')
  const root = out.createElement('div')
  clean(parsed.body, root, out, image)
  return root.innerHTML
}

/** The editor's content as the message body: inline images point at their cid: again. */
export function bodyHtml(editor: HTMLElement): string {
  // A template's content is inert: the cid: sources aren't fetched.
  const copy = document.createElement('template')
  copy.innerHTML = editor.innerHTML
  for (const img of copy.content.querySelectorAll('img')) {
    if (img.dataset.cid) img.setAttribute('src', `cid:${img.dataset.cid}`)
    else img.remove()
    img.removeAttribute('data-cid')
  }
  return copy.innerHTML.replaceAll(ZWSP, '')
}

export function isBlank(html: string): boolean {
  return new DOMParser().parseFromString(html, 'text/html').body.textContent?.trim() === ''
}

export const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
