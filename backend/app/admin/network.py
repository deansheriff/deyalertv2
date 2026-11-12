"""Live map, partners and webhooks, and area boundaries."""

import json
from typing import Any

from fastapi import Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.auth import Admin, AdminForbidden
from app.admin.common import back, message, page, record, router
from app.core.clients import get_queue
from app.core.db import get_session
from app.services import mailer
from app.services import webhooks as webhook_service
from data.import_boundaries import import_features

MAP_WINDOWS = {"live": None, "24h": 24, "7d": 168}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
PARTNER_KINDS = {
    "police": "Police",
    "government": "Government",
    "security_group": "Community security group",
    "ngo": "NGO",
    "media": "Media",
    "other": "Other",
}


# Live map


@router.get("/map", response_class=HTMLResponse)
async def live_map(
    request: Request,
    admin: Admin = Depends(auth.can("view_map")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    types = (
        await session.execute(text("SELECT code, label FROM incident_types ORDER BY label"))
    ).all()
    return page(request, admin, "map.html", types=types)


@router.get("/map/data")
async def map_data(
    window: str = "live",
    admin: Admin = Depends(auth.can("view_map")),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Public (fuzzed) points only; exact locations need a recorded reveal."""
    hours = MAP_WINDOWS.get(window)
    condition = (
        "i.status IN ('unconfirmed','corroborated','confirmed') AND NOT i.is_hidden"
        if hours is None
        else "i.created_at > NOW() - make_interval(hours => :hours)"
    )
    rows = (
        await session.execute(
            text(
                f"""
                SELECT i.id, i.type_code, t.label, i.severity, i.status, i.created_at,
                       i.corroboration_count, w.name AS ward, l.name AS lga,
                       ST_Y(i.public_location::geometry) AS lat,
                       ST_X(i.public_location::geometry) AS lng
                FROM incidents i JOIN incident_types t ON t.code = i.type_code
                LEFT JOIN wards w ON w.id = i.ward_id LEFT JOIN lgas l ON l.id = i.lga_id
                WHERE {condition} AND i.merged_into IS NULL AND {auth.scope_sql()}
                ORDER BY i.created_at DESC LIMIT 2000
                """
            ),
            {"hours": hours or 0, **admin.scope.params()},
        )
    ).all()
    return JSONResponse(
        [
            {
                "id": str(row.id),
                "type": row.type_code,
                "label": row.label,
                "severity": row.severity,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "corroborations": row.corroboration_count,
                "area": ", ".join(part for part in (row.ward, row.lga) if part),
                "lat": row.lat,
                "lng": row.lng,
            }
            for row in rows
        ],
        headers={"Cache-Control": "no-store"},
    )


# Partners and webhooks


@router.get("/partners", response_class=HTMLResponse)
async def partners_page(
    request: Request,
    partner: int | None = None,
    secret: str = "",
    admin: Admin = Depends(auth.can("manage_partners")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT pa.*,
                  (SELECT count(*) FROM profiles p WHERE p.partner_id = pa.id) AS members,
                  (SELECT string_agg(COALESCE(l.name, w.name), ', ') FROM staff_scopes s
                     LEFT JOIN lgas l ON l.id = s.lga_id LEFT JOIN wards w ON w.id = s.ward_id
                    WHERE s.partner_id = pa.id) AS scope,
                  (SELECT max(d.created_at) FROM webhook_deliveries d JOIN webhooks h
                     ON h.id = d.webhook_id WHERE h.partner_id = pa.id) AS last_delivery,
                  (SELECT bool_or(d.delivered_at IS NULL AND d.attempts > 0) FROM (
                     SELECT d.* FROM webhook_deliveries d JOIN webhooks h ON h.id = d.webhook_id
                     WHERE h.partner_id = pa.id ORDER BY d.created_at DESC LIMIT 5) d) AS failing,
                  (SELECT count(*) FROM webhooks h WHERE h.partner_id = pa.id AND h.is_active)
                    AS hooks
                FROM partners pa ORDER BY pa.name
                """
            )
        )
    ).all()
    selected = partner or (rows[0].id if rows else None)
    hooks, deliveries = [], []
    if selected:
        hooks = (
            await session.execute(
                text("SELECT * FROM webhooks WHERE partner_id = :id ORDER BY created_at"),
                {"id": selected},
            )
        ).all()
        deliveries = (
            await session.execute(
                text(
                    "SELECT d.id, d.event, d.status_code, d.error, d.attempts, d.created_at, "
                    "d.delivered_at, h.url FROM webhook_deliveries d JOIN webhooks h "
                    "ON h.id = d.webhook_id WHERE h.partner_id = :id "
                    "ORDER BY d.created_at DESC LIMIT 25"
                ),
                {"id": selected},
            )
        ).all()
    lgas = (await session.execute(text("SELECT id, name FROM lgas ORDER BY name"))).all()
    return page(
        request,
        admin,
        "partners.html",
        rows=rows,
        selected=selected,
        hooks=hooks,
        deliveries=deliveries,
        lgas=lgas,
        kinds=PARTNER_KINDS,
        events=webhook_service.EVENTS,
        new_secret=secret,
    )


@router.post("/partners", response_model=None)
async def add_partner(
    request: Request,
    name: str = Form(min_length=2, max_length=120),
    kind: str = Form(),
    lga_ids: list[int] = Form(default=[]),
    admin: Admin = Depends(auth.act("manage_partners")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | HTMLResponse:
    if kind not in PARTNER_KINDS:
        raise AdminForbidden
    exists = await session.scalar(
        text("SELECT 1 FROM partners WHERE lower(name) = lower(:name)"), {"name": name.strip()}
    )
    if exists:
        return message(
            request,
            admin,
            "Not added",
            "A partner with this name already exists.",
            "/admin/partners",
        )
    partner_id = await session.scalar(
        text(
            "INSERT INTO partners (name, kind, created_by) VALUES (:name, :kind, :by) RETURNING id"
        ),
        {"name": name.strip(), "kind": kind, "by": admin.profile.id},
    )
    for lga_id in set(lga_ids):
        await session.execute(
            text("INSERT INTO staff_scopes (partner_id, lga_id) VALUES (:p, :l)"),
            {"p": partner_id, "l": lga_id},
        )
    await record(
        session,
        admin,
        request,
        "partner_add",
        "partner",
        str(partner_id),
        details={"name": name.strip(), "lgas": lga_ids},
    )
    await session.commit()
    return back(f"/admin/partners?partner={partner_id}")


@router.post("/partners/{partner_id}/toggle")
async def toggle_partner(
    request: Request,
    partner_id: int,
    admin: Admin = Depends(auth.act("manage_partners")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        text("UPDATE partners SET is_active = NOT is_active WHERE id = :id"), {"id": partner_id}
    )
    # Partner staff lose access while their organisation is switched off.
    await session.execute(
        text("UPDATE profiles SET session_version = session_version + 1 WHERE partner_id = :id"),
        {"id": partner_id},
    )
    await record(session, admin, request, "partner_toggle", "partner", str(partner_id))
    await session.commit()
    return back(f"/admin/partners?partner={partner_id}")


@router.post("/partners/{partner_id}/webhooks", response_model=None)
async def add_webhook(
    request: Request,
    partner_id: int,
    url: str = Form(max_length=500),
    events: list[str] = Form(default=[]),
    admin: Admin = Depends(auth.act("manage_partners")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | HTMLResponse:
    events = [e for e in events if e in webhook_service.EVENTS]
    if not url.startswith("https://") or not events:
        return message(
            request,
            admin,
            "Not added",
            "Use an https:// address and choose at least one event.",
            f"/admin/partners?partner={partner_id}",
        )
    secret = webhook_service.new_secret()
    hook_id = await session.scalar(
        text(
            "INSERT INTO webhooks (partner_id, url, secret_enc, events) "
            "VALUES (:p, :url, :secret, :events) RETURNING id"
        ),
        {"p": partner_id, "url": url.strip(), "secret": mailer.encrypt(secret), "events": events},
    )
    await record(
        session,
        admin,
        request,
        "webhook_add",
        "partner",
        str(partner_id),
        details={"webhook": hook_id, "url": url.strip(), "events": events},
    )
    await session.commit()
    return page(
        request, admin, "webhook_secret.html", secret=secret, url=url.strip(), partner_id=partner_id
    )


@router.post("/webhooks/{hook_id}/rotate", response_class=HTMLResponse)
async def rotate_webhook(
    request: Request,
    hook_id: int,
    admin: Admin = Depends(auth.act("manage_partners")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    secret = webhook_service.new_secret()
    row = (
        await session.execute(
            text("UPDATE webhooks SET secret_enc = :s WHERE id = :id RETURNING partner_id, url"),
            {"s": mailer.encrypt(secret), "id": hook_id},
        )
    ).first()
    if row is None:
        raise AdminForbidden
    await record(
        session,
        admin,
        request,
        "webhook_rotate",
        "partner",
        str(row.partner_id),
        details={"webhook": hook_id},
    )
    await session.commit()
    return page(
        request, admin, "webhook_secret.html", secret=secret, url=row.url, partner_id=row.partner_id
    )


@router.post("/webhooks/{hook_id}/toggle")
async def toggle_webhook(
    request: Request,
    hook_id: int,
    admin: Admin = Depends(auth.act("manage_partners")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    partner_id = await session.scalar(
        text("UPDATE webhooks SET is_active = NOT is_active WHERE id = :id RETURNING partner_id"),
        {"id": hook_id},
    )
    await record(session, admin, request, "webhook_toggle", "partner", str(partner_id))
    await session.commit()
    return back(f"/admin/partners?partner={partner_id}")


@router.post("/webhooks/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: int,
    admin: Admin = Depends(auth.act("manage_partners")),
    session: AsyncSession = Depends(get_session),
    queue_client: Any = Depends(get_queue),
) -> RedirectResponse:
    partner_id = await session.scalar(
        text(
            "UPDATE webhook_deliveries d SET attempts = 0 FROM webhooks h "
            "WHERE h.id = d.webhook_id AND d.id = :id AND d.delivered_at IS NULL "
            "RETURNING h.partner_id"
        ),
        {"id": delivery_id},
    )
    await session.commit()
    if partner_id is not None:
        await queue_client.enqueue_job("deliver_webhook", delivery_id)
    return back(f"/admin/partners?partner={partner_id or ''}")


@router.post("/partners/{partner_id}/test")
async def test_webhooks(
    partner_id: int,
    admin: Admin = Depends(auth.act("manage_partners")),
    session: AsyncSession = Depends(get_session),
    queue_client: Any = Depends(get_queue),
) -> RedirectResponse:
    ids = (
        (
            await session.execute(
                text(
                    "INSERT INTO webhook_deliveries (webhook_id, event, payload) "
                    "SELECT id, 'test', CAST(:payload AS JSONB) FROM webhooks "
                    "WHERE partner_id = :p AND is_active RETURNING id"
                ),
                {
                    "p": partner_id,
                    "payload": json.dumps({"message": "Test delivery from Dey Alert"}),
                },
            )
        )
        .scalars()
        .all()
    )
    await session.commit()
    for delivery_id in ids:
        await queue_client.enqueue_job("deliver_webhook", delivery_id)
    return back(f"/admin/partners?partner={partner_id}")


# Areas


@router.get("/areas", response_class=HTMLResponse)
async def areas_page(
    request: Request,
    admin: Admin = Depends(auth.can("manage_areas")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT l.id, l.state, l.name, l.geom IS NOT NULL AS has_shape,
                       (SELECT count(*) FROM wards w WHERE w.lga_id = l.id) AS wards,
                       (SELECT count(*) FROM incidents i WHERE i.lga_id = l.id
                          AND i.created_at > NOW() - INTERVAL '30 days') AS reports
                FROM lgas l ORDER BY l.state, l.name
                """
            )
        )
    ).all()
    return page(request, admin, "areas.html", rows=rows)


@router.post("/areas/import", response_class=HTMLResponse)
async def import_areas(
    request: Request,
    level: str = Form(),
    state: str = Form(min_length=2, max_length=60),
    name_field: str = Form(min_length=1, max_length=60),
    code_field: str = Form(default=""),
    file: UploadFile = File(),
    admin: Admin = Depends(auth.act("manage_areas")),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if level not in ("lga", "ward"):
        raise AdminForbidden
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return message(
            request, admin, "File too large", "Upload a GeoJSON file under 30 MB.", "/admin/areas"
        )
    try:
        features = json.loads(raw)["features"]
    except (ValueError, KeyError, TypeError):
        return message(
            request,
            admin,
            "Not a GeoJSON file",
            "The file must be a GeoJSON FeatureCollection.",
            "/admin/areas",
        )
    # The import uses the raw database connection inside this request's transaction.
    connection = await session.connection()
    driver = (await connection.get_raw_connection()).driver_connection
    written, skipped = await import_features(
        driver, level, state.strip(), features, name_field.strip(), code_field.strip() or None
    )
    await record(
        session,
        admin,
        request,
        "boundary_import",
        "area",
        state.strip(),
        details={"level": level, "written": written, "skipped": len(skipped)},
    )
    await session.commit()
    detail = f"Imported {written} {'LGAs' if level == 'lga' else 'wards'} for {state.strip()}."
    if skipped:
        detail += (
            f" Skipped {len(skipped)}: {', '.join(skipped[:15])}{'…' if len(skipped) > 15 else ''}."
        )
    return message(request, admin, "Boundaries imported", detail, "/admin/areas")
