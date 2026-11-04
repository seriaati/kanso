import type { MenuItem } from '../../components/Menu'
import { Icon } from '../../components/Icon'
import { Sheet } from './Sheet'
import styles from './ActionSheet.module.css'

type Props = {
  /** What the actions are for, e.g. a thread's subject. */
  title: string
  items: MenuItem[]
  onClose: () => void
}

// The phone's context menu: the same items as the desktop's right-click Menu, in a bottom sheet.
export function ActionSheet({ title, items, onClose }: Props) {
  return (
    <Sheet label={`Actions for ${title}`} role="menu" onClose={onClose}>
      <h2 className={styles.title}>{title}</h2>
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className={styles.item}
          onClick={() => {
            onClose()
            item.onSelect()
          }}
        >
          {item.icon && <Icon name={item.icon} size={17} />}
          {item.label}
        </button>
      ))}
    </Sheet>
  )
}
