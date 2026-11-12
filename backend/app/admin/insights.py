"""Analytics, scheduled reports and the audit log (super admins only)."""

import csv
import io
from datetime import date
from typing import Any

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.auth import Admin
from app.admin.common import back, page, record, router
from app.core.db import get_session
from app.services import app_settings, insights

AUDIT_PAGE = 50

# Plain-language names for audit actions.
ACTION_LABELS = {
    "location_reveal": "Revealed exact location",
    "reporter_lookup": "Looked up reporter",
    "user_view": "Opened a user profile",
    "staff_invite": "Invited staff",
    "staff_scope": "Changed staff areas",
    "staff_password_reset": "Reset a staff password",
    "staff_sign_out_everywhere": "Signed staff out everywhere",
    "staff_disable": "Disabled a staff account",
    "staff_enable": "Enabled a staff account",
    "staff_remove": "Removed staff access",
    "permissions_change": "Changed role permissions",
    "broadcast_submit": "Submitted a broadcast",
    "broadcast_approve": "Approved a broadcast",
    "broadcast_reject": "Rejected a broadcast",
    "smtp_change": "Changed SMTP settings",
    "settings_change": "Changed a setting",
    "incident_merge": "Merged duplicate incidents",
    "sanction_add": "Sanctioned a user",
    "sanction_lift": "Lifted a sanction",
    "appeal_decide": "Decided an appeal",
    "reputation_adjust": "Adjusted reputation",
    "privacy_export": "Exported user data",
}


def action_label(action: str) -> str:
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    head, _, tail = action.partition(":")
    pretty = head.replace("_", " ").capitalize()
    return f"{pretty}: {tail.replace('_', ' ')}" if tail else pretty


# Analytics


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(
    request: Request,
    range: str = "30",  # noqa: A002
    lga: str = "",
    saved: bool = False,
    admin: Admin = Depends(auth.can("view_analytics")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    days = insights.RANGES.get(range, 30)
    lga_id = int(lga) if lga.isdigit() else None
    data = await insights.overview(session, days, admin.scope.params(), lga_id)
    lgas = (await session.execute(text("SELECT id, name FROM lgas ORDER BY name"))).all()
    weekly = await app_settings.get(session, app_settings.WEEKLY_REPORT, {}) or {}
    severities = ("low", "medium", "high", "critical")
    days_list = sorted({row.day for row in data["per_day"]})
    series = {
        level: [
            sum(row.total for row in data["per_day"] if row.day == day and row.severity == level)
            for day in days_list
        ]
        for level in severities
    }
    return page(
        request,
        admin,
        "analytics.html",
        data=data,
        period=str(days),
        lga=lga_id,
        lgas=lgas,
        weekly=weekly,
        days_list=days_list,
        series=series,
        notice="Weekly report settings saved." if saved else None,
    )


@router.get("/analytics.csv")
async def analytics_csv(
    range: str = "30",  # noqa: A002
    lga: str = "",
    admin: Admin = Depends(auth.can("view_analytics")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    days = insights.RANGES.get(range, 30)
    data = await insights.overview(
        session, days, admin.scope.params(), int(lga) if lga.isdigit() else None
    )
    return Response(
        insights.csv_report(data),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="dey-alert-{days}d-{date.today()}.csv"'
        },
    )


@router.post("/analytics/weekly")
async def save_weekly(
    request: Request,
    enabled: bool = Form(default=False),
    recipients: str = Form(default=""),
    admin: Admin = Depends(auth.act("manage_settings")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    emails = [e.strip().lower() for e in recipients.replace(";", ",").split(",") if "@" in e]
    await app_settings.put(
        session,
        app_settings.WEEKLY_REPORT,
        {"enabled": enabled, "recipients": emails[:20]},
        admin.profile.id,
    )
    await record(
        session,
        admin,
        request,
        "settings_change",
        "settings",
        "weekly_report",
        details={"enabled": enabled, "recipients": len(emails)},
    )
    await session.commit()
    return back("/admin/analytics?saved=1")


# Audit log


def _audit_filters(
    actor: str, action: str, entity: str, start: str, end: str, q: str
) -> tuple[str, dict[str, Any]]:
    clauses = ["true"]
    params: dict[str, Any] = {}
    if actor:
        clauses.append("a.actor_id::text = :actor")
        params["actor"] = actor
    if action:
        clauses.append("a.action LIKE :action || '%'")
        params["action"] = action
    if entity:
        clauses.append("a.entity_type = :entity")
        params["entity"] = entity
    if start:
        clauses.append("a.created_at >= CAST(:start AS DATE)")
        params["start"] = start
    if end:
        clauses.append("a.created_at < CAST(:end AS DATE) + 1")
        params["end"] = end
    if q:
        clauses.append(
            "(a.reason ILIKE '%' || :q || '%' OR a.entity_id ILIKE '%' || :q || '%' "
            "OR a.actor_label ILIKE '%' || :q || '%' OR a.metadata::text ILIKE '%' || :q || '%')"
        )
        params["q"] = q
    return " AND ".join(clauses), params


# Recomputes every entry's hash and checks each links to the one before.
# Returns the first bad entry, or nothing when the chain is intact.
CHAIN_CHECK_SQL = """
                SELECT chain_seq FROM (
                    SELECT chain_seq, hash, prev_hash,
                           lag(hash) OVER (ORDER BY chain_seq) AS expected_prev,
                           lag(chain_seq) OVER (ORDER BY chain_seq) AS previous_seq,
                           audit_entry_hash(prev_hash, chain_seq, created_at, actor_label, action,
                               entity_type, entity_id, reason, metadata, ip) AS recomputed
                    FROM audit_log
                ) s
                WHERE recomputed IS DISTINCT FROM hash
                   OR (expected_prev IS NOT NULL AND prev_hash IS DISTINCT FROM expected_prev)
                   OR (previous_seq IS NOT NULL AND chain_seq <> previous_seq + 1)
                ORDER BY chain_seq LIMIT 1
                """


async def verify_chain(session: AsyncSession) -> dict[str, Any]:
    """Recomputes every entry's hash and checks each links to the one before."""
    broken = (await session.execute(text(CHAIN_CHECK_SQL))).scalar()
    total = await session.scalar(text("SELECT count(*) FROM audit_log")) or 0
    return {"ok": broken is None, "broken_at": broken, "total": total}


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    actor: str = "",
    action: str = "",
    entity: str = "",
    start: str = "",
    end: str = "",
    q: str = "",
    page_number: int = 1,
    admin: Admin = Depends(auth.require_super),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    where, params = _audit_filters(actor, action, entity, start, end, q)
    page_number = max(1, page_number)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT a.chain_seq, a.created_at, a.actor_label, a.action, a.entity_type,
                       a.entity_id, a.reason, a.metadata, host(a.ip) AS ip, a.hash
                FROM audit_log a WHERE {where}
                ORDER BY a.chain_seq DESC NULLS LAST, a.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": AUDIT_PAGE + 1, "offset": (page_number - 1) * AUDIT_PAGE},
        )
    ).all()
    people = (
        await session.execute(
            text(
                "SELECT DISTINCT p.id, COALESCE(p.display_name, p.email) AS name FROM audit_log a "
                "JOIN profiles p ON p.id = a.actor_id ORDER BY 2"
            )
        )
    ).all()
    actions = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT split_part(action, ':', 1) AS action FROM audit_log ORDER BY 1"
                )
            )
        )
        .scalars()
        .all()
    )
    entities = (
        (await session.execute(text("SELECT DISTINCT entity_type FROM audit_log ORDER BY 1")))
        .scalars()
        .all()
    )
    return page(
        request,
        admin,
        "audit.html",
        rows=rows[:AUDIT_PAGE],
        more=len(rows) > AUDIT_PAGE,
        page_number=page_number,
        people=people,
        actions=actions,
        entities=entities,
        filters={
            "actor": actor,
            "action": action,
            "entity": entity,
            "start": start,
            "end": end,
            "q": q,
        },
        chain=await verify_chain(session),
        action_label=action_label,
    )


@router.get("/audit.csv")
async def audit_csv(
    request: Request,
    actor: str = "",
    action: str = "",
    entity: str = "",
    start: str = "",
    end: str = "",
    q: str = "",
    admin: Admin = Depends(auth.require_super),
    session: AsyncSession = Depends(get_session),
) -> Response:
    where, params = _audit_filters(actor, action, entity, start, end, q)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT a.chain_seq, a.created_at, a.actor_label, a.action, a.entity_type,
                       a.entity_id, a.reason, a.metadata::text AS metadata, host(a.ip) AS ip,
                       a.prev_hash, a.hash
                FROM audit_log a WHERE {where} ORDER BY a.chain_seq LIMIT 100000
                """
            ),
            params,
        )
    ).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "seq",
            "time_utc",
            "who",
            "action",
            "target_type",
            "target_id",
            "reason",
            "details",
            "ip",
            "prev_hash",
            "hash",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.chain_seq,
                row.created_at.isoformat(),
                row.actor_label,
                row.action,
                row.entity_type,
                row.entity_id,
                row.reason,
                row.metadata,
                row.ip,
                row.prev_hash,
                row.hash,
            ]
        )
    # Exporting the log is itself recorded.
    await record(
        session, admin, request, "audit_export", "audit_log", None, details={"rows": len(rows)}
    )
    await session.commit()
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-log-{date.today()}.csv"'},
    )
