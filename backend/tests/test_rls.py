import asyncpg
import pytest

from tests.conftest import acting_as, make_incident, make_user

SERVER_ONLY_TABLES = [
    "incidents",
    "incident_reporters",
    "incident_events",
    "incident_media",
    "corroborations",
    "incident_flags",
    "devices",
    "verifiers",
    "sos_location_pings",
    "sos_deliveries",
    "notification_log",
    "audit_log",
    "app_settings",
]

CLIENT_READ_OBJECTS = [
    "incidents_public",
    "incident_events_public",
    "incident_media_public",
    "profiles",
    "notifications",
    "trusted_contacts",
    "sos_alerts",
]

Denied = asyncpg.InsufficientPrivilegeError


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", SERVER_ONLY_TABLES)
async def test_clients_cannot_read_server_only_tables(conn, role, table):
    user = await make_user(conn, "+2348000000010")
    with pytest.raises(Denied):
        async with acting_as(conn, role, user):
            await conn.fetch(f"SELECT * FROM {table}")


@pytest.mark.parametrize("table", CLIENT_READ_OBJECTS)
async def test_anon_cannot_read_client_objects(conn, table):
    with pytest.raises(Denied):
        async with acting_as(conn, "anon"):
            await conn.fetch(f"SELECT * FROM {table}")


async def test_anon_cannot_call_feed(conn):
    with pytest.raises(Denied):
        async with acting_as(conn, "anon"):
            await conn.fetch("SELECT * FROM nearby_incidents(6.55, 3.32)")


async def test_public_view_cannot_be_written_through(conn):
    user = await make_user(conn, "+2348000000011")
    incident = await make_incident(conn, reporter_id=user)
    statements = [
        ("UPDATE incidents_public SET status = 'confirmed' WHERE id = $1", incident),
        ("DELETE FROM incidents_public WHERE id = $1", incident),
    ]
    for statement, argument in statements:
        with pytest.raises(Denied):
            async with acting_as(conn, "authenticated", user):
                await conn.execute(statement, argument)


async def test_public_view_never_exposes_true_location_or_reporter(conn):
    user = await make_user(conn, "+2348000000012")
    incident = await make_incident(
        conn, reporter_id=user, lat=6.55, lng=3.32, public_lat=6.56, public_lng=3.33
    )
    async with acting_as(conn, "authenticated", user):
        row = await conn.fetchrow("SELECT * FROM incidents_public WHERE id = $1", incident)
    assert row["lat"] == pytest.approx(6.56)
    assert row["lng"] == pytest.approx(3.33)
    assert not {"reporter_id", "public_location", "confirmed_by", "is_anonymous"} & set(row.keys())


async def test_public_timeline_hides_actor_and_moderation_events(conn):
    user = await make_user(conn, "+2348000000013")
    incident = await make_incident(conn, reporter_id=user)
    await conn.execute(
        "INSERT INTO incident_events (incident_id, actor_id, event_type, note) VALUES "
        "($1, $2, 'created', 'private'), ($1, $2, 'flagged', 'private')",
        incident,
        user,
    )
    async with acting_as(conn, "authenticated", user):
        rows = await conn.fetch(
            "SELECT * FROM incident_events_public WHERE incident_id = $1", incident
        )
    assert [row["event_type"] for row in rows] == ["created"]
    assert not {"actor_id", "note"} & set(rows[0].keys())


async def test_public_media_only_shows_moderated_files(conn):
    user = await make_user(conn, "+2348000000014")
    incident = await make_incident(conn, reporter_id=user)
    await conn.execute(
        "INSERT INTO incident_media (incident_id, kind, storage_path, moderation_status) VALUES "
        "($1, 'image', 'a', 'pending'), ($1, 'image', 'b', 'approved'), "
        "($1, 'image', 'c', 'blurred'), ($1, 'image', 'd', 'rejected')",
        incident,
    )
    async with acting_as(conn, "authenticated", user):
        rows = await conn.fetch(
            "SELECT storage_path FROM incident_media_public WHERE incident_id = $1", incident
        )
    assert sorted(row["storage_path"] for row in rows) == ["b", "c"]


async def test_members_only_see_their_own_rows(conn):
    member_a = await make_user(conn, "+2348000000015")
    member_b = await make_user(conn, "+2348000000016")
    for user, phone in ((member_a, "+2348000000020"), (member_b, "+2348000000021")):
        await conn.execute(
            "INSERT INTO notifications (user_id, kind, title) VALUES ($1, 'new_nearby', 'x')", user
        )
        await conn.execute(
            "INSERT INTO trusted_contacts (user_id, name, phone) VALUES ($1, 'c', $2)", user, phone
        )

    async with acting_as(conn, "authenticated", member_a):
        for table, column in (
            ("profiles", "id"),
            ("notifications", "user_id"),
            ("trusted_contacts", "user_id"),
        ):
            rows = await conn.fetch(f"SELECT {column} AS owner FROM {table}")
            assert [row["owner"] for row in rows] == [member_a]


async def test_notification_update_is_limited_to_own_read_marker(conn):
    member_a = await make_user(conn, "+2348000000017")
    member_b = await make_user(conn, "+2348000000018")
    other = await conn.fetchval(
        "INSERT INTO notifications (user_id, kind, title) VALUES ($1, 'new_nearby', 'x') "
        "RETURNING id",
        member_b,
    )
    mine = await conn.fetchval(
        "INSERT INTO notifications (user_id, kind, title) VALUES ($1, 'new_nearby', 'x') "
        "RETURNING id",
        member_a,
    )
    update = "UPDATE notifications SET read_at = NOW() WHERE id = $1"
    async with acting_as(conn, "authenticated", member_a):
        assert await conn.execute(update, mine) == "UPDATE 1"
        assert await conn.execute(update, other) == "UPDATE 0"

    with pytest.raises(Denied):
        async with acting_as(conn, "authenticated", member_a):
            await conn.execute("UPDATE notifications SET title = 'edited' WHERE id = $1", mine)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE profiles SET reputation_score = 999",
        "UPDATE profiles SET role = 'admin'",
        "DELETE FROM profiles",
    ],
)
async def test_members_cannot_edit_their_profile_directly(conn, statement):
    user = await make_user(conn, "+2348000000019")
    with pytest.raises(Denied):
        async with acting_as(conn, "authenticated", user):
            await conn.execute(statement)
