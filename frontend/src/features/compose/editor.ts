// The server sanitizes the body again (nh3) before it goes anywhere.

const KEEP = new Set(['B', 'STRONG', 'I', 'EM', 'A', 'UL', 'OL', 'LI', 'BR', 'P', 'DIV', 'BLOCKQUOTE'])
const DROP = new Set(['SCRIPT', 'STYLE', 'TITLE', 'HEAD', 'META', 'LINK', 'TEMPLATE', 'IFRAME', 'OBJECT', 'SVG', 'MATH', 'IMG'])
// Blocks of pasted HTML that would otherwise run into the next line.
const BLOCKS = new Set(['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'TR', 'TABLE', 'SECTION', 'ARTICLE', 'HEADER', 'FOOTER', 'PRE'])

export function safeUrl(href: string): string | null {
  const value = href.trim()
  if (/^(https?:|mailto:)/i.test(value)) return value
  if (/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(value)) return `mailto:${value}`
  if (/^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(value)) return `https://${value}`
  return null
}

function clean(source: Node, target: Node, doc: Document) {
  for (const node of Array.from(source.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      target.appendChild(doc.createTextNode(node.textContent ?? ''))
      continue
    }
    if (!(node instanceof Element) || DROP.has(node.tagName)) continue
    if (KEEP.has(node.tagName)) {
      const copy = doc.createElement(node.tagName.toLowerCase())
      if (node.tagName === 'A') {
        const href = safeUrl(node.getAttribute('href') ?? '')
        if (href) copy.setAttribute('href', href)
      }
      clean(node, copy, doc)
      target.appendChild(copy)
    } else if (BLOCKS.has(node.tagName)) {
      const block = doc.createElement('div')
      clean(node, block, doc)
      target.appendChild(block)
    } else {
      clean(node, target, doc) // unwrap: keep the text, drop the tag
    }
  }
}

// DOMParser documents are inert: no scripts run and no images load while this reads them.
export function cleanHtml(html: string): string {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const out = document.implementation.createHTMLDocument('')
  const root = out.createElement('div')
  clean(parsed.body, root, out)
  return root.innerHTML
}

export function isBlank(html: string): boolean {
  return new DOMParser().parseFromString(html, 'text/html').body.textContent?.trim() === ''
}

export const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
