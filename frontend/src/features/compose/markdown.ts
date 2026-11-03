import { escapeHtml, safeUrl, ZWSP } from './editor'

// Markdown shortcuts, applied as they are typed. Each goes through execCommand, so Undo
// (Ctrl/⌘+Z) turns it back into the characters typed.

const BLOCK_TAGS = new Set(['DIV', 'P', 'LI', 'BLOCKQUOTE', 'H1', 'H2', 'H3', 'PRE'])

// At the start of a line, then a space.
const BLOCK_RULES: [RegExp, () => void][] = [
  [/^[-*]$/, () => document.execCommand('insertUnorderedList')],
  [/^1[.)]$/, () => document.execCommand('insertOrderedList')],
  [/^>$/, () => document.execCommand('formatBlock', false, '<blockquote>')],
  [/^#$/, () => document.execCommand('formatBlock', false, '<h1>')],
  [/^##$/, () => document.execCommand('formatBlock', false, '<h2>')],
  [/^###$/, () => document.execCommand('formatBlock', false, '<h3>')],
  [/^```$/, () => document.execCommand('formatBlock', false, '<pre>')],
]

// Closed by the last character typed. No word character may come right before one (snake_case
// and 2*3*4 stay).
const INLINE_RULES: [RegExp, (m: RegExpExecArray) => string | null][] = [
  [/(?<![\w*])\*\*([^*\s](?:[^*]*[^*\s])?)\*\*$/, (m) => `<b>${escapeHtml(m[1])}</b>`],
  [/(?<![\w_])__([^_\s](?:[^_]*[^_\s])?)__$/, (m) => `<b>${escapeHtml(m[1])}</b>`],
  [/(?<![\w*])\*([^*\s](?:[^*]*[^*\s])?)\*$/, (m) => `<i>${escapeHtml(m[1])}</i>`],
  [/(?<![\w_])_([^_\s](?:[^_]*[^_\s])?)_$/, (m) => `<i>${escapeHtml(m[1])}</i>`],
  [/`([^`]+)`$/, (m) => `<code>${escapeHtml(m[1])}</code>`],
  [
    /\[([^\]]+)\]\(([^()\s]+)\)$/,
    (m) => {
      const url = safeUrl(m[2])
      return url && `<a href="${escapeHtml(url)}">${escapeHtml(m[1])}</a>`
    },
  ],
]

function blockOf(node: Node, editor: HTMLElement): HTMLElement {
  for (let at: Node | null = node; at && at !== editor; at = at.parentNode)
    if (at instanceof HTMLElement && BLOCK_TAGS.has(at.tagName)) return at
  return editor
}

/** Call after text was typed into the editor; returns whether a shortcut was applied. */
export function applyMarkdown(editor: HTMLElement, typed: string): boolean {
  const selection = getSelection()
  if (!selection?.isCollapsed || !selection.rangeCount) return false
  const node = selection.anchorNode
  const offset = selection.anchorOffset
  if (!(node instanceof Text) || !editor.contains(node)) return false
  const block = blockOf(node, editor)
  if (block.closest('pre') || node.parentElement?.closest('code')) return false

  const select = (start: Node, startOffset: number) => {
    const range = document.createRange()
    range.setStart(start, startOffset)
    range.setEnd(node, offset)
    selection.removeAllRanges()
    selection.addRange(range)
  }

  if (typed === ' ' && block.tagName !== 'LI') {
    const range = document.createRange()
    range.setStart(block, 0)
    range.setEnd(node, offset)
    const line = range.toString().replace(/\u00a0/g, ' ').replaceAll(ZWSP, '')
    const rule = line.endsWith(' ') && BLOCK_RULES.find(([pattern]) => pattern.test(line.slice(0, -1)))
    if (!rule) return false
    select(block, 0)
    document.execCommand('delete')
    rule[1]()
    return true
  }

  const before = node.data.slice(0, offset)
  for (const [pattern, html] of INLINE_RULES) {
    const match = pattern.exec(before)
    const replacement = match && html(match)
    if (!match || !replacement) continue
    select(node, match.index)
    document.execCommand('insertHTML', false, replacement + ZWSP)
    return true
  }
  return false
}
