from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.services.cors_origins import is_origin_allowed

_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_ALLOW_HEADERS = "Authorization, Content-Type, Accept, X-Requested-With"


def _is_public_embed_route(path: str) -> bool:
    """Routes appelées par le widget dans le navigateur du visiteur (clé widget = auth)."""
    if path.startswith("/api/v1/widget/"):
        return True
    if path == "/api/v1/chat":
        return True
    return False


def _cors_allowed(origin: str | None, path: str) -> bool:
    if not origin:
        return False
    if _is_public_embed_route(path):
        return True
    return is_origin_allowed(origin)


class DynamicCorsMiddleware(BaseHTTPMiddleware):
    """CORS : routes widget/chat ouvertes à toute origine (embed SaaS) ; reste filtré par domaine."""

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        path = request.url.path

        if request.method == "OPTIONS" and origin:
            if not _cors_allowed(origin, path):
                return Response(status_code=403, content="Origin not allowed")
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": _ALLOW_METHODS,
                    "Access-Control-Allow-Headers": _ALLOW_HEADERS,
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )

        response = await call_next(request)

        if origin and _cors_allowed(origin, path):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"

        return response
