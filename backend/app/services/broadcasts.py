"""Official broadcasts: written by one staff member, approved by another, then sent."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import app_settings
from app.services.messaging import PushProvider, SmsProvider

PUSH_BATCH = 500
SMS_OFF_FLAG = "flag:sms_fallback_off"

# Everyone when no area is chosen; otherwise people whose home or alert point is inside it.
AUDIENCE = """
    FROM profiles p
    JOIN devices d ON d.user_id = p.id AND d.is_active
    WHERE p.is_active AND (
        (cardinality(CAST(:lgas AS INT[])) = 0 AND cardinality(CAST(:wards AS INT[])) = 0)
        OR p.home_lga_id = ANY(CAST(:lgas AS INT[]))
        OR p.home_ward_id = ANY(CAST(:wards AS INT[]))
        OR EXISTS (SELECT 1 FROM lgas l WHERE l.id = ANY(CAST(:lgas AS INT[]))
                   AND l.geom IS NOT NULL AND ST_Covers(l.geom, d.alert_point::geometry))
        OR EXISTS (SELECT 1 FROM wards w WHERE w.id = ANY(CAST(:wards AS INT[]))
                   AND w.geom IS NOT NULL AND ST_Covers(w.geom, d.alert_point::geometry))
    )
"""


def _area(lga_ids: list[int], ward_ids: list[int]) -> dict[str, list[int]]:
    return {"lgas": list(lga_ids), "wards": list(ward_ids)}


async def estimate(
    session: AsyncSession, lga_ids: list[int], ward_ids: list[int]
) -> dict[str, int]:
    row = (
        await session.execute(
            text(
                "SELECT count(DISTINCT p.id) AS people, count(DISTINCT p.id) FILTER "
                "(WHERE p.sms_alerts_opt_in AND p.phone IS NOT NULL) AS sms " + AUDIENCE
            ),
            _area(lga_ids, ward_ids),
        )
    ).one()
    return {"people": row.people, "sms": row.sms}


async def send(
    session: AsyncSession,
    push: PushProvider,
    sms: SmsProvider,
    redis: Any,
    broadcast_id: UUID,
) -> dict[str, int]:
    claimed = (
        await session.execute(
            text(
                "UPDATE broadcasts SET status = 'sending' WHERE id = :id AND status = 'approved' "
                "RETURNING id, title, body, severity, lga_ids, ward_ids, channels"
            ),
            {"id": broadcast_id},
        )
    ).first()
    await session.commit()
    if claimed is None:
        return {}
    area = _area(claimed.lga_ids, claimed.ward_ids)
    devices = (
        await session.execute(
            text("SELECT DISTINCT d.fcm_token, p.id AS user_id " + AUDIENCE), area
        )
    ).all()
    stats = {
        "recipients": len({d.user_id for d in devices}),
        "push_sent": 0,
        "push_failed": 0,
        "sms_sent": 0,
    }
    if "push" in claimed.channels:
        data = {
            "kind": "broadcast",
            "broadcast_id": str(claimed.id),
            "title": claimed.title,
            "body": claimed.body,
            "severity": claimed.severity,
            # The app picks its notification channel from this.
            "priority": {"critical": "critical", "high": "high"}.get(claimed.severity, "normal"),
        }
        tokens = [d.fcm_token for d in devices]
        for start in range(0, len(tokens), PUSH_BATCH):
            batch = tokens[start : start + PUSH_BATCH]
            invalid = await push.send(batch, data, claimed.severity == "critical")
            stats["push_sent"] += len(batch) - len(invalid)
            stats["push_failed"] += len(invalid)
            if invalid:
                await session.execute(
                    text("UPDATE devices SET is_active = false WHERE fcm_token = ANY(:tokens)"),
                    {"tokens": invalid},
                )
    if "sms" in claimed.channels and not await redis.get(SMS_OFF_FLAG):
        phones = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT p.phone "
                        + AUDIENCE
                        + " AND p.sms_alerts_opt_in AND p.phone IS NOT NULL"
                    ),
                    area,
                )
            )
            .scalars()
            .all()
        )
        settings = await app_settings.effective(session)
        wording = (await app_settings.message_templates(session))["broadcast_sms"]
        message = app_settings.fill(wording, title=claimed.title, body=claimed.body)
        day = datetime.now(UTC).date()
        for phone in phones:
            count = await redis.incr(f"cap:sms:{day}")
            if count == 1:
                await redis.expire(f"cap:sms:{day}", 172800)
            if count > settings.sms_daily_cap:
                break
            await sms.send(phone, message)
            stats["sms_sent"] += 1
    await session.execute(
        text(
            "UPDATE broadcasts SET status = 'sent', sent_at = NOW(), recipients = :recipients, "
            "push_sent = :push_sent, push_failed = :push_failed, sms_sent = :sms_sent "
            "WHERE id = :id"
        ),
        {**stats, "id": broadcast_id},
    )
    await session.commit()
    return stats
