"""Test database setup.

Uses TEST_DATABASE_URL when set (the database name must end in _test because it is
wiped), otherwise starts a throwaway PostGIS container with testcontainers.
"""

import contextlib
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
STUB_SQL = (BACKEND_DIR / "dev" / "supabase_stub.sql").read_text(encoding="utf-8")

RESET_SQL = """
    DROP SCHEMA IF EXISTS public CASCADE;
    DROP SCHEMA IF EXISTS auth CASCADE;
    CREATE SCHEMA public;
"""

# A 0.1 degree square LGA split into a west ward and an east ward.
LGA_WKT = "MULTIPOLYGON(((3.3 6.5,3.4 6.5,3.4 6.6,3.3 6.6,3.3 6.5)))"
WEST_WARD_WKT = "MULTIPOLYGON(((3.3 6.5,3.35 6.5,3.35 6.6,3.3 6.6,3.3 6.5)))"
EAST_WARD_WKT = "MULTIPOLYGON(((3.35 6.5,3.4 6.5,3.4 6.6,3.35 6.6,3.35 6.5)))"


def plain_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


def run_alembic(database_url: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
    )


async def reset_database(database_url: str) -> None:
    conn = await asyncpg.connect(plain_dsn(database_url))
    try:
        await conn.execute(RESET_SQL)
        await conn.execute(STUB_SQL)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        if not url.rsplit("/", 1)[-1].endswith("_test"):
            pytest.exit("TEST_DATABASE_URL must point at a database ending in _test", 2)
        yield url
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgis/postgis:17-3.5", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session", autouse=True)
async def migrated(database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    await reset_database(database_url)
    run_alembic(database_url, "upgrade", "head")


@pytest.fixture
async def conn(database_url: str, migrated: None) -> AsyncIterator[asyncpg.Connection]:
    """A connection whose work is rolled back after the test."""
    connection = await asyncpg.connect(plain_dsn(database_url))
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@contextlib.asynccontextmanager
async def acting_as(conn: asyncpg.Connection, role: str, user_id: uuid.UUID | None = None):
    """Runs a block as a Supabase client role. Wrap in pytest.raises to expect a denial."""
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {role}")
        await conn.execute(
            "SELECT set_config('request.jwt.claim.sub', $1, true)", str(user_id or "")
        )
        yield
        await conn.execute("RESET ROLE")


async def make_user(conn: asyncpg.Connection, phone: str, role: str = "member") -> uuid.UUID:
    user_id = uuid.uuid4()
    await conn.execute("INSERT INTO auth.users (id, phone) VALUES ($1, $2)", user_id, phone)
    await conn.execute(
        "INSERT INTO profiles (id, phone, role) VALUES ($1, $2, $3)", user_id, phone, role
    )
    return user_id


async def make_incident(
    conn: asyncpg.Connection,
    reporter_id: uuid.UUID | None = None,
    lat: float = 6.55,
    lng: float = 3.32,
    public_lat: float | None = None,
    public_lng: float | None = None,
    status: str = "unconfirmed",
    expires_in: timedelta = timedelta(hours=3),
    is_hidden: bool = False,
    created_ago: timedelta = timedelta(0),
) -> uuid.UUID:
    now = datetime.now(UTC)
    incident_id = await conn.fetchval(
        """
        INSERT INTO incidents (client_id, type_code, location, public_location, status,
                               is_hidden, occurred_at, created_at, expires_at)
        VALUES ($1, 'roadblock',
                ST_SetSRID(ST_MakePoint($2::float8, $3::float8), 4326)::geography,
                ST_SetSRID(ST_MakePoint($4::float8, $5::float8), 4326)::geography,
                $6, $7, $8, $8, $9)
        RETURNING id
        """,
        uuid.uuid4(),
        lng,
        lat,
        public_lng if public_lng is not None else lng,
        public_lat if public_lat is not None else lat,
        status,
        is_hidden,
        now - created_ago,
        now + expires_in,
    )
    if reporter_id is not None:
        await conn.execute(
            "INSERT INTO incident_reporters (incident_id, reporter_id) VALUES ($1, $2)",
            incident_id,
            reporter_id,
        )
    return incident_id
