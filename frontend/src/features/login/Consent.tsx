import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../../app/api'
import styles from './Login.module.css'

type SignIn = { client_name: string | null; redirect_uri: string }

// An AI agent signing in to Kanso's MCP server (OAuth): the user allows or denies it here.
export function Consent({ request }: { request: string }) {
  const path = `/oauth/requests/${encodeURIComponent(request)}`
  const signIn = useQuery({ queryKey: ['oauth', request], queryFn: () => api<SignIn>(path), retry: false })
  const answer = useMutation({
    mutationFn: (allow: boolean) => api<{ redirect_uri: string }>(`${path}/${allow ? 'approve' : 'deny'}`, { method: 'POST' }),
    onSuccess: ({ redirect_uri }) => window.location.assign(redirect_uri),
  })
  const name = signIn.data?.client_name || 'An app'
  const error = signIn.error ?? answer.error

  return (
    <main className={styles.page}>
      <div className={styles.form}>
        <h1 className={styles.title}>
          <img src="/favicon.svg" alt="" width="44" height="44" />
          Kanso <span lang="ja">簡素</span>
        </h1>
        {signIn.data && (
          <>
            <p className={styles.text}>
              <strong>{name}</strong> wants to read your mail: it can search and read every message in your accounts, but
              not change, send or delete anything.
            </p>
            <p className={styles.note}>You’ll go back to {new URL(signIn.data.redirect_uri).host}.</p>
            <div className={styles.actions}>
              <button type="button" className={styles.deny} disabled={answer.isPending} onClick={() => answer.mutate(false)}>
                Deny
              </button>
              <button type="button" className={styles.submit} disabled={answer.isPending} onClick={() => answer.mutate(true)}>
                Allow
              </button>
            </div>
          </>
        )}
        {error && (
          <p role="alert" className={styles.error}>
            {error.message}
          </p>
        )}
      </div>
    </main>
  )
}
