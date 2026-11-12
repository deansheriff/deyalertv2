"""Users, sanctions, reputation, verifiers and applications, support and privacy requests."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.auth import Admin, AdminForbidden
from app.admin.common import back, mask_contact, message, page, record, router
from app.api.me import export_payload
from app.core.db import get_session

VIEW_MINUTES = 30
SANCTION_DAYS = {"1": 1, "3": 3, "7": 7, "30": 30, "0": None}


# Users


@router.get("/users", response_class=HTMLResponse)
async def users(
    request: Request,
    q: str = "",
    role: str = "",
    admin: Admin = Depends(auth.can("view_users")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT p.id, p.phone, p.email, p.display_name, p.role, p.is_active,
                       p.reputation_score, p.created_at, w.name AS ward,
                       EXISTS (SELECT 1 FROM user_sanctions s WHERE s.user_id = p.id
                               AND s.lifted_at IS NULL AND (s.ends_at IS NULL OR s.ends_at > NOW()))
                         AS sanctioned
                FROM profiles p LEFT JOIN wards w ON w.id = p.home_ward_id
                WHERE (:q = '' OR p.phone LIKE '%' || :q || '%'
                       OR p.email ILIKE '%' || :q || '%' OR p.display_name ILIKE '%' || :q || '%')
                  AND (:role = '' OR p.role = :role)
                ORDER BY p.created_at DESC LIMIT 100
                """
            ),
            {"q": q.strip(), "role": role},
        )
    ).all()
    items = [{**row._mapping, "contact": mask_contact(row.phone, row.email)} for row in rows]
    return page(request, admin, "users.html", items=items, q=q, role=role)


def _viewed_key(admin: Admin, user_id: UUID) -> str:
    return f"user_view:{admin.profile.id}:{user_id}"


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(
    request: Request,
    user_id: UUID,
    tab: str = "reports",
    admin: Admin = Depends(auth.can("view_users")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    person = (
        await session.execute(
            text(
                """
                SELECT p.id, p.display_name, p.phone, p.email, p.role, p.is_active,
                       p.reputation_score, p.created_at, w.name AS ward, l.name AS lga
                FROM profiles p LEFT JOIN wards w ON w.id = p.home_ward_id
                LEFT JOIN lgas l ON l.id = p.home_lga_id WHERE p.id = :id
                """
            ),
            {"id": user_id},
        )
    ).first()
    if person is None:
        return message(request, admin, "Not found", "No account has this ID.", "/admin/users")
    # Opening a profile needs a recent reason in the audit log.
    reason = await session.scalar(
        text(
            "SELECT reason FROM audit_log WHERE actor_id = :actor AND action = 'user_view' "
            "AND entity_id = :id AND created_at > NOW() - make_interval(mins => :minutes) "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"actor": admin.profile.id, "id": str(user_id), "minutes": VIEW_MINUTES},
    )
    if reason is None:
        return page(
            request,
            admin,
            "user_gate.html",
            person=person,
            contact=mask_contact(person.phone, person.email),
        )
    params = {"id": user_id}
    data: dict[str, Any] = {}
    data["reports"] = (
        await session.execute(
            text(
                "SELECT i.id, t.label, i.severity, i.status, i.created_at "
                "FROM incident_reporters r JOIN incidents i ON i.id = r.incident_id "
                "JOIN incident_types t ON t.code = i.type_code "
                "WHERE r.reporter_id = :id ORDER BY i.created_at DESC LIMIT 50"
            ),
            params,
        )
    ).all()
    data["flags"] = (
        await session.execute(
            text(
                "SELECT f.reason, f.outcome, f.created_at, f.incident_id FROM incident_flags f "
                "WHERE f.flagged_by = :id ORDER BY f.created_at DESC LIMIT 50"
            ),
            params,
        )
    ).all()
    data["devices"] = (
        await session.execute(
            text(
                "SELECT platform, app_version, is_active, last_seen_at FROM devices "
                "WHERE user_id = :id ORDER BY last_seen_at DESC"
            ),
            params,
        )
    ).all()
    data["sos"] = (
        await session.execute(
            text(
                "SELECT status, triggered_at AS created_at, resolved_at FROM sos_alerts "
                "WHERE user_id = :id "
                "ORDER BY triggered_at DESC LIMIT 20"
            ),
            params,
        )
    ).all()
    sanctions = (
        await session.execute(
            text(
                "SELECT s.*, c.display_name AS by_name FROM user_sanctions s "
                "LEFT JOIN profiles c ON c.id = s.created_by WHERE s.user_id = :id "
                "ORDER BY s.created_at DESC"
            ),
            params,
        )
    ).all()
    reputation = (
        await session.execute(
            text(
                "SELECT r.change, r.reason, r.created_at, a.display_name AS actor "
                "FROM reputation_changes r LEFT JOIN profiles a ON a.id = r.actor_id "
                "WHERE r.user_id = :id ORDER BY r.created_at DESC LIMIT 20"
            ),
            params,
        )
    ).all()
    now = datetime.now(UTC)
    return page(
        request,
        admin,
        "user.html",
        person=person,
        tab=tab if tab in data else "reports",
        data=data,
        sanctions=sanctions,
        active=[
            s for s in sanctions if s.lifted_at is None and (s.ends_at is None or s.ends_at > now)
        ],
        reputation=reputation,
        reason=reason,
    )


@router.post("/users/{user_id}/view")
async def open_user(
    request: Request,
    user_id: UUID,
    reason: str = Form(min_length=10, max_length=500),
    admin: Admin = Depends(auth.act("view_users")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await record(session, admin, request, "user_view", "profile", str(user_id), reason.strip())
    await session.commit()
    return back(f"/admin/users/{user_id}")


@router.post("/users/{user_id}/sanction")
async def add_sanction(
    request: Request,
    user_id: UUID,
    kind: str = Form(),
    days: str = Form(),
    reason: str = Form(min_length=10, max_length=500),
    admin: Admin = Depends(auth.act("sanction_users")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if kind not in ("suspend", "pause_reporting") or days not in SANCTION_DAYS:
        raise AdminForbidden
    if user_id == admin.profile.id:
        raise AdminForbidden
    role = await session.scalar(text("SELECT role FROM profiles WHERE id = :id"), {"id": user_id})
    if role in ("super_admin", "admin") and not admin.is_super:
        raise AdminForbidden
    length = SANCTION_DAYS[days]
    ends = datetime.now(UTC) + timedelta(days=length) if length else None
    await session.execute(
        text(
            "INSERT INTO user_sanctions (user_id, kind, reason, ends_at, created_by) "
            "VALUES (:user, :kind, :reason, :ends, :by)"
        ),
        {
            "user": user_id,
            "kind": kind,
            "reason": reason.strip(),
            "ends": ends,
            "by": admin.profile.id,
        },
    )
    if kind == "suspend":
        # Suspended staff lose panel access at once.
        await session.execute(
            text("UPDATE profiles SET session_version = session_version + 1 WHERE id = :id"),
            {"id": user_id},
        )
    await record(
        session,
        admin,
        request,
        "sanction_add",
        "profile",
        str(user_id),
        reason.strip(),
        details={"kind": kind, "days": length},
    )
    await session.commit()
    return back(f"/admin/users/{user_id}?tab=reports")


@router.post("/sanctions/{sanction_id}/lift")
async def lift_sanction(
    request: Request,
    sanction_id: int,
    admin: Admin = Depends(auth.act("sanction_users")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    user_id = await session.scalar(
        text(
            "UPDATE user_sanctions SET lifted_at = NOW(), lifted_by = :by WHERE id = :id "
            "AND lifted_at IS NULL RETURNING user_id"
        ),
        {"by": admin.profile.id, "id": sanction_id},
    )
    if user_id is None:
        raise AdminForbidden
    await record(session, admin, request, "sanction_lift", "profile", str(user_id))
    await session.commit()
    return back(f"/admin/users/{user_id}")


@router.post("/sanctions/{sanction_id}/appeal")
async def decide_appeal(
    request: Request,
    sanction_id: int,
    decision: str = Form(),
    admin: Admin = Depends(auth.act("sanction_users")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if decision not in ("accepted", "rejected"):
        raise AdminForbidden
    user_id = await session.scalar(
        text(
            "UPDATE user_sanctions SET appeal_status = :decision, appeal_decided_by = :by, "
            "lifted_at = CASE WHEN :decision = 'accepted' THEN NOW() ELSE lifted_at END, "
            "lifted_by = CASE WHEN :decision = 'accepted' THEN :by ELSE lifted_by END "
            "WHERE id = :id AND appeal_status = 'pending' RETURNING user_id"
        ),
        {"decision": decision, "by": admin.profile.id, "id": sanction_id},
    )
    if user_id is None:
        raise AdminForbidden
    await record(
        session,
        admin,
        request,
        "appeal_decide",
        "profile",
        str(user_id),
        details={"decision": decision},
    )
    await session.commit()
    return back(f"/admin/users/{user_id}")


@router.post("/users/{user_id}/reputation")
async def adjust_reputation(
    request: Request,
    user_id: UUID,
    change: int = Form(ge=-50, le=50),
    reason: str = Form(min_length=5, max_length=300),
    admin: Admin = Depends(auth.act("adjust_reputation")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if change == 0:
        return back(f"/admin/users/{user_id}")
    await session.execute(
        text("UPDATE profiles SET reputation_score = reputation_score + :change WHERE id = :id"),
        {"change": change, "id": user_id},
    )
    await session.execute(
        text(
            "INSERT INTO reputation_changes (user_id, change, reason, actor_id) "
            "VALUES (:id, :change, :reason, :actor)"
        ),
        {"id": user_id, "change": change, "reason": reason.strip(), "actor": admin.profile.id},
    )
    await record(
        session,
        admin,
        request,
        "reputation_adjust",
        "profile",
        str(user_id),
        reason.strip(),
        details={"change": change},
    )
    await session.commit()
    return back(f"/admin/users/{user_id}")


# Verifiers and applications


@router.get("/verifiers", response_class=HTMLResponse)
async def verifiers(
    request: Request,
    admin: Admin = Depends(auth.can("manage_verifiers")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT v.id, v.title, v.is_active, p.display_name, p.phone, p.email,
                       l.name AS lga, w.name AS ward, a.display_name AS approved_by,
                       (SELECT count(*) FROM incident_events e WHERE e.actor_id = v.user_id
                          AND e.event_type IN ('confirmed','resolved','marked_false')) AS decisions,
                       (SELECT count(*) FROM incident_updates u
                          WHERE u.author_id = v.user_id) AS updates
                FROM verifiers v JOIN profiles p ON p.id = v.user_id
                JOIN lgas l ON l.id = v.lga_id
                LEFT JOIN wards w ON w.id = v.ward_id
                LEFT JOIN profiles a ON a.id = v.approved_by
                ORDER BY v.created_at DESC
                """
            )
        )
    ).all()
    applications = (
        await session.execute(
            text(
                """
                SELECT a.id, a.full_name, a.community_role, a.reason, a.created_at, a.user_id,
                       l.name AS lga, w.name AS ward, p.reputation_score, p.created_at AS joined
                FROM verifier_applications a JOIN lgas l ON l.id = a.lga_id
                LEFT JOIN wards w ON w.id = a.ward_id JOIN profiles p ON p.id = a.user_id
                WHERE a.status = 'pending' ORDER BY a.created_at
                """
            )
        )
    ).all()
    lgas = (await session.execute(text("SELECT id, state, name FROM lgas ORDER BY name"))).all()
    return page(
        request,
        admin,
        "verifiers.html",
        rows=rows,
        applications=applications,
        lgas=lgas,
        contact=mask_contact,
    )


@router.post("/verifiers", response_model=None)
async def add_verifier(
    request: Request,
    contact: str = Form(),
    title: str = Form(min_length=2),
    lga_id: int = Form(),
    ward_name: str = Form(default=""),
    admin: Admin = Depends(auth.act("manage_verifiers")),
    session: AsyncSession = Depends(get_session),
) -> Any:
    value = contact.strip()
    user_id = await session.scalar(
        text("SELECT id FROM profiles WHERE phone = :value OR lower(email) = lower(:value)"),
        {"value": value},
    )
    ward_id = None
    if ward_name.strip():
        ward_id = await session.scalar(
            text("SELECT id FROM wards WHERE lga_id = :lga AND name = :name"),
            {"lga": lga_id, "name": ward_name.strip()},
        )
    if user_id is None or (ward_name.strip() and ward_id is None):
        return message(
            request,
            admin,
            "Could not add verifier",
            "No account uses that phone number or email, or that ward is not in this LGA.",
            "/admin/verifiers",
        )
    await _make_verifier(session, admin, user_id, lga_id, ward_id, title.strip())
    await record(session, admin, request, "verifier_add", "profile", str(user_id))
    await session.commit()
    return back("/admin/verifiers")


async def _make_verifier(
    session: AsyncSession, admin: Admin, user_id: UUID, lga_id: int, ward_id: int | None, title: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO verifiers (user_id, lga_id, ward_id, title, approved_by) "
            "VALUES (:user, :lga, :ward, :title, :admin) ON CONFLICT DO NOTHING"
        ),
        {
            "user": user_id,
            "lga": lga_id,
            "ward": ward_id,
            "title": title,
            "admin": admin.profile.id,
        },
    )
    await session.execute(
        text("UPDATE profiles SET role = 'verifier' WHERE id = :id AND role = 'member'"),
        {"id": user_id},
    )


@router.post("/applications/{application_id}")
async def decide_application(
    request: Request,
    application_id: int,
    decision: str = Form(),
    title: str = Form(default=""),
    admin: Admin = Depends(auth.act("manage_verifiers")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if decision not in ("approved", "rejected"):
        raise AdminForbidden
    row = (
        await session.execute(
            text(
                "UPDATE verifier_applications SET status = :decision, reviewed_by = :by, "
                "reviewed_at = NOW() WHERE id = :id AND status = 'pending' "
                "RETURNING user_id, lga_id, ward_id, community_role"
            ),
            {"decision": decision, "by": admin.profile.id, "id": application_id},
        )
    ).first()
    if row is None:
        raise AdminForbidden
    if decision == "approved":
        await _make_verifier(
            session,
            admin,
            row.user_id,
            row.lga_id,
            row.ward_id,
            title.strip() or row.community_role,
        )
    await record(
        session, admin, request, f"verifier_application:{decision}", "profile", str(row.user_id)
    )
    await session.commit()
    return back("/admin/verifiers")


@router.post("/verifiers/{verifier_id}/toggle")
async def toggle_verifier(
    request: Request,
    verifier_id: UUID,
    admin: Admin = Depends(auth.act("manage_verifiers")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        text("UPDATE verifiers SET is_active = NOT is_active WHERE id = :id"), {"id": verifier_id}
    )
    await record(session, admin, request, "verifier_toggle", "verifier", str(verifier_id))
    await session.commit()
    return back("/admin/verifiers")


# Support inbox


@router.get("/support", response_class=HTMLResponse)
async def support(
    request: Request,
    status: str = "open",
    ticket: int | None = None,
    admin: Admin = Depends(auth.can("support_inbox")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    status = status if status in ("open", "waiting", "closed") else "open"
    tickets = (
        await session.execute(
            text(
                """
                SELECT t.id, t.category, t.status, t.updated_at, p.email, p.phone, p.display_name,
                       (SELECT body FROM support_messages m WHERE m.ticket_id = t.id
                         ORDER BY m.created_at LIMIT 1) AS first_line,
                       (SELECT NOT from_staff FROM support_messages m WHERE m.ticket_id = t.id
                         AND NOT m.is_internal ORDER BY m.created_at DESC LIMIT 1) AS unread
                FROM support_tickets t JOIN profiles p ON p.id = t.user_id
                WHERE t.status = :status ORDER BY t.updated_at DESC LIMIT 100
                """
            ),
            {"status": status},
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                text("SELECT status, count(*) FROM support_tickets GROUP BY status")
            )
        ).all()
    )
    selected = ticket or (tickets[0].id if tickets else None)
    current, messages = None, []
    if selected:
        current = (
            await session.execute(
                text(
                    "SELECT t.*, p.email, p.phone, p.display_name FROM support_tickets t "
                    "JOIN profiles p ON p.id = t.user_id WHERE t.id = :id"
                ),
                {"id": selected},
            )
        ).first()
        messages = (
            await session.execute(
                text(
                    "SELECT m.*, a.display_name AS author FROM support_messages m "
                    "LEFT JOIN profiles a ON a.id = m.author_id WHERE m.ticket_id = :id "
                    "ORDER BY m.created_at"
                ),
                {"id": selected},
            )
        ).all()
    return page(
        request,
        admin,
        "support.html",
        tickets=tickets,
        counts=counts,
        status=status,
        current=current,
        messages=messages,
        contact=mask_contact,
    )


@router.post("/support/{ticket_id}/reply")
async def support_reply(
    request: Request,
    ticket_id: int,
    body: str = Form(min_length=1, max_length=4000),
    internal: bool = Form(default=False),
    close: bool = Form(default=False),
    admin: Admin = Depends(auth.act("support_inbox")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        text(
            "INSERT INTO support_messages (ticket_id, author_id, from_staff, is_internal, body) "
            "VALUES (:ticket, :author, true, :internal, :body)"
        ),
        {
            "ticket": ticket_id,
            "author": admin.profile.id,
            "internal": internal,
            "body": body.strip(),
        },
    )
    new_status = "closed" if close else ("open" if internal else "waiting")
    status = await session.scalar(
        text(
            "UPDATE support_tickets SET status = :status, updated_at = NOW(), "
            "assigned_to = COALESCE(assigned_to, :by) WHERE id = :id RETURNING status"
        ),
        {"status": new_status, "by": admin.profile.id, "id": ticket_id},
    )
    if status is None:
        raise AdminForbidden
    await session.commit()
    return back(f"/admin/support?status={status}&ticket={ticket_id}")


@router.post("/support/{ticket_id}/status")
async def support_status(
    ticket_id: int,
    status: str = Form(),
    admin: Admin = Depends(auth.act("support_inbox")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if status not in ("open", "waiting", "closed"):
        raise AdminForbidden
    await session.execute(
        text("UPDATE support_tickets SET status = :status, updated_at = NOW() WHERE id = :id"),
        {"status": status, "id": ticket_id},
    )
    await session.commit()
    return back(f"/admin/support?status={status}&ticket={ticket_id}")


# Privacy requests (NDPA)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(
    request: Request,
    admin: Admin = Depends(auth.can("privacy_requests")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT r.*, h.display_name AS handler,
                       (r.due_at - NOW()) AS remaining
                FROM privacy_requests r LEFT JOIN profiles h ON h.id = r.handled_by
                ORDER BY (r.status IN ('done','rejected')), r.due_at LIMIT 200
                """
            )
        )
    ).all()
    return page(request, admin, "privacy.html", rows=rows)


@router.post("/privacy", response_model=None)
async def log_privacy_request(
    request: Request,
    contact: str = Form(min_length=3),
    kind: str = Form(),
    details: str = Form(default=""),
    admin: Admin = Depends(auth.act("privacy_requests")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Logs a request that came in by email, letter or phone."""
    if kind not in ("export", "delete", "correct"):
        raise AdminForbidden
    user_id = await session.scalar(
        text("SELECT id FROM profiles WHERE lower(email) = lower(:c) OR phone = :c"),
        {"c": contact.strip()},
    )
    await session.execute(
        text(
            "INSERT INTO privacy_requests (user_id, contact, kind, details) "
            "VALUES (:user, :contact, :kind, :details)"
        ),
        {
            "user": user_id,
            "contact": contact.strip(),
            "kind": kind,
            "details": details.strip() or None,
        },
    )
    await record(
        session, admin, request, "privacy_request_log", "privacy", contact.strip()[:3] + "…"
    )
    await session.commit()
    return back("/admin/privacy")


@router.post("/privacy/{request_id}/status")
async def privacy_status(
    request: Request,
    request_id: int,
    status: str = Form(),
    notes: str = Form(default=""),
    admin: Admin = Depends(auth.act("privacy_requests")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if status not in ("in_progress", "done", "rejected"):
        raise AdminForbidden
    await session.execute(
        text(
            "UPDATE privacy_requests SET status = :status, handled_by = :by, "
            "notes = COALESCE(NULLIF(:notes, ''), notes), "
            "done_at = CASE WHEN :status IN ('done','rejected') THEN NOW() END WHERE id = :id"
        ),
        {"status": status, "by": admin.profile.id, "notes": notes.strip(), "id": request_id},
    )
    await record(session, admin, request, f"privacy_request:{status}", "privacy", str(request_id))
    await session.commit()
    return back("/admin/privacy")


@router.get("/privacy/{request_id}/export")
async def privacy_export(
    request: Request,
    request_id: int,
    admin: Admin = Depends(auth.can("privacy_requests")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Everything held about the requester, as JSON to send them."""
    user_id = await session.scalar(
        text("SELECT user_id FROM privacy_requests WHERE id = :id"), {"id": request_id}
    )
    if user_id is None:
        raise AdminForbidden
    payload = await export_payload(session, user_id)
    await record(session, admin, request, "privacy_export", "privacy", str(request_id))
    await session.commit()
    return Response(
        json.dumps(payload, default=str, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="dey-alert-data-{request_id}.json"'},
    )
