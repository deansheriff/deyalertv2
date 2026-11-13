"""Shared pieces of the admin panel: the router, templates, page rendering and auditing."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import rbac
from app.admin.auth import Admin
from app.core.config import get_settings
from app.services.incidents import audit

router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates" / "admin")

SEVERITIES = ("low", "medium", "high", "critical")
SEVERITY_ICONS = {"low": "info", "medium": "alert", "high": "warning", "critical": "siren"}


def _ago(value: datetime | None) -> str:
    if value is None:
        return ""
    seconds = (datetime.now(UTC) - value).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def _waited(value: datetime | None) -> str:
    if value is None:
        return ""
    minutes = int((datetime.now(UTC) - value).total_seconds() // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60} min"


def _wait_level(value: datetime | None) -> str:
    if value is None:
        return ""
    minutes = (datetime.now(UTC) - value).total_seconds() / 60
    return "late" if minutes > 120 else "slow" if minutes > 30 else ""


templates.env.filters["ago"] = _ago
templates.env.filters["waited"] = _waited
templates.env.filters["wait_level"] = _wait_level
templates.env.filters["when"] = lambda value: value.strftime("%d %b %Y, %H:%M") if value else ""
templates.env.globals["role_labels"] = rbac.ROLE_LABELS
templates.env.globals["severities"] = SEVERITIES
templates.env.globals["admin_assignable"] = lambda admin: [
    role for role in rbac.PANEL_ROLES if rbac.may_assign(admin.role, role)
]


def page(
    request: Request, admin: Admin, name: str, status_code: int = 200, **context: Any
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        name,
        {"admin": admin, "path": request.url.path, **context},
        headers={"Cache-Control": "no-store"},
    )
    response.status_code = status_code
    return response


def back(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def message(request: Request, admin: Admin, title: str, value: str, back_to: str) -> HTMLResponse:
    return page(request, admin, "revealed.html", title=title, value=value, back=back_to)


def client_ip(request: Request) -> str | None:
    """The visitor's address. Behind Cloudflare that is CF-Connecting-IP; otherwise uvicorn
    takes it from the proxy's X-Forwarded-For (it runs with --forwarded-allow-ips)."""
    cloudflare = request.headers.get("cf-connecting-ip")
    if cloudflare:
        return cloudflare.strip()
    return request.client.host if request.client else None


async def record(
    session: AsyncSession,
    admin: Admin,
    request: Request | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Writes one audit entry. The database chains it to the previous entry."""
    await audit(
        session,
        admin.profile.id,
        action,
        entity_type,
        entity_id or "",
        reason,
        json.dumps(details) if details is not None else None,
        client_ip(request) if request else None,
    )


def set_admin_cookie(response: Response, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=get_settings().environment != "dev",
        samesite="strict",
        path="/admin",
    )


def mask_contact(phone: str | None, email: str | None = None) -> str:
    if not phone:
        if email and "@" in email:
            name, domain = email.split("@", 1)
            return f"{name[:2]}***@{domain}"
        return "Email sign-in"
    return f"{phone[:7]}****{phone[-2:]}" if len(phone) > 9 else "****"
