import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, queryClient } from '../../app/api'
import styles from './Login.module.css'

export function Login() {
  const [password, setPassword] = useState('')
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
        {login.error && (
          <p role="alert" className={styles.error}>
            {login.error.message}
          </p>
        )}
      </form>
    </main>
  )
}
