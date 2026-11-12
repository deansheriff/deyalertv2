from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, not_found
from app.services import app_settings, sanctions
from app.services.areas import Area, resolve_area
from app.services.moderation import needs_review
from app.services.profiles import Profile

POINT = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography"
LIVE_STATUSES = ("unconfirmed", "corroborated", "confirmed")
MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_REPORT_AGE = timedelta(days=7)
TRUSTED_REPORTER_SCORE = 50
MIN_CORROBORATOR_AGE = timedelta(hours=24)
FALSE_REPORT_PENALTY = -10


@dataclass(frozen=True)
class NewIncident:
    client_id: UUID
    type_code: str
    lat: float
    lng: float
    occurred_at: datetime
    description: str | None = None
    location_name: str | None = None
    is_anonymous: bool = False
    device_id: UUID | None = None


@dataclass(frozen=True)
class Result:
    data: dict[str, Any]
    created: bool
    enqueue_trigger: str | None = None


async def _public_point(
    session: AsyncSession, area: Area, lat: float, lng: float, mode: str
) -> tuple[float, float]:
    if mode == "exact":
        return lat, lng
    table, area_id = ("lgas", area.lga_id) if mode == "lga" else ("wards", area.ward_id)
    if area_id is not None:
        row = (
            await session.execute(
                text(
                    "SELECT ST_Y(p) AS lat, ST_X(p) AS lng FROM "
                    f"(SELECT ST_PointOnSurface(geom) AS p FROM {table} WHERE id = :id) s"
                ),
                {"id": area_id},
            )
        ).one()
        return row.lat, row.lng
    return round(lat, 2), round(lng, 2)


def _clamp_occurred(occurred_at: datetime, now: datetime) -> datetime:
    if occurred_at.tzinfo is None:
        raise ApiError(422, "invalid_time", "occurred_at must include a timezone")
    if occurred_at > now + MAX_FUTURE_SKEW:
        raise ApiError(422, "invalid_time", "occurred_at is in the future")
    return max(occurred_at, now - MAX_REPORT_AGE)


async def _summary(session: AsyncSession, incident_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT id, status, severity, corroboration_count, expires_at, "
                "ST_Y(public_location::geometry) AS lat, ST_X(public_location::geometry) AS lng "
                "FROM incidents WHERE id = :id"
            ),
            {"id": incident_id},
        )
    ).one()
    return {
        "id": str(row.id),
        "status": row.status,
        "severity": row.severity,
        "corroboration_count": row.corroboration_count,
        "expires_at": row.expires_at.isoformat(),
        "lat": row.lat,
        "lng": row.lng,
    }


async def create_incident(session: AsyncSession, profile: Profile, new: NewIncident) -> Result:
    existing = (
        await session.execute(
            text(
                "SELECT i.id, r.reporter_id FROM incidents i "
                "LEFT JOIN incident_reporters r ON r.incident_id = i.id "
                "WHERE i.client_id = :client_id"
            ),
            {"client_id": new.client_id},
        )
    ).first()
    if existing is not None:
        if existing.reporter_id != profile.id:
            raise ApiError(409, "client_id_conflict", "This report id is already in use")
        return Result(await _summary(session, existing.id), created=False)

    await sanctions.check_reporting(session, profile.id)
    switches = await app_settings.switches(session)
    if switches["reports_paused"] and not switches["reports_paused_lgas"]:
        raise ApiError(503, "reports_paused", "New reports are paused for now. Try again later.")
    settings = await app_settings.effective(session)
    cap = (
        settings.new_account_daily_report_cap
        if profile.account_age < MIN_CORROBORATOR_AGE or profile.reputation_score < 0
        else settings.daily_report_cap
    )
    today = (
        await session.execute(
            text(
                "SELECT count(*) FROM incident_reporters r "
                "JOIN incidents i ON i.id = r.incident_id "
                "WHERE r.reporter_id = :id AND i.created_at > NOW() - INTERVAL '24 hours'"
            ),
            {"id": profile.id},
        )
    ).scalar_one()
    if today >= cap:
        raise ApiError(429, "daily_cap", "You have reached today's report limit")

    incident_type = (
        await session.execute(
            text(
                "SELECT default_severity, ttl_minutes, sensitive FROM incident_types "
                "WHERE code = :code AND is_active"
            ),
            {"code": new.type_code},
        )
    ).first()
    if incident_type is None:
        raise ApiError(422, "unknown_type", "Unknown incident type")

    if new.device_id is not None:
        await _require_device(session, profile.id, new.device_id)

    area = await resolve_area(session, new.lat, new.lng)
    if switches["reports_paused"] and area.lga_id in switches["reports_paused_lgas"]:
        raise ApiError(503, "reports_paused", "New reports are paused in this area for now.")
    if new.is_anonymous or incident_type.sensitive:
        mode = "lga" if profile.location_precision == "lga" else "ward"
    else:
        mode = profile.location_precision
    public_lat, public_lng = await _public_point(session, area, new.lat, new.lng, mode)

    now = datetime.now(UTC)
    occurred_at = _clamp_occurred(new.occurred_at, now)
    ttl = timedelta(minutes=settings.ttl_override_minutes or incident_type.ttl_minutes)
    expires_at = occurred_at + ttl
    words = await app_settings.word_filter(session)
    matched = app_settings.matched_word(new.description, words["words"]) if words["hold"] else None
    held = needs_review(new.description) or matched is not None
    hold_reason = f"word filter: {matched}" if matched else "automatic text check" if held else None
    status = "under_review" if held else "expired" if expires_at <= now else "unconfirmed"

    incident_id = (
        await session.execute(
            text(
                f"""
                INSERT INTO incidents (client_id, type_code, description, location, public_location,
                    location_name, lga_id, ward_id, status, severity, is_anonymous, is_hidden,
                    hidden_reason, hold_reason, occurred_at, expires_at)
                VALUES (:client_id, :type_code, :description, {POINT},
                    ST_SetSRID(ST_MakePoint(:public_lng, :public_lat), 4326)::geography,
                    :location_name, :lga_id, :ward_id, :status, :severity, :anonymous, :hidden,
                    :hidden_reason, :hold_reason, :occurred_at, :expires_at)
                ON CONFLICT (client_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "client_id": new.client_id,
                "type_code": new.type_code,
                "description": new.description,
                "lat": new.lat,
                "lng": new.lng,
                "public_lat": public_lat,
                "public_lng": public_lng,
                "location_name": new.location_name,
                "lga_id": area.lga_id,
                "ward_id": area.ward_id,
                "status": status,
                "severity": incident_type.default_severity,
                "anonymous": new.is_anonymous,
                "hidden": held,
                "hidden_reason": "automatic text check" if held else None,
                "hold_reason": hold_reason,
                "occurred_at": occurred_at,
                "expires_at": expires_at,
            },
        )
    ).scalar()
    if incident_id is None:
        await session.rollback()
        return await create_incident(session, profile, new)

    await session.execute(
        text(
            "INSERT INTO incident_reporters (incident_id, reporter_id, device_id) "
            "VALUES (:incident, :reporter, :device)"
        ),
        {"incident": incident_id, "reporter": profile.id, "device": new.device_id},
    )
    await _event(session, incident_id, profile.id, "created", None, status)
    data = await _summary(session, incident_id)
    await session.commit()
    trigger = "created" if status == "unconfirmed" else None
    return Result(data, created=True, enqueue_trigger=trigger)


async def _require_device(session: AsyncSession, user_id: UUID, device_id: UUID) -> None:
    owned = (
        await session.execute(
            text("SELECT 1 FROM devices WHERE id = :id AND user_id = :user"),
            {"id": device_id, "user": user_id},
        )
    ).first()
    if owned is None:
        raise ApiError(403, "device_not_owned", "Device does not belong to this account")


async def _event(
    session: AsyncSession,
    incident_id: UUID,
    actor_id: UUID | None,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    note: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO incident_events (incident_id, actor_id, event_type, from_status, "
            "to_status, note) VALUES (:incident, :actor, :event, :from_status, :to_status, :note)"
        ),
        {
            "incident": incident_id,
            "actor": actor_id,
            "event": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "note": note,
        },
    )


async def _lock(session: AsyncSession, incident_id: UUID) -> Any:
    row = (
        await session.execute(
            text(
                "SELECT id, status, severity, lga_id, ward_id, corroboration_count, flag_weight "
                "FROM incidents WHERE id = :id FOR UPDATE"
            ),
            {"id": incident_id},
        )
    ).first()
    if row is None:
        raise not_found("Incident not found")
    return row


async def _reporter(session: AsyncSession, incident_id: UUID) -> Any:
    return (
        await session.execute(
            text("SELECT reporter_id, device_id FROM incident_reporters WHERE incident_id = :id"),
            {"id": incident_id},
        )
    ).first()


async def _scoped_title(
    session: AsyncSession, user_id: UUID, lga_id: int | None, ward_id: int | None
) -> tuple[bool, str | None]:
    if lga_id is None:
        return False, None
    row = (
        await session.execute(
            text(
                "SELECT v.title, w.name AS ward FROM verifiers v "
                "LEFT JOIN wards w ON w.id = COALESCE(v.ward_id, :ward) "
                "WHERE v.user_id = :user AND v.is_active AND v.lga_id = :lga "
                "AND (v.ward_id IS NULL OR v.ward_id = :ward) LIMIT 1"
            ),
            {"user": user_id, "lga": lga_id, "ward": ward_id},
        )
    ).first()
    if row is None:
        return False, None
    title = row.title or "Verifier"
    return True, f"{title}, {row.ward}" if row.ward else title


async def _reward(session: AsyncSession, user_id: UUID | None, change: int) -> None:
    if user_id is None:
        return
    await session.execute(
        text("UPDATE profiles SET reputation_score = reputation_score + :change WHERE id = :id"),
        {"change": change, "id": user_id},
    )


async def _upgrade_to_corroborated(session: AsyncSession, incident: Any, actor: UUID) -> None:
    await session.execute(
        text(
            "UPDATE incidents SET status = 'corroborated', last_activity_at = NOW() "
            "WHERE id = :id"
        ),
        {"id": incident.id},
    )
    await _event(session, incident.id, actor, "corroborated", incident.status, "corroborated")
    reporter = await _reporter(session, incident.id)
    await _reward(session, reporter.reporter_id if reporter else None, 2)


async def corroborate(
    session: AsyncSession,
    profile: Profile,
    incident_id: UUID,
    lat: float,
    lng: float,
    device_id: UUID,
) -> Result:
    await sanctions.check_reporting(session, profile.id)
    settings = await app_settings.effective(session)
    await _require_device(session, profile.id, device_id)
    incident = await _lock(session, incident_id)
    if incident.status not in LIVE_STATUSES:
        raise ApiError(409, "not_live", "This report is no longer active")

    is_verifier, _ = await _scoped_title(session, profile.id, incident.lga_id, incident.ward_id)
    reporter = await _reporter(session, incident_id)
    if reporter is not None and reporter.reporter_id == profile.id:
        raise ApiError(403, "own_report", "You cannot confirm your own report")
    if reporter is not None and reporter.device_id == device_id:
        raise ApiError(403, "same_device", "This device filed the report")
    if profile.account_age < MIN_CORROBORATOR_AGE and not is_verifier:
        raise ApiError(403, "account_too_new", "New accounts cannot confirm reports yet")

    distance = (
        await session.execute(
            text(f"SELECT ST_Distance(location, {POINT}) FROM incidents WHERE id = :id"),
            {"lat": lat, "lng": lng, "id": incident_id},
        )
    ).scalar_one()
    if distance > settings.corroboration_radius_m:
        raise ApiError(403, "too_far", "You need to be closer to confirm this")

    duplicate_device = (
        await session.execute(
            text(
                "SELECT 1 FROM corroborations WHERE incident_id = :incident AND device_id = :device"
            ),
            {"incident": incident_id, "device": device_id},
        )
    ).first()
    if duplicate_device is not None:
        raise ApiError(409, "device_used", "This device already confirmed this report")

    recent = (
        await session.execute(
            text(
                "SELECT count(*) FROM corroborations WHERE user_id = :user "
                "AND created_at > NOW() - INTERVAL '1 hour'"
            ),
            {"user": profile.id},
        )
    ).scalar_one()
    if recent >= settings.corroboration_hourly_cap:
        raise ApiError(429, "velocity", "Too many confirmations. Try again later.")

    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    f"""
                    INSERT INTO corroborations (incident_id, user_id, device_id, location,
                        distance_m) VALUES (:incident, :user, :device, {POINT}, :distance)
                    """
                ),
                {
                    "incident": incident_id,
                    "user": profile.id,
                    "device": device_id,
                    "lat": lat,
                    "lng": lng,
                    "distance": int(distance),
                },
            )
    except IntegrityError as exc:
        raise ApiError(409, "already_confirmed", "You already confirmed this report") from exc

    count = incident.corroboration_count + 1
    await session.execute(
        text(
            "UPDATE incidents SET corroboration_count = :count, last_activity_at = NOW() "
            "WHERE id = :id"
        ),
        {"count": count, "id": incident_id},
    )
    await _event(session, incident_id, profile.id, "corroboration_added", None, None)

    trigger = None
    if incident.status == "unconfirmed" and (
        count >= settings.corroboration_threshold or is_verifier
    ):
        await _upgrade_to_corroborated(session, incident, profile.id)
        trigger = "corroborated"
    data = await _summary(session, incident_id)
    await session.commit()
    return Result(data, created=True, enqueue_trigger=trigger)


def flag_weight(profile: Profile, in_area: bool) -> float:
    if not in_area:
        return 0.05
    if profile.account_age < MIN_CORROBORATOR_AGE:
        return 0.2
    if profile.reputation_score < 0:
        return 0.3
    return 1.5 if profile.reputation_score >= 20 else 1.0


async def flag(
    session: AsyncSession,
    profile: Profile,
    incident_id: UUID,
    reason: str,
    notes: str | None,
    lat: float | None,
    lng: float | None,
) -> Result:
    settings = await app_settings.effective(session)
    incident = await _lock(session, incident_id)
    if incident.status in ("false_report", "under_review"):
        raise ApiError(409, "already_under_review", "This report is already being reviewed")

    in_area = False
    if lat is not None and lng is not None:
        in_area = (
            await session.execute(
                text(
                    f"SELECT ST_DWithin(location, {POINT}, :radius) FROM incidents WHERE id = :id"
                ),
                {"lat": lat, "lng": lng, "radius": settings.flag_radius_m, "id": incident_id},
            )
        ).scalar_one()
    if not in_area and incident.lga_id is not None:
        in_area = (
            await session.execute(
                text(
                    "SELECT 1 FROM corroborations c JOIN incidents i ON i.id = c.incident_id "
                    "WHERE c.user_id = :user AND i.lga_id = :lga LIMIT 1"
                ),
                {"user": profile.id, "lga": incident.lga_id},
            )
        ).first() is not None

    weight = flag_weight(profile, in_area)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO incident_flags (incident_id, flagged_by, reason, notes, weight) "
                    "VALUES (:incident, :user, :reason, :notes, :weight)"
                ),
                {
                    "incident": incident_id,
                    "user": profile.id,
                    "reason": reason,
                    "notes": notes,
                    "weight": weight,
                },
            )
    except IntegrityError as exc:
        raise ApiError(409, "already_flagged", "You already flagged this report") from exc

    total = float(incident.flag_weight) + weight
    hide = total >= settings.flag_hide_threshold
    await session.execute(
        text(
            "UPDATE incidents SET flag_weight = :total, "
            "status = CASE WHEN :hide THEN 'under_review' ELSE status END, "
            "is_hidden = is_hidden OR :hide, "
            "hidden_reason = CASE WHEN :hide THEN 'flag threshold' ELSE hidden_reason END "
            "WHERE id = :id"
        ),
        {"total": total, "hide": hide, "id": incident_id},
    )
    await _event(session, incident_id, profile.id, "flagged", None, None, reason)
    if hide:
        await _event(session, incident_id, None, "hidden", incident.status, "under_review")
    data = await _summary(session, incident_id)
    await session.commit()
    return Result(data, created=True)


async def audit(
    session: AsyncSession,
    actor_id: UUID,
    action: str,
    entity_type: str,
    entity_id: str,
    reason: str | None = None,
    metadata: str | None = None,
    ip: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_log (actor_id, action, entity_type, entity_id, reason, "
            "metadata, ip) "
            "VALUES (:actor, :action, :entity_type, :entity_id, :reason, "
            "CAST(:metadata AS JSONB), CAST(:ip AS INET))"
        ),
        {
            "actor": actor_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "reason": reason,
            "metadata": metadata,
            "ip": ip,
        },
    )


async def _settle_flags(session: AsyncSession, incident_id: UUID, upheld: bool) -> None:
    outcome = "upheld" if upheld else "rejected"
    change = 1 if upheld else -1
    await session.execute(
        text(
            "UPDATE profiles SET reputation_score = reputation_score + :change WHERE id IN "
            "(SELECT flagged_by FROM incident_flags WHERE incident_id = :id AND outcome IS NULL "
            "AND flagged_by IS NOT NULL)"
        ),
        {"change": change, "id": incident_id},
    )
    await session.execute(
        text(
            "UPDATE incident_flags SET outcome = :outcome "
            "WHERE incident_id = :id AND outcome IS NULL"
        ),
        {"outcome": outcome, "id": incident_id},
    )


async def verify(
    session: AsyncSession,
    profile: Profile,
    incident_id: UUID,
    action: str,
    note: str | None,
    extend_minutes: int | None,
) -> Result:
    incident = await _lock(session, incident_id)
    scoped, label = await _scoped_title(session, profile.id, incident.lga_id, incident.ward_id)
    if not (scoped or profile.is_staff):
        raise ApiError(403, "out_of_scope", "You cannot verify reports in this area")

    trigger = None
    reporter = await _reporter(session, incident_id)
    reporter_id = reporter.reporter_id if reporter else None

    if action == "confirm":
        if incident.status not in ("unconfirmed", "corroborated"):
            raise ApiError(409, "invalid_state", "This report cannot be confirmed")
        await session.execute(
            text(
                "UPDATE incidents SET status = 'confirmed', confirmed_by = :actor, "
                "confirmed_by_label = :label, confirmed_at = NOW(), last_activity_at = NOW() "
                "WHERE id = :id"
            ),
            {"actor": profile.id, "label": label or "Moderator", "id": incident_id},
        )
        await _event(session, incident_id, profile.id, "confirmed", incident.status, "confirmed")
        await _reward(session, reporter_id, 5)
        await session.execute(
            text(
                "UPDATE profiles SET reputation_score = reputation_score + 1 WHERE id IN "
                "(SELECT user_id FROM corroborations WHERE incident_id = :id "
                "AND user_id IS NOT NULL)"
            ),
            {"id": incident_id},
        )
        await _settle_flags(session, incident_id, upheld=False)
        trigger = "confirmed"
    elif action == "resolve":
        if incident.status not in LIVE_STATUSES:
            raise ApiError(409, "invalid_state", "This report cannot be resolved")
        await session.execute(
            text(
                "UPDATE incidents SET status = 'resolved', resolved_at = NOW(), "
                "last_activity_at = NOW() WHERE id = :id"
            ),
            {"id": incident_id},
        )
        await _event(session, incident_id, profile.id, "resolved", incident.status, "resolved")
        trigger = "resolved"
    elif action == "extend":
        if incident.status not in LIVE_STATUSES:
            raise ApiError(409, "invalid_state", "This report cannot be extended")
        await session.execute(
            text(
                "UPDATE incidents SET expires_at = GREATEST(expires_at, NOW()) "
                "+ make_interval(mins => :minutes), last_activity_at = NOW() WHERE id = :id"
            ),
            {"minutes": extend_minutes or 60, "id": incident_id},
        )
        await _event(session, incident_id, profile.id, "extended", None, None)
    elif action == "mark_false":
        if incident.status in ("false_report", "resolved"):
            raise ApiError(409, "invalid_state", "This report cannot be marked false")
        await session.execute(
            text(
                "UPDATE incidents SET status = 'false_report', is_hidden = true, "
                "hidden_reason = 'marked false', last_activity_at = NOW() WHERE id = :id"
            ),
            {"id": incident_id},
        )
        await _event(
            session, incident_id, profile.id, "marked_false", incident.status, "false_report"
        )
        await _reward(session, reporter_id, FALSE_REPORT_PENALTY)
        await _settle_flags(session, incident_id, upheld=True)
    elif action == "restore":
        if not profile.is_staff and not scoped:
            raise ApiError(403, "out_of_scope", "You cannot restore this report")
        if incident.status != "under_review":
            raise ApiError(409, "invalid_state", "This report is not under review")
        await session.execute(
            text(
                "UPDATE incidents SET status = CASE WHEN corroboration_count >= :threshold "
                "THEN 'corroborated' ELSE 'unconfirmed' END, is_hidden = false, "
                "hidden_reason = NULL, flag_weight = 0, last_activity_at = NOW() WHERE id = :id"
            ),
            {
                "threshold": (await app_settings.effective(session)).corroboration_threshold,
                "id": incident_id,
            },
        )
        await _event(session, incident_id, profile.id, "restored", "under_review", None)
        await _settle_flags(session, incident_id, upheld=False)
    else:
        raise ApiError(422, "unknown_action", "Unknown action")

    await audit(session, profile.id, f"verify:{action}", "incident", str(incident_id), note)
    if note:
        await _event(session, incident_id, profile.id, "note", None, None, note)
    data = await _summary(session, incident_id)
    await session.commit()
    return Result(data, created=False, enqueue_trigger=trigger)


async def is_trusted_reporter(session: AsyncSession, incident_id: UUID) -> bool:
    row = (
        await session.execute(
            text(
                "SELECT p.role, p.reputation_score FROM incident_reporters r "
                "JOIN profiles p ON p.id = r.reporter_id WHERE r.incident_id = :id"
            ),
            {"id": incident_id},
        )
    ).first()
    if row is None:
        return False
    return row.role in ("verifier", "moderator", "admin", "super_admin") or (
        row.reputation_score >= TRUSTED_REPORTER_SCORE
    )
