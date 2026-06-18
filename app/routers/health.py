from app.config import settings
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "chatbot-api",
        "push_enabled": settings.firebase_enabled,
    }
