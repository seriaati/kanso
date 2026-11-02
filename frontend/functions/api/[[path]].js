// Cloudflare Pages only: proxies /api/* to the backend at KANSO_API, so the browser sees one origin.
export function onRequest({ request, env }) {
  const url = new URL(request.url)
  const headers = new Headers(request.headers)
  headers.set('X-Forwarded-For', request.headers.get('CF-Connecting-IP') ?? '') // login rate limiting
  return fetch(new URL(url.pathname + url.search, env.KANSO_API), {
    method: request.method,
    headers,
    body: request.body,
    redirect: 'manual', // e.g. "Connect account" redirects the browser to Google
  })
}
