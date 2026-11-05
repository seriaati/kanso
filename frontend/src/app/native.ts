import { useEffect, useEffectEvent, useSyncExternalStore } from 'react'
import type { AppPlugin } from '@capacitor/app'
import type { PluginListenerHandle } from '@capacitor/core'
import { useTheme } from './theme'

// The Android app (android/) is this web app in a WebView, loaded from the kanso server, with
// Capacitor's bridge in the page. Its plugins load only there, so browsers never fetch them.
export const inApp = !!(window as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor?.isNativePlatform?.()

const app = inApp ? import('@capacitor/app') : null

// The app's own plugin: android/app/src/main/java/app/kanso/KansoPlugin.kt. Wrapped, as a plugin
// answers any property, `then` too, so a promise can't resolve to it.
type Kanso = {
  setSystemBars(options: { color: string; dark: boolean }): Promise<void>
  // New-mail notifications (app/notifications.ts).
  notificationState(): Promise<{ state: 'unsupported' | 'denied' | 'off' | 'on'; token?: string }>
  requestNotifications(): Promise<{ token?: string }>
  rememberNotifications(options: { token: string | null }): Promise<void>
  // Leaves this server for the app's screen that asks for one (Settings → Server).
  changeServer(): Promise<void>
}
export const kanso = inApp ? import('@capacitor/core').then(({ registerPlugin }) => ({ plugin: registerPlugin<Kanso>('Kanso') })) : null

/** The status and navigation bars take the page's paper color, with icons for the theme. */
export function useSystemBars() {
  const { dark } = useTheme()
  useEffect(() => {
    if (!kanso) return
    const color = getComputedStyle(document.documentElement).getPropertyValue('--paper').trim()
    void kanso.then(({ plugin }) => plugin.setSystemBars({ color, dark }))
  }, [dark])
}

/** Android's Back. `back` returns false when there's nothing to go back to: the app then goes to
 * the background, as Back does from any app's start. */
export function useBackButton(back: (canGoBack: boolean) => boolean) {
  const onBack = useEffectEvent(back)
  useEffect(
    () =>
      listen((App) =>
        App.addListener('backButton', ({ canGoBack }) => {
          if (!onBack(canGoBack)) void App.minimizeApp()
        }),
      ),
    [],
  )
}

/** The links Android opens the app with (mailto:, and its notifications' links to a thread), at
 * launch or while it's open. */
export function useAppLinks(open: (url: string) => void) {
  const onOpen = useEffectEvent(open)
  useEffect(() => listen((App) => App.addListener('appUrlOpen', ({ url }) => onOpen(url))), [])
}

function listen(add: (App: AppPlugin) => Promise<PluginListenerHandle>) {
  if (!app) return
  const handle = app.then(({ App }) => add(App))
  return () => void handle.then((h) => h.remove())
}

// Releases of the app are on GitHub (.github/workflows/android.yml). The app asks its server for
// the latest one (api/releases.py, which asks GitHub) when it starts and every few hours while open.
const RELEASE_CHECK_MS = 6 * 60 * 60 * 1000

let release: string | null = null // a newer app's download
const releaseListeners = new Set<() => void>()

type Release = { tag: string; url: string }

void app
  ?.then(({ App }) => App.getInfo())
  .then(({ version }) => {
    const mine = semver(version)
    if (!mine) return // a local build (dev)
    const check = () =>
      fetch('/api/app/release')
        .then((response) => (response.ok ? (response.json() as Promise<Release>) : null))
        .then((latest) => {
          const theirs = latest && semver(latest.tag)
          if (!latest || !theirs || !newer(theirs, mine)) return
          release = latest.url
          releaseListeners.forEach((listener) => listener())
        })
        .catch(() => undefined) // offline, or GitHub unreachable: next time
    void check()
    setInterval(() => void check(), RELEASE_CHECK_MS)
  })

function semver(version: string): number[] | null {
  const match = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(version)
  return match && match.slice(1).map(Number)
}

function newer(a: number[], b: number[]): boolean {
  const i = a.findIndex((n, j) => n !== b[j])
  return i >= 0 && a[i] > b[i]
}

/** A newer release of the Android app: the URL of its APK (else its release page). */
export function useAppUpdate(): string | null {
  return useSyncExternalStore(
    (listener) => {
      releaseListeners.add(listener)
      return () => releaseListeners.delete(listener)
    },
    () => release,
  )
}

/** Another host than the server's, so Capacitor hands it to the browser (Bridge.launchIntent),
 * which downloads it and offers to install it over this app. The WebView never loads it. */
export function applyAppUpdate(url: string) {
  location.assign(url)
}
