// Cloudflare Pages only: passes /api/* to the backend in the project's KANSO_API variable (e.g.
// https://kanso-api.seria.moe), so the browser still sees one origin. A Docker install serves the
// frontend from the backend itself and never uses this file.
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
