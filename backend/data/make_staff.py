"""Give an existing account a staff role. The first admin has to be created this way.

Usage:
    python -m data.make_staff +2348012345678 admin
"""

import asyncio
import sys

import asyncpg

from app.core.config import get_settings


async def main(phone: str, role: str) -> None:
    if role not in ("moderator", "admin"):
        raise SystemExit("Role must be moderator or admin")
    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        result = await conn.execute("UPDATE profiles SET role = $1 WHERE phone = $2", role, phone)
    finally:
        await conn.close()
    if result.endswith(" 0"):
        raise SystemExit("No account uses that phone number. Sign in on the app first.")
    print(f"{phone} is now {role}.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
