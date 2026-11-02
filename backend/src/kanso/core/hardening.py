"""CSRF: another site can't add a custom header without a CORS preflight, which kanso never grants.

CSP: email frames inherit it, so img-src allows remote images ("Show images") and style-src
'unsafe-inline' (email HTML needs it); scripts stay 'self' only.
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
if settings.secure_cookies:
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
