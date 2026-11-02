import asyncio
import logging
import sys

# Playwright lance Chromium via subprocess — impossible avec SelectorEventLoop (Windows).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.db_errors import APIError
from app.middleware.dynamic_cors import DynamicCorsMiddleware
from app.routers import agent, billing, chat, crawl, health, leads, profiles, webhooks, widget, widget_handoff

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.exception_handler(APIError)
async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    logging.getLogger(__name__).error("Database error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# CORS dynamique : domaines des sites actifs (Supabase) + APP_URL + CORS_ORIGINS
app.add_middleware(DynamicCorsMiddleware)

app.include_router(health.router)
app.include_router(crawl.router, prefix="/api/v1", tags=["crawl"])
app.include_router(profiles.router, prefix="/api/v1", tags=["profiles"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(leads.router, prefix="/api/v1", tags=["leads"])
app.include_router(widget.router, prefix="/api/v1", tags=["widget"])
app.include_router(widget_handoff.router, prefix="/api/v1", tags=["widget-handoff"])
app.include_router(agent.router, prefix="/api/v1", tags=["agent"])
app.include_router(billing.router, prefix="/api/v1", tags=["billing"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
