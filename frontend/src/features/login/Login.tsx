import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, queryClient } from '../../app/api'
import { useDemoPassword } from '../../app/settings'
import styles from './Login.module.css'

export function Login() {
  const demo = useDemoPassword()
  const [typed, setPassword] = useState<string | null>(null)
  const password = typed ?? demo ?? '' // a demo's password is filled in
  const login = useMutation({
    mutationFn: () => api('/auth/login', { method: 'POST', body: { password } }),
    onSuccess: () => queryClient.resetQueries(),
  })

  return (
    <main className={styles.page}>
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault()
          login.mutate()
        }}
      >
        <h1 className={styles.title}>
          <img src="/favicon.svg" alt="" width="44" height="44" />
          kanso <span lang="ja">簡素</span>
        </h1>
        <label className={styles.field}>
          Password
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <button type="submit" className={styles.submit} disabled={!password || login.isPending}>
          Log in
        </button>
        {demo && (
          <p className={styles.demo}>
            A demo with made-up mail. The password is <strong>{demo}</strong>; changes reset every day.
          </p>
        )}
        {login.error && (
          <p role="alert" className={styles.error}>
            {login.error.message}
          </p>
        )}
      </form>
    </main>
  )
}
