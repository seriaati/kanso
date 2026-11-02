"""Security headers on every response, and CSRF protection for the API.

CSRF: every state-changing API request (anything but GET, HEAD and OPTIONS) must carry the
header `X-Requested-With: kanso`, which kanso's own frontend always sends. A page on another
site can't add a custom header to a request to kanso: a form can't set headers at all, and a
cross-origin fetch with one needs a CORS preflight, which kanso never grants. This sits on top
of the SameSite=Lax session cookie.

CSP: no inline scripts and nothing from other origins, except images (see IMG_SRC). An email's
sandboxed srcdoc frame inherits this policy on top of its own stricter one, so:
- img-src allows https: and http:, or "Show images" couldn't load an email's remote images. The
  frame's own policy still blocks them until the user asks (kanso.frontend EmailFrame).
- style-src allows 'unsafe-inline', which email HTML depends on. Scripts are what matter, and
  there are none: script-src is 'self' only, and the frame has no allow-scripts.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kanso.core.settings import settings

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "kanso"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https: http:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-src 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}
if settings.secure_cookies:  # served over HTTPS (PUBLIC_URL)
    HEADERS["Strict-Transport-Security"] = "max-age=31536000"


class Hardening:
    """Pure ASGI middleware, so Server-Sent Event streams pass through unbuffered."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        path: str = scope["path"]
        if (
            (path == "/api" or path.startswith("/api/"))
            and scope["method"] not in SAFE_METHODS
            and Headers(scope=scope).get(CSRF_HEADER) != CSRF_VALUE
        ):
            response = JSONResponse(
                {"detail": "Missing the X-Requested-With header (CSRF protection)"},
                status_code=403,
            )
            await response(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)
