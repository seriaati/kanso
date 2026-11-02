import type { ReactNode } from 'react'

// The assistant's answers in a tiny, safe subset of Markdown: paragraphs, "- " and "1. " lists,
// **bold**, and links to threads written as [subject](thread:123). Everything is rendered as
// React text (escaped); any other link or markup shows as the plain text it is.

type Props = { text: string; threadHref: (id: number) => string; onOpenThread: (id: number) => void }

const INLINE = /\*\*(.+?)\*\*|\[([^\]\n]+)\]\(thread:(\d+)\)/g

function inline(text: string, props: Props, key: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const [, bold, label, id] = match
    if (bold !== undefined) nodes.push(<b key={`${key}-${match.index}`}>{inline(bold, props, `${key}-${match.index}`)}</b>)
    else {
      const threadId = Number(id)
      nodes.push(
        <a
          key={`${key}-${match.index}`}
          href={props.threadHref(threadId)}
          onClick={(event) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return // a new tab
            event.preventDefault()
            props.onOpenThread(threadId)
          }}
        >
          {label}
        </a>,
      )
    }
    last = match.index + match[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

const ITEM = /^\s*(?:[-*•]|\d+[.)])\s+/

export function Markdown(props: Props) {
  const blocks: ReactNode[] = []
  props.text
    .trim()
    .split(/\n\s*\n/)
    .forEach((chunk, b) => {
      const lines = chunk.split('\n').filter((line) => line.trim())
      // A chunk may start with a line of text and go on with a list.
      let i = 0
      const para: string[] = []
      while (i < lines.length && !ITEM.test(lines[i])) para.push(lines[i++])
      if (para.length) blocks.push(<p key={`p${b}`}>{inline(para.join('\n'), props, `p${b}`)}</p>)
      if (i < lines.length) {
        const ordered = /^\s*\d/.test(lines[i])
        const items: string[] = []
        for (; i < lines.length; i++) {
          if (ITEM.test(lines[i])) items.push(lines[i].replace(ITEM, ''))
          else if (items.length) items[items.length - 1] += `\n${lines[i].trim()}`
        }
        const List = ordered ? 'ol' : 'ul'
        blocks.push(
          <List key={`l${b}`}>
            {items.map((item, n) => (
              <li key={n}>{inline(item, props, `l${b}-${n}`)}</li>
            ))}
          </List>,
        )
      }
    })
  return <>{blocks}</>
}
