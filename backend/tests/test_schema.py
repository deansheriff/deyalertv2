import asyncpg
import pytest

from tests.conftest import make_incident, make_user, plain_dsn, reset_database, run_alembic

EXPECTED_TTLS = {
    "roadblock": 180,
    "suspicious_activity": 180,
    "accident": 180,
    "fire_outbreak": 360,
    "gunshots": 360,
    "mob_action": 360,
    "other": 360,
    "armed_robbery": 720,
    "cult_clash": 720,
    "flooding": 720,
    "kidnapping": 1440,
    "banditry": 1440,
}


async def test_incident_types_are_seeded_with_plan_ttls(conn):
    rows = await conn.fetch("SELECT code, ttl_minutes FROM incident_types")
    assert {row["code"]: row["ttl_minutes"] for row in rows} == EXPECTED_TTLS


async def test_every_application_table_has_rls(conn):
    rows = await conn.fetch(
        """
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
          AND c.relname NOT IN ('spatial_ref_sys', 'alembic_version')
        """
    )
    assert [row["relname"] for row in rows] == []


async def test_alert_point_uses_last_location_then_home_point(conn):
    user = await make_user(conn, "+2348000000001")
    await conn.execute(
        "UPDATE profiles SET home_point = ST_GeogFromText('POINT(3.3 6.5)') WHERE id = $1", user
    )
    device_id = await conn.fetchval(
        "INSERT INTO devices (user_id, fcm_token, platform) VALUES ($1, 'token', 'android') "
        "RETURNING id",
        user,
    )
    x_query = "SELECT ST_X(alert_point::geometry) FROM devices WHERE id = $1"
    assert await conn.fetchval(x_query, device_id) == pytest.approx(3.3)

    await conn.execute(
        "UPDATE profiles SET home_point = ST_GeogFromText('POINT(3.31 6.5)') WHERE id = $1", user
    )
    assert await conn.fetchval(x_query, device_id) == pytest.approx(3.31)

    await conn.execute(
        "UPDATE devices SET last_location = ST_GeogFromText('POINT(3.5 6.7)') WHERE id = $1",
        device_id,
    )
    assert await conn.fetchval(x_query, device_id) == pytest.approx(3.5)


async def test_deleting_account_removes_identity_and_keeps_incident(conn):
    reporter = await make_user(conn, "+2348000000002")
    other = await make_user(conn, "+2348000000003")
    incident = await make_incident(conn, reporter_id=reporter)
    device = await conn.fetchval(
        "INSERT INTO devices (user_id, fcm_token, platform) VALUES ($1, 'token-2', 'android') "
        "RETURNING id",
        other,
    )
    point = "ST_GeogFromText('POINT(3.32 6.55)')"
    await conn.execute(
        f"INSERT INTO corroborations (incident_id, user_id, device_id, location, distance_m) "
        f"VALUES ($1, $2, $3, {point}, 10)",
        incident,
        other,
        device,
    )
    await conn.execute(
        "INSERT INTO incident_flags (incident_id, flagged_by, reason) "
        "VALUES ($1, $2, 'spam')",
        incident,
        other,
    )
    await conn.execute(
        "INSERT INTO incident_events (incident_id, actor_id, event_type) "
        "VALUES ($1, $2, 'created')",
        incident,
        reporter,
    )

    await conn.execute("DELETE FROM auth.users WHERE id = ANY($1::uuid[])", [reporter, other])

    assert await conn.fetchval("SELECT count(*) FROM incidents WHERE id = $1", incident) == 1
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM incident_reporters WHERE incident_id = $1", incident
        )
        == 0
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM incident_events WHERE incident_id = $1 AND actor_id IS NULL",
            incident,
        )
        == 1
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM corroborations WHERE incident_id = $1 AND user_id IS NULL",
            incident,
        )
        == 1
    )


async def test_migrations_downgrade_and_reapply(database_url):
    run_alembic(database_url, "downgrade", "base")
    conn = await asyncpg.connect(plain_dsn(database_url))
    try:
        assert await conn.fetchval("SELECT to_regclass('public.incidents')") is None
    finally:
        await conn.close()
    await reset_database(database_url)
    run_alembic(database_url, "upgrade", "head")
