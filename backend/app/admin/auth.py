import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Form, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.services.profiles import Profile

COOKIE = "dey_admin"
PENDING_COOKIE = "dey_admin_pending"
SESSION_HOURS = 8
PENDING_MINUTES = 5


class AdminRedirect(Exception):
    """Raised when the visitor needs to sign in."""


class AdminForbidden(Exception):
    """Raised when a signed-in moderator lacks the required role."""


@dataclass(frozen=True)
class Admin:
    profile: Profile
    name: str
    csrf: str

    @property
    def is_admin(self) -> bool:
        return self.profile.role == "admin"


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


def session_cookie(user_id: UUID) -> str:
    return _sign({"sub": str(user_id), "purpose": "session"}, timedelta(hours=SESSION_HOURS))


def password_cookie(user_id: UUID) -> str:
    return _sign({"sub": str(user_id), "purpose": "password"}, timedelta(minutes=PENDING_MINUTES))


def read_password_pending(token: str | None) -> dict[str, Any] | None:
    return _read(token, "password")


async def is_staff(session: AsyncSession, user_id: UUID) -> bool:
    role = await session.scalar(
        text("SELECT role FROM profiles WHERE id = :id AND is_active"), {"id": user_id}
    )
    return role in ("moderator", "admin")


def csrf_token(session_value: str) -> str:
    secret = get_settings().admin_session_secret.encode()
    return hmac.new(secret, session_value.encode(), hashlib.sha256).hexdigest()


async def current_admin(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Admin:
    raw = request.cookies.get(COOKIE)
    claims = _read(raw, "session")
    if claims is None or raw is None:
        raise AdminRedirect
    row = (
        await session.execute(
            text(
                "SELECT id, role, reputation_score, created_at, location_precision, "
                "home_ward_id, display_name, phone, is_active FROM profiles WHERE id = :id"
            ),
            {"id": UUID(claims["sub"])},
        )
    ).first()
    if row is None or not row.is_active or row.role not in ("moderator", "admin"):
        raise AdminRedirect
    return Admin(
        Profile(
            id=row.id,
            role=row.role,
            reputation_score=row.reputation_score,
            created_at=row.created_at,
            location_precision=row.location_precision,
            home_ward_id=row.home_ward_id,
        ),
        row.display_name or "Moderator",
        csrf_token(raw),
    )


async def require_csrf(
    csrf: str = Form(default=""), admin: Admin = Depends(current_admin)
) -> Admin:
    if not hmac.compare_digest(csrf, admin.csrf):
        raise AdminForbidden
    return admin


async def require_full_admin(admin: Admin = Depends(require_csrf)) -> Admin:
    if not admin.is_admin:
        raise AdminForbidden
    return admin


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


async def set_password(user_id: UUID, password: str) -> bool:
    """Sets a new password and clears the must-change flag left by data.seed_admin."""
    settings = get_settings()
    key = settings.supabase_service_role_key
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            json={"password": password, "app_metadata": {"must_change_password": False}},
            timeout=15,
        )
    return response.status_code == 200
