from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import AuthUser

PROFILE_COLUMNS = """
    id, phone, display_name, role, language, alert_radius_km, location_precision,
    reputation_score, quiet_hours_start, quiet_hours_end, is_active, created_at,
    home_lga_id, home_ward_id, sms_alerts_opt_in,
    ST_Y(home_point::geometry) AS home_lat, ST_X(home_point::geometry) AS home_lng
"""


@dataclass(frozen=True)
class Profile:
    id: UUID
    role: str
    reputation_score: int
    created_at: datetime
    location_precision: str
    home_ward_id: int | None

    @property
    def account_age(self) -> timedelta:
        return datetime.now(UTC) - self.created_at

    @property
    def is_staff(self) -> bool:
        return self.role in ("moderator", "admin")


def normalize_phone(phone: str | None) -> str:
    if not phone:
        raise ApiError(403, "phone_required", "A verified phone number is required")
    return phone if phone.startswith("+") else f"+{phone}"


async def load_or_create(session: AsyncSession, user: AuthUser) -> Profile:
    row = (
        await session.execute(
            text(f"SELECT {PROFILE_COLUMNS} FROM profiles WHERE id = :id"), {"id": user.id}
        )
    ).first()
    if row is None:
        await session.execute(
            text("INSERT INTO profiles (id, phone) VALUES (:id, :phone) ON CONFLICT DO NOTHING"),
            {"id": user.id, "phone": normalize_phone(user.phone)},
        )
        await session.commit()
        row = (
            await session.execute(
                text(f"SELECT {PROFILE_COLUMNS} FROM profiles WHERE id = :id"), {"id": user.id}
            )
        ).one()
    if not row.is_active:
        raise ApiError(403, "account_disabled", "This account has been disabled")
    return Profile(
        id=row.id,
        role=row.role,
        reputation_score=row.reputation_score,
        created_at=row.created_at,
        location_precision=row.location_precision,
        home_ward_id=row.home_ward_id,
    )


async def profile_json(session: AsyncSession, user_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            text(f"SELECT {PROFILE_COLUMNS} FROM profiles WHERE id = :id"), {"id": user_id}
        )
    ).one()
    return {
        "id": str(row.id),
        "phone": row.phone,
        "display_name": row.display_name,
        "role": row.role,
        "language": row.language,
        "alert_radius_km": float(row.alert_radius_km),
        "location_precision": row.location_precision,
        "quiet_hours_start": row.quiet_hours_start.isoformat() if row.quiet_hours_start else None,
        "quiet_hours_end": row.quiet_hours_end.isoformat() if row.quiet_hours_end else None,
        "home_lat": row.home_lat,
        "home_lng": row.home_lng,
        "home_lga_id": row.home_lga_id,
        "home_ward_id": row.home_ward_id,
        "sms_alerts_opt_in": row.sms_alerts_opt_in,
    }
