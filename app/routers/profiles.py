import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.profile_compose import compose_profile_presentation

logger = logging.getLogger(__name__)

router = APIRouter()


class ProfileComposeRequest(BaseModel):
    site_id: str
    force: bool = False


@router.post("/profiles/compose")
async def compose_profile(payload: ProfileComposeRequest):
    try:
        result = await compose_profile_presentation(payload.site_id, force=payload.force)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Profile compose failed site=%s", payload.site_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
