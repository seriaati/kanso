import type { LabelIcon } from '../app/mail'
import { Icon } from './Icon'
import styles from './LabelMark.module.css'

type Props = {
  color: string
  icon?: LabelIcon | null
  /** The square it fills, so names line up whether a label has an icon or a dot. */
  size: number
  dot?: number
  className?: string
}

/** A label's icon in its color, or without one a colored dot. */
export function LabelMark({ color, icon, size, dot = 8, className }: Props) {
  return (
    <span className={`${styles.mark} ${className ?? ''}`} style={{ color, width: size, height: size }} aria-hidden="true">
      {icon ? <Icon name={icon} size={size} strokeWidth={2} /> : <span className={styles.dot} style={{ width: dot, height: dot }} />}
    </span>
  )
}
