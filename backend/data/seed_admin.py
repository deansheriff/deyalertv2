"""Create the email admin for the admin panel.

Usage:
    python -m data.seed_admin you@example.com
        Create the admin, or reset an existing one, and print a one-time password.
    python -m data.seed_admin --from-env
        Runs on every deploy. Creates ADMIN_SEED_EMAIL with ADMIN_SEED_PASSWORD if that
        account does not exist yet, and never changes an existing account's password.

Either way the panel makes the admin choose a new password at first sign-in.
"""

import asyncio
import secrets
import sys
from uuid import UUID

import asyncpg
import httpx

from app.core.config import get_settings

MIN_PASSWORD = 12


async def ensure_admin(email: str, password: str, *, reset: bool) -> str:
    """Returns "created", "reset" or "exists"."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
    key = settings.supabase_service_role_key
    body = {
        "password": password,
        "email_confirm": True,
        "app_metadata": {"must_change_password": True},
    }
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        existing = await conn.fetchval(
            "SELECT id FROM auth.users WHERE lower(email) = lower($1)", email
        )
        if existing is not None and not reset:
            # The account may have been made by hand in Supabase without a profile.
            await conn.execute(
                "INSERT INTO profiles (id, email, role, display_name) "
                "VALUES ($1, lower($2), 'admin', 'Admin') ON CONFLICT (id) DO NOTHING",
                existing,
                email,
            )
            return "exists"
        async with httpx.AsyncClient(
            base_url=f"{settings.supabase_url}/auth/v1/admin",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=15,
        ) as client:
            if existing is None:
                response = await client.post("/users", json={"email": email, **body})
            else:
                response = await client.put(f"/users/{existing}", json=body)
        if response.status_code >= 300:
            raise RuntimeError(f"Supabase refused ({response.status_code}): {response.text[:300]}")
        await conn.execute(
            "INSERT INTO profiles (id, email, role, display_name) "
            "VALUES ($1, lower($2), 'admin', 'Admin') "
            "ON CONFLICT (id) DO UPDATE SET role = 'admin', is_active = true",
            UUID(response.json()["id"]),
            email,
        )
    finally:
        await conn.close()
    return "reset" if existing else "created"


async def from_env() -> None:
    """Never fails the deploy: a problem here is printed and the API still starts."""
    settings = get_settings()
    email, password = settings.admin_seed_email, settings.admin_seed_password
    if not email:
        print("Admin seed: ADMIN_SEED_EMAIL is not set, skipping.")
        return
    if len(password) < MIN_PASSWORD:
        print(f"Admin seed: ADMIN_SEED_PASSWORD needs at least {MIN_PASSWORD} characters.")
        return
    try:
        outcome = await ensure_admin(email, password, reset=False)
    except Exception as error:  # noqa: BLE001
        print(f"Admin seed failed for {email}: {error}")
        return
    if outcome == "exists":
        print(f"Admin seed: {email} already exists, left unchanged.")
    else:
        print(f"Admin seed: created {email}. It must choose a new password at first sign-in.")


async def reset_from_cli(email: str) -> None:
    password = secrets.token_urlsafe(12)
    try:
        outcome = await ensure_admin(email, password, reset=True)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    settings = get_settings()
    print(f"{outcome.capitalize()} admin {email}.")
    print(f"One-time password: {password}")
    print(f"Sign in at {settings.public_base_url}/admin/login to choose your own password.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(from_env() if sys.argv[1] == "--from-env" else reset_from_cli(sys.argv[1]))
