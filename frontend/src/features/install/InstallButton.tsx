import { useState } from 'react'
import { install, useInstall } from '../../app/install'
import { Dialog } from '../../components/Dialog'
import { Icon } from '../../components/Icon'
import styles from './InstallButton.module.css'

// On a phone, until kanso is installed to the home screen.
export function InstallButton() {
  const how = useInstall()
  const [steps, setSteps] = useState(false)
  if (!how) return null
  return (
    <>
      <button type="button" className={styles.install} onClick={() => (how === 'ios' ? setSteps(true) : install())}>
        <Icon name="install" size={15} />
        Install
      </button>
      {steps && (
        <Dialog title="Install kanso" onClose={() => setSteps(false)}>
          <p className={styles.steps}>
            In Safari, tap <Icon name="share" size={15} /> <strong>Share</strong>, then <strong>Add to Home Screen</strong>.
          </p>
          <button type="button" className={styles.ok} onClick={() => setSteps(false)}>
            OK
          </button>
        </Dialog>
      )}
    </>
  )
}
