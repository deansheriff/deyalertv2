from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.auth import Admin, AdminForbidden, AdminRedirect
from app.core.clients import get_queue, get_redis, hit_limit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ApiError
from app.services import incidents as incident_service
from app.services.incidents import audit

router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates" / "admin")
SMS_OFF_FLAG = "flag:sms_fallback_off"
ACTIONS = {"restore", "mark_false", "extend", "confirm", "resolve"}


def _set_cookie(response: RedirectResponse, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=get_settings().environment != "dev",
        samesite="strict",
        path="/admin",
    )


def _back(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _page(request: Request, admin: Admin, name: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(
        request, name, {"admin": admin, **context}, headers={"Cache-Control": "no-store"}
    )


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    redis: Redis = Depends(get_redis),
) -> Any:
    client = request.client.host if request.client else "unknown"
    try:
        await hit_limit(redis, f"rl:admin_login:{client}", 10, 900)
    except ApiError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many attempts. Try again in 15 minutes."},
            status_code=429,
        )
    result = await auth.password_sign_in(email, password)
    factor = await auth.first_totp_factor(result["access_token"]) if result else None
    if result is None or factor is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Email or password is incorrect, or two-step sign-in is not set up."},
            status_code=401,
        )
    response = _back("/admin/mfa")
    _set_cookie(response, auth.PENDING_COOKIE, auth.pending_cookie(result["access_token"], factor))
    return response


@router.get("/mfa", response_class=HTMLResponse)
async def mfa_form(request: Request) -> Any:
    if auth.read_pending(request.cookies.get(auth.PENDING_COOKIE)) is None:
        return _back("/admin/login")
    return templates.TemplateResponse(request, "mfa.html", {"error": None})


@router.post("/mfa", response_class=HTMLResponse)
async def mfa(
    request: Request,
    code: str = Form(),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> Any:
    pending = auth.read_pending(request.cookies.get(auth.PENDING_COOKIE))
    if pending is None:
        return _back("/admin/login")
    client = request.client.host if request.client else "unknown"
    try:
        await hit_limit(redis, f"rl:admin_mfa:{client}", 10, 900)
    except ApiError:
        return templates.TemplateResponse(
            request, "mfa.html", {"error": "Too many attempts."}, status_code=429
        )
    user_id = await auth.verify_totp(pending["token"], pending["factor"], code)
    if user_id is None:
        return templates.TemplateResponse(
            request, "mfa.html", {"error": "That code is not right."}, status_code=401
        )
    response = _back("/admin")
    _set_cookie(response, auth.COOKIE, auth.session_cookie(UUID(user_id)))
    response.delete_cookie(auth.PENDING_COOKIE, path="/admin")
    return response


@router.post("/logout")
async def logout(_: Admin = Depends(auth.require_csrf)) -> RedirectResponse:
    response = _back("/admin/login")
    response.delete_cookie(auth.COOKIE, path="/admin")
    return response


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    stats = (
        await session.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE created_at > date_trunc('day', NOW())) AS today,
                  count(*) FILTER (WHERE status IN ('unconfirmed','corroborated','confirmed'))
                    AS active,
                  count(*) FILTER (WHERE confirmed_at > date_trunc('day', NOW())) AS confirmed,
                  count(*) FILTER (WHERE status = 'under_review') AS queue
                FROM incidents
                """
            )
        )
    ).one()
    by_type = (
        await session.execute(
            text(
                "SELECT t.label, count(*) AS total FROM incidents i "
                "JOIN incident_types t ON t.code = i.type_code "
                "WHERE i.created_at > NOW() - INTERVAL '30 days' "
                "GROUP BY t.label ORDER BY total DESC"
            )
        )
    ).all()
    top_lgas = (
        await session.execute(
            text(
                "SELECT l.name, count(*) AS total FROM incidents i JOIN lgas l ON l.id = i.lga_id "
                "WHERE i.created_at > NOW() - INTERVAL '30 days' "
                "GROUP BY l.name ORDER BY total DESC LIMIT 10"
            )
        )
    ).all()
    return _page(
        request, admin, "dashboard.html", stats=stats, by_type=by_type, top_lgas=top_lgas
    )


def _band(score: int) -> str:
    return "Low" if score < 0 else "High" if score >= 20 else "Normal"


@router.get("/queue", response_class=HTMLResponse)
async def queue(
    request: Request,
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT i.id, t.label, i.location_name, i.flag_weight, i.created_at, i.severity,
                       l.name AS lga, w.name AS ward, p.reputation_score
                FROM incidents i
                JOIN incident_types t ON t.code = i.type_code
                LEFT JOIN lgas l ON l.id = i.lga_id
                LEFT JOIN wards w ON w.id = i.ward_id
                LEFT JOIN incident_reporters r ON r.incident_id = i.id
                LEFT JOIN profiles p ON p.id = r.reporter_id
                WHERE i.status = 'under_review'
                ORDER BY i.flag_weight DESC, i.created_at
                LIMIT 200
                """
            )
        )
    ).all()
    items = [{**row._mapping, "band": _band(row.reputation_score or 0)} for row in rows]
    return _page(request, admin, "queue.html", items=items)


async def _incident(session: AsyncSession, incident_id: UUID) -> Any:
    return (
        await session.execute(
            text(
                """
                SELECT i.id, i.status, i.severity, i.description, i.location_name, i.flag_weight,
                       i.created_at, i.hidden_reason, t.label,
                       ST_Y(i.public_location::geometry) AS public_lat,
                       ST_X(i.public_location::geometry) AS public_lng
                FROM incidents i JOIN incident_types t ON t.code = i.type_code
                WHERE i.id = :id
                """
            ),
            {"id": incident_id},
        )
    ).first()


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
async def incident_detail(
    request: Request,
    incident_id: UUID,
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> Any:
    incident = await _incident(session, incident_id)
    if incident is None:
        return _back("/admin/queue")
    flags = (
        await session.execute(
            text(
                "SELECT reason, notes, weight, created_at FROM incident_flags "
                "WHERE incident_id = :id ORDER BY created_at"
            ),
            {"id": incident_id},
        )
    ).all()
    events = (
        await session.execute(
            text(
                "SELECT event_type, note, created_at FROM incident_events "
                "WHERE incident_id = :id ORDER BY created_at DESC"
            ),
            {"id": incident_id},
        )
    ).all()
    media = (
        await session.execute(
            text(
                "SELECT id, kind, moderation_status, thumb_path FROM incident_media "
                "WHERE incident_id = :id"
            ),
            {"id": incident_id},
        )
    ).all()
    return _page(
        request, admin, "incident.html", incident=incident, flags=flags, events=events, media=media
    )


@router.post("/incidents/{incident_id}/action")
async def incident_action(
    incident_id: UUID,
    action: str = Form(),
    note: str | None = Form(default=None),
    admin: Admin = Depends(auth.require_csrf),
    session: AsyncSession = Depends(get_session),
    queue_client: Any = Depends(get_queue),
) -> RedirectResponse:
    if action not in ACTIONS:
        raise AdminForbidden
    result = await incident_service.verify(
        session, admin.profile, incident_id, action, note, 60 if action == "extend" else None
    )
    if result.enqueue_trigger:
        await queue_client.enqueue_job("fan_out_alert", str(incident_id), result.enqueue_trigger)
    return _back(f"/admin/incidents/{incident_id}")


@router.post("/media/{media_id}")
async def moderate_media(
    media_id: UUID,
    decision: str = Form(),
    admin: Admin = Depends(auth.require_csrf),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if decision not in ("approved", "blurred", "rejected"):
        raise AdminForbidden
    incident_id = (
        await session.execute(
            text(
                "UPDATE incident_media SET moderation_status = :decision WHERE id = :id "
                "RETURNING incident_id"
            ),
            {"decision": decision, "id": media_id},
        )
    ).scalar_one()
    await audit(session, admin.profile.id, f"media:{decision}", "media", str(media_id))
    await session.commit()
    return _back(f"/admin/incidents/{incident_id}")


@router.post("/incidents/{incident_id}/reveal-location", response_class=HTMLResponse)
async def reveal_location(
    request: Request,
    incident_id: UUID,
    reason: str = Form(min_length=10),
    admin: Admin = Depends(auth.require_csrf),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    row = (
        await session.execute(
            text(
                "SELECT ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng "
                "FROM incidents WHERE id = :id"
            ),
            {"id": incident_id},
        )
    ).one()
    await audit(session, admin.profile.id, "location_reveal", "incident", str(incident_id), reason)
    await session.commit()
    return _page(
        request, admin, "revealed.html", title="Exact location", value=f"{row.lat}, {row.lng}",
        back=f"/admin/incidents/{incident_id}",
    )


@router.post("/incidents/{incident_id}/reporter", response_class=HTMLResponse)
async def reporter_lookup(
    request: Request,
    incident_id: UUID,
    reason: str = Form(min_length=20),
    admin: Admin = Depends(auth.require_full_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    row = (
        await session.execute(
            text(
                "SELECT p.phone, p.display_name FROM incident_reporters r "
                "JOIN profiles p ON p.id = r.reporter_id WHERE r.incident_id = :id"
            ),
            {"id": incident_id},
        )
    ).first()
    await audit(session, admin.profile.id, "reporter_lookup", "incident", str(incident_id), reason)
    await session.commit()
    value = f"{row.display_name or 'No name'} ({row.phone})" if row else "Account deleted"
    return _page(
        request, admin, "revealed.html", title="Reporter", value=value,
        back=f"/admin/incidents/{incident_id}",
    )


@router.get("/verifiers", response_class=HTMLResponse)
async def verifiers(
    request: Request,
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT v.id, v.title, v.is_active, p.display_name, p.phone,
                       l.name AS lga, w.name AS ward, a.display_name AS approved_by
                FROM verifiers v JOIN profiles p ON p.id = v.user_id
                JOIN lgas l ON l.id = v.lga_id
                LEFT JOIN wards w ON w.id = v.ward_id
                LEFT JOIN profiles a ON a.id = v.approved_by
                ORDER BY v.created_at DESC
                """
            )
        )
    ).all()
    lgas = (await session.execute(text("SELECT id, state, name FROM lgas ORDER BY name"))).all()
    return _page(request, admin, "verifiers.html", rows=rows, lgas=lgas)


@router.post("/verifiers", response_model=None)
async def add_verifier(
    request: Request,
    phone: str = Form(),
    title: str = Form(min_length=2),
    lga_id: int = Form(),
    ward_name: str = Form(default=""),
    admin: Admin = Depends(auth.require_full_admin),
    session: AsyncSession = Depends(get_session),
) -> Any:
    user_id = (
        await session.execute(
            text("SELECT id FROM profiles WHERE phone = :phone"), {"phone": phone.strip()}
        )
    ).scalar()
    ward_id = None
    if ward_name.strip():
        ward_id = (
            await session.execute(
                text("SELECT id FROM wards WHERE lga_id = :lga AND name = :name"),
                {"lga": lga_id, "name": ward_name.strip()},
            )
        ).scalar()
    if user_id is None or (ward_name.strip() and ward_id is None):
        return _page(
            request, admin, "revealed.html", title="Could not add verifier",
            value="No account uses that phone number, or that ward is not in this LGA.",
            back="/admin/verifiers",
        )
    await session.execute(
        text(
            "INSERT INTO verifiers (user_id, lga_id, ward_id, title, approved_by) "
            "VALUES (:user, :lga, :ward, :title, :admin) ON CONFLICT DO NOTHING"
        ),
        {
            "user": user_id,
            "lga": lga_id,
            "ward": ward_id,
            "title": title.strip(),
            "admin": admin.profile.id,
        },
    )
    await session.execute(
        text("UPDATE profiles SET role = 'verifier' WHERE id = :id AND role = 'member'"),
        {"id": user_id},
    )
    await audit(session, admin.profile.id, "verifier_add", "profile", str(user_id))
    await session.commit()
    return _back("/admin/verifiers")


@router.post("/verifiers/{verifier_id}/toggle")
async def toggle_verifier(
    verifier_id: UUID,
    admin: Admin = Depends(auth.require_full_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        text("UPDATE verifiers SET is_active = NOT is_active WHERE id = :id"),
        {"id": verifier_id},
    )
    await audit(session, admin.profile.id, "verifier_toggle", "verifier", str(verifier_id))
    await session.commit()
    return _back("/admin/verifiers")


def _mask(phone: str) -> str:
    return f"{phone[:7]}****{phone[-2:]}" if len(phone) > 9 else "****"


@router.get("/users", response_class=HTMLResponse)
async def users(
    request: Request,
    q: str = "",
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT p.id, p.phone, p.display_name, p.role, p.is_active, w.name AS ward
                FROM profiles p LEFT JOIN wards w ON w.id = p.home_ward_id
                WHERE :q = '' OR p.phone LIKE '%' || :q || '%'
                    OR p.display_name ILIKE '%' || :q || '%'
                ORDER BY p.created_at DESC LIMIT 100
                """
            ),
            {"q": q},
        )
    ).all()
    items = [{**row._mapping, "phone": _mask(row.phone)} for row in rows]
    return _page(request, admin, "users.html", items=items, q=q)


@router.post("/users/{user_id}/role")
async def change_role(
    user_id: UUID,
    role: str = Form(),
    admin: Admin = Depends(auth.require_full_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if role not in ("member", "verifier", "moderator", "admin") or user_id == admin.profile.id:
        raise AdminForbidden
    await session.execute(
        text("UPDATE profiles SET role = :role WHERE id = :id"), {"role": role, "id": user_id}
    )
    await audit(session, admin.profile.id, f"role_change:{role}", "profile", str(user_id))
    await session.commit()
    return _back("/admin/users")


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    action: str = "",
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (
        await session.execute(
            text(
                """
                SELECT a.created_at, a.action, a.entity_type, a.entity_id, a.reason, a.ip,
                       p.display_name AS actor
                FROM audit_log a LEFT JOIN profiles p ON p.id = a.actor_id
                WHERE :action = '' OR a.action LIKE :action || '%'
                ORDER BY a.created_at DESC LIMIT 200
                """
            ),
            {"action": action},
        )
    ).all()
    return _page(request, admin, "audit.html", rows=rows, action=action)


@router.get("/settings", response_class=HTMLResponse)
async def platform_settings(
    request: Request,
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    types = (
        await session.execute(
            text("SELECT code, label, ttl_minutes FROM incident_types ORDER BY ttl_minutes, label")
        )
    ).all()
    day = datetime.now(UTC).date()
    return _page(
        request,
        admin,
        "settings.html",
        config=get_settings(),
        types=types,
        sms_on=await redis.get(SMS_OFF_FLAG) is None,
        otp_today=int(await redis.get(f"cap:otp:{day}") or 0),
        sms_today=int(await redis.get(f"cap:sms:{day}") or 0),
    )


@router.post("/settings/ttl")
async def update_ttl(
    code: str = Form(),
    minutes: int = Form(ge=15, le=10080),
    admin: Admin = Depends(auth.require_full_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        text("UPDATE incident_types SET ttl_minutes = :minutes WHERE code = :code"),
        {"minutes": minutes, "code": code},
    )
    await audit(session, admin.profile.id, "settings_change", "incident_type", code, str(minutes))
    await session.commit()
    return _back("/admin/settings")


@router.post("/settings/sms")
async def toggle_sms(
    enable: str = Form(),
    admin: Admin = Depends(auth.require_full_admin),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    if enable == "yes":
        await redis.delete(SMS_OFF_FLAG)
    else:
        await redis.set(SMS_OFF_FLAG, "1")
    await audit(session, admin.profile.id, f"sms_fallback:{enable}", "settings", "sms")
    await session.commit()
    return _back("/admin/settings")


def install(app: FastAPI) -> None:
    app.include_router(router)

    @app.exception_handler(AdminRedirect)
    async def _sign_in(_: Request, __: AdminRedirect) -> RedirectResponse:
        return _back("/admin/login")

    @app.exception_handler(AdminForbidden)
    async def _forbidden(_: Request, __: AdminForbidden) -> HTMLResponse:
        return HTMLResponse("Not allowed.", status_code=403)
