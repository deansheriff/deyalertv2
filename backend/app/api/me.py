from datetime import time
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_profile
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ApiError
from app.services.areas import resolve_area
from app.services.profiles import Profile, profile_json

router = APIRouter(prefix="/v1", tags=["me"])


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    language: Literal["en", "pcm", "ha", "yo", "ig"] | None = None
    alert_radius_km: float | None = Field(default=None, ge=1, le=20)
    location_precision: Literal["exact", "ward", "lga"] | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    clear_quiet_hours: bool = False
    sms_alerts_opt_in: bool | None = None
    home_lat: float | None = Field(default=None, ge=-90, le=90)
    home_lng: float | None = Field(default=None, ge=-180, le=180)


class DeviceRegistration(BaseModel):
    fcm_token: str = Field(min_length=1)
    platform: Literal["android", "ios"]
    device_fingerprint: str | None = None
    app_version: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


@router.get("/me")
async def get_me(
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await profile_json(session, profile.id)


@router.patch("/me")
async def update_me(
    body: ProfileUpdate,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    values = body.model_dump(
        exclude_unset=True, exclude={"home_lat", "home_lng", "clear_quiet_hours"}
    )
    if body.clear_quiet_hours:
        values["quiet_hours_start"] = None
        values["quiet_hours_end"] = None
    sets = [f"{column} = :{column}" for column in values]
    params: dict[str, Any] = {**values, "id": profile.id}
    if body.home_lat is not None and body.home_lng is not None:
        area = await resolve_area(session, body.home_lat, body.home_lng)
        sets += [
            "home_point = ST_SetSRID(ST_MakePoint(:home_lng, :home_lat), 4326)::geography",
            "home_lga_id = :home_lga_id",
            "home_ward_id = :home_ward_id",
        ]
        params |= {
            "home_lat": body.home_lat,
            "home_lng": body.home_lng,
            "home_lga_id": area.lga_id,
            "home_ward_id": area.ward_id,
        }
    if sets:
        await session.execute(
            text(f"UPDATE profiles SET {', '.join(sets)} WHERE id = :id"), params
        )
        await session.commit()
    return await profile_json(session, profile.id)


@router.delete("/me", status_code=204)
async def delete_me(
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> None:
    settings = get_settings()
    if settings.supabase_url:
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{settings.supabase_url}/auth/v1/admin/users/{profile.id}",
                headers={
                    "apikey": settings.supabase_service_role_key,
                    "Authorization": f"Bearer {settings.supabase_service_role_key}",
                },
                timeout=15,
            )
        if response.status_code not in (200, 204, 404):
            raise ApiError(502, "delete_failed", "Could not delete the account. Try again.")
    else:
        await session.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": profile.id})
        await session.commit()


@router.get("/me/export")
async def export_me(
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    contacts = (
        await session.execute(
            text(
                "SELECT name, phone, relationship, consent_status FROM trusted_contacts "
                "WHERE user_id = :id"
            ),
            {"id": profile.id},
        )
    ).all()
    reports = (
        await session.execute(
            text(
                "SELECT i.type_code, i.description, i.status, i.occurred_at, i.location_name "
                "FROM incidents i JOIN incident_reporters r ON r.incident_id = i.id "
                "WHERE r.reporter_id = :id ORDER BY i.occurred_at DESC"
            ),
            {"id": profile.id},
        )
    ).all()
    return {
        "profile": await profile_json(session, profile.id),
        "trusted_contacts": [dict(row._mapping) for row in contacts],
        "reports": [
            {**row._mapping, "occurred_at": row.occurred_at.isoformat()} for row in reports
        ],
    }


@router.get("/me/reports")
async def my_reports(
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT i.id, i.type_code, i.status, i.location_name, i.created_at "
                "FROM incidents i JOIN incident_reporters r ON r.incident_id = i.id "
                "WHERE r.reporter_id = :id ORDER BY i.created_at DESC LIMIT 100"
            ),
            {"id": profile.id},
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "type_code": row.type_code,
            "status": row.status,
            "location_name": row.location_name,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/devices")
async def register_device(
    body: DeviceRegistration,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    has_location = body.lat is not None and body.lng is not None
    location_sql = (
        "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography" if has_location else "NULL"
    )
    device_id: UUID = (
        await session.execute(
            text(
                f"""
                INSERT INTO devices (user_id, fcm_token, platform, device_fingerprint, app_version,
                    last_location, last_location_at)
                VALUES (:user, :token, :platform, :fingerprint, :version, {location_sql},
                    {"NOW()" if has_location else "NULL"})
                ON CONFLICT (fcm_token) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    platform = EXCLUDED.platform,
                    device_fingerprint = EXCLUDED.device_fingerprint,
                    app_version = EXCLUDED.app_version,
                    last_location = COALESCE(EXCLUDED.last_location, devices.last_location),
                    last_location_at = COALESCE(EXCLUDED.last_location_at,
                        devices.last_location_at),
                    is_active = true,
                    last_seen_at = NOW()
                RETURNING id
                """
            ),
            {
                "user": profile.id,
                "token": body.fcm_token,
                "platform": body.platform,
                "fingerprint": body.device_fingerprint,
                "version": body.app_version,
                **({"lat": body.lat, "lng": body.lng} if has_location else {}),
            },
        )
    ).scalar_one()
    await session.commit()
    return {"device_id": str(device_id)}
