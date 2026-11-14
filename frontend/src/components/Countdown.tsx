import { useEffect, useState } from 'react'

/** The seconds left until `at`: "7s". */
export function Countdown({ at }: { at: number }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(timer)
  }, [])
  // A timer isn't read out again on every change, as the status around it would be.
  return <span role="timer">{Math.max(Math.ceil((at - now) / 1000), 0)}s</span>
}
