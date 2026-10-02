from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class LocalSessionMiddleware(BaseHTTPMiddleware):
    """Keep the credential-bearing API local and reject cross-site requests."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        try:
            address = urlsplit(f"http://{host}")
            if address.hostname not in {"127.0.0.1", "localhost", "::1"} or address.username or address.password:
                raise ValueError()
            port = address.port or 80
        except ValueError:
            return JSONResponse({"detail": "Only loopback hosts are allowed."}, status_code=400)
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{host}"
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site access is not allowed."}, status_code=403)
        cookie_name = f"studio_session_{port}"
        if request.url.path.startswith("/api/"):
            cookie = request.cookies.get(cookie_name, "")
            if not secrets.compare_digest(cookie.encode("utf-8"), self.token.encode("utf-8")):
                return JSONResponse({"detail": "Open the local Web UI first."}, status_code=401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                supplied = request.headers.get("x-studio-token", "")
                if not secrets.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8")):
                    return JSONResponse({"detail": "Missing or invalid session token."}, status_code=403)
        response = await call_next(request)
        if request.url.path == "/":
            response.set_cookie(cookie_name, self.token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        return response
