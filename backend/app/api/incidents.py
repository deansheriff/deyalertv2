from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_profile
from app.core.clients import get_queue, get_redis, hit_limit
from app.core.db import get_session
from app.core.errors import ApiError, not_found
from app.services import incidents as service
from app.services.profiles import Profile

router = APIRouter(prefix="/v1/incidents", tags=["incidents"])

Lat = Field(ge=-90, le=90)
Lng = Field(ge=-180, le=180)


class IncidentCreate(BaseModel):
    client_id: UUID
    type_code: str
    lat: float = Lat
    lng: float = Lng
    occurred_at: datetime
    description: str | None = Field(default=None, max_length=1000)
    location_name: str | None = Field(default=None, max_length=200)
    is_anonymous: bool = False
    device_id: UUID | None = None


class CorroborateBody(BaseModel):
    lat: float = Lat
    lng: float = Lng
    device_id: UUID


class FlagBody(BaseModel):
    reason: Literal["false_report", "duplicate", "spam", "inappropriate", "names_individual"]
    notes: str | None = Field(default=None, max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class VerifyBody(BaseModel):
    action: Literal["confirm", "resolve", "extend", "mark_false", "restore"]
    note: str | None = Field(default=None, max_length=500)
    extend_minutes: Literal[60, 180, 360] | None = None


class MediaBody(BaseModel):
    kind: Literal["image", "video"]
    storage_path: str = Field(min_length=1, max_length=400)
    size_bytes: int | None = Field(default=None, ge=0)


async def _enqueue(queue: Any, result: service.Result, incident_id: str) -> None:
    if result.enqueue_trigger:
        await queue.enqueue_job("fan_out_alert", incident_id, result.enqueue_trigger)


@router.post("", status_code=201)
async def create(
    body: IncidentCreate,
    response: Response,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    queue: Any = Depends(get_queue),
) -> dict[str, Any]:
    await hit_limit(redis, f"rl:incident:{profile.id}", 10, 60)
    result = await service.create_incident(
        session,
        profile,
        service.NewIncident(
            client_id=body.client_id,
            type_code=body.type_code,
            lat=body.lat,
            lng=body.lng,
            occurred_at=body.occurred_at,
            description=body.description,
            location_name=body.location_name,
            is_anonymous=body.is_anonymous,
            device_id=body.device_id,
        ),
    )
    if not result.created:
        response.status_code = 200
    await _enqueue(queue, result, result.data["id"])
    return result.data


@router.post("/{incident_id}/corroborate")
async def corroborate(
    incident_id: UUID,
    body: CorroborateBody,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    queue: Any = Depends(get_queue),
) -> dict[str, Any]:
    await hit_limit(redis, f"rl:corroborate:{profile.id}", 30, 60)
    result = await service.corroborate(
        session, profile, incident_id, body.lat, body.lng, body.device_id
    )
    await _enqueue(queue, result, str(incident_id))
    return result.data


@router.post("/{incident_id}/flag")
async def flag(
    incident_id: UUID,
    body: FlagBody,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    await hit_limit(redis, f"rl:flag:{profile.id}", 20, 3600)
    result = await service.flag(
        session, profile, incident_id, body.reason, body.notes, body.lat, body.lng
    )
    return result.data


@router.patch("/{incident_id}/verify")
async def verify(
    incident_id: UUID,
    body: VerifyBody,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    queue: Any = Depends(get_queue),
) -> dict[str, Any]:
    result = await service.verify(
        session, profile, incident_id, body.action, body.note, body.extend_minutes
    )
    await _enqueue(queue, result, str(incident_id))
    return result.data


@router.post("/{incident_id}/media", status_code=201)
async def register_media(
    incident_id: UUID,
    body: MediaBody,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    queue: Any = Depends(get_queue),
) -> dict[str, str]:
    reporter = (
        await session.execute(
            text("SELECT reporter_id FROM incident_reporters WHERE incident_id = :id"),
            {"id": incident_id},
        )
    ).first()
    if reporter is None:
        raise not_found("Incident not found")
    if reporter.reporter_id != profile.id:
        raise ApiError(403, "not_reporter", "Only the reporter can add media")
    if not body.storage_path.startswith(f"{profile.id}/") or ".." in body.storage_path:
        raise ApiError(422, "invalid_path", "Upload path is not allowed")
    media_id = (
        await session.execute(
            text(
                "INSERT INTO incident_media (incident_id, kind, storage_path, size_bytes) "
                "VALUES (:incident, :kind, :path, :size) RETURNING id"
            ),
            {
                "incident": incident_id,
                "kind": body.kind,
                "path": body.storage_path,
                "size": body.size_bytes,
            },
        )
    ).scalar_one()
    await session.commit()
    await queue.enqueue_job("process_media", str(media_id))
    return {"id": str(media_id)}
