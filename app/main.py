import logging

from fastapi import FastAPI

from app.config import settings
from app.middleware.dynamic_cors import DynamicCorsMiddleware
from app.routers import agent, chat, crawl, health, leads, webhooks, widget, widget_handoff

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title=settings.app_name, version="0.1.0")

# CORS dynamique : domaines des sites actifs (Supabase) + APP_URL + CORS_ORIGINS
app.add_middleware(DynamicCorsMiddleware)

app.include_router(health.router)
app.include_router(crawl.router, prefix="/api/v1", tags=["crawl"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(leads.router, prefix="/api/v1", tags=["leads"])
app.include_router(widget.router, prefix="/api/v1", tags=["widget"])
app.include_router(widget_handoff.router, prefix="/api/v1", tags=["widget-handoff"])
app.include_router(agent.router, prefix="/api/v1", tags=["agent"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
