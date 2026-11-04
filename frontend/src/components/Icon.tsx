import type { ReactNode } from 'react'

const paths = {
  done: <path d="M4.5 12.5l5 5L19.5 7" />,
  inbox: <path d="M3 13h5l2 3h4l2-3h5M5.5 5h13L21 13v6H3v-6z" />,
  sent: <path d="M21 3L3 10.5l7.5 3M21 3l-7.5 18-3-7.5M21 3L10.5 13.5" />,
  draft: <path d="M14 3H5v18h14V8zM14 3v5h5M8.5 13h7M8.5 17h4.5" />,
  accounts: (
    <>
      <path d="M12 3L3 8l9 5 9-5z" />
      <path d="M3 12.5l9 5 9-5M3 17l9 5 9-5" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
  logout: <path d="M10 4H5v16h5M15 8l4 4-4 4M19 12H9" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
    </>
  ),
  moon: <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" />,
  system: <path d="M3 5h18v11H3zM8 20h8M12 16v4" />,
  assistant: <path d="M4 5h16v11h-9l-5 4v-4H4z" />,
  sparkle: <path d="M10 3c.7 5 2 6.3 7 7-5 .7-6.3 2-7 7-.7-5-2-6.3-7-7 5-.7 6.3-2 7-7zM18 14c.3 2.2.8 2.7 3 3-2.2.3-2.7.8-3 3-.3-2.2-.8-2.7-3-3 2.2-.3 2.7-.8 3-3z" />,
  spam: <path d="M8.3 3h7.4L21 8.3v7.4L15.7 21H8.3L3 15.7V8.3zM12 7.5v5.5M12 16.2v.3" />,
  select: <path d="M4.5 4.5h15v15h-15zM8.5 12.2l2.5 2.5 4.5-5" />,
  mailOpen: <path d="M3.5 10l8.5-6 8.5 6v9.5h-17zM3.5 10l8.5 6 8.5-6" />,
  mail: <path d="M3.5 6h17v12h-17zM3.5 6.5l8.5 6.5 8.5-6.5" />,
  compact: <path d="M4 5h16M4 9.7h16M4 14.3h16M4 19h16" />,
  comfortable: <path d="M4 6h16M4 12h16M4 18h16" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  delete: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  restore: <path d="M4 7h16M6 7l1 13h10l1-13M9 7V4h6v3M3.5 3.5l17 17" />,
  star: <path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z" />,
  important: <path d="M5 21V4h13l-3 4.5L18 13H5" />,
  install: <path d="M12 3.5v11M7.5 10l4.5 4.5 4.5-4.5M4.5 20h15" />,
  share: <path d="M12 14V3.5M8 7.5l4-4 4 4M8.5 10.5H5.5v10h13v-10h-3" />,
  label: (
    <>
      <path d="M3 12V4h8l10 10-8 8z" />
      <circle cx="7.5" cy="8.5" r="1.5" />
    </>
  ),
  reply: (
    <>
      <path d="M9 14L4 9l5-5" />
      <path d="M4 9h10a6 6 0 0 1 6 6v4" />
    </>
  ),
  forward: (
    <>
      <path d="M15 14l5-5-5-5" />
      <path d="M20 9H10a6 6 0 0 0-6 6v4" />
    </>
  ),
  more: (
    <g fill="currentColor" stroke="none">
      <circle cx="5" cy="12" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="19" cy="12" r="1.6" />
    </g>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-4-4" />
    </>
  ),
  compose: <path d="M4 20h4L19 9l-4-4L4 16z" />,
  minimize: <path d="M6 18h12" />,
  chevronDown: <path d="M6 9l6 6 6-6" />,
  chevronUp: <path d="M6 15l6-6 6 6" />,
  chevronRight: <path d="M9 6l6 6-6 6" />,
  back: <path d="M15 6l-6 6 6 6" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  // Drawn a little low: the arrowhead carries its weight at the top.
  send: <path d="M12 20.5v-14M5 13.5l7-7 7 7" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  history: <path d="M3.5 12a8.5 8.5 0 1 0 2.5-6M3.5 4v4h4M12 7.5V12l3 2" />,
  calendar: <path d="M4 5.5h16V20H4zM4 10h16M8.5 3.5v4M15.5 3.5v4" />,
  image: <path d="M4 5h16v14H4zM4 16l4.5-4.5 4 4 2.5-2.5L20 18M14.5 9.5h.01" />,
  code: <path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5" />,
  quote: <path d="M6 17.5c2.5-.8 3.5-2.6 3.5-5.5V7.5H5v4.5h4.5M15 17.5c2.5-.8 3.5-2.6 3.5-5.5V7.5H14v4.5h4.5" />,
  attach: <path d="M20 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.6-8.6a3.4 3.4 0 0 1 4.8 4.8l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9" />,
  link: (
    <>
      <path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1.2 1.2" />
      <path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2" />
    </>
  ),
  listBullet: (
    <>
      <path d="M9 6h11M9 12h11M9 18h11" />
      <g fill="currentColor" stroke="none">
        <circle cx="4.5" cy="6" r="1.3" />
        <circle cx="4.5" cy="12" r="1.3" />
        <circle cx="4.5" cy="18" r="1.3" />
      </g>
    </>
  ),
  listNumber: (
    <>
      <path d="M10 6h10M10 12h10M10 18h10" />
      <path d="M4 4.5l1.5-1v5M3.5 14h3l-3 4h3" strokeWidth="1.5" />
    </>
  ),
} satisfies Record<string, ReactNode>

export type IconName = keyof typeof paths

type Props = {
  name: IconName
  size?: number
  strokeWidth?: number
  filled?: boolean
}

export function Icon({ name, size = 17, strokeWidth = 1.8, filled = false }: Props) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  )
}
