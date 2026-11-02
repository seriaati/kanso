import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

// Web Push, turned on per browser or installed app; public/push-sw.js shows the notifications.

/** `unsupported`: plain http, no service worker, or iOS outside a Home Screen app. */
export type PushState = 'unsupported' | 'denied' | 'off' | 'on'

async function registration(): Promise<ServiceWorkerRegistration | undefined> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return undefined
  return navigator.serviceWorker.getRegistration()
}

async function current(): Promise<PushState> {
  const reg = await registration()
  if (!reg) return 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  const subscription = await reg.pushManager.getSubscription()
  return Notification.permission === 'granted' && subscription ? 'on' : 'off'
}

function decode(base64url: string): Uint8Array<ArrayBuffer> {
  const binary = atob(base64url.replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(binary, (c) => c.charCodeAt(0))
}

async function enable(permission: Promise<NotificationPermission>) {
  if ((await permission) !== 'granted') return
  const reg = await registration()
  if (!reg) return
  // One made with another key (SECRET_KEY changed) can't be reused: start over.
  await (await reg.pushManager.getSubscription())?.unsubscribe()
  const { public_key } = await api<{ public_key: string }>('/notifications/key')
  const subscription = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decode(public_key) })
  await api('/notifications/subscription', { method: 'PUT', body: subscription.toJSON() })
}

async function disable() {
  const subscription = await (await registration())?.pushManager.getSubscription()
  if (!subscription) return
  await api('/notifications/subscription', { method: 'DELETE', body: { endpoint: subscription.endpoint } })
  await subscription.unsubscribe()
}

export function usePush() {
  const [state, setState] = useState<PushState>()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => void current().then(setState), [])

  const set = useCallback((on: boolean) => {
    // Asked for at once, inside the click: Safari only shows the prompt for a user gesture.
    const change = on ? enable(Notification.requestPermission()) : disable()
    setPending(true)
    setError(null)
    change
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .then(current)
      .then(setState)
      .finally(() => setPending(false))
  }, [])

  return { state, set, pending, error }
}
