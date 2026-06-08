from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.services.cors_origins import is_origin_allowed

_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_ALLOW_HEADERS = "Authorization, Content-Type, Accept, X-Requested-With"


class DynamicCorsMiddleware(BaseHTTPMiddleware):
    """Autorise le widget embed depuis les domaines enregistrés (table sites)."""

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")

        if request.method == "OPTIONS" and origin:
            if not is_origin_allowed(origin):
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

        if origin and is_origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"

        return response
