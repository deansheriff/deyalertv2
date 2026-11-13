"""Sign-in, the dashboard, global search and nav counts."""

from typing import Any
from uuid import UUID

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth, rbac
from app.admin.auth import Admin
from app.admin.common import back, client_ip, page, router, set_admin_cookie, templates
from app.core.clients import get_redis, hit_limit
from app.core.db import get_session
from app.core.errors import ApiError
from app.services import health

MIN_PASSWORD = 12


async def _login_stats(session: AsyncSession) -> Any:
    return (
        await session.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE status IN ('unconfirmed','corroborated','confirmed')
                                   AND NOT is_hidden) AS live,
                  count(*) FILTER (WHERE confirmed_at > date_trunc('day', NOW())) AS verified,
                  count(DISTINCT ward_id) FILTER (WHERE created_at > NOW() - INTERVAL '30 days')
                    AS wards
                FROM incidents
                """
            )
        )
    ).one()


async def _login_page(
    request: Request,
    session: AsyncSession,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "notice": notice, "stats": await _login_stats(session)},
        status_code=status_code,
    )


async def _log_attempt(
    session: AsyncSession, request: Request, email: str, outcome: str, user_id: UUID | None
) -> None:
    await session.execute(
        text(
            "INSERT INTO staff_logins (profile_id, email, outcome, ip, user_agent) "
            "VALUES (:id, :email, :outcome, :ip, :agent)"
        ),
        {
            "id": user_id,
            "email": email.strip().lower()[:200],
            "outcome": outcome,
            "ip": client_ip(request),
            "agent": (request.headers.get("user-agent") or "")[:200],
        },
    )
    await session.commit()


def _start_session(row: Any) -> RedirectResponse:
    response = back("/admin")
    set_admin_cookie(response, auth.COOKIE, auth.session_cookie(row.id, row.session_version))
    response.delete_cookie(auth.PENDING_COOKIE, path="/admin")
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request, changed: bool = False, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    notice = "Password changed. Sign in with your new password." if changed else None
    return await _login_page(request, session, notice=notice)


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> Any:
    # Limit by account, so one person's mistakes can't lock out everyone behind the same
    # proxy, and more loosely by address to slow down guessing across many accounts.
    try:
        await hit_limit(redis, f"rl:admin_login:email:{email.strip().lower()}", 10, 900)
        await hit_limit(redis, f"rl:admin_login:ip:{client_ip(request)}", 30, 900)
    except ApiError:
        return await _login_page(
            request, session, "Too many attempts. Try again in 15 minutes.", status_code=429
        )
    result = await auth.password_sign_in(email, password)
    user = (result or {}).get("user") or {}
    user_id = UUID(user["id"]) if user.get("id") else None
    row = await auth.staff_row(session, user_id) if user_id else None
    if result is None or row is None or not row.is_active or row.role not in rbac.PANEL_ROLES:
        outcome = "bad_password" if result is None else "not_staff"
        await _log_attempt(session, request, email, outcome, user_id)
        return await _login_page(
            request, session, "Email or password is incorrect.", status_code=401
        )
    if (user.get("app_metadata") or {}).get("must_change_password"):
        await _log_attempt(session, request, email, "password_change", row.id)
        response = back("/admin/password")
        set_admin_cookie(response, auth.PENDING_COOKIE, auth.password_cookie(row.id))
        return response
    if not row.require_two_step:
        await _log_attempt(session, request, email, "success", row.id)
        return _start_session(row)
    token = result["access_token"]
    factor = await auth.first_totp_factor(token)
    if factor is not None:
        response = back("/admin/mfa")
        set_admin_cookie(response, auth.PENDING_COOKIE, auth.pending_cookie(token, factor, row.id))
        return response
    enrolment = await auth.enroll_totp(token)
    if enrolment is None:
        return await _login_page(
            request,
            session,
            "Two-step sign-in could not be set up. Ask a super admin to check TOTP in Supabase.",
            status_code=503,
        )
    # The QR code cannot be fetched again, so it is shown straight away.
    response = templates.TemplateResponse(
        request,
        "mfa.html",
        {"error": None, "enrolment": enrolment},
        headers={"Cache-Control": "no-store"},
    )
    set_admin_cookie(
        response, auth.PENDING_COOKIE, auth.pending_cookie(token, enrolment["id"], row.id)
    )
    return response


@router.get("/mfa", response_class=HTMLResponse)
async def mfa_form(request: Request) -> Any:
    if auth.read_pending(request.cookies.get(auth.PENDING_COOKIE)) is None:
        return back("/admin/login")
    return templates.TemplateResponse(request, "mfa.html", {"error": None, "enrolment": None})


@router.post("/mfa", response_class=HTMLResponse)
async def mfa(
    request: Request,
    code: str = Form(),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> Any:
    pending = auth.read_pending(request.cookies.get(auth.PENDING_COOKIE))
    if pending is None:
        return back("/admin/login")
    try:
        await hit_limit(redis, f"rl:admin_mfa:{pending['sub']}", 10, 900)
    except ApiError:
        return templates.TemplateResponse(
            request, "mfa.html", {"error": "Too many attempts.", "enrolment": None}, status_code=429
        )
    row = await auth.staff_row(session, UUID(pending["sub"]))
    if row is None or not await auth.verify_totp(pending["token"], pending["factor"], code):
        await _log_attempt(session, request, "", "bad_code", UUID(pending["sub"]))
        return templates.TemplateResponse(
            request,
            "mfa.html",
            {"error": "That code is not right.", "enrolment": None},
            status_code=401,
        )
    await _log_attempt(session, request, row.email or "", "success", row.id)
    return _start_session(row)


@router.get("/password", response_class=HTMLResponse)
async def password_form(request: Request) -> Any:
    if auth.read_password_pending(request.cookies.get(auth.PENDING_COOKIE)) is None:
        return back("/admin/login")
    return templates.TemplateResponse(request, "password.html", {"error": None})


@router.post("/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    new_password: str = Form(),
    confirm_password: str = Form(),
) -> Any:
    pending = auth.read_password_pending(request.cookies.get(auth.PENDING_COOKIE))
    if pending is None:
        return back("/admin/login")
    error = None
    if len(new_password) < MIN_PASSWORD:
        error = f"Use at least {MIN_PASSWORD} characters."
    elif new_password != confirm_password:
        error = "The two passwords do not match."
    elif not await auth.set_password(UUID(pending["sub"]), new_password):
        error = "Could not save the new password. Try again."
    if error:
        return templates.TemplateResponse(
            request, "password.html", {"error": error}, status_code=400
        )
    response = back("/admin/login?changed=1")
    response.delete_cookie(auth.PENDING_COOKIE, path="/admin")
    return response


@router.post("/logout")
async def logout(_: Admin = Depends(auth.require_csrf)) -> RedirectResponse:
    response = back("/admin/login")
    response.delete_cookie(auth.COOKIE, path="/admin")
    return response


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    admin: Admin = Depends(auth.can("view_dashboard")),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    scope = admin.scope.params()
    where = auth.scope_sql()
    stats = (
        await session.execute(
            text(
                f"""
                SELECT
                  count(*) FILTER (WHERE status IN ('unconfirmed','corroborated','confirmed')
                                   AND NOT is_hidden) AS live,
                  count(*) FILTER (WHERE status = 'under_review') AS queue,
                  min(created_at) FILTER (WHERE status = 'under_review') AS oldest,
                  count(*) FILTER (WHERE confirmed_at > date_trunc('day', NOW())) AS confirmed,
                  count(*) FILTER (WHERE created_at > date_trunc('day', NOW())) AS today
                FROM incidents i WHERE {where} AND merged_into IS NULL
                """
            ),
            scope,
        )
    ).one()
    sos_active = await session.scalar(
        text("SELECT count(*) FROM sos_alerts WHERE status IN ('active','pending_cancel')")
    )
    attention = (
        await session.execute(
            text(
                f"""
                SELECT i.id, i.severity, t.label, w.name AS ward, l.name AS lga, i.created_at
                FROM incidents i JOIN incident_types t ON t.code = i.type_code
                LEFT JOIN wards w ON w.id = i.ward_id LEFT JOIN lgas l ON l.id = i.lga_id
                WHERE i.status = 'under_review' AND {where}
                ORDER BY array_position(ARRAY['critical','high','medium','low'], i.severity),
                         i.created_at
                LIMIT 6
                """
            ),
            scope,
        )
    ).all()
    by_type = (
        await session.execute(
            text(
                f"""
                SELECT t.label, count(*) AS total FROM incidents i
                JOIN incident_types t ON t.code = i.type_code
                WHERE i.created_at > NOW() - INTERVAL '7 days' AND {where}
                GROUP BY t.label ORDER BY total DESC LIMIT 8
                """
            ),
            scope,
        )
    ).all()
    per_day = (
        await session.execute(
            text(
                f"""
                SELECT d::date AS day, count(i.id) AS total
                FROM generate_series(date_trunc('day', NOW()) - INTERVAL '13 days',
                                     date_trunc('day', NOW()), INTERVAL '1 day') d
                LEFT JOIN incidents i ON date_trunc('day', i.created_at) = d AND {where}
                GROUP BY d ORDER BY d
                """
            ),
            scope,
        )
    ).all()
    checks = await health.run(session, redis) if admin.can("view_health") else []
    return page(
        request,
        admin,
        "dashboard.html",
        stats=stats,
        sos_active=sos_active,
        attention=attention,
        by_type=by_type,
        per_day=per_day,
        checks=checks,
    )


@router.get("/counts")
async def counts(
    admin: Admin = Depends(auth.current_admin), session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    """Badges in the sidebar, loaded after the page so pages stay fast."""
    result: dict[str, int] = {}
    if admin.can("view_queue"):
        result["queue"] = (
            await session.scalar(
                text(
                    "SELECT count(*) FROM incidents i WHERE status = 'under_review' "
                    f"AND {auth.scope_sql()}"
                ),
                admin.scope.params(),
            )
            or 0
        )
    if admin.can("support_inbox"):
        result["support"] = (
            await session.scalar(text("SELECT count(*) FROM support_tickets WHERE status = 'open'"))
            or 0
        )
    if admin.can("privacy_requests"):
        result["privacy"] = (
            await session.scalar(
                text("SELECT count(*) FROM privacy_requests WHERE status IN ('new','in_progress')")
            )
            or 0
        )
    if admin.can("approve_broadcasts"):
        result["broadcasts"] = (
            await session.scalar(text("SELECT count(*) FROM broadcasts WHERE status = 'pending'"))
            or 0
        )
    if admin.can("manage_verifiers"):
        result["applications"] = (
            await session.scalar(
                text("SELECT count(*) FROM verifier_applications WHERE status = 'pending'")
            )
            or 0
        )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    term = q.strip()
    incidents: list[Any] = []
    places: list[Any] = []
    people: list[Any] = []
    if len(term) >= 2 and admin.can("view_incidents"):
        incidents = (
            await session.execute(
                text(
                    f"""
                    SELECT i.id, i.severity, i.status, t.label, i.location_name, i.created_at,
                           w.name AS ward, l.name AS lga
                    FROM incidents i JOIN incident_types t ON t.code = i.type_code
                    LEFT JOIN wards w ON w.id = i.ward_id LEFT JOIN lgas l ON l.id = i.lga_id
                    WHERE {auth.scope_sql()} AND (
                        i.id::text LIKE :q || '%' OR i.description ILIKE '%' || :q || '%'
                        OR i.location_name ILIKE '%' || :q || '%' OR w.name ILIKE '%' || :q || '%'
                        OR l.name ILIKE '%' || :q || '%' OR t.label ILIKE '%' || :q || '%')
                    ORDER BY i.created_at DESC LIMIT 25
                    """
                ),
                {"q": term, **admin.scope.params()},
            )
        ).all()
        places = (
            await session.execute(
                text(
                    "SELECT w.id, w.name, l.name AS lga FROM wards w "
                    "JOIN lgas l ON l.id = w.lga_id "
                    "WHERE w.name ILIKE '%' || :q || '%' OR l.name ILIKE '%' || :q || '%' "
                    "ORDER BY l.name, w.name LIMIT 10"
                ),
                {"q": term},
            )
        ).all()
    if len(term) >= 3 and admin.can("view_users"):
        people = (
            await session.execute(
                text(
                    "SELECT id, display_name, role, email, phone FROM profiles "
                    "WHERE display_name ILIKE '%' || :q || '%' OR email ILIKE '%' || :q || '%' "
                    "OR phone LIKE '%' || :q || '%' ORDER BY created_at DESC LIMIT 15"
                ),
                {"q": term},
            )
        ).all()
    return page(
        request, admin, "search.html", q=term, incidents=incidents, places=places, people=people
    )
