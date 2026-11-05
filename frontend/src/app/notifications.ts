import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { kanso } from './native'

// Web Push, turned on per browser or installed app; public/push-sw.js shows the notifications.
// The Android app uses FCM instead, and draws them itself (android/…/Notifications.kt).

/** `unsupported`: plain http, no service worker, or iOS outside a Home Screen app; in the Android
 * app, a Kanso server without Firebase settings for it (FIREBASE_ANDROID_CONFIG). */
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

// The Android app's: the plugin asks Android, and Kanso gets the FCM token.
const app = kanso && appPush(kanso)

function appPush(native: NonNullable<typeof kanso>) {
  return {
    current: async (): Promise<PushState> => (await (await native).plugin.notificationState()).state,
    async set(on: boolean) {
      const { plugin } = await native
      if (on) {
        const { token } = await plugin.requestNotifications()
        if (!token) return
        await api('/notifications/fcm', { method: 'PUT', body: { token } })
        await plugin.rememberNotifications({ token })
      } else {
        const { token } = await plugin.notificationState()
        if (token) await api('/notifications/fcm', { method: 'DELETE', body: { token } })
        await plugin.rememberNotifications({ token: null })
      }
    },
  }
}

export function usePush() {
  const [state, setState] = useState<PushState>()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => void (app?.current ?? current)().then(setState), [])

  const set = useCallback((on: boolean) => {
    // Asked for at once, inside the click: Safari only shows the prompt for a user gesture.
    const change = app ? app.set(on) : on ? enable(Notification.requestPermission()) : disable()
    setPending(true)
    setError(null)
    change
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .then(app?.current ?? current)
      .then(setState)
      .finally(() => setPending(false))
  }, [])

  return { state, set, pending, error }
}
