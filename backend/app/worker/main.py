import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any
from uuid import UUID

import httpx
import sentry_sdk
from arq import Retry, cron
from arq.connections import RedisSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services import app_settings, broadcasts, health, insights, mailer, notifications, sos
from app.services import media as media_service
from app.services import webhooks as webhook_service
from app.services.messaging import broadcast, build_push, build_sms

configure_logging()


def recorded(job: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Notes when a scheduled job last finished, for the System health page."""

    @wraps(job)
    async def run(ctx: dict[str, Any]) -> Any:
        result = await job(ctx)
        await ctx["redis"].set(health.CRON_KEY.format(job.__name__), str(time.time()))
        return result

    return run


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)
    ctx["settings"] = settings
    ctx["engine"] = create_async_engine(settings.database_url, pool_pre_ping=True)
    ctx["sessions"] = async_sessionmaker(ctx["engine"], expire_on_commit=False)
    ctx["http"] = httpx.AsyncClient()
    ctx["sms"] = build_sms(settings, ctx["http"])
    ctx["push"] = build_push(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


async def fan_out_alert(ctx: dict[str, Any], incident_id: str, trigger: str) -> int:
    count = await notifications.fan_out(
        ctx["sessions"],
        ctx["settings"],
        ctx["push"],
        ctx["sms"],
        ctx["redis"],
        UUID(incident_id),
        trigger,
    )
    async with ctx["sessions"]() as session:
        area = (
            await session.execute(
                text("SELECT lga_id, ward_id FROM incidents WHERE id = :id"),
                {"id": UUID(incident_id)},
            )
        ).first()
    if area is not None and trigger in ("confirmed", "resolved"):
        await queue_webhooks(
            ctx, f"incident.{trigger}", {"incident_id": incident_id}, area.lga_id, area.ward_id
        )
    if area is not None:
        await broadcast(
            ctx["settings"],
            ctx["http"],
            notifications.topics_for(area.lga_id, area.ward_id),
            {"incident_id": incident_id, "trigger": trigger},
        )
    return count


async def queue_webhooks(
    ctx: dict[str, Any],
    event: str,
    payload: dict[str, Any],
    lga_id: int | None = None,
    ward_id: int | None = None,
) -> None:
    async with ctx["sessions"]() as session:
        ids = await webhook_service.queue_event(session, event, payload, lga_id, ward_id)
    for delivery_id in ids:
        await ctx["redis"].enqueue_job("deliver_webhook", delivery_id)


async def deliver_webhook(ctx: dict[str, Any], delivery_id: int) -> None:
    async with ctx["sessions"]() as session:
        done = await webhook_service.deliver(session, ctx["http"], delivery_id)
    if not done:
        # Back off 1, 4, 9, 16 minutes between attempts.
        tries = ctx.get("job_try", 1)
        raise Retry(defer=60 * tries * tries)


async def send_broadcast(ctx: dict[str, Any], broadcast_id: str) -> dict[str, int]:
    async with ctx["sessions"]() as session:
        stats = await broadcasts.send(
            session, ctx["push"], ctx["sms"], ctx["redis"], UUID(broadcast_id)
        )
        title = await session.scalar(
            text("SELECT title FROM broadcasts WHERE id = :id"), {"id": UUID(broadcast_id)}
        )
    if stats:
        await queue_webhooks(
            ctx, "broadcast.sent", {"broadcast_id": broadcast_id, "title": title, **stats}
        )
    return stats


async def weekly_report(ctx: dict[str, Any]) -> int:
    """Monday morning summary to everyone listed in Analytics > Weekly report."""
    async with ctx["sessions"]() as session:
        config = await app_settings.get(session, app_settings.WEEKLY_REPORT, {}) or {}
        recipients = [r for r in config.get("recipients", []) if "@" in r]
        smtp = await mailer.load(session)
        if not config.get("enabled") or not recipients or smtp is None:
            return 0
        scope = {"scope_all": True, "scope_lgas": [], "scope_wards": []}
        data = await insights.overview(session, 7, scope)
    body = f"{insights.weekly_text(data)}\n\n{insights.csv_report(data)}"
    subject = f"Dey Alert weekly report, {datetime.now(UTC):%d %b %Y}"
    sent = 0
    for address in recipients:
        try:
            await mailer.send(smtp, address, subject, body)
            sent += 1
        except mailer.MailError:
            continue
    return sent


async def expire_incidents(ctx: dict[str, Any]) -> int:
    async with ctx["sessions"]() as session:
        expired = await notifications.expire_due(session)
    for row in expired:
        await broadcast(
            ctx["settings"],
            ctx["http"],
            notifications.topics_for(row.lga_id, row.ward_id),
            {"incident_id": str(row.id), "trigger": "expired"},
        )
    return len(expired)


async def process_media(ctx: dict[str, Any], media_id: str) -> None:
    async with ctx["sessions"]() as session:
        await media_service.process(session, ctx["settings"], ctx["http"], UUID(media_id))


async def send_contact_invite(ctx: dict[str, Any], phone: str, owner_name: str, token: str) -> None:
    link = f"{ctx['settings'].public_base_url}/c/{token}"
    message = f"{owner_name} wants to add you as a trusted contact on Dey Alert. Reply here: {link}"
    await ctx["sms"].send(phone, message)


async def send_sos_alerts(ctx: dict[str, Any], sos_id: str, token: str) -> int:
    link = f"{ctx['settings'].public_base_url}/s/{token}"
    async with ctx["sessions"]() as session:
        name, contacts = await sos.contacts_for(session, UUID(sos_id))
        for contact in contacts:
            status = "failed"
            message_id = None
            try:
                wording = (await app_settings.message_templates(session))["sos_sms"]
                message_id = await ctx["sms"].send(
                    contact.phone, app_settings.fill(wording, name=name, link=link)
                )
                status = "sent"
            finally:
                await session.execute(
                    text(
                        "INSERT INTO sos_deliveries (sos_id, contact_id, channel, status, "
                        "provider_message_id) VALUES (:sos, :contact, 'sms', :status, :message)"
                    ),
                    {
                        "sos": UUID(sos_id),
                        "contact": contact.id,
                        "status": status,
                        "message": message_id,
                    },
                )
                await session.commit()
    return len(contacts)


async def send_sos_safe(ctx: dict[str, Any], sos_id: str) -> None:
    async with ctx["sessions"]() as session:
        name, contacts = await sos.contacts_for(session, UUID(sos_id))
        wording = (await app_settings.message_templates(session))["sos_safe_sms"]
    for contact in contacts:
        await ctx["sms"].send(contact.phone, app_settings.fill(wording, name=name))


async def fire_overdue_checkins(ctx: dict[str, Any]) -> int:
    async with ctx["sessions"]() as session:
        fired = await sos.fire_overdue(session)
    for sos_id, token in fired:
        await ctx["redis"].enqueue_job("send_sos_alerts", str(sos_id), token)
    return len(fired)


async def refresh_stale_alert_points(ctx: dict[str, Any]) -> None:
    async with ctx["sessions"]() as session:
        await session.execute(
            text(
                "UPDATE devices d SET alert_point = p.home_point FROM profiles p "
                "WHERE p.id = d.user_id AND p.home_point IS NOT NULL "
                "AND d.last_location_at < NOW() - INTERVAL '12 hours'"
            )
        )
        await session.commit()


async def purge_retention(ctx: dict[str, Any]) -> None:
    async with ctx["sessions"]() as session:
        await sos.retention_purge(session)


class WorkerSettings:
    functions = [
        fan_out_alert,
        process_media,
        send_contact_invite,
        send_sos_alerts,
        send_sos_safe,
        send_broadcast,
        deliver_webhook,
    ]
    cron_jobs = [
        cron(recorded(expire_incidents), name="expire_incidents", second=0),
        cron(recorded(fire_overdue_checkins), name="fire_overdue_checkins", second=30),
        cron(
            recorded(refresh_stale_alert_points),
            name="refresh_stale_alert_points",
            minute=set(range(0, 60, 15)),
            second=10,
        ),
        cron(recorded(purge_retention), name="purge_retention", hour=2, minute=30),
        # 07:00 in Lagos.
        cron(recorded(weekly_report), name="weekly_report", weekday="mon", hour=6, minute=0),
    ]
    max_tries = 5
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
