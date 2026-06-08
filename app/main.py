import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import chat, crawl, health, leads, webhooks, widget

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title=settings.app_name, version="0.1.0")

_cors = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in _cors else _cors,
    allow_credentials="*" not in _cors,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(crawl.router, prefix="/api/v1", tags=["crawl"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(leads.router, prefix="/api/v1", tags=["leads"])
app.include_router(widget.router, prefix="/api/v1", tags=["widget"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
