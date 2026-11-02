import styles from './ShortcutHelp.module.css'

const shortcuts = [
  ['j / k', 'Next / previous thread'],
  ['Enter / o', 'Open thread'],
  ['Esc', 'Back to the list'],
  ['e', 'Done (or move back to the inbox)'],
  ['#', 'Delete (or restore from Trash)'],
  ['s', 'Star'],
  ['=', 'Important'],
  ['l', 'Label'],
  ['u', 'Mark unread / read'],
  ['x', 'Select; actions apply to all selected'],
  ['z', 'Undo Done or Delete'],
  ['c', 'Compose'],
  ['r / a / f', 'Reply / reply all / forward'],
  ['Ctrl+Enter', 'Send (⌘+Enter on a Mac)'],
  ['/', 'Search'],
  ['q', 'Ask the assistant'],
  ['?', 'This help'],
]

export function ShortcutHelp({ ai, onClose }: { ai: boolean; onClose: () => void }) {
  return (
    <div className={styles.backdrop} onClick={onClose}>
      <div className={styles.card} role="dialog" aria-label="Keyboard shortcuts" onClick={(event) => event.stopPropagation()}>
        <h2 className={styles.title}>Keyboard shortcuts</h2>
        <dl className={styles.list}>
          {shortcuts.filter(([keys]) => ai || keys !== 'q').map(([keys, action]) => (
            <div key={keys} className={styles.item}>
              <dt>
                {keys.split(' / ').map((key, i) => (
                  <span key={key}>
                    {i > 0 && ' / '}
                    <kbd>{key}</kbd>
                  </span>
                ))}
              </dt>
              <dd>{action}</dd>
            </div>
          ))}
        </dl>
        <p className={styles.note}>Every button’s tooltip names its shortcut.</p>
      </div>
    </div>
  )
}
