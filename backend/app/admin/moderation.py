"""Moderation queue, incidents, notes, updates, merging and sensitive lookups."""

from typing import Any
from uuid import UUID

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.auth import Admin, AdminForbidden
from app.admin.common import back, message, page, record, router
from app.core.clients import get_queue
from app.core.db import get_session
from app.core.errors import ApiError
from app.services import incidents as incident_service

ACTIONS = {"restore", "mark_false", "extend", "confirm", "resolve"}
BULK_ACTIONS = {"restore", "mark_false", "resolve"}
SORTS = {
    "wait": "i.created_at",
    "severity": "array_position(ARRAY['critical','high','medium','low'], i.severity), i.created_at",
    "flags": "i.flag_weight DESC, i.created_at",
}
DUPLICATE_RADIUS_M = 1000
DUPLICATE_HOURS = 6


def _band(score: int) -> str:
    return "Low" if score < 0 else "High" if score >= 20 else "Normal"


async def _filter_options(session: AsyncSession) -> dict[str, Any]:
    return {
        "lgas": (await session.execute(text("SELECT id, name FROM lgas ORDER BY name"))).all(),
        "types": (
            await session.execute(text("SELECT code, label FROM incident_types ORDER BY label"))
        ).all(),
    }


def _incident_filters(lga: str, type_code: str, severity: str) -> tuple[str, dict[str, Any]]:
    clauses, params = [], {}
    if lga.isdigit():
        clauses.append("i.lga_id = :lga")
        params["lga"] = int(lga)
    if type_code:
        clauses.append("i.type_code = :type_code")
        params["type_code"] = type_code
    if severity in ("low", "medium", "high", "critical"):
        clauses.append("i.severity = :severity")
        params["severity"] = severity
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


@router.get("/queue", response_class=HTMLResponse)
async def queue(
    request: Request,
    lga: str = "",
    type: str = "",  # noqa: A002
    severity: str = "",
    flags: str = "",
    sort: str = "wait",
    admin: Admin = Depends(auth.can("view_queue")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    extra, params = _incident_filters(lga, type, severity)
    if flags.isdigit():
        extra += " AND i.flag_weight >= :flags"
        params["flags"] = int(flags)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT i.id, t.label, i.description, i.location_name, i.flag_weight, i.created_at,
                       i.severity, i.hold_reason, l.name AS lga, w.name AS ward,
                       p.reputation_score, c.display_name AS claimed_name, i.claimed_by,
                       (SELECT count(*) FROM incidents m WHERE m.merged_into = i.id) AS merged
                FROM incidents i
                JOIN incident_types t ON t.code = i.type_code
                LEFT JOIN lgas l ON l.id = i.lga_id
                LEFT JOIN wards w ON w.id = i.ward_id
                LEFT JOIN incident_reporters r ON r.incident_id = i.id
                LEFT JOIN profiles p ON p.id = r.reporter_id
                LEFT JOIN profiles c ON c.id = i.claimed_by
                WHERE i.status = 'under_review' AND i.merged_into IS NULL
                  AND {auth.scope_sql()} {extra}
                ORDER BY {SORTS.get(sort, SORTS['wait'])}
                LIMIT 200
                """
            ),
            {**params, **admin.scope.params()},
        )
    ).all()
    photos = (
        (
            await session.execute(
                text(
                    f"""
                SELECT m.id, m.incident_id, m.created_at, t.label FROM incident_media m
                JOIN incidents i ON i.id = m.incident_id
                JOIN incident_types t ON t.code = i.type_code
                WHERE m.moderation_status = 'pending' AND {auth.scope_sql()}
                ORDER BY m.created_at LIMIT 50
                """
                ),
                admin.scope.params(),
            )
        ).all()
        if admin.can("moderate_media")
        else []
    )
    items = [{**row._mapping, "band": _band(row.reputation_score or 0)} for row in rows]
    word_holds = [item for item in items if (item["hold_reason"] or "").startswith("word filter")]
    held_followups = (
        await session.execute(
            text(
                f"""
                SELECT u.id, u.body, u.hold_reason, u.created_at, u.incident_id, t.label
                FROM incident_updates u JOIN incidents i ON i.id = u.incident_id
                JOIN incident_types t ON t.code = i.type_code
                WHERE u.kind = 'community' AND u.is_hidden AND u.hold_reason IS NOT NULL
                  AND {auth.scope_sql()}
                ORDER BY u.created_at LIMIT 50
                """
            ),
            admin.scope.params(),
        )
    ).all()
    return page(
        request,
        admin,
        "queue.html",
        items=items,
        photos=photos,
        word_holds=word_holds,
        held_followups=held_followups,
        filters={"lga": lga, "type": type, "severity": severity, "flags": flags, "sort": sort},
        **await _filter_options(session),
    )


@router.get("/incidents", response_class=HTMLResponse)
async def incidents_list(
    request: Request,
    lga: str = "",
    ward: str = "",
    type: str = "",  # noqa: A002
    severity: str = "",
    status: str = "",
    admin: Admin = Depends(auth.can("view_incidents")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    extra, params = _incident_filters(lga, type, severity)
    if ward.isdigit():
        extra += " AND i.ward_id = :ward"
        params["ward"] = int(ward)
    if status:
        extra += " AND i.status = :status"
        params["status"] = status
    rows = (
        await session.execute(
            text(
                f"""
                SELECT i.id, t.label, i.severity, i.status, i.location_name, i.created_at,
                       i.corroboration_count, i.flag_weight, l.name AS lga, w.name AS ward
                FROM incidents i JOIN incident_types t ON t.code = i.type_code
                LEFT JOIN lgas l ON l.id = i.lga_id LEFT JOIN wards w ON w.id = i.ward_id
                WHERE i.merged_into IS NULL AND {auth.scope_sql()} {extra}
                ORDER BY i.created_at DESC LIMIT 200
                """
            ),
            {**params, **admin.scope.params()},
        )
    ).all()
    return page(
        request,
        admin,
        "incidents.html",
        rows=rows,
        filters={"lga": lga, "type": type, "severity": severity, "status": status},
        **await _filter_options(session),
    )


async def _incident(session: AsyncSession, admin: Admin, incident_id: UUID) -> Any:
    """The incident if this staff member's areas include it."""
    return (
        await session.execute(
            text(
                f"""
                SELECT i.id, i.status, i.severity, i.description, i.location_name, i.flag_weight,
                       i.created_at, i.occurred_at, i.expires_at, i.hidden_reason, i.hold_reason,
                       i.is_hidden, i.corroboration_count, i.confirmed_by_label, i.type_code,
                       i.claimed_by, i.merged_into, t.label, l.name AS lga, w.name AS ward,
                       c.display_name AS claimed_name,
                       ST_Y(i.public_location::geometry) AS public_lat,
                       ST_X(i.public_location::geometry) AS public_lng
                FROM incidents i JOIN incident_types t ON t.code = i.type_code
                LEFT JOIN lgas l ON l.id = i.lga_id LEFT JOIN wards w ON w.id = i.ward_id
                LEFT JOIN profiles c ON c.id = i.claimed_by
                WHERE i.id = :id AND {auth.scope_sql()}
                """
            ),
            {"id": incident_id, **admin.scope.params()},
        )
    ).first()


async def _require_incident(session: AsyncSession, admin: Admin, incident_id: UUID) -> Any:
    incident = await _incident(session, admin, incident_id)
    if incident is None:
        raise AdminForbidden
    return incident


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
async def incident_detail(
    request: Request,
    incident_id: UUID,
    admin: Admin = Depends(auth.can("view_incidents")),
    session: AsyncSession = Depends(get_session),
) -> Any:
    incident = await _incident(session, admin, incident_id)
    if incident is None:
        return back("/admin/incidents")
    params = {"id": incident_id}
    flags = (
        await session.execute(
            text(
                "SELECT reason, notes, weight, created_at FROM incident_flags "
                "WHERE incident_id = :id ORDER BY created_at"
            ),
            params,
        )
    ).all()
    events = (
        await session.execute(
            text(
                "SELECT e.event_type, e.note, e.created_at, p.display_name AS actor "
                "FROM incident_events e LEFT JOIN profiles p ON p.id = e.actor_id "
                "WHERE e.incident_id = :id ORDER BY e.created_at DESC"
            ),
            params,
        )
    ).all()
    media = (
        await session.execute(
            text(
                "SELECT id, kind, moderation_status, thumb_path FROM incident_media "
                "WHERE incident_id = :id ORDER BY created_at"
            ),
            params,
        )
    ).all()
    notes = (
        await session.execute(
            text(
                "SELECT n.body, n.created_at, p.display_name AS author FROM incident_notes n "
                "LEFT JOIN profiles p ON p.id = n.author_id WHERE n.incident_id = :id "
                "ORDER BY n.created_at"
            ),
            params,
        )
    ).all()
    updates = (
        await session.execute(
            text(
                "SELECT id, kind, author_label, body, created_at, is_hidden, hold_reason "
                "FROM incident_updates "
                "WHERE incident_id = :id ORDER BY created_at DESC"
            ),
            params,
        )
    ).all()
    duplicates = (
        await session.execute(
            text(
                f"""
                SELECT d.id, d.status, d.severity, d.created_at, d.corroboration_count,
                       round(ST_Distance(d.location, i.location)) AS metres
                FROM incidents i JOIN incidents d ON d.type_code = i.type_code AND d.id <> i.id
                WHERE i.id = :id AND d.merged_into IS NULL AND i.merged_into IS NULL
                  AND d.status NOT IN ('false_report','expired')
                  AND ST_DWithin(d.location, i.location, :radius)
                  AND abs(EXTRACT(EPOCH FROM d.created_at - i.created_at)) < :hours * 3600
                  AND {auth.scope_sql('d')}
                ORDER BY metres LIMIT 5
                """
            ),
            {
                **params,
                "radius": DUPLICATE_RADIUS_M,
                "hours": DUPLICATE_HOURS,
                **admin.scope.params(),
            },
        )
    ).all()
    merged = (
        await session.execute(
            text(
                "SELECT id, created_at FROM incidents WHERE merged_into = :id ORDER BY created_at"
            ),
            params,
        )
    ).all()
    return page(
        request,
        admin,
        "incident.html",
        incident=incident,
        flags=flags,
        events=events,
        media=media,
        notes=notes,
        updates=updates,
        duplicates=duplicates,
        merged=merged,
    )


async def _act(
    session: AsyncSession,
    queue_client: Any,
    admin: Admin,
    incident_id: UUID,
    action: str,
    note: str | None,
) -> None:
    result = await incident_service.verify(
        session, admin.profile, incident_id, action, note, 60 if action == "extend" else None
    )
    if result.enqueue_trigger:
        await queue_client.enqueue_job("fan_out_alert", str(incident_id), result.enqueue_trigger)


@router.post("/incidents/{incident_id}/action")
async def incident_action(
    incident_id: UUID,
    action: str = Form(),
    note: str | None = Form(default=None),
    admin: Admin = Depends(auth.act("act_on_incidents")),
    session: AsyncSession = Depends(get_session),
    queue_client: Any = Depends(get_queue),
) -> RedirectResponse:
    if action not in ACTIONS:
        raise AdminForbidden
    await _require_incident(session, admin, incident_id)
    await _act(session, queue_client, admin, incident_id, action, note)
    return back(f"/admin/incidents/{incident_id}")


@router.post("/queue/bulk")
async def bulk_action(
    action: str = Form(),
    incident_ids: list[UUID] = Form(default=[]),
    admin: Admin = Depends(auth.act("bulk_actions")),
    session: AsyncSession = Depends(get_session),
    queue_client: Any = Depends(get_queue),
) -> RedirectResponse:
    if action not in BULK_ACTIONS or not admin.can("act_on_incidents"):
        raise AdminForbidden
    for incident_id in incident_ids[:100]:
        if await _incident(session, admin, incident_id) is None:
            continue
        try:
            await _act(session, queue_client, admin, incident_id, action, "Bulk action")
        except ApiError:
            await session.rollback()
    return back("/admin/queue")


@router.post("/incidents/{incident_id}/claim")
async def claim(
    incident_id: UUID,
    admin: Admin = Depends(auth.act("view_queue")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    incident = await _require_incident(session, admin, incident_id)
    mine = incident.claimed_by == admin.profile.id
    await session.execute(
        text(
            "UPDATE incidents SET claimed_by = :by, "
            "claimed_at = CASE WHEN CAST(:by AS UUID) IS NULL THEN NULL ELSE NOW() END "
            "WHERE id = :id"
        ),
        {"by": None if mine else admin.profile.id, "id": incident_id},
    )
    await session.commit()
    return back(f"/admin/incidents/{incident_id}")


@router.post("/incidents/{incident_id}/notes")
async def add_note(
    incident_id: UUID,
    body: str = Form(min_length=1, max_length=2000),
    admin: Admin = Depends(auth.act("add_notes")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await _require_incident(session, admin, incident_id)
    await session.execute(
        text(
            "INSERT INTO incident_notes (incident_id, author_id, body) VALUES (:id, :author, :body)"
        ),
        {"id": incident_id, "author": admin.profile.id, "body": body.strip()},
    )
    await session.commit()
    return back(f"/admin/incidents/{incident_id}#notes")


@router.post("/incidents/{incident_id}/updates")
async def post_update(
    incident_id: UUID,
    body: str = Form(min_length=2, max_length=500),
    admin: Admin = Depends(auth.act("add_notes")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """A public update people see on the incident in the app."""
    await _require_incident(session, admin, incident_id)
    await session.execute(
        text(
            "INSERT INTO incident_updates (incident_id, author_id, author_label, body) "
            "VALUES (:id, :author, 'Dey Alert team', :body)"
        ),
        {"id": incident_id, "author": admin.profile.id, "body": body.strip()},
    )
    await session.execute(
        text("UPDATE incidents SET last_activity_at = NOW() WHERE id = :id"), {"id": incident_id}
    )
    await session.commit()
    return back(f"/admin/incidents/{incident_id}")


@router.post("/updates/{update_id}/hide")
async def hide_update(
    request: Request,
    update_id: int,
    admin: Admin = Depends(auth.act("act_on_incidents")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    incident_id = await session.scalar(
        text(
            "UPDATE incident_updates SET is_hidden = NOT is_hidden, "
            "hold_reason = CASE WHEN is_hidden THEN NULL ELSE hold_reason END "
            "WHERE id = :id "
            "RETURNING incident_id"
        ),
        {"id": update_id},
    )
    if incident_id is None:
        raise AdminForbidden
    await record(session, admin, request, "update_visibility", "incident", str(incident_id))
    await session.commit()
    return back(f"/admin/incidents/{incident_id}")


@router.post("/incidents/{incident_id}/merge")
async def merge(
    request: Request,
    incident_id: UUID,
    duplicate_id: UUID = Form(),
    admin: Admin = Depends(auth.act("merge_incidents")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Folds the duplicate into this incident: it is hidden and counts as a corroboration."""
    target = await _require_incident(session, admin, incident_id)
    duplicate = await _require_incident(session, admin, duplicate_id)
    if target.merged_into or duplicate.merged_into or target.id == duplicate.id:
        raise AdminForbidden
    await session.execute(
        text(
            "UPDATE incidents SET merged_into = :target, is_hidden = true, "
            "hidden_reason = 'merged into another report', last_activity_at = NOW() "
            "WHERE id = :duplicate OR merged_into = :duplicate"
        ),
        {"target": incident_id, "duplicate": duplicate_id},
    )
    await session.execute(
        text(
            "UPDATE incidents SET corroboration_count = corroboration_count + :extra, "
            "last_activity_at = NOW() WHERE id = :target"
        ),
        {"extra": duplicate.corroboration_count + 1, "target": incident_id},
    )
    for subject, note in (
        (incident_id, f"Merged report {str(duplicate_id)[:8]}"),
        (duplicate_id, f"Merged into {str(incident_id)[:8]}"),
    ):
        await session.execute(
            text(
                "INSERT INTO incident_events (incident_id, actor_id, event_type, note) "
                "VALUES (:id, :actor, 'merged', :note)"
            ),
            {"id": subject, "actor": admin.profile.id, "note": note},
        )
    await record(
        session,
        admin,
        request,
        "incident_merge",
        "incident",
        str(incident_id),
        details={"merged": str(duplicate_id)},
    )
    await session.commit()
    return back(f"/admin/incidents/{incident_id}")


@router.post("/media/{media_id}")
async def moderate_media(
    request: Request,
    media_id: UUID,
    decision: str = Form(),
    admin: Admin = Depends(auth.act("moderate_media")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if decision not in ("approved", "blurred", "rejected"):
        raise AdminForbidden
    incident_id = await session.scalar(
        text("SELECT incident_id FROM incident_media WHERE id = :id"), {"id": media_id}
    )
    if incident_id is None:
        raise AdminForbidden
    await _require_incident(session, admin, incident_id)
    await session.execute(
        text("UPDATE incident_media SET moderation_status = :decision WHERE id = :id"),
        {"decision": decision, "id": media_id},
    )
    await record(session, admin, request, f"media:{decision}", "media", str(media_id))
    await session.commit()
    return back(request.headers.get("referer") or f"/admin/incidents/{incident_id}")


@router.post("/incidents/{incident_id}/reveal-location", response_class=HTMLResponse)
async def reveal_location(
    request: Request,
    incident_id: UUID,
    reason: str = Form(min_length=10),
    admin: Admin = Depends(auth.act("reveal_location")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    await _require_incident(session, admin, incident_id)
    row = (
        await session.execute(
            text(
                "SELECT ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng "
                "FROM incidents WHERE id = :id"
            ),
            {"id": incident_id},
        )
    ).one()
    await record(session, admin, request, "location_reveal", "incident", str(incident_id), reason)
    await session.commit()
    return message(
        request, admin, "Exact location", f"{row.lat}, {row.lng}", f"/admin/incidents/{incident_id}"
    )


@router.post("/incidents/{incident_id}/reporter", response_class=HTMLResponse)
async def reporter_lookup(
    request: Request,
    incident_id: UUID,
    reason: str = Form(min_length=20),
    admin: Admin = Depends(auth.act("lookup_reporter")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    await _require_incident(session, admin, incident_id)
    row = (
        await session.execute(
            text(
                "SELECT p.id, COALESCE(p.phone, p.email) AS contact, p.display_name "
                "FROM incident_reporters r "
                "JOIN profiles p ON p.id = r.reporter_id WHERE r.incident_id = :id"
            ),
            {"id": incident_id},
        )
    ).first()
    await record(session, admin, request, "reporter_lookup", "incident", str(incident_id), reason)
    await session.commit()
    value = f"{row.display_name or 'No name'} ({row.contact})" if row else "Account deleted"
    return message(request, admin, "Reporter", value, f"/admin/incidents/{incident_id}")
