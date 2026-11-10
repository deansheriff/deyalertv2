from typing import Any
from uuid import UUID

import httpx
import sentry_sdk
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services import media as media_service
from app.services import notifications, sos
from app.services.messaging import broadcast, build_push, build_sms

configure_logging()


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
    if area is not None:
        await broadcast(
            ctx["settings"],
            ctx["http"],
            notifications.topics_for(area.lga_id, area.ward_id),
            {"incident_id": incident_id, "trigger": trigger},
        )
    return count


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


async def send_contact_invite(
    ctx: dict[str, Any], phone: str, owner_name: str, token: str
) -> None:
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
                message_id = await ctx["sms"].send(
                    contact.phone, f"SOS: {name} needs help. See their location: {link}"
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
    for contact in contacts:
        await ctx["sms"].send(contact.phone, f"Dey Alert: {name} has marked themselves safe.")


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
    ]
    cron_jobs = [
        cron(expire_incidents, second=0),
        cron(fire_overdue_checkins, second=30),
        cron(refresh_stale_alert_points, minute=set(range(0, 60, 15)), second=10),
        cron(purge_retention, hour=2, minute=30),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
