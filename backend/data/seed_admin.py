"""Create an email admin for the admin panel, or reset one that already exists.

Usage:
    python -m data.seed_admin you@example.com

Prints a one-time password. At first sign-in the panel makes the admin choose a new one.
"""

import asyncio
import secrets
import sys
from uuid import UUID

import asyncpg
import httpx

from app.core.config import get_settings


async def main(email: str) -> None:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
    password = secrets.token_urlsafe(12)
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
            raise SystemExit(f"Supabase refused ({response.status_code}): {response.text[:300]}")
        user_id = UUID(response.json()["id"])
        await conn.execute(
            "INSERT INTO profiles (id, role, display_name) VALUES ($1, 'admin', 'Admin') "
            "ON CONFLICT (id) DO UPDATE SET role = 'admin', is_active = true",
            user_id,
        )
    finally:
        await conn.close()
    action = "Reset" if existing else "Created"
    print(f"{action} admin {email}.")
    print(f"One-time password: {password}")
    print(f"Sign in at {settings.public_base_url}/admin/login to choose your own password.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1]))
