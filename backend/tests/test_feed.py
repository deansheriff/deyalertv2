from datetime import timedelta

from tests.conftest import acting_as, make_incident, make_user

CENTRE = (6.55, 3.32)
# Roughly 1 km and 22 km north of the centre.
NEAR_LAT = 6.56
FAR_LAT = 6.75


async def feed_ids(conn, user, *extra_args):
    args = ", ".join(str(value) for value in (*CENTRE, *extra_args))
    async with acting_as(conn, "authenticated", user):
        rows = await conn.fetch(f"SELECT id FROM nearby_incidents({args})")
    return {row["id"] for row in rows}


async def test_feed_returns_live_incidents_inside_radius(conn):
    user = await make_user(conn, "+2348000000030")
    near = await make_incident(conn, lat=NEAR_LAT)
    far = await make_incident(conn, lat=FAR_LAT)
    ids = await feed_ids(conn, user)
    assert near in ids
    assert far not in ids


async def test_feed_radius_is_capped_at_20km(conn):
    user = await make_user(conn, "+2348000000031")
    beyond_cap = await make_incident(conn, lat=FAR_LAT)
    assert beyond_cap not in await feed_ids(conn, user, 500000)


async def test_feed_drops_incidents_past_expiry_before_the_job_runs(conn):
    user = await make_user(conn, "+2348000000032")
    stale = await make_incident(conn, expires_in=timedelta(minutes=-1), status="unconfirmed")
    assert stale not in await feed_ids(conn, user)


async def test_feed_excludes_terminal_states_and_hidden(conn):
    user = await make_user(conn, "+2348000000033")
    excluded = [
        await make_incident(conn, status=status)
        for status in ("expired", "resolved", "under_review", "false_report")
    ]
    excluded.append(await make_incident(conn, is_hidden=True))
    assert not set(excluded) & await feed_ids(conn, user)


async def test_history_includes_expired_and_resolved_only(conn):
    user = await make_user(conn, "+2348000000034")
    expired = await make_incident(
        conn, status="expired", expires_in=timedelta(hours=-1), created_ago=timedelta(hours=2)
    )
    resolved = await make_incident(conn, status="resolved")
    false_report = await make_incident(conn, status="false_report")
    args = ("5000", "NOW() - INTERVAL '7 days'", "true")
    ids = await feed_ids(conn, user, *args)
    assert {expired, resolved} <= ids
    assert false_report not in ids


async def test_public_view_drops_incidents_older_than_seven_days(conn):
    user = await make_user(conn, "+2348000000035")
    old = await make_incident(conn, created_ago=timedelta(days=8), expires_in=timedelta(hours=1))
    args = ("5000", "NOW() - INTERVAL '30 days'", "true")
    assert old not in await feed_ids(conn, user, *args)
