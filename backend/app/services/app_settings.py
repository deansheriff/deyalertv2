"""Platform settings admins change in the panel, kept in the server-only app_settings table."""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PHONE_SIGN_IN = "phone_sign_in"
SMTP = "smtp"


async def get(session: AsyncSession, key: str, default: Any = None) -> Any:
    value = await session.scalar(
        text("SELECT value FROM app_settings WHERE key = :key"), {"key": key}
    )
    return default if value is None else value


async def put(session: AsyncSession, key: str, value: Any, by: UUID | None) -> None:
    await session.execute(
        text(
            "INSERT INTO app_settings (key, value, updated_by) "
            "VALUES (:key, CAST(:value AS JSONB), :by) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = NOW()"
        ),
        {"key": key, "value": json.dumps(value), "by": by},
    )


async def phone_sign_in_enabled(session: AsyncSession) -> bool:
    return bool(await get(session, PHONE_SIGN_IN, False))


async def sign_in_methods(session: AsyncSession) -> list[str]:
    return ["email", "phone"] if await phone_sign_in_enabled(session) else ["email"]

