from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.services import app_settings

router = APIRouter(prefix="/v1", tags=["config"])


@router.get("/config")
async def public_config(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """What the app needs before sign-in. Admins switch phone sign-in on in the panel."""
    return {"sign_in_methods": await app_settings.sign_in_methods(session)}
