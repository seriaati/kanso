// Loaded into the generated service worker by workbox.importScripts (vite.config.ts).

const CSRF = { 'X-Requested-With': 'kanso' } // as in src/app/api.ts
// As accountColors (src/app/accounts.ts), which letterAvatar (src/app/format.ts) picks from.
const COLORS = ['#2F4B6E', '#A07338', '#556B3F', '#87607E', '#A35A3A', '#4F6E8C', '#6B6B66']
const SIZE = 192

/** The sender's photo or logo in a circle, else their initial, as the app draws them, as a data:
 * URL: the image needs the session cookie, and the notification can't draw an SVG logo. */
async function picture(name, src) {
  const canvas = new OffscreenCanvas(SIZE, SIZE)
  const ctx = canvas.getContext('2d')
  ctx.beginPath()
  ctx.arc(SIZE / 2, SIZE / 2, SIZE / 2, 0, 2 * Math.PI)
  ctx.clip()
  let bitmap = null
  if (src) {
    try {
      const response = await fetch(src)
      if (response.ok) bitmap = await createImageBitmap(await response.blob())
    } catch {
      // offline, or an SVG (createImageBitmap can't decode one here): the initial
    }
  }
  if (bitmap) {
    const scale = Math.max(SIZE / bitmap.width, SIZE / bitmap.height)
    const [w, h] = [bitmap.width * scale, bitmap.height * scale]
    ctx.fillStyle = '#F7F5EE' // logos with transparency need a light ground
    ctx.fillRect(0, 0, SIZE, SIZE)
    ctx.drawImage(bitmap, (SIZE - w) / 2, (SIZE - h) / 2, w, h)
  } else {
    // letterAvatar
    const letter = [...name.replace(/[^\p{L}\p{N}]/gu, '')][0]?.toUpperCase() ?? '?'
    let hash = 0
    for (const char of name.toLowerCase()) hash = (hash * 31 + char.codePointAt(0)) | 0
    ctx.fillStyle = COLORS[Math.abs(hash) % COLORS.length]
    ctx.fillRect(0, 0, SIZE, SIZE)
    ctx.fillStyle = '#F7F5EE'
    ctx.font = `bold ${SIZE * 0.45}px sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(letter, SIZE / 2, SIZE / 2 + SIZE * 0.03)
  }
  const blob = await canvas.convertToBlob()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {}
  event.waitUntil(
    (async () => {
      const icon = data.sender ? await picture(data.sender, data.image).catch(() => undefined) : undefined
      await self.registration.showNotification(data.title || 'Kanso', {
        body: data.body || '',
        icon: icon || '/icon-192.png',
        badge: '/badge-96.png', // Android's small icon: the ensō instead of a bell
        // A newer message in the same thread replaces its notification, and alerts again.
        tag: data.tag,
        renotify: Boolean(data.tag),
        // Chrome shows at most two.
        actions: data.thread_id
          ? [
              { action: 'done', title: 'Done' },
              { action: 'delete', title: 'Delete' },
            ]
          : [],
        data: { url: data.url || '/inbox', thread_id: data.thread_id },
      })
    })(),
  )
})

// Opens the thread in a Kanso window already open (the app navigates, no reload), or a new one.
async function open(url) {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  const client = windows.find((c) => c.focused) || windows[0]
  if (!client) return self.clients.openWindow(url)
  await client.focus()
  client.postMessage({ type: 'kanso:open', url })
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const { url = '/inbox', thread_id } = event.notification.data || {}
  if ((event.action === 'done' || event.action === 'delete') && thread_id) {
    const act = async () => {
      const response = await fetch('/api/threads/actions', {
        method: 'POST',
        headers: { ...CSRF, 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: event.action, thread_ids: [thread_id] }),
      })
      if (!response.ok) throw new Error(response.statusText)
    }
    // E.g. logged out: open the thread instead, so it can be done there.
    event.waitUntil(act().catch(() => open(url)))
    return
  }
  event.waitUntil(open(url))
})
