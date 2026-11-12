from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import app_settings
from app.services.incidents import is_trusted_reporter
from app.services.messaging import PushProvider, SmsProvider

LAGOS = ZoneInfo("Africa/Lagos")
COARSE_RADIUS_M = 20000
NEAR_ONLY_KM = 2


@dataclass(frozen=True)
class Decision:
    priority: str
    max_km: float | None
    sms: bool
    bypass_quiet: bool


def decide(status: str, severity: str, trusted_reporter: bool) -> Decision | None:
    if status == "unconfirmed":
        if not trusted_reporter:
            return None
        status = "corroborated"
    urgent = severity in ("high", "critical")
    if status == "corroborated":
        if urgent:
            return Decision("high", None, False, False)
        return Decision("normal", NEAR_ONLY_KM, False, False)
    if status == "confirmed":
        if urgent:
            return Decision("critical", None, True, severity == "critical")
        return Decision("normal", NEAR_ONLY_KM, False, False)
    return None


def in_quiet_hours(start: time | None, end: time | None, now: time) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _copy(trigger: str, incident: Any) -> tuple[str, str]:
    place = incident.location_name or "near you"
    if trigger == "resolved":
        return f"All clear: {incident.label}", f"{place} has been marked resolved."
    if incident.status == "confirmed":
        return incident.label, f"Confirmed by {incident.confirmed_by_label}. {place}."
    return incident.label, f"Reported and corroborated by people nearby. {place}."


async def fan_out(
    sessions: Callable[[], AsyncSession],
    settings: Settings,
    push: PushProvider,
    sms: SmsProvider,
    redis: Any,
    incident_id: UUID,
    trigger: str,
    now: datetime | None = None,
) -> int:
    local_now = (now or datetime.now(LAGOS)).astimezone(LAGOS).time()
    async with sessions() as session:
        incident = (
            await session.execute(
                text(
                    """
                    SELECT i.id, i.status, i.severity, i.is_hidden, i.location, i.location_name,
                           i.confirmed_by_label, i.lga_id, i.ward_id, t.label,
                           r.reporter_id
                    FROM incidents i JOIN incident_types t ON t.code = i.type_code
                    LEFT JOIN incident_reporters r ON r.incident_id = i.id
                    WHERE i.id = :id
                    """
                ),
                {"id": incident_id},
            )
        ).first()
        if incident is None or incident.is_hidden:
            return 0

        if trigger == "resolved":
            decision: Decision | None = Decision("normal", None, False, False)
            devices = (
                await session.execute(
                    text(
                        """
                        SELECT DISTINCT d.fcm_token, p.id AS user_id, p.quiet_hours_start,
                               p.quiet_hours_end, p.phone, p.sms_alerts_opt_in
                        FROM notification_log n
                        JOIN profiles p ON p.id = n.user_id
                        JOIN devices d ON d.user_id = p.id AND d.is_active
                        WHERE n.incident_id = :id AND n.channel = 'push'
                        """
                    ),
                    {"id": incident_id},
                )
            ).all()
        else:
            decision = decide(
                incident.status,
                incident.severity,
                await is_trusted_reporter(session, incident_id),
            )
            if decision is None:
                return 0
            devices = (
                await session.execute(
                    text(
                        """
                        SELECT d.fcm_token, p.id AS user_id, p.quiet_hours_start,
                               p.quiet_hours_end, p.phone, p.sms_alerts_opt_in
                        FROM devices d
                        JOIN profiles p ON p.id = d.user_id
                        JOIN incidents i ON i.id = :id
                        WHERE d.is_active AND p.is_active
                          AND ST_DWithin(d.alert_point, i.location, :coarse)
                          AND ST_Distance(d.alert_point, i.location)
                              <= LEAST(p.alert_radius_km, :max_km) * 1000
                          AND p.id IS DISTINCT FROM :reporter
                        UNION
                        -- Watch zones: places people asked to hear about, wherever they are.
                        SELECT d.fcm_token, p.id AS user_id, p.quiet_hours_start,
                               p.quiet_hours_end, p.phone, p.sms_alerts_opt_in
                        FROM watch_zones z
                        JOIN profiles p ON p.id = z.user_id
                        JOIN devices d ON d.user_id = p.id AND d.is_active
                        JOIN incidents i ON i.id = :id
                        WHERE z.is_active AND p.is_active
                          AND ST_DWithin(z.point, i.location, z.radius_m)
                          AND p.id IS DISTINCT FROM :reporter
                        """
                    ),
                    {
                        "id": incident_id,
                        "coarse": COARSE_RADIUS_M,
                        "max_km": decision.max_km or 20,
                        "reporter": incident.reporter_id,
                    },
                )
            ).all()

        title, body = _copy(trigger, incident)
        kind = "all_clear" if trigger == "resolved" else trigger
        by_user: dict[UUID, list[Any]] = {}
        for device in devices:
            by_user.setdefault(device.user_id, []).append(device)

        tokens: list[str] = []
        recipients = 0
        for user_id, user_devices in by_user.items():
            first = user_devices[0]
            if not decision.bypass_quiet and in_quiet_hours(
                first.quiet_hours_start, first.quiet_hours_end, local_now
            ):
                continue
            if decision.priority != "critical":
                recent = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM notification_log WHERE user_id = :user "
                            "AND channel = 'push' AND sent_at > NOW() - INTERVAL '1 hour'"
                        ),
                        {"user": user_id},
                    )
                ).scalar_one()
                if recent >= settings.push_hourly_cap:
                    continue
            logged = (
                await session.execute(
                    text(
                        "INSERT INTO notification_log (user_id, incident_id, kind, channel) "
                        "VALUES (:user, :incident, :kind, 'push') "
                        "ON CONFLICT DO NOTHING RETURNING 1"
                    ),
                    {"user": user_id, "incident": incident_id, "kind": kind},
                )
            ).first()
            if logged is None:
                continue
            await session.execute(
                text(
                    "INSERT INTO notifications (user_id, incident_id, kind, title, body) "
                    "VALUES (:user, :incident, :kind, :title, :body)"
                ),
                {
                    "user": user_id,
                    "incident": incident_id,
                    "kind": kind,
                    "title": title,
                    "body": body,
                },
            )
            tokens += [device.fcm_token for device in user_devices]
            recipients += 1

            if (
                decision.sms
                and first.phone
                and first.sms_alerts_opt_in
                and settings.sms_fallback_enabled
            ):
                await _sms_fallback(
                    session, settings, sms, redis, user_id, first.phone, incident, body
                )

        await session.commit()

        if tokens:
            invalid = await push.send(
                tokens,
                {
                    "incident_id": str(incident_id),
                    "kind": kind,
                    "severity": incident.severity,
                    "status": incident.status,
                    "priority": decision.priority,
                    "title": title,
                    "body": body,
                },
                high=decision.priority != "normal",
            )
            if invalid:
                await session.execute(
                    text("UPDATE devices SET is_active = false WHERE fcm_token = ANY(:tokens)"),
                    {"tokens": invalid},
                )
                await session.commit()
        return recipients


async def _sms_fallback(
    session: AsyncSession,
    settings: Settings,
    sms: SmsProvider,
    redis: Any,
    user_id: UUID,
    phone: str,
    incident: Any,
    body: str,
) -> None:
    if await redis.get("flag:sms_fallback_off") is not None:
        return
    key = f"cap:sms:{datetime.now(LAGOS).date()}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 172800)
    if count > settings.sms_daily_cap:
        return
    logged = (
        await session.execute(
            text(
                "INSERT INTO notification_log (user_id, incident_id, kind, channel) "
                "VALUES (:user, :incident, 'confirmed', 'sms') "
                "ON CONFLICT DO NOTHING RETURNING 1"
            ),
            {"user": user_id, "incident": incident.id},
        )
    ).first()
    if logged is not None:
        wording = (await app_settings.message_templates(session))["alert_sms"]
        await sms.send(phone, app_settings.fill(wording, label=incident.label, body=body))


EXPIRE_SQL = text(
    """
    WITH due AS (
        SELECT id, status FROM incidents
        WHERE status IN ('unconfirmed', 'corroborated', 'confirmed') AND expires_at <= NOW()
        FOR UPDATE SKIP LOCKED
    ), updated AS (
        UPDATE incidents i SET status = 'expired', last_activity_at = NOW()
        FROM due WHERE i.id = due.id
        RETURNING i.id, due.status AS old_status, i.lga_id, i.ward_id
    ), logged AS (
        INSERT INTO incident_events (incident_id, event_type, from_status, to_status)
        SELECT id, 'expired', old_status, 'expired' FROM updated
    )
    SELECT id, lga_id, ward_id FROM updated
    """
)


async def expire_due(session: AsyncSession) -> list[Any]:
    rows = (await session.execute(EXPIRE_SQL)).all()
    await session.commit()
    return rows


def topics_for(lga_id: int | None, ward_id: int | None) -> list[str]:
    topics = []
    if ward_id is not None:
        topics.append(f"alerts:ward:{ward_id}")
    if lga_id is not None:
        topics.append(f"alerts:lga:{lga_id}")
    return topics
