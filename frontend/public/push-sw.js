// Loaded into the generated service worker by workbox.importScripts (vite.config.ts).

self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {}
  event.waitUntil(
    self.registration.showNotification(data.title || 'kanso', {
      body: data.body || '',
      icon: '/icon-192.png',
      // A newer message in the same thread replaces its notification, and alerts again.
      tag: data.tag,
      renotify: Boolean(data.tag),
      data: { url: data.url || '/inbox' },
    }),
  )
})

// Opens the thread in a kanso window already open (the app navigates, no reload), or a new one.
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = event.notification.data?.url || '/inbox'
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      const client = windows.find((c) => c.focused) || windows[0]
      if (!client) return self.clients.openWindow(url)
      await client.focus()
      client.postMessage({ type: 'kanso:open', url })
    })(),
  )
})
