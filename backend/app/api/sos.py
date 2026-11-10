from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_profile
from app.core.clients import get_queue
from app.core.db import get_session
from app.core.errors import ApiError, not_found
from app.services import sos as service
from app.services.profiles import Profile, normalize_phone

router = APIRouter(prefix="/v1", tags=["sos"])


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class SosStart(Point):
    client_id: UUID


class CheckinStart(Point):
    client_id: UUID
    minutes: int = Field(ge=5, le=1440)


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=r"^\+234[0-9]{10}$")
    relationship: str | None = Field(default=None, max_length=40)


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    relationship: Literal["Family", "Friend", "Neighbour", "Other"] | None = None


@router.post("/sos", status_code=201)
async def start_sos(
    body: SosStart,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    queue: Any = Depends(get_queue),
) -> dict[str, Any]:
    data, token = await service.start(session, profile.id, body.client_id, body.lat, body.lng)
    if token is not None:
        await queue.enqueue_job("send_sos_alerts", data["id"], token)
    return data


@router.post("/sos/checkins", status_code=201)
async def start_checkin(
    body: CheckinStart,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await service.schedule_checkin(
        session, profile.id, body.client_id, body.minutes, body.lat, body.lng
    )


@router.post("/sos/{sos_id}/ping", status_code=204)
async def ping(
    sos_id: UUID,
    body: Point,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> None:
    await service.ping(session, profile.id, sos_id, body.lat, body.lng)


async def _end(
    session: AsyncSession, queue: Any, user_id: UUID, sos_id: UUID, status: str
) -> dict[str, Any]:
    data, was_sent = await service.end(session, user_id, sos_id, status)
    if was_sent:
        await queue.enqueue_job("send_sos_safe", str(sos_id))
    return data


@router.post("/sos/{sos_id}/cancel")
async def cancel(
    sos_id: UUID,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    queue: Any = Depends(get_queue),
) -> dict[str, Any]:
    return await _end(session, queue, profile.id, sos_id, "cancelled")


@router.post("/sos/{sos_id}/resolve")
async def resolve(
    sos_id: UUID,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    queue: Any = Depends(get_queue),
) -> dict[str, Any]:
    return await _end(session, queue, profile.id, sos_id, "resolved")


@router.post("/sos/{sos_id}/checkin")
async def checkin_safe(
    sos_id: UUID,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    queue: Any = Depends(get_queue),
) -> dict[str, Any]:
    return await _end(session, queue, profile.id, sos_id, "resolved")


@router.post("/trusted-contacts", status_code=201)
async def add_contact(
    body: ContactCreate,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
    queue: Any = Depends(get_queue),
) -> dict[str, str]:
    token, token_hash = service.new_token()
    contact_id = (
        await session.execute(
            text(
                "INSERT INTO trusted_contacts (user_id, name, phone, relationship, "
                "consent_token_hash) VALUES (:user, :name, :phone, :relationship, :hash) "
                "ON CONFLICT (user_id, phone) DO NOTHING RETURNING id"
            ),
            {
                "user": profile.id,
                "name": body.name,
                "phone": normalize_phone(body.phone),
                "relationship": body.relationship,
                "hash": token_hash,
            },
        )
    ).scalar()
    if contact_id is None:
        raise ApiError(409, "duplicate_contact", "This contact is already on your list")
    owner = (
        await session.execute(
            text("SELECT COALESCE(display_name, 'A Dey Alert user') FROM profiles WHERE id = :id"),
            {"id": profile.id},
        )
    ).scalar_one()
    await session.commit()
    await queue.enqueue_job("send_contact_invite", body.phone, owner, token)
    return {"id": str(contact_id), "consent_status": "pending"}


@router.patch("/trusted-contacts/{contact_id}", status_code=204)
async def update_contact(
    contact_id: UUID,
    body: ContactUpdate,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> None:
    values = body.model_dump(exclude_unset=True)
    if not values:
        return
    sets = ", ".join(f"{column} = :{column}" for column in values)
    result = await session.execute(
        text(f"UPDATE trusted_contacts SET {sets} WHERE id = :id AND user_id = :user"),
        {**values, "id": contact_id, "user": profile.id},
    )
    if result.rowcount == 0:
        raise not_found("Contact not found")
    await session.commit()


@router.delete("/trusted-contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: UUID,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> None:
    result = await session.execute(
        text("DELETE FROM trusted_contacts WHERE id = :id AND user_id = :user"),
        {"id": contact_id, "user": profile.id},
    )
    if result.rowcount == 0:
        raise not_found("Contact not found")
    await session.commit()
