"""App features beyond reporting: watch zones, verifier updates, support, verifier
applications, appeals and data correction requests."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_profile
from app.core.db import get_session
from app.core.errors import ApiError
from app.core.security import AuthUser, get_auth_user
from app.services import app_settings, sanctions
from app.services.incidents import _scoped_title
from app.services.moderation import needs_review
from app.services.profiles import Profile

router = APIRouter(prefix="/v1", tags=["community"])
MAX_ZONES = 5


# Watch zones


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: Literal["home", "work", "school", "other"] = "other"
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=2000, ge=200, le=10000)
    is_active: bool = True


ZONE_COLUMNS = (
    "id, name, kind, radius_m, is_active, ST_Y(point::geometry) AS lat, "
    "ST_X(point::geometry) AS lng"
)


def _zone(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "lat": row.lat,
        "lng": row.lng,
        "radius_m": row.radius_m,
        "is_active": row.is_active,
    }


@router.get("/me/zones")
async def list_zones(
    profile: Profile = Depends(current_profile), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(f"SELECT {ZONE_COLUMNS} FROM watch_zones WHERE user_id = :id ORDER BY id"),
            {"id": profile.id},
        )
    ).all()
    return [_zone(row) for row in rows]


@router.post("/me/zones", status_code=201)
async def add_zone(
    body: ZoneIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    count = await session.scalar(
        text("SELECT count(*) FROM watch_zones WHERE user_id = :id"), {"id": profile.id}
    )
    if (count or 0) >= MAX_ZONES:
        raise ApiError(409, "zone_limit", f"You can have up to {MAX_ZONES} watch zones")
    row = (
        await session.execute(
            text(
                f"""
                INSERT INTO watch_zones (user_id, name, kind, point, radius_m, is_active)
                VALUES (:user, :name, :kind,
                        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :radius, :active)
                RETURNING {ZONE_COLUMNS}
                """
            ),
            {
                "user": profile.id,
                "name": body.name.strip(),
                "kind": body.kind,
                "lat": body.lat,
                "lng": body.lng,
                "radius": body.radius_m,
                "active": body.is_active,
            },
        )
    ).one()
    await session.commit()
    return _zone(row)


@router.put("/me/zones/{zone_id}")
async def update_zone(
    zone_id: int,
    body: ZoneIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                f"""
                UPDATE watch_zones SET name = :name, kind = :kind, radius_m = :radius,
                    is_active = :active,
                    point = ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                WHERE id = :zone AND user_id = :user
                RETURNING {ZONE_COLUMNS}
                """
            ),
            {
                "zone": zone_id,
                "user": profile.id,
                "name": body.name.strip(),
                "kind": body.kind,
                "lat": body.lat,
                "lng": body.lng,
                "radius": body.radius_m,
                "active": body.is_active,
            },
        )
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "Watch zone not found")
    await session.commit()
    return _zone(row)


@router.delete("/me/zones/{zone_id}", status_code=204)
async def delete_zone(
    zone_id: int,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.execute(
        text("DELETE FROM watch_zones WHERE id = :zone AND user_id = :user"),
        {"zone": zone_id, "user": profile.id},
    )
    await session.commit()


# Verifier updates on incidents


class UpdateIn(BaseModel):
    body: str = Field(min_length=2, max_length=500)


@router.get("/incidents/{incident_id}/updates")
async def incident_updates(
    incident_id: UUID,
    _: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, kind, author_label, body, created_at FROM incident_updates "
                "WHERE incident_id = :id AND NOT is_hidden ORDER BY created_at DESC LIMIT 50"
            ),
            {"id": incident_id},
        )
    ).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "author": row.author_label,
            "body": row.body,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


# Community follow-ups: anyone can add what they've seen since, even after confirmation.

FOLLOWUP_STATUSES = ("unconfirmed", "corroborated", "confirmed")
FOLLOWUPS_PER_INCIDENT_PER_DAY = 3
FOLLOWUPS_PER_DAY = 20
COMMUNITY_LABEL = "Community member"


@router.post("/incidents/{incident_id}/followups", status_code=201)
async def post_followup(
    incident_id: UUID,
    body: UpdateIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await sanctions.check_reporting(session, profile.id)
    incident = (
        await session.execute(
            text("SELECT status, is_hidden, merged_into FROM incidents WHERE id = :id"),
            {"id": incident_id},
        )
    ).first()
    if incident is None or incident.is_hidden or incident.merged_into is not None:
        raise ApiError(404, "not_found", "Report not found")
    if incident.status not in FOLLOWUP_STATUSES:
        raise ApiError(409, "closed", "This report is closed, so follow-ups are off")
    counts = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE incident_id = :incident) AS here, count(*) AS total "
                "FROM incident_updates WHERE author_id = :author AND kind = 'community' "
                "AND created_at > NOW() - INTERVAL '24 hours'"
            ),
            {"incident": incident_id, "author": profile.id},
        )
    ).one()
    if counts.here >= FOLLOWUPS_PER_INCIDENT_PER_DAY or counts.total >= FOLLOWUPS_PER_DAY:
        raise ApiError(429, "followup_limit", "You've added several follow-ups today. Thank you!")
    text_body = body.body.strip()
    words = await app_settings.word_filter(session)
    matched = app_settings.matched_word(text_body, words["words"]) if words["hold"] else None
    if matched:
        hold_reason: str | None = f"word filter: {matched}"
    elif needs_review(text_body):
        hold_reason = "automatic text check"
    else:
        hold_reason = None
    row = (
        await session.execute(
            text(
                "INSERT INTO incident_updates (incident_id, author_id, author_label, body, kind, "
                "is_hidden, hold_reason) VALUES (:incident, :author, :label, :body, 'community', "
                ":hidden, :reason) RETURNING id, created_at"
            ),
            {
                "incident": incident_id,
                "author": profile.id,
                "label": COMMUNITY_LABEL,
                "body": text_body,
                "hidden": hold_reason is not None,
                "reason": hold_reason,
            },
        )
    ).one()
    await session.execute(
        text("UPDATE incidents SET last_activity_at = NOW() WHERE id = :id"), {"id": incident_id}
    )
    await session.commit()
    return {
        "id": row.id,
        "kind": "community",
        "author": COMMUNITY_LABEL,
        "body": text_body,
        "created_at": row.created_at.isoformat(),
        "held": hold_reason is not None,
    }


# All reports, nationwide

ALL_REPORTS_PAGE = 30
STATUS_FILTERS = {
    "confirmed": "p.status = 'confirmed'",
    "unverified": "p.status IN ('unconfirmed','corroborated')",
    "resolved": "p.status IN ('resolved','expired')",
}


@router.get("/reports")
async def all_reports(
    q: str = "",
    status: str = "",
    severity: str = "",
    type_code: str = "",
    since_hours: int | None = None,
    before: str | None = None,
    _: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Recent public reports everywhere, newest first, for people anywhere to follow."""
    clauses = ["true"]
    params: dict[str, Any] = {"limit": ALL_REPORTS_PAGE + 1}
    if status in STATUS_FILTERS:
        clauses.append(STATUS_FILTERS[status])
    if severity in ("low", "medium", "high", "critical"):
        clauses.append("p.severity = :severity")
        params["severity"] = severity
    if type_code:
        clauses.append("p.type_code = :type_code")
        params["type_code"] = type_code
    if since_hours:
        clauses.append("p.created_at > NOW() - make_interval(hours => :hours)")
        params["hours"] = min(max(since_hours, 1), 168)
    if before:
        clauses.append("p.created_at < CAST(:before AS TIMESTAMPTZ)")
        params["before"] = before
    if q.strip():
        clauses.append(
            "(p.location_name ILIKE '%' || :q || '%' OR l.name ILIKE '%' || :q || '%' "
            "OR l.state ILIKE '%' || :q || '%' OR w.name ILIKE '%' || :q || '%' "
            "OR t.label ILIKE '%' || :q || '%')"
        )
        params["q"] = q.strip()[:60]
    where = " AND ".join(clauses)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT p.id, p.type_code, p.description, p.lat, p.lng, p.location_name,
                       p.lga_id, p.ward_id, p.status, p.severity, p.corroboration_count,
                       p.occurred_at, p.created_at, p.expires_at, p.confirmed_by_label,
                       l.name AS lga_name, l.state, w.name AS ward_name,
                       (SELECT count(*) FROM incident_updates u WHERE u.incident_id = p.id
                          AND NOT u.is_hidden) AS update_count
                FROM incidents_public p
                JOIN incident_types t ON t.code = p.type_code
                LEFT JOIN lgas l ON l.id = p.lga_id
                LEFT JOIN wards w ON w.id = p.ward_id
                WHERE {where}
                ORDER BY p.created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).all()
    items = [
        {
            "id": str(row.id),
            "type_code": row.type_code,
            "description": row.description,
            "lat": row.lat,
            "lng": row.lng,
            "location_name": row.location_name,
            "lga_id": row.lga_id,
            "ward_id": row.ward_id,
            "status": row.status,
            "severity": row.severity,
            "corroboration_count": row.corroboration_count,
            "occurred_at": row.occurred_at.isoformat(),
            "created_at": row.created_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "confirmed_by_label": row.confirmed_by_label,
            "area": ", ".join(part for part in (row.ward_name, row.lga_name, row.state) if part),
            "update_count": row.update_count,
        }
        for row in rows[:ALL_REPORTS_PAGE]
    ]
    return {
        "items": items,
        "next_before": items[-1]["created_at"] if len(rows) > ALL_REPORTS_PAGE else None,
    }


@router.post("/incidents/{incident_id}/updates", status_code=201)
async def post_update(
    incident_id: UUID,
    body: UpdateIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Verifiers in the incident's area, and staff, can post public updates."""
    area = (
        await session.execute(
            text("SELECT lga_id, ward_id FROM incidents WHERE id = :id"), {"id": incident_id}
        )
    ).first()
    if area is None:
        raise ApiError(404, "not_found", "Incident not found")
    scoped, label = await _scoped_title(session, profile.id, area.lga_id, area.ward_id)
    if not (scoped or profile.is_staff):
        raise ApiError(403, "not_verifier", "Only verifiers in this area can post updates")
    row = (
        await session.execute(
            text(
                "INSERT INTO incident_updates (incident_id, author_id, author_label, body) "
                "VALUES (:incident, :author, :label, :body) RETURNING id, created_at"
            ),
            {
                "incident": incident_id,
                "author": profile.id,
                "label": label or "Dey Alert team",
                "body": body.body.strip(),
            },
        )
    ).one()
    await session.execute(
        text("UPDATE incidents SET last_activity_at = NOW() WHERE id = :id"), {"id": incident_id}
    )
    await session.commit()
    return {
        "id": row.id,
        "author": label or "Dey Alert team",
        "body": body.body.strip(),
        "created_at": row.created_at.isoformat(),
    }


# Support


class TicketIn(BaseModel):
    category: Literal["bug", "question", "abuse", "idea"]
    message: str = Field(min_length=5, max_length=4000)
    app_info: str | None = Field(default=None, max_length=200)


class ReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


async def _ticket_json(session: AsyncSession, ticket: Any) -> dict[str, Any]:
    messages = (
        await session.execute(
            text(
                "SELECT from_staff, body, created_at FROM support_messages "
                "WHERE ticket_id = :id AND NOT is_internal ORDER BY created_at"
            ),
            {"id": ticket.id},
        )
    ).all()
    return {
        "id": ticket.id,
        "category": ticket.category,
        "status": ticket.status,
        "created_at": ticket.created_at.isoformat(),
        "messages": [
            {"from_staff": m.from_staff, "body": m.body, "created_at": m.created_at.isoformat()}
            for m in messages
        ],
    }


@router.get("/support")
async def my_tickets(
    profile: Profile = Depends(current_profile), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    tickets = (
        await session.execute(
            text(
                "SELECT id, category, status, created_at FROM support_tickets "
                "WHERE user_id = :id ORDER BY updated_at DESC LIMIT 20"
            ),
            {"id": profile.id},
        )
    ).all()
    return [await _ticket_json(session, ticket) for ticket in tickets]


@router.post("/support", status_code=201)
async def open_ticket(
    body: TicketIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    open_count = await session.scalar(
        text(
            "SELECT count(*) FROM support_tickets WHERE user_id = :id "
            "AND created_at > NOW() - INTERVAL '1 day'"
        ),
        {"id": profile.id},
    )
    if (open_count or 0) >= 5:
        raise ApiError(
            429, "support_limit", "You have sent several messages today. We'll reply soon."
        )
    ticket = (
        await session.execute(
            text(
                "INSERT INTO support_tickets (user_id, category, app_info) "
                "VALUES (:user, :category, :info) RETURNING id, category, status, created_at"
            ),
            {"user": profile.id, "category": body.category, "info": body.app_info},
        )
    ).one()
    await session.execute(
        text(
            "INSERT INTO support_messages (ticket_id, author_id, from_staff, body) "
            "VALUES (:ticket, :author, false, :body)"
        ),
        {"ticket": ticket.id, "author": profile.id, "body": body.message.strip()},
    )
    await session.commit()
    return await _ticket_json(session, ticket)


@router.post("/support/{ticket_id}/reply")
async def reply_ticket(
    ticket_id: int,
    body: ReplyIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ticket = (
        await session.execute(
            text(
                "UPDATE support_tickets SET status = 'open', updated_at = NOW() "
                "WHERE id = :id AND user_id = :user RETURNING id, category, status, created_at"
            ),
            {"id": ticket_id, "user": profile.id},
        )
    ).first()
    if ticket is None:
        raise ApiError(404, "not_found", "Conversation not found")
    await session.execute(
        text(
            "INSERT INTO support_messages (ticket_id, author_id, from_staff, body) "
            "VALUES (:ticket, :author, false, :body)"
        ),
        {"ticket": ticket_id, "author": profile.id, "body": body.message.strip()},
    )
    await session.commit()
    return await _ticket_json(session, ticket)


# Verifier applications


class ApplicationIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    community_role: str = Field(min_length=2, max_length=100)
    lga_id: int
    ward_id: int | None = None
    reason: str = Field(min_length=20, max_length=1000)


async def _latest_application(session: AsyncSession, user_id: UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT a.id, a.status, a.created_at, a.community_role, l.name AS lga, "
                "w.name AS ward FROM verifier_applications a JOIN lgas l ON l.id = a.lga_id "
                "LEFT JOIN wards w ON w.id = a.ward_id WHERE a.user_id = :id "
                "ORDER BY a.created_at DESC LIMIT 1"
            ),
            {"id": user_id},
        )
    ).first()


def _application_json(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "community_role": row.community_role,
        "area": f"{row.ward}, {row.lga}" if row.ward else row.lga,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/verifier-application")
async def my_application(
    profile: Profile = Depends(current_profile), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    lgas = (await session.execute(text("SELECT id, name FROM lgas ORDER BY name"))).all()
    return {
        "application": _application_json(await _latest_application(session, profile.id)),
        "is_verifier": profile.role != "member",
        "lgas": [{"id": row.id, "name": row.name} for row in lgas],
    }


@router.post("/verifier-application", status_code=201)
async def apply(
    body: ApplicationIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if profile.role != "member":
        raise ApiError(409, "already_staff", "You already have a verifier or staff role")
    if body.ward_id is not None:
        in_lga = await session.scalar(
            text("SELECT 1 FROM wards WHERE id = :ward AND lga_id = :lga"),
            {"ward": body.ward_id, "lga": body.lga_id},
        )
        if not in_lga:
            raise ApiError(422, "bad_ward", "That ward is not in the chosen LGA")
    pending = await session.scalar(
        text("SELECT 1 FROM verifier_applications WHERE user_id = :id AND status = 'pending'"),
        {"id": profile.id},
    )
    if pending:
        raise ApiError(409, "pending", "Your application is already being reviewed")
    await session.execute(
        text(
            "INSERT INTO verifier_applications (user_id, full_name, community_role, lga_id, "
            "ward_id, reason) VALUES (:user, :name, :role, :lga, :ward, :reason)"
        ),
        {
            "user": profile.id,
            "name": body.full_name.strip(),
            "role": body.community_role.strip(),
            "lga": body.lga_id,
            "ward": body.ward_id,
            "reason": body.reason.strip(),
        },
    )
    await session.commit()
    return {"application": _application_json(await _latest_application(session, profile.id))}


# Sanctions and privacy


class AppealIn(BaseModel):
    message: str = Field(min_length=10, max_length=2000)


@router.get("/me/sanctions")
async def my_sanctions(
    user: AuthUser = Depends(get_auth_user), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    """Readable while suspended, so the app can show why and offer an appeal."""
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "reason": row.reason,
            "ends_at": row.ends_at.isoformat() if row.ends_at else None,
            "appeal_status": row.appeal_status,
        }
        for row in await sanctions.active(session, user.id)
    ]


@router.post("/me/sanctions/{sanction_id}/appeal")
async def appeal(
    sanction_id: int,
    body: AppealIn,
    user: AuthUser = Depends(get_auth_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    updated = await session.scalar(
        text(
            "UPDATE user_sanctions SET appeal_text = :text, appeal_status = 'pending', "
            "appeal_at = NOW() WHERE id = :id AND user_id = :user AND appeal_status = 'none' "
            "AND lifted_at IS NULL RETURNING id"
        ),
        {"text": body.message.strip(), "id": sanction_id, "user": user.id},
    )
    if updated is None:
        raise ApiError(409, "appeal_not_allowed", "This decision cannot be appealed again")
    await session.commit()
    return {"status": "pending"}


class CorrectionIn(BaseModel):
    details: str = Field(min_length=10, max_length=2000)


@router.post("/me/privacy-request", status_code=201)
async def request_correction(
    body: CorrectionIn,
    profile: Profile = Depends(current_profile),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    contact = await session.scalar(
        text("SELECT COALESCE(email, phone) FROM profiles WHERE id = :id"), {"id": profile.id}
    )
    await session.execute(
        text(
            "INSERT INTO privacy_requests (user_id, contact, kind, details) "
            "VALUES (:user, :contact, 'correct', :details)"
        ),
        {"user": profile.id, "contact": contact or "", "details": body.details.strip()},
    )
    await session.commit()
    return {"status": "received"}
