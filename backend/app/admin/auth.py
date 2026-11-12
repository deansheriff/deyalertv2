import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Form, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import rbac
from app.core.config import get_settings
from app.core.db import get_session
from app.services.profiles import Profile

COOKIE = "dey_admin"
PENDING_COOKIE = "dey_admin_pending"
SESSION_HOURS = 12
IDLE_MINUTES = 30
REFRESH_SECONDS = 60
PENDING_MINUTES = 5


class AdminRedirect(Exception):
    """Raised when the visitor needs to sign in."""


class AdminForbidden(Exception):
    """Raised when a signed-in staff member lacks the required permission."""


@dataclass(frozen=True)
class Scope:
    """Areas a staff member may see. Empty means every area."""

    lga_ids: tuple[int, ...] = ()
    ward_ids: tuple[int, ...] = ()

    @property
    def all(self) -> bool:
        return not self.lga_ids and not self.ward_ids

    def params(self) -> dict[str, Any]:
        return {
            "scope_all": self.all,
            "scope_lgas": list(self.lga_ids),
            "scope_wards": list(self.ward_ids),
        }


def scope_sql(alias: str = "i") -> str:
    return (
        f"(CAST(:scope_all AS BOOLEAN) OR {alias}.lga_id = ANY(CAST(:scope_lgas AS INT[])) "
        f"OR {alias}.ward_id = ANY(CAST(:scope_wards AS INT[])))"
    )


@dataclass(frozen=True)
class Admin:
    profile: Profile
    name: str
    csrf: str
    email: str | None = None
    permissions: frozenset[str] = frozenset()
    scope: Scope = Scope()
    partner_id: int | None = None

    @property
    def role(self) -> str:
        return self.profile.role

    @property
    def role_label(self) -> str:
        return rbac.ROLE_LABELS.get(self.role, self.role)

    @property
    def is_super(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "super_admin")

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _sign(claims: dict[str, Any], lifetime: timedelta) -> str:
    settings = get_settings()
    return jwt.encode(
        {**claims, "exp": datetime.now(UTC) + lifetime},
        settings.admin_session_secret,
        algorithm="HS256",
    )


def _read(token: str | None, purpose: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        claims = jwt.decode(token, get_settings().admin_session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return claims if claims.get("purpose") == purpose else None


def session_cookie(
    user_id: UUID,
    session_version: int = 0,
    sid: str | None = None,
    started: float | None = None,
) -> str:
    """The session lasts SESSION_HOURS from sign-in and ends after IDLE_MINUTES unused."""
    started = started or time.time()
    claims = {
        "sub": str(user_id),
        "purpose": "session",
        "sv": session_version,
        "sid": sid or secrets.token_urlsafe(12),
        "started": int(started),
        "seen": int(time.time()),
    }
    remaining = max(0.0, started + SESSION_HOURS * 3600 - time.time())
    return _sign(claims, timedelta(seconds=remaining))


def pending_cookie(access_token: str, factor_id: str, user_id: UUID) -> str:
    return _sign(
        {"token": access_token, "factor": factor_id, "sub": str(user_id), "purpose": "mfa"},
        timedelta(minutes=PENDING_MINUTES),
    )


def read_pending(token: str | None) -> dict[str, Any] | None:
    return _read(token, "mfa")


def password_cookie(user_id: UUID) -> str:
    return _sign({"sub": str(user_id), "purpose": "password"}, timedelta(minutes=PENDING_MINUTES))


def read_password_pending(token: str | None) -> dict[str, Any] | None:
    return _read(token, "password")


def csrf_token(session_value: str) -> str:
    """Bound to the sign-in, not the cookie, so refreshing the cookie keeps open forms valid."""
    claims = _read(session_value, "session") or {}
    secret = get_settings().admin_session_secret.encode()
    bound = f"{claims.get('sub')}:{claims.get('sid')}"
    return hmac.new(secret, bound.encode(), hashlib.sha256).hexdigest()


ADMIN_COLUMNS = (
    "id, role, reputation_score, created_at, location_precision, home_ward_id, display_name, "
    "email, phone, is_active, session_version, partner_id, require_two_step"
)


async def staff_row(session: AsyncSession, user_id: UUID) -> Any:
    return (
        await session.execute(
            text(f"SELECT {ADMIN_COLUMNS} FROM profiles WHERE id = :id"), {"id": user_id}
        )
    ).first()


async def load_scope(session: AsyncSession, user_id: UUID, partner_id: int | None) -> Scope:
    rows = (
        await session.execute(
            text(
                "SELECT lga_id, ward_id FROM staff_scopes WHERE profile_id = :id "
                "OR (CAST(:partner AS INT) IS NOT NULL AND partner_id = :partner)"
            ),
            {"id": user_id, "partner": partner_id},
        )
    ).all()
    return Scope(
        tuple(sorted({row.lga_id for row in rows if row.lga_id is not None})),
        tuple(sorted({row.ward_id for row in rows if row.ward_id is not None})),
    )


async def is_staff(session: AsyncSession, user_id: UUID) -> bool:
    role = await session.scalar(
        text("SELECT role FROM profiles WHERE id = :id AND is_active"), {"id": user_id}
    )
    return role in rbac.PANEL_ROLES


async def current_admin(request: Request, session: AsyncSession = Depends(get_session)) -> Admin:
    raw = request.cookies.get(COOKIE)
    claims = _read(raw, "session")
    if claims is None or raw is None:
        raise AdminRedirect
    now = time.time()
    if now - claims.get("seen", 0) > IDLE_MINUTES * 60:
        raise AdminRedirect
    row = await staff_row(session, UUID(claims["sub"]))
    if (
        row is None
        or not row.is_active
        or row.role not in rbac.PANEL_ROLES
        or row.session_version != claims.get("sv", 0)
    ):
        raise AdminRedirect
    if now - claims.get("seen", 0) > REFRESH_SECONDS:
        # Sliding idle timeout; the middleware in routes.install sets the new cookie.
        request.state.admin_cookie = session_cookie(
            row.id, row.session_version, claims.get("sid"), claims.get("started")
        )
    return Admin(
        Profile(
            id=row.id,
            role=row.role,
            reputation_score=row.reputation_score,
            created_at=row.created_at,
            location_precision=row.location_precision,
            home_ward_id=row.home_ward_id,
        ),
        row.display_name or row.email or "Staff",
        csrf_token(raw),
        email=row.email,
        permissions=await rbac.permissions_for(session, row.role),
        scope=await load_scope(session, row.id, row.partner_id),
        partner_id=row.partner_id,
    )


async def require_csrf(
    csrf: str = Form(default=""), admin: Admin = Depends(current_admin)
) -> Admin:
    if not hmac.compare_digest(csrf, admin.csrf):
        raise AdminForbidden
    return admin


def can(permission: str) -> Callable[..., Coroutine[Any, Any, Admin]]:
    """Page access: the signed-in staff member must hold this permission."""

    async def dependency(admin: Admin = Depends(current_admin)) -> Admin:
        if not admin.can(permission):
            raise AdminForbidden
        return admin

    return dependency


def act(permission: str) -> Callable[..., Coroutine[Any, Any, Admin]]:
    """Form posts: a valid CSRF token and this permission."""

    async def dependency(admin: Admin = Depends(require_csrf)) -> Admin:
        if not admin.can(permission):
            raise AdminForbidden
        return admin

    return dependency


async def require_super(admin: Admin = Depends(current_admin)) -> Admin:
    if not admin.is_super:
        raise AdminForbidden
    return admin


async def require_super_post(admin: Admin = Depends(require_csrf)) -> Admin:
    if not admin.is_super:
        raise AdminForbidden
    return admin


# Supabase calls


async def password_sign_in(email: str, password: str) -> dict[str, Any] | None:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": settings.supabase_anon_key},
            json={"email": email, "password": password},
            timeout=15,
        )
    return response.json() if response.status_code == 200 else None


def _user_headers(access_token: str) -> dict[str, str]:
    return {
        "apikey": get_settings().supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
    }


async def first_totp_factor(access_token: str) -> str | None:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.supabase_url}/auth/v1/user", headers=_user_headers(access_token), timeout=15
        )
    if response.status_code != 200:
        return None
    for factor in response.json().get("factors") or []:
        if factor.get("factor_type") == "totp" and factor.get("status") == "verified":
            return factor["id"]
    return None


async def enroll_totp(access_token: str) -> dict[str, str] | None:
    """Starts a new authenticator factor. It only counts once a code from it is verified."""
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.supabase_url}/auth/v1/factors",
            headers=_user_headers(access_token),
            json={"factor_type": "totp", "friendly_name": f"Dey Alert {secrets.token_hex(3)}"},
            timeout=15,
        )
    if response.status_code != 200:
        return None
    body = response.json()
    qr_code = body["totp"]["qr_code"]
    # Supabase returns raw SVG after a utf-8 prefix, which breaks in an img tag at the first '#'.
    prefix = "data:image/svg+xml;utf-8,"
    if qr_code.startswith(prefix):
        svg = base64.b64encode(qr_code[len(prefix) :].encode()).decode()
        qr_code = f"data:image/svg+xml;base64,{svg}"
    return {"id": body["id"], "qr_code": qr_code, "secret": body["totp"]["secret"]}


async def verify_totp(access_token: str, factor_id: str, code: str) -> bool:
    settings = get_settings()
    headers = _user_headers(access_token)
    base = f"{settings.supabase_url}/auth/v1/factors/{factor_id}"
    async with httpx.AsyncClient() as client:
        challenge = await client.post(f"{base}/challenge", headers=headers, timeout=15)
        if challenge.status_code != 200:
            return False
        verified = await client.post(
            f"{base}/verify",
            headers=headers,
            json={"challenge_id": challenge.json()["id"], "code": code},
            timeout=15,
        )
    return verified.status_code == 200


async def admin_update_user(user_id: UUID, body: dict[str, Any]) -> bool:
    settings = get_settings()
    key = settings.supabase_service_role_key
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            json=body,
            timeout=15,
        )
    return response.status_code == 200


async def set_password(user_id: UUID, password: str) -> bool:
    """Sets a new password and clears the must-change flag left by an invite or reset."""
    return await admin_update_user(
        user_id, {"password": password, "app_metadata": {"must_change_password": False}}
    )
