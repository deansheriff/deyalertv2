import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, not_found

POINT = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography"
SHARE_LIFETIME = timedelta(hours=24)
MAX_CHECKIN = timedelta(hours=24)


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def serialize(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "status": row.status,
        "checkin_due_at": row.checkin_due_at.isoformat() if row.checkin_due_at else None,
    }


async def _existing(session: AsyncSession, user_id: UUID, client_id: UUID) -> Any:
    row = (
        await session.execute(
            text(
                "SELECT id, status, checkin_due_at, user_id FROM sos_alerts "
                "WHERE client_id = :id"
            ),
            {"id": client_id},
        )
    ).first()
    if row is not None and row.user_id != user_id:
        raise ApiError(409, "client_id_conflict", "This id is already in use")
    return row


async def start(
    session: AsyncSession, user_id: UUID, client_id: UUID, lat: float, lng: float
) -> tuple[dict[str, Any], str | None]:
    existing = await _existing(session, user_id, client_id)
    if existing is not None:
        return serialize(existing), None
    token, token_hash = new_token()
    row = (
        await session.execute(
            text(
                f"""
                INSERT INTO sos_alerts (client_id, user_id, start_location, status,
                    share_token_hash, share_expires_at)
                VALUES (:client, :user, {POINT}, 'active', :hash, :expires)
                ON CONFLICT (client_id) DO NOTHING
                RETURNING id, status, checkin_due_at
                """
            ),
            {
                "client": client_id,
                "user": user_id,
                "lat": lat,
                "lng": lng,
                "hash": token_hash,
                "expires": datetime.now(UTC) + SHARE_LIFETIME,
            },
        )
    ).first()
    if row is None:
        await session.rollback()
        return serialize(await _existing(session, user_id, client_id)), None
    await _ping(session, row.id, lat, lng)
    await session.commit()
    return serialize(row), token


async def schedule_checkin(
    session: AsyncSession, user_id: UUID, client_id: UUID, minutes: int, lat: float, lng: float
) -> dict[str, Any]:
    existing = await _existing(session, user_id, client_id)
    if existing is not None:
        return serialize(existing)
    if timedelta(minutes=minutes) > MAX_CHECKIN:
        raise ApiError(422, "too_long", "Check-ins can last up to 24 hours")
    row = (
        await session.execute(
            text(
                f"""
                INSERT INTO sos_alerts (client_id, user_id, start_location, status, checkin_due_at)
                VALUES (:client, :user, {POINT}, 'pending_cancel',
                    NOW() + make_interval(mins => :minutes))
                RETURNING id, status, checkin_due_at
                """
            ),
            {"client": client_id, "user": user_id, "lat": lat, "lng": lng, "minutes": minutes},
        )
    ).one()
    await session.commit()
    return serialize(row)


async def _ping(session: AsyncSession, sos_id: UUID, lat: float, lng: float) -> None:
    await session.execute(
        text(f"INSERT INTO sos_location_pings (sos_id, location) VALUES (:id, {POINT})"),
        {"id": sos_id, "lat": lat, "lng": lng},
    )


async def _owned(session: AsyncSession, user_id: UUID, sos_id: UUID) -> Any:
    row = (
        await session.execute(
            text(
                "SELECT id, status, checkin_due_at FROM sos_alerts "
                "WHERE id = :id AND user_id = :u"
            ),
            {"id": sos_id, "u": user_id},
        )
    ).first()
    if row is None:
        raise not_found("SOS not found")
    return row


async def ping(session: AsyncSession, user_id: UUID, sos_id: UUID, lat: float, lng: float) -> None:
    row = await _owned(session, user_id, sos_id)
    if row.status not in ("active", "pending_cancel"):
        raise ApiError(409, "not_active", "This SOS has ended")
    await _ping(session, sos_id, lat, lng)
    await session.commit()


async def end(
    session: AsyncSession, user_id: UUID, sos_id: UUID, new_status: str
) -> tuple[dict[str, Any], bool]:
    row = await _owned(session, user_id, sos_id)
    if row.status not in ("active", "pending_cancel"):
        return serialize(row), False
    was_sent = row.status == "active"
    updated = (
        await session.execute(
            text(
                "UPDATE sos_alerts SET status = :status, resolved_at = NOW(), resolved_by = :user, "
                "share_expires_at = LEAST(share_expires_at, NOW()) WHERE id = :id "
                "RETURNING id, status, checkin_due_at"
            ),
            {"status": new_status, "user": user_id, "id": sos_id},
        )
    ).one()
    await session.commit()
    return serialize(updated), was_sent


async def fire_overdue(session: AsyncSession) -> list[tuple[UUID, str]]:
    due = (
        await session.execute(
            text(
                "SELECT id FROM sos_alerts WHERE status = 'pending_cancel' "
                "AND checkin_due_at <= NOW() FOR UPDATE SKIP LOCKED"
            )
        )
    ).all()
    fired = []
    for row in due:
        token, token_hash = new_token()
        await session.execute(
            text(
                "UPDATE sos_alerts SET status = 'active', share_token_hash = :hash, "
                "share_expires_at = :expires WHERE id = :id"
            ),
            {"hash": token_hash, "expires": datetime.now(UTC) + SHARE_LIFETIME, "id": row.id},
        )
        fired.append((row.id, token))
    await session.commit()
    return fired


async def tracking_view(session: AsyncSession, token: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT s.id, s.status, s.share_expires_at, s.resolved_at,
                       COALESCE(p.display_name, 'Your contact') AS name, p.phone
                FROM sos_alerts s JOIN profiles p ON p.id = s.user_id
                WHERE s.share_token_hash = :hash
                """
            ),
            {"hash": hash_token(token)},
        )
    ).first()
    if row is None:
        return None
    expired = row.share_expires_at is None or row.share_expires_at <= datetime.now(UTC)
    if row.status != "active" or expired:
        return {"active": False, "safe": row.status == "resolved", "name": row.name}
    pings = (
        await session.execute(
            text(
                "SELECT ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng, "
                "recorded_at FROM sos_location_pings WHERE sos_id = :id "
                "ORDER BY recorded_at DESC LIMIT 20"
            ),
            {"id": row.id},
        )
    ).all()
    return {
        "active": True,
        "name": row.name,
        "phone": row.phone,
        "expires_at": row.share_expires_at,
        "pings": [{"lat": p.lat, "lng": p.lng, "at": p.recorded_at} for p in reversed(pings)],
    }


async def contacts_for(session: AsyncSession, sos_id: UUID) -> tuple[str, list[Any]]:
    owner = (
        await session.execute(
            text(
                "SELECT COALESCE(p.display_name, 'Your contact') AS name, p.id FROM sos_alerts s "
                "JOIN profiles p ON p.id = s.user_id WHERE s.id = :id"
            ),
            {"id": sos_id},
        )
    ).one()
    contacts = (
        await session.execute(
            text(
                "SELECT id, phone FROM trusted_contacts "
                "WHERE user_id = :user AND consent_status = 'accepted'"
            ),
            {"user": owner.id},
        )
    ).all()
    return owner.name, contacts


async def retention_purge(session: AsyncSession) -> None:
    await session.execute(
        text(
            "DELETE FROM sos_location_pings WHERE sos_id IN (SELECT id FROM sos_alerts "
            "WHERE resolved_at < NOW() - INTERVAL '24 hours')"
        )
    )
    await session.execute(
        text("DELETE FROM corroborations WHERE created_at < NOW() - INTERVAL '30 days'")
    )
    await session.execute(
        text("DELETE FROM audit_log WHERE created_at < NOW() - INTERVAL '24 months'")
    )
    await session.execute(
        text("DELETE FROM incidents WHERE created_at < NOW() - INTERVAL '90 days'")
    )
    await session.commit()
