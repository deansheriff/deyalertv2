"""Suspensions and reporting pauses staff put on accounts."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError

ACTIVE = "lifted_at IS NULL AND (ends_at IS NULL OR ends_at > NOW())"


async def active(session: AsyncSession, user_id: UUID) -> list[Any]:
    return (
        await session.execute(
            text(
                f"SELECT id, kind, reason, ends_at, appeal_status FROM user_sanctions "
                f"WHERE user_id = :id AND {ACTIVE} ORDER BY created_at DESC"
            ),
            {"id": user_id},
        )
    ).all()


def _until(row: Any) -> str:
    return f" until {row.ends_at:%d %b %Y, %H:%M}" if row.ends_at else ""


async def check_account(session: AsyncSession, user_id: UUID) -> None:
    for row in await active(session, user_id):
        if row.kind == "suspend":
            raise ApiError(
                403, "account_suspended", f"This account is suspended{_until(row)}. You can appeal."
            )


async def check_reporting(session: AsyncSession, user_id: UUID) -> None:
    for row in await active(session, user_id):
        if row.kind in ("suspend", "pause_reporting"):
            raise ApiError(
                403,
                "reporting_paused",
                f"Reporting is paused for this account{_until(row)}. You can appeal.",
            )
