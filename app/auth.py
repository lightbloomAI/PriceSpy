import bcrypt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

from .config import get_settings

# Routes that never require auth
PUBLIC_PATHS = {"/login", "/health"}
PUBLIC_PREFIXES = ("/static/",)

# API paths that accept Bearer token (cron job)
CRON_API_PREFIXES = ("/api/product/", "/api/products")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(
        plain_password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Always allow public paths
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # 2. API routes: accept Bearer token OR session
        is_api = path.startswith("/api/")
        if is_api:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                settings = get_settings()
                if settings.cron_secret and token == settings.cron_secret:
                    return await call_next(request)

        # 3. Check session
        user_email = request.session.get("user_email")
        if user_email:
            return await call_next(request)

        # 4. Not authenticated
        if is_api:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        return RedirectResponse(url="/login", status_code=303)
