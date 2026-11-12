"""Status checks for the System health page."""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services import mailer

CRON_JOBS = {
    "expire_incidents": "Expire incidents",
    "fire_overdue_checkins": "Overdue SOS check-ins",
    "refresh_stale_alert_points": "Refresh alert points",
    "purge_retention": "Retention purge",
    "weekly_report": "Weekly report email",
}
CRON_KEY = "cron:last:{}"


@dataclass
class Check:
    name: str
    status: str  # ok, warn, down
    detail: str
    latency_ms: int | None = None


async def _timed(coroutine: Any) -> tuple[Any, int]:
    started = time.perf_counter()
    result = await coroutine
    return result, int((time.perf_counter() - started) * 1000)


async def run(session: AsyncSession, redis: Any) -> list[Check]:
    settings = get_settings()
    checks: list[Check] = [Check("API", "ok", f"Running ({settings.environment})")]
    try:
        _, ms = await _timed(session.execute(text("SELECT 1")))
        checks.append(Check("Database", "ok", "Connected", ms))
    except Exception as error:  # noqa: BLE001
        checks.append(Check("Database", "down", str(error)[:120]))
    try:
        _, ms = await _timed(redis.ping())
        checks.append(Check("Redis", "ok", "Connected", ms))
        depth = await redis.zcard("arq:queue")
        beat = await redis.get("arq:health-check")
        worker = "ok" if beat else "warn"
        detail = f"Queue depth {depth}" + ("" if beat else " · no recent heartbeat")
        checks.append(Check("Background worker", worker, detail))
    except Exception as error:  # noqa: BLE001
        checks.append(Check("Redis", "down", str(error)[:120]))
    if settings.supabase_url:
        try:
            async with httpx.AsyncClient() as client:
                response, ms = await _timed(
                    asyncio.wait_for(
                        client.get(
                            f"{settings.supabase_url}/auth/v1/health",
                            headers={"apikey": settings.supabase_anon_key},
                        ),
                        timeout=5,
                    )
                )
            ok = response.status_code == 200
            checks.append(
                Check(
                    "Supabase sign-in", "ok" if ok else "down", f"HTTP {response.status_code}", ms
                )
            )
        except Exception as error:  # noqa: BLE001
            checks.append(Check("Supabase sign-in", "down", str(error)[:120] or "Unreachable"))
    else:
        checks.append(Check("Supabase sign-in", "warn", "SUPABASE_URL is not set"))
    smtp = await mailer.load(session)
    checks.append(
        Check("Email (SMTP)", "ok", f"{smtp.host}:{smtp.port}")
        if smtp
        else Check("Email (SMTP)", "warn", "Not set up. Email sign-in cannot send codes.")
    )
    checks.append(
        Check("SMS provider", "ok", "Termii configured")
        if settings.termii_api_key
        else Check("SMS provider", "warn", "Not configured. SMS is logged, not sent.")
    )
    checks.append(
        Check("Push (Firebase)", "ok", "Configured")
        if settings.fcm_service_account_json
        else Check("Push (Firebase)", "warn", "Not configured. Alerts are logged, not delivered.")
    )
    return checks


async def cron_runs(redis: Any) -> list[dict[str, Any]]:
    runs = []
    for key, label in CRON_JOBS.items():
        value = await redis.get(CRON_KEY.format(key))
        when = datetime.fromtimestamp(float(value), UTC) if value else None
        runs.append({"key": key, "label": label, "last": when})
    return runs


def overall(checks: list[Check]) -> str:
    if any(check.status == "down" for check in checks):
        return "down"
    return "warn" if any(check.status == "warn" for check in checks) else "ok"
