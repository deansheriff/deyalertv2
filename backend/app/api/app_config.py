import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ApiError
from app.services import app_settings, areas

router = APIRouter(prefix="/v1", tags=["config"])
CACHE_SECONDS = 15
_switches_cache: dict[str, Any] = {"at": 0.0, "value": None}


async def current_switches(session: AsyncSession) -> dict[str, Any]:
    if time.monotonic() - _switches_cache["at"] > CACHE_SECONDS:
        _switches_cache["value"] = await app_settings.switches(session)
        _switches_cache["at"] = time.monotonic()
    return _switches_cache["value"]


def clear_cache() -> None:
    _switches_cache["at"] = 0.0


async def not_in_maintenance(session: AsyncSession = Depends(get_session)) -> None:
    """Applied to the app's API. Sign-in hooks, public pages and /v1/config stay up."""
    switches = await current_switches(session)
    if switches["maintenance"]:
        raise ApiError(503, "maintenance", switches["maintenance_message"])


@router.get("/config")
async def public_config(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """What the app needs before sign-in. Admins switch phone sign-in on in the panel."""
    switches = await current_switches(session)
    return {
        "sign_in_methods": await app_settings.sign_in_methods(session),
        "maintenance": switches["maintenance"],
        "maintenance_message": switches["maintenance_message"] if switches["maintenance"] else None,
    }


@router.get("/announcements")
async def announcements(
    lat: float | None = None,
    lng: float | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Notices for everyone, plus those for the LGA the point falls in."""
    lga_id = None
    if lat is not None and lng is not None:
        lga_id = (await areas.resolve_area(session, lat, lng)).lga_id
    rows = (
        await session.execute(
            text(
                """
                SELECT a.id, a.title, a.body, a.starts_at, l.name AS lga
                FROM announcements a LEFT JOIN lgas l ON l.id = a.lga_id
                WHERE a.is_active AND a.starts_at <= NOW()
                  AND (a.ends_at IS NULL OR a.ends_at > NOW())
                  AND (a.lga_id IS NULL OR a.lga_id = CAST(:lga AS INT))
                ORDER BY a.starts_at DESC LIMIT 5
                """
            ),
            {"lga": lga_id},
        )
    ).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "body": row.body,
            "area": row.lga,
            "starts_at": row.starts_at.isoformat(),
        }
        for row in rows
    ]
