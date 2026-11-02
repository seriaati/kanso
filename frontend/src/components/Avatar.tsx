import { useState } from 'react'
import { letterAvatar } from '../app/format'
import styles from './Avatar.module.css'

/** A sender's photo or their domain's logo, else their initial (also when the image fails to load).
 * The thread list and the reader draw the same one for the same sender. */
export function Avatar({ name, src, className }: { name: string; src: string | null; className?: string }) {
  const [failed, setFailed] = useState<string | null>(null)
  const classes = [styles.avatar, className].filter(Boolean).join(' ')
  if (src && src !== failed) {
    return <img className={classes} src={src} alt="" loading="lazy" decoding="async" onError={() => setFailed(src)} />
  }
  const { letter, color } = letterAvatar(name)
  return (
    <span className={classes} style={{ background: color }} aria-hidden="true">
      {letter}
    </span>
  )
}
