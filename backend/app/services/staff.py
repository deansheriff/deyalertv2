"""Staff accounts: Supabase email sign-in plus a profile with a panel role."""

import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services import mailer


class StaffError(Exception):
    """Shown to the admin as is."""


@dataclass(frozen=True)
class Invite:
    user_id: UUID
    created: bool
    password: str
    emailed: bool


def temporary_password() -> str:
    return secrets.token_urlsafe(12)


def _admin_api() -> httpx.AsyncClient:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise StaffError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set on the server.")
    key = settings.supabase_service_role_key
    return httpx.AsyncClient(
        base_url=f"{settings.supabase_url}/auth/v1/admin",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=15,
    )


async def _existing_user(session: AsyncSession, email: str) -> UUID | None:
    return await session.scalar(
        text("SELECT id FROM auth.users WHERE lower(email) = lower(:email)"), {"email": email}
    )


async def upsert_account(session: AsyncSession, email: str, password: str) -> tuple[UUID, bool]:
    """Creates the sign-in, or gives an existing one a new one-time password."""
    body = {
        "password": password,
        "email_confirm": True,
        "app_metadata": {"must_change_password": True},
    }
    existing = await _existing_user(session, email)
    async with _admin_api() as client:
        if existing is None:
            response = await client.post("/users", json={"email": email, **body})
        else:
            response = await client.put(f"/users/{existing}", json=body)
    if response.status_code >= 300:
        raise StaffError(f"Supabase refused ({response.status_code}): {response.text[:200]}")
    return UUID(response.json()["id"]), existing is None


async def reset_password(session: AsyncSession, user_id: UUID) -> str:
    password = temporary_password()
    async with _admin_api() as client:
        response = await client.put(
            f"/users/{user_id}",
            json={"password": password, "app_metadata": {"must_change_password": True}},
        )
    if response.status_code >= 300:
        raise StaffError(f"Supabase refused ({response.status_code}): {response.text[:200]}")
    await sign_out_everywhere(session, user_id)
    return password


async def sign_out_everywhere(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        text("UPDATE profiles SET session_version = session_version + 1 WHERE id = :id"),
        {"id": user_id},
    )


async def invite(
    session: AsyncSession,
    *,
    email: str,
    name: str,
    role: str,
    invited_by: UUID | None,
    require_two_step: bool = False,
    partner_id: int | None = None,
    lga_ids: list[int] | None = None,
    ward_ids: list[int] | None = None,
) -> Invite:
    email = email.strip().lower()
    if "@" not in email:
        raise StaffError("Enter a valid email address.")
    password = temporary_password()
    user_id, created = await upsert_account(session, email, password)
    await session.execute(
        text(
            """
            INSERT INTO profiles (id, email, role, display_name, invited_by, require_two_step,
                                  partner_id)
            VALUES (:id, :email, :role, :name, :by, :two_step, :partner)
            ON CONFLICT (id) DO UPDATE SET
                role = EXCLUDED.role,
                display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), profiles.display_name),
                require_two_step = EXCLUDED.require_two_step,
                partner_id = EXCLUDED.partner_id,
                invited_by = COALESCE(profiles.invited_by, EXCLUDED.invited_by),
                is_active = true,
                session_version = profiles.session_version + 1
            """
        ),
        {
            "id": user_id,
            "email": email,
            "role": role,
            "name": name.strip() or None,
            "by": invited_by,
            "two_step": require_two_step,
            "partner": partner_id,
        },
    )
    await set_scope(session, user_id, lga_ids or [], ward_ids or [])
    emailed = await _send_invite(session, email, name, password)
    return Invite(user_id, created, password, emailed)


async def set_scope(
    session: AsyncSession, user_id: UUID, lga_ids: list[int], ward_ids: list[int]
) -> None:
    await session.execute(text("DELETE FROM staff_scopes WHERE profile_id = :id"), {"id": user_id})
    for lga_id in sorted(set(lga_ids)):
        await session.execute(
            text("INSERT INTO staff_scopes (profile_id, lga_id) VALUES (:id, :lga)"),
            {"id": user_id, "lga": lga_id},
        )
    for ward_id in sorted(set(ward_ids)):
        await session.execute(
            text("INSERT INTO staff_scopes (profile_id, ward_id) VALUES (:id, :ward)"),
            {"id": user_id, "ward": ward_id},
        )


async def _send_invite(session: AsyncSession, email: str, name: str, password: str) -> bool:
    config = await mailer.load(session)
    if config is None:
        return False
    link = f"{get_settings().public_base_url}/admin/login"
    greeting = f"Hello {name.strip()}," if name.strip() else "Hello,"
    body = (
        f"{greeting}\n\nYou have been given access to the Dey Alert admin panel.\n\n"
        f"Sign in at {link}\nEmail: {email}\nOne-time password: {password}\n\n"
        "You will choose your own password when you first sign in. Every action in the panel "
        "is recorded."
    )
    try:
        await mailer.send(config, email, "Your Dey Alert admin access", body)
    except mailer.MailError:
        return False
    return True


async def active_super_admins(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            text("SELECT count(*) FROM profiles WHERE role = 'super_admin' AND is_active")
        )
        or 0
    )


async def staff_list(session: AsyncSession, status: str) -> list[Any]:
    condition = {
        "active": "p.is_active AND NOT must_change",
        "invited": "p.is_active AND must_change",
        "disabled": "NOT p.is_active",
    }[status]
    return (
        await session.execute(
            text(
                f"""
                SELECT * FROM (
                    SELECT p.id, p.email, p.display_name, p.role, p.is_active,
                           p.require_two_step, p.partner_id, pa.name AS partner,
                           COALESCE((u.raw_app_meta_data ->> 'must_change_password')::boolean,
                                    false) AS must_change,
                           (SELECT max(created_at) FROM staff_logins s
                             WHERE s.profile_id = p.id AND s.outcome = 'success') AS last_sign_in,
                           (SELECT string_agg(COALESCE(l.name, w.name), ', ')
                              FROM staff_scopes sc
                              LEFT JOIN lgas l ON l.id = sc.lga_id
                              LEFT JOIN wards w ON w.id = sc.ward_id
                             WHERE sc.profile_id = p.id) AS scope
                    FROM profiles p
                    LEFT JOIN auth.users u ON u.id = p.id
                    LEFT JOIN partners pa ON pa.id = p.partner_id
                    WHERE p.role IN ('super_admin','admin','moderator','verifier','partner')
                      AND p.email IS NOT NULL
                ) p
                WHERE {condition}
                ORDER BY array_position(
                    ARRAY['super_admin','admin','moderator','verifier','partner'], p.role),
                    p.display_name NULLS LAST
                """
            )
        )
    ).all()
