from app.config import settings
from app.services.page_fetcher import check_playwright_ready
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    playwright = await check_playwright_ready()
    return {
        "status": "ok",
        "service": "chatbot-api",
        "push_enabled": settings.firebase_enabled,
        "playwright": playwright,
    }
