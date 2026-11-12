"""Settings, incident types, word filter, kill switches, limits and system health."""

import re
from typing import Any

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import auth
from app.admin.auth import Admin, AdminForbidden
from app.admin.common import SEVERITIES, back, page, record, router
from app.api import app_config
from app.core.clients import get_redis, hit_limit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ApiError
from app.services import app_settings, health, mailer

SMS_OFF_FLAG = "flag:sms_fallback_off"
ICONS = ("alert", "car", "fire", "flood", "shield", "siren", "users", "road", "medical", "bolt")


def _settings_access(admin: Admin) -> None:
    if not (admin.can("manage_settings") or admin.can("manage_incident_types")):
        raise AdminForbidden


async def _settings_page(
    request: Request,
    admin: Admin,
    session: AsyncSession,
    redis: Redis,
    status_code: int = 200,
    **extra: Any,
) -> HTMLResponse:
    types = (
        await session.execute(
            text(
                "SELECT code, label, default_severity, ttl_minutes, sensitive, is_active, icon "
                "FROM incident_types ORDER BY is_active DESC, label"
            )
        )
    ).all()
    return page(
        request,
        admin,
        "settings.html",
        status_code=status_code,
        config=get_settings(),
        types=types,
        icons=ICONS,
        sms_on=await redis.get(SMS_OFF_FLAG) is None,
        phone_sign_in=await app_settings.phone_sign_in_enabled(session),
        smtp=mailer.summary(await app_settings.get(session, app_settings.SMTP)),
        security_modes=mailer.SECURITY_MODES,
        words=await app_settings.word_filter(session),
        **{"smtp_notice": None, "smtp_error": None, "notice": None, **extra},
    )


@router.get("/settings", response_class=HTMLResponse)
async def platform_settings(
    request: Request,
    saved: str = "",
    admin: Admin = Depends(auth.current_admin),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    _settings_access(admin)
    return await _settings_page(request, admin, session, redis, notice="Saved." if saved else None)


@router.post("/settings/sign-in")
async def toggle_phone_sign_in(
    request: Request,
    enable: str = Form(),
    admin: Admin = Depends(auth.act("manage_settings")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await app_settings.put(session, app_settings.PHONE_SIGN_IN, enable == "yes", admin.profile.id)
    await record(session, admin, request, f"phone_sign_in:{enable}", "settings", "sign_in")
    await session.commit()
    return back("/admin/settings?saved=1")


@router.post("/settings/sms")
async def toggle_sms(
    request: Request,
    enable: str = Form(),
    admin: Admin = Depends(auth.act("manage_settings")),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    if enable == "yes":
        await redis.delete(SMS_OFF_FLAG)
    else:
        await redis.set(SMS_OFF_FLAG, "1")
    await record(session, admin, request, f"sms_fallback:{enable}", "settings", "sms")
    await session.commit()
    return back("/admin/settings?saved=1")


@router.post("/settings/smtp", response_class=HTMLResponse)
async def save_smtp(
    request: Request,
    host: str = Form(),
    port: int = Form(ge=1, le=65535),
    security: str = Form(),
    username: str = Form(default=""),
    password: str = Form(default=""),
    from_email: str = Form(),
    from_name: str = Form(default="Dey Alert"),
    admin: Admin = Depends(auth.act("manage_settings")),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    if security not in mailer.SECURITY_MODES or "@" not in from_email:
        return await _settings_page(
            request, admin, session, redis, 400, smtp_error="Check the security mode and sender."
        )
    if not password:
        # A blank password field keeps the one already saved.
        current = await mailer.load(session)
        password = current.password if current else ""
    config = mailer.SmtpConfig(
        host=host.strip(),
        port=port,
        username=username.strip(),
        password=password,
        from_email=from_email.strip(),
        from_name=from_name.strip() or "Dey Alert",
        security=security,
    )
    await mailer.save(session, config, admin.profile.id)
    await record(session, admin, request, "smtp_change", "settings", "smtp", config.host)
    await session.commit()
    return await _settings_page(
        request, admin, session, redis, smtp_notice="SMTP settings saved. Send a test email."
    )


@router.post("/settings/smtp/test", response_class=HTMLResponse)
async def test_smtp(
    request: Request,
    to: str = Form(),
    admin: Admin = Depends(auth.act("manage_settings")),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    try:
        await hit_limit(redis, f"rl:smtp_test:{admin.profile.id}", 10, 3600)
    except ApiError:
        return await _settings_page(
            request, admin, session, redis, 429, smtp_error="Too many test emails. Wait an hour."
        )
    config = await mailer.load(session)
    if config is None:
        return await _settings_page(
            request, admin, session, redis, 400, smtp_error="Save SMTP settings first."
        )
    _, body, html = mailer.code_email("signup", "123456")
    intro = "This is a test from the Dey Alert admin panel. Sign-in emails look like this:"
    try:
        await mailer.send(config, to.strip(), "Dey Alert test email", f"{intro}\n\n{body}", html)
    except mailer.MailError as error:
        return await _settings_page(request, admin, session, redis, 502, smtp_error=str(error))
    return await _settings_page(
        request, admin, session, redis, smtp_notice=f"Test email sent to {to.strip()}."
    )


# Incident types and the word filter


@router.post("/settings/types", response_model=None)
async def save_type(
    request: Request,
    code: str = Form(default=""),
    label: str = Form(min_length=2, max_length=60),
    default_severity: str = Form(),
    ttl_minutes: int = Form(ge=15, le=10080),
    icon: str = Form(default="alert"),
    sensitive: bool = Form(default=False),
    is_active: bool = Form(default=False),
    admin: Admin = Depends(auth.act("manage_incident_types")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if default_severity not in SEVERITIES or icon not in ICONS:
        raise AdminForbidden
    new_code = code.strip() or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
    if not new_code:
        raise AdminForbidden
    await session.execute(
        text(
            """
            INSERT INTO incident_types (code, label, default_severity, ttl_minutes, sensitive,
                                        is_active, icon)
            VALUES (:code, :label, :severity, :ttl, :sensitive, :active, :icon)
            ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label,
                default_severity = EXCLUDED.default_severity, ttl_minutes = EXCLUDED.ttl_minutes,
                sensitive = EXCLUDED.sensitive, is_active = EXCLUDED.is_active, icon = EXCLUDED.icon
            """
        ),
        {
            "code": new_code,
            "label": label.strip(),
            "severity": default_severity,
            "ttl": ttl_minutes,
            "sensitive": sensitive,
            "active": is_active or not code,
            "icon": icon,
        },
    )
    await record(
        session,
        admin,
        request,
        "settings_change",
        "incident_type",
        new_code,
        details={
            "label": label.strip(),
            "severity": default_severity,
            "ttl": ttl_minutes,
            "active": is_active or not code,
        },
    )
    await session.commit()
    return back("/admin/settings?saved=1#types")


@router.post("/settings/words")
async def save_words(
    request: Request,
    words: str = Form(default=""),
    hold: bool = Form(default=False),
    admin: Admin = Depends(auth.act("manage_incident_types")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    cleaned = sorted({w.strip().lower() for w in re.split(r"[,\n]", words) if w.strip()})[:500]
    await app_settings.put(
        session, app_settings.WORD_FILTER, {"words": cleaned, "hold": hold}, admin.profile.id
    )
    # The words themselves stay out of the audit log; the count is enough.
    await record(
        session,
        admin,
        request,
        "settings_change",
        "settings",
        "word_filter",
        details={"words": len(cleaned), "hold": hold},
    )
    await session.commit()
    return back("/admin/settings?saved=1#word-filter")


# System health, kill switches and limits


@router.get("/health", response_class=HTMLResponse)
async def system_health(
    request: Request,
    saved: bool = False,
    admin: Admin = Depends(auth.can("view_health")),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    checks = await health.run(session, redis)
    lgas = (await session.execute(text("SELECT id, name FROM lgas ORDER BY name"))).all()
    return page(
        request,
        admin,
        "health.html",
        checks=checks,
        overall=health.overall(checks),
        cron=await health.cron_runs(redis),
        switches=await app_settings.switches(session),
        limits=await app_settings.limits(session),
        bounds=app_settings.LIMIT_BOUNDS,
        lgas=lgas,
        notice="Saved." if saved else None,
    )


@router.post("/health/switches")
async def save_switches(
    request: Request,
    signups_paused: bool = Form(default=False),
    reports_paused: bool = Form(default=False),
    reports_paused_lgas: list[int] = Form(default=[]),
    maintenance: bool = Form(default=False),
    maintenance_message: str = Form(default="", max_length=200),
    admin: Admin = Depends(auth.act("manage_settings")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    value = {
        "signups_paused": signups_paused,
        "reports_paused": reports_paused,
        "reports_paused_lgas": sorted(set(reports_paused_lgas)),
        "maintenance": maintenance,
        "maintenance_message": maintenance_message.strip()
        or app_settings.DEFAULT_SWITCHES["maintenance_message"],
    }
    await app_settings.put(session, app_settings.SWITCHES, value, admin.profile.id)
    await record(session, admin, request, "kill_switches", "settings", "switches", details=value)
    await session.commit()
    app_config.clear_cache()
    return back("/admin/health?saved=1")


@router.post("/health/limits")
async def save_limits(
    request: Request,
    admin: Admin = Depends(auth.act("manage_settings")),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    form = await request.form()
    stored: dict[str, Any] = {}
    for key, (_, low, high) in app_settings.LIMIT_BOUNDS.items():
        raw = str(form.get(key, "")).strip()
        if not raw:
            continue
        try:
            value = float(raw) if key == "flag_hide_threshold" else int(raw)
        except ValueError:
            continue
        stored[key] = min(max(value, low), high)
    before = await app_settings.limits(session)
    await app_settings.put(session, app_settings.LIMITS, stored, admin.profile.id)
    changes = {k: {"from": before.get(k), "to": v} for k, v in stored.items() if before.get(k) != v}
    await record(session, admin, request, "limits_change", "settings", "limits", details=changes)
    await session.commit()
    return back("/admin/health?saved=1")
