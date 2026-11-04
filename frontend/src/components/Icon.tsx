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
  replyAll: (
    <>
      <path d="M12 14L7 9l5-5M7 14L2 9l5-5" />
      <path d="M7 9h7a6 6 0 0 1 6 6v4" />
    </>
  ),
  unsubscribe: <path d="M3.5 6h17v12h-17zM3.5 6.5l8.5 6.5 8.5-6.5M4 3.5l16 17" />,
  // Label icons (labelIcons in app/mail.ts), besides some above.
  home: <path d="M4 11l8-7 8 7M6 9.5V20h12V9.5M10 20v-5.5h4V20" />,
  briefcase: <path d="M3.5 8h17v11.5h-17zM9 8V4.5h6V8M3.5 13h17" />,
  receipt: <path d="M6 3h12v18l-2-1.5-2 1.5-2-1.5-2 1.5-2-1.5L6 21zM9 8h6M9 12h6M9 16h3" />,
  cart: (
    <>
      <path d="M3 4h2.5l2 11h10.5l2-8H6.3" />
      <circle cx="9" cy="19.5" r="1.3" />
      <circle cx="17" cy="19.5" r="1.3" />
    </>
  ),
  plane: (
    <path d="M12 3c.8 0 1.5.7 1.5 1.5V10l7.5 4.5v2L13.5 14v4l2.5 2v1.5L12 20.5l-4 1V20l2.5-2v-4L3 16.5v-2L10.5 10V4.5C10.5 3.7 11.2 3 12 3z" />
  ),
  heart: (
    <path d="M12 20l-1.2-1.1C6.4 15 3.5 12.4 3.5 9.2 3.5 6.6 5.5 4.5 8 4.5c1.5 0 2.9.7 4 1.8 1.1-1.1 2.5-1.8 4-1.8 2.5 0 4.5 2.1 4.5 4.7 0 3.2-2.9 5.8-7.3 9.7z" />
  ),
  book: <path d="M4 5.5c2.5-1.5 5.5-1.5 8 0v14c-2.5-1.5-5.5-1.5-8 0zM12 5.5c2.5-1.5 5.5-1.5 8 0v14c-2.5-1.5-5.5-1.5-8 0z" />,
  people: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19.5a5.5 5.5 0 0 1 11 0M15.5 5.2a3 3 0 0 1 0 5.6M17 14.2c2 .7 3.5 2.7 3.5 5.3" />
    </>
  ),
  bell: <path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15zM10 21h4" />,
  bolt: <path d="M13 3L5 13.5h6L10 21l8-10.5h-6z" />,
  gift: <path d="M4 9h16v4H4zM5.5 13v7.5h13V13M12 9v11.5M12 9C11 6 7 4.5 7 7s3 2 5 2zM12 9c1-3 5-4.5 5-2s-3 2-5 2z" />,
  newspaper: <path d="M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2h-1M7.5 9h6M7.5 12.5h6M7.5 16h4" />,
  tag: (
    <>
      <path d="M4 4h8l8.5 8.5-8 8L4 12z" />
      <circle cx="8.5" cy="8.5" r="1.4" />
    </>
  ),
  package: <path d="M3.5 7.5L12 3.5l8.5 4v9L12 20.5l-8.5-4zM3.5 7.5L12 11.5l8.5-4M12 11.5v9" />,
  shield: <path d="M12 3l7.5 3v5.5c0 4.5-3.2 8-7.5 9.5-4.3-1.5-7.5-5-7.5-9.5V6z" />,
  bank: <path d="M3.5 9.5L12 4l8.5 5.5zM5.5 10v8M10 10v8M14 10v8M18.5 10v8M3.5 20.5h17" />,
  chat: <path d="M3.5 4.5h12V13H8l-4.5 3.5zM15.5 9h5v8.5l-3-2.5h-6.5v-2" />,
  school: <path d="M2.5 9L12 4.5 21.5 9 12 13.5zM6.5 11v5c3 2.5 8 2.5 11 0v-5M21.5 9v5" />,
  food: <path d="M7 3v18M4.5 3v5a2.5 2.5 0 0 0 5 0V3M17 21V3c-2 1-3.5 3.5-3.5 7v3H17" />,
  fitness: <path d="M6.5 7v10M17.5 7v10M3.5 9.5v5M20.5 9.5v5M6.5 12h11" />,
  music: (
    <>
      <path d="M9 17.5V5.5l11-2v12" />
      <circle cx="6.5" cy="17.5" r="2.5" />
      <circle cx="17.5" cy="15.5" r="2.5" />
    </>
  ),
  film: <path d="M4 4.5h16v15H4zM8 4.5v15M16 4.5v15M4 9.5h4M4 14.5h4M16 9.5h4M16 14.5h4" />,
  game: (
    <>
      <path d="M7 8h10a4 4 0 0 1 4 4l.5 4a2.5 2.5 0 0 1-4.5 1.5L15.5 16h-7L7 17.5A2.5 2.5 0 0 1 2.5 16L3 12a4 4 0 0 1 4-4zM7.5 10.5v3M6 12h3" />
      <circle cx="16" cy="11" r="0.6" />
      <circle cx="17.5" cy="13" r="0.6" />
    </>
  ),
  car: (
    <>
      <path d="M3.5 12.5l2-5.5h13l2 5.5v4.5h-17zM3.5 12.5h17M6 17v2.5M18 17v2.5" />
      <circle cx="7.5" cy="14.8" r="0.6" />
      <circle cx="16.5" cy="14.8" r="0.6" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z" />
      <circle cx="12" cy="10" r="2.3" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.3 2.4 3.5 5.2 3.5 8.5s-1.2 6.1-3.5 8.5c-2.3-2.4-3.5-5.2-3.5-8.5s1.2-6.1 3.5-8.5z" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="15.5" r="4" />
      <path d="M11 12.5l8.5-8.5M16.5 7l2.5 2.5M14 9.5l2 2" />
    </>
  ),
  megaphone: <path d="M4 10v4h3l9 4.5v-13L7 10zM7 14l1.5 5.5h2.5L10 15M19 9.5v5" />,
  ticket: <path d="M3.5 7h17v3a2 2 0 0 0 0 4v3h-17v-3a2 2 0 0 0 0-4zM14 7v2M14 11v2M14 15v2" />,
  paw: (
    <>
      <path d="M12 12.5c-2.5 0-5 3-5 5.2 0 1.4 1.3 2 2.4 2 1 0 1.6-.6 2.6-.6s1.6.6 2.6.6c1.1 0 2.4-.6 2.4-2 0-2.2-2.5-5.2-5-5.2z" />
      <circle cx="5.5" cy="10.5" r="1.7" />
      <circle cx="9.3" cy="6.5" r="1.7" />
      <circle cx="14.7" cy="6.5" r="1.7" />
      <circle cx="18.5" cy="10.5" r="1.7" />
    </>
  ),
  leaf: <path d="M5 19.5c0-8.5 5-14.5 15-14.5 0 10-6 15-14 15M5 19.5l7-7" />,
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
  sidebar: <path d="M4 5h16v14H4zM9.5 5v14" />,
  // Drawn a little low: the arrowhead carries its weight at the top.
  send: <path d="M12 20.5v-14M5 13.5l7-7 7 7" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  history: <path d="M3.5 12a8.5 8.5 0 1 0 2.5-6M3.5 4v4h4M12 7.5V12l3 2" />,
  calendar: <path d="M4 5.5h16V20H4zM4 10h16M8.5 3.5v4M15.5 3.5v4" />,
  image: <path d="M4 5h16v14H4zM4 16l4.5-4.5 4 4 2.5-2.5L20 18M14.5 9.5h.01" />,
  code: <path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5" />,
  quote: <path d="M6 17.5c2.5-.8 3.5-2.6 3.5-5.5V7.5H5v4.5h4.5M15 17.5c2.5-.8 3.5-2.6 3.5-5.5V7.5H14v4.5h4.5" />,
  play: <path d="M8 5.5v13l10.5-6.5z" />,
  copy: <path d="M9 9h11v11H9zM15 9V4H4v11h5" />,
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
  // Done's check is only a line: filled, it is drawn bolder instead.
  const bolder = filled && name === 'done'
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled && !bolder ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth={bolder ? strokeWidth + 0.8 : strokeWidth}
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  )
}
