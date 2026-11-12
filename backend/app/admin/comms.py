"""Broadcasts with two-person approval, announcements and message templates."""

from typing import Any
from uuid import UUID

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.auth import Admin, AdminForbidden
from app.admin.common import SEVERITIES, back, message, page, record, router
from app.core.clients import get_queue
from app.core.db import get_session
from app.services import app_settings, broadcasts

TABS = ("pending", "sent", "rejected")


async def _lgas(session: AsyncSession) -> list[Any]:
    return (await session.execute(text("SELECT id, name FROM lgas ORDER BY name"))).all()


@router.get("/broadcasts", response_class=HTMLResponse)
async def broadcasts_page(
    request: Request,
    tab: str = "pending",
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if not (admin.can("write_broadcasts") or admin.can("approve_broadcasts")):
        raise AdminForbidden
    tab = tab if tab in TABS else "pending"
    statuses = {
        "pending": ("pending",),
        "sent": ("approved", "sending", "sent", "failed"),
        "rejected": ("rejected",),
    }[tab]
    rows = (
        await session.execute(
            text(
                """
                SELECT b.*, a.display_name AS author, d.display_name AS decider,
                       (SELECT string_agg(name, ', ') FROM lgas WHERE id = ANY(b.lga_ids)) AS area
                FROM broadcasts b LEFT JOIN profiles a ON a.id = b.created_by
                LEFT JOIN profiles d ON d.id = b.decided_by
                WHERE b.status = ANY(:statuses) ORDER BY b.created_at DESC LIMIT 50
                """
            ),
            {"statuses": list(statuses)},
        )
    ).all()
    counts = dict(
        (
            await session.execute(text("SELECT status, count(*) FROM broadcasts GROUP BY status"))
        ).all()
    )
    return page(
        request,
        admin,
        "broadcasts.html",
        rows=rows,
        tab=tab,
        lgas=await _lgas(session),
        pending=counts.get("pending", 0),
    )


@router.get("/broadcasts/estimate")
async def estimate(
    lga_ids: list[int] = [],  # noqa: B006
    admin: Admin = Depends(auth.can("write_broadcasts")),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    return JSONResponse(await broadcasts.estimate(session, lga_ids, []))


@router.post("/broadcasts", response_model=None)
async def submit_broadcast(
    request: Request,
    title: str = Form(min_length=1, max_length=80),
    body: str = Form(min_length=1, max_length=480),
    severity: str = Form(),
    lga_ids: list[int] = Form(default=[]),
    channels: list[str] = Form(default=[]),
    admin: Admin = Depends(auth.act("write_broadcasts")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | HTMLResponse:
    channels = [c for c in channels if c in ("push", "sms")]
    if severity not in SEVERITIES or not channels:
        return message(
            request,
            admin,
            "Not submitted",
            "Choose a severity and at least one channel.",
            "/admin/broadcasts",
        )
    if not admin.scope.all and (not lga_ids or not set(lga_ids) <= set(admin.scope.lga_ids)):
        # Area-limited staff may only broadcast to their own LGAs.
        raise AdminForbidden
    broadcast_id = await session.scalar(
        text(
            "INSERT INTO broadcasts (title, body, severity, lga_ids, channels, created_by) "
            "VALUES (:title, :body, :severity, :lgas, :channels, :by) RETURNING id"
        ),
        {
            "title": title.strip(),
            "body": body.strip(),
            "severity": severity,
            "lgas": lga_ids,
            "channels": channels,
            "by": admin.profile.id,
        },
    )
    await record(
        session,
        admin,
        request,
        "broadcast_submit",
        "broadcast",
        str(broadcast_id),
        details={"title": title.strip(), "lgas": lga_ids, "channels": channels},
    )
    await session.commit()
    return back("/admin/broadcasts")


@router.post("/broadcasts/{broadcast_id}/decide")
async def decide_broadcast(
    request: Request,
    broadcast_id: UUID,
    decision: str = Form(),
    reason: str = Form(default=""),
    admin: Admin = Depends(auth.act("approve_broadcasts")),
    session: AsyncSession = Depends(get_session),
    queue_client: Any = Depends(get_queue),
) -> RedirectResponse:
    if decision not in ("approve", "reject"):
        raise AdminForbidden
    author = await session.scalar(
        text("SELECT created_by FROM broadcasts WHERE id = :id AND status = 'pending'"),
        {"id": broadcast_id},
    )
    # Two-person rule: nobody approves their own broadcast.
    if author is None or (decision == "approve" and author == admin.profile.id):
        raise AdminForbidden
    status = "approved" if decision == "approve" else "rejected"
    await session.execute(
        text(
            "UPDATE broadcasts SET status = :status, decided_by = :by, decided_at = NOW(), "
            "decision_reason = NULLIF(:reason, '') WHERE id = :id"
        ),
        {"status": status, "by": admin.profile.id, "reason": reason.strip(), "id": broadcast_id},
    )
    await record(
        session,
        admin,
        request,
        f"broadcast_{decision}",
        "broadcast",
        str(broadcast_id),
        reason.strip() or None,
    )
    await session.commit()
    if status == "approved":
        await queue_client.enqueue_job("send_broadcast", str(broadcast_id))
    return back("/admin/broadcasts?tab=sent" if status == "approved" else "/admin/broadcasts")


# Announcements


@router.get("/announcements", response_class=HTMLResponse)
async def announcements_page(
    request: Request,
    admin: Admin = Depends(auth.can("post_announcements")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT a.*, l.name AS lga, p.display_name AS author,
                       (a.is_active AND a.starts_at <= NOW()
                        AND (a.ends_at IS NULL OR a.ends_at > NOW())) AS live
                FROM announcements a LEFT JOIN lgas l ON l.id = a.lga_id
                LEFT JOIN profiles p ON p.id = a.created_by
                ORDER BY a.created_at DESC LIMIT 100
                """
            )
        )
    ).all()
    return page(request, admin, "announcements.html", rows=rows, lgas=await _lgas(session))


@router.post("/announcements")
async def add_announcement(
    request: Request,
    title: str = Form(min_length=1, max_length=80),
    body: str = Form(min_length=1, max_length=400),
    lga_id: str = Form(default=""),
    days: int = Form(default=7, ge=1, le=90),
    admin: Admin = Depends(auth.act("post_announcements")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    lga = int(lga_id) if lga_id.isdigit() else None
    if not admin.scope.all and lga not in admin.scope.lga_ids:
        raise AdminForbidden
    row_id = await session.scalar(
        text(
            "INSERT INTO announcements (title, body, lga_id, ends_at, created_by) "
            "VALUES (:title, :body, :lga, NOW() + make_interval(days => :days), :by) RETURNING id"
        ),
        {
            "title": title.strip(),
            "body": body.strip(),
            "lga": lga,
            "days": days,
            "by": admin.profile.id,
        },
    )
    await record(
        session,
        admin,
        request,
        "announcement_post",
        "announcement",
        str(row_id),
        details={"title": title.strip(), "lga": lga, "days": days},
    )
    await session.commit()
    return back("/admin/announcements")


@router.post("/announcements/{announcement_id}/toggle")
async def toggle_announcement(
    request: Request,
    announcement_id: int,
    admin: Admin = Depends(auth.act("post_announcements")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        text("UPDATE announcements SET is_active = NOT is_active WHERE id = :id"),
        {"id": announcement_id},
    )
    await record(
        session, admin, request, "announcement_toggle", "announcement", str(announcement_id)
    )
    await session.commit()
    return back("/admin/announcements")


# Message templates


@router.get("/templates", response_class=HTMLResponse)
async def templates_page(
    request: Request,
    saved: bool = False,
    admin: Admin = Depends(auth.can("edit_templates")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    return page(
        request,
        admin,
        "message_templates.html",
        current=await app_settings.message_templates(session),
        defaults=app_settings.DEFAULT_TEMPLATES,
        labels=app_settings.TEMPLATE_LABELS,
        notice="Templates saved." if saved else None,
    )


@router.post("/templates")
async def save_templates(
    request: Request,
    admin: Admin = Depends(auth.act("edit_templates")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    form = await request.form()
    stored: dict[str, str] = {}
    for key, default in app_settings.DEFAULT_TEMPLATES.items():
        value = str(form.get(key, "")).strip()[:320]
        if value and value != default:
            stored[key] = value
    await app_settings.put(session, app_settings.TEMPLATES, stored, admin.profile.id)
    await record(
        session,
        admin,
        request,
        "settings_change",
        "settings",
        "message_templates",
        details={"customised": sorted(stored)},
    )
    await session.commit()
    return back("/admin/templates?saved=1")
