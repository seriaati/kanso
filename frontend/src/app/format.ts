import { accountColors } from './accounts'

const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', hourCycle: 'h23' })
const weekday = new Intl.DateTimeFormat(undefined, { weekday: 'short' })
const monthDay = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const fullDate = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
const longDate = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
const longDateYear = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })

const DAY = 86_400_000

export function shortDate(iso: string, now = new Date()): string {
  const date = new Date(iso)
  if (date.toDateString() === now.toDateString()) return time.format(date)
  if (now.getTime() - date.getTime() < 6 * DAY) return weekday.format(date)
  if (date.getFullYear() === now.getFullYear()) return monthDay.format(date)
  return fullDate.format(date)
}

export function longDateTime(iso: string, now = new Date()): string {
  const date = new Date(iso)
  const day = date.getFullYear() === now.getFullYear() ? longDate : longDateYear
  return `${day.format(date)}, ${time.format(date)}`
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N}\s]/gu, '').split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return [...words[0]].slice(0, 2).join('').toUpperCase()
  return (words[0][0] + words[words.length - 1][0]).toUpperCase()
}

/** A letter avatar: the name's first letter, on a color that stays the same for that name. */
export function letterAvatar(name: string): { letter: string; color: string } {
  const letter = [...name.replace(/[^\p{L}\p{N}]/gu, '')][0]?.toUpperCase() ?? '?'
  let hash = 0
  for (const char of name.toLowerCase()) hash = (hash * 31 + char.codePointAt(0)!) | 0
  return { letter, color: accountColors[Math.abs(hash) % accountColors.length].value }
}
