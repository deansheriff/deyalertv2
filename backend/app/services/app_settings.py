"""Platform settings admins change in the panel, kept in the server-only app_settings table."""

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

PHONE_SIGN_IN = "phone_sign_in"
SMTP = "smtp"
SWITCHES = "switches"
LIMITS = "limits"
TEMPLATES = "message_templates"
WORD_FILTER = "word_filter"
WEEKLY_REPORT = "weekly_report"

# Kill switches. Paused reports may be limited to some LGAs.
DEFAULT_SWITCHES: dict[str, Any] = {
    "signups_paused": False,
    "reports_paused": False,
    "reports_paused_lgas": [],
    "maintenance": False,
    "maintenance_message": "Dey Alert is down for maintenance. Please try again soon.",
}

# Limits admins may change without a redeploy, with the bounds the panel enforces.
LIMIT_BOUNDS: dict[str, tuple[str, int, int]] = {
    "email_daily_cap": ("Sign-in emails per day", 10, 100000),
    "email_per_address_hourly_cap": ("Sign-in codes per address per hour", 1, 20),
    "otp_daily_cap": ("SMS codes per day", 10, 100000),
    "otp_per_phone_hourly_cap": ("SMS codes per phone per hour", 1, 20),
    "sms_daily_cap": ("Alert SMS per day", 0, 100000),
    "daily_report_cap": ("Reports per person per day", 1, 200),
    "new_account_daily_report_cap": ("Reports per new account per day", 1, 50),
    "corroboration_threshold": ("Corroborations to upgrade a report", 1, 20),
    "corroboration_radius_m": ("Corroboration radius (metres)", 200, 20000),
    "flag_hide_threshold": ("Flag weight that hides a report", 1, 50),
}

# Wording admins can edit. {placeholders} are filled in when the message is sent.
DEFAULT_TEMPLATES: dict[str, str] = {
    "otp_sms": "Your Dey Alert code is {code}. It expires soon. Do not share it.",
    "alert_sms": "Dey Alert: {label}. {body}",
    "sos_sms": "SOS: {name} needs help. See their location: {link}",
    "sos_safe_sms": "Dey Alert: {name} has marked themselves safe.",
    "broadcast_sms": "Dey Alert notice: {title}. {body}",
}
TEMPLATE_LABELS = {
    "otp_sms": "Sign-in code SMS",
    "alert_sms": "Incident alert SMS",
    "sos_sms": "SOS SMS to trusted contacts",
    "sos_safe_sms": "Marked safe SMS",
    "broadcast_sms": "Broadcast SMS",
}


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


async def switches(session: AsyncSession) -> dict[str, Any]:
    return {**DEFAULT_SWITCHES, **(await get(session, SWITCHES, {}) or {})}


async def limits(session: AsyncSession) -> dict[str, Any]:
    base = get_settings()
    stored = await get(session, LIMITS, {}) or {}
    return {key: stored.get(key, getattr(base, key)) for key in LIMIT_BOUNDS}


async def effective(session: AsyncSession, base: Settings | None = None) -> Settings:
    """Server settings with the limits admins set in the panel applied on top."""
    base = base or get_settings()
    stored = await get(session, LIMITS, {}) or {}
    overrides = {key: value for key, value in stored.items() if key in LIMIT_BOUNDS}
    return base.model_copy(update=overrides) if overrides else base


async def message_templates(session: AsyncSession) -> dict[str, str]:
    return {**DEFAULT_TEMPLATES, **(await get(session, TEMPLATES, {}) or {})}


def fill(template: str, **values: Any) -> str:
    """Fills {placeholders}; unknown ones are left as typed rather than failing a send."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


async def word_filter(session: AsyncSession) -> dict[str, Any]:
    return {"words": [], "hold": True, **(await get(session, WORD_FILTER, {}) or {})}


def matched_word(description: str | None, words: list[str]) -> str | None:
    if not description:
        return None
    for word in words:
        if word and re.search(rf"\b{re.escape(word)}\b", description, re.IGNORECASE):
            return word
    return None
